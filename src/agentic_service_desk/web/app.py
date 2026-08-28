"""웹 진입점 — 운영자 대시보드 (§8, FR-44·59).

**최종 이용자 대면 UI 가 아니다.** 그것은 모 시스템이 렌더링한다(§13). 여기 오는
사람은 운영자 하나뿐이며, 그는 **루프의 일부**다 — 여기서 처리되지 않으면 시스템은
사고 없이 조용히 성장을 멈춘다 (§8.4).

지금 켜진 것은 S0 이라 **Q4(모순)·Q8(지식 공백)과 지식베이스 현황**뿐이다.
나머지 대기열은 그 기능이 켜지는 단계에 맞춰 붙는다 (FR-59).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from agentic_service_desk import __version__
from agentic_service_desk.config import Settings, load_settings
from agentic_service_desk.web.dashboard import Dashboard, queues_for_stage
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind
from agentic_service_desk.operations import intake
from agentic_service_desk.operations import manual_entry
from agentic_service_desk.knowledge.search import Search
from agentic_service_desk.operations import promotion as promotion_domain
from agentic_service_desk.adapters.factory import build_parent_system
from agentic_service_desk.pipeline import draft_store, review as review_domain
from agentic_service_desk.pipeline import publication
from agentic_service_desk.operations import resolution as resolution_domain
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations.schema import connect, initialize

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(settings: Settings | None = None) -> FastAPI:
    """앱을 만든다. 설정을 인자로 받는 이유는 테스트에서 갈아 끼우기 위해서다."""
    cfg = settings or load_settings()
    app = FastAPI(title="Agentic Service Desk", version=__version__)

    def dashboard() -> tuple[Dashboard, object]:
        """요청마다 새로 연다.

        **연결을 들고 있지 않는다.** 온라인과 배치가 같은 SQLite 파일을 쓰므로
        (ADR-002) 쓰기를 짧게 유지해야 한다 — 화면 하나가 연결을 붙들고 있으면
        배치가 그 뒤에서 기다린다.
        """
        conn = connect(cfg.operations_db)
        initialize(conn)
        return Dashboard(repo=KnowledgeRepository(cfg.knowledge_dir), conn=conn), conn

    parent_cache: dict[str, object] = {}

    def parent_system():  # noqa: ANN202
        """모 시스템 어댑터. **앱 수명 동안 하나만 만든다.**

        요청마다 만들면 HTTP 클라이언트가 매번 새로 열리고, 무엇보다 `mock` 은
        인메모리라 **게재한 답변이 그 요청과 함께 사라진다** — 개발 중에 게재가
        되는지 확인할 방법이 없어진다.

        지연 생성인 것은 `http` 어댑터가 주소 없이 만들어지길 거부하기 때문이다
        (`NotConfigured`). 연동 전 단계(S0)에서도 대시보드는 떠야 한다.
        """
        if "it" not in parent_cache:
            parent_cache["it"] = build_parent_system(cfg)
        return parent_cache["it"]

    def shell() -> dict:
        """모든 화면이 함께 쓰는 것 — 켜진 단계와 그 단계의 대기열."""
        return {"stage": cfg.stage, "queues": queues_for_stage(cfg.stage)}

    @app.get("/health")
    def health() -> dict[str, str]:
        """살아 있는지."""
        return {"status": "ok", "version": __version__}

    @app.get("/")
    def index(request: Request):  # noqa: ANN201
        board, conn = dashboard()
        try:
            status = board.knowledge_status()
            counts = {
                "Q1": len(board.tickets()),
                "Q2": len(draft_store.pending(conn)),
                "Q4": status.open_contradictions,
                "Q8": len(board.knowledge_gaps()),
            }
            next_up = board.next_up(cfg.stage)
        finally:
            conn.close()
        ctx = shell()
        # 아직 자료가 없는 대기열은 0 으로 둔다 — 화면에 뜨는 것과 셀 수 있는 것은 다르다.
        ctx |= {
            "s": status,
            "counts": {q.id: counts.get(q.id, 0) for q in ctx["queues"]},
            "next_up": next_up,
        }
        return TEMPLATES.TemplateResponse(request, "index.html", ctx)

    @app.get("/queues/Q4")
    def q4(request: Request):  # noqa: ANN201
        board, conn = dashboard()
        try:
            rows = board.contradictions()
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(request, "q4.html", shell() | {"rows": rows})

    @app.post("/queues/Q4/{contradiction_id}/resolve")
    def q4_resolve(contradiction_id: str, resolution: str = Form(...)):  # noqa: ANN201
        board, conn = dashboard()
        try:
            board.resolve_contradiction(contradiction_id, resolution)
        finally:
            conn.close()
        return RedirectResponse("/queues/Q4", status_code=303)

    @app.get("/queues/Q1")
    def q1(request: Request):  # noqa: ANN201
        board, conn = dashboard()
        try:
            rows = board.tickets()
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(request, "q1.html", shell() | {"rows": rows})

    @app.get("/queues/Q1/{ticket_id}")
    def q1_detail(request: Request, ticket_id: str):  # noqa: ANN201
        board, conn = dashboard()
        try:
            detail = board.ticket_detail(ticket_id)
        finally:
            conn.close()
        if detail is None:
            return RedirectResponse("/queues/Q1", status_code=303)
        return TEMPLATES.TemplateResponse(
            request, "ticket.html", shell() | {"d": detail, "transitions": _transitions(detail)}
        )

    @app.post("/queues/Q1/{ticket_id}/state")
    def q1_state(ticket_id: str, to: str = Form(...)):  # noqa: ANN201
        _, conn = dashboard()
        try:
            ticket_domain.transition(conn, ticket_id, to)
        except (ticket_domain.InvalidTransition, ticket_domain.ResolutionRequired):
            pass  # 화면이 다시 그려지며 왜 안 되는지 보인다
        finally:
            conn.close()
        return RedirectResponse(f"/queues/Q1/{ticket_id}", status_code=303)

    @app.post("/queues/Q1/{ticket_id}/confirm")
    def q1_confirm(  # noqa: ANN201
        request: Request,
        ticket_id: str,
        choice: str = Form(...),
        kind: str = Form("linked"),
        refs: str = Form(""),
        period_days: str = Form(""),
    ):
        """무효화 조건을 채우고 티켓을 닫는다.

        **채우는 행위가 곧 승인이다** (§6.5.4). 그래서 성공하면 바로 종결까지 간다 —
        따로 "닫기" 버튼을 두면 채워 놓고 닫지 않은 티켓이 쌓인다.
        """
        board, conn = dashboard()
        try:
            detail = board.ticket_detail(ticket_id)
            if detail is None or detail.resolution is None:
                return RedirectResponse("/queues/Q1", status_code=303)
            try:
                invalidation = _chosen_invalidation(
                    detail.resolution, choice, kind, refs, period_days
                )
            except ValueError as exc:
                return TEMPLATES.TemplateResponse(
                    request,
                    "ticket.html",
                    shell() | {"d": detail, "transitions": _transitions(detail), "error": str(exc)},
                )
            resolution_domain.confirm(conn, ticket_id, invalidation=invalidation)
            ticket_domain.transition(conn, ticket_id, ticket_domain.State.CLOSED)
            # **채운 것이 곧 승격 승인이다** (§6.8.1 경로 A). Q7 을 거치지 않는다 —
            # 여기에 또 승인을 붙이면 이중 승인이고, 1인 겸업에게 그 중복이 곧
            # 대기열 정체다. 자격이 없는 티켓(모순·Lint)은 조용히 넘어간다.
            promotion_domain.promote_if_eligible(
                conn, KnowledgeRepository(cfg.knowledge_dir), ticket_id
            )
        finally:
            conn.close()
        return RedirectResponse(f"/queues/Q1/{ticket_id}", status_code=303)

    @app.get("/entry")
    def entry_form(request: Request):  # noqa: ANN201
        """질문 등록 화면 — **두 칸뿐이다** (ADR-007 결정 4).

        등록 부담이 크면 유인이 상쇄된다(§1.4.4). 붙여넣고 닫는 것이 전부여야 한다.
        """
        _, conn = dashboard()
        try:
            ctx = shell() | {"manual_count": manual_entry.count(conn)}
        finally:
            conn.close()
        if not _q1_visible(cfg.stage):
            # **등록이 만드는 것은 티켓이다.** 티켓을 볼 수 없는 단계에서 등록을 받으면
            # 보이지 않는 대기열이 쌓인다 (FR-59 를 세운 이유와 같다).
            ctx |= {"error": f"단계 {cfg.stage} 에서는 Q1 이 보이지 않아 등록을 받지 않는다."}
            return TEMPLATES.TemplateResponse(request, "entry.html", ctx | {"locked": True})
        return TEMPLATES.TemplateResponse(request, "entry.html", ctx)

    @app.post("/entry")
    def entry_submit(  # noqa: ANN201
        request: Request, question: str = Form(...), answer: str = Form(...)
    ):
        """등록한다. **여기서 LLM 을 부르지 않는다** — 초안은 배치가 채운다.

        수십 초 걸리는 호출로 응답을 붙들면 부담이 되돌아와 §1.4.4 가 무너진다.
        """
        _, conn = dashboard()
        if not _q1_visible(cfg.stage):
            # 화면만 막으면 POST 로 지나갈 수 있다. 규칙은 받는 쪽에도 있어야 한다.
            try:
                ctx = shell() | {
                    "locked": True,
                    "error": f"단계 {cfg.stage} 에서는 Q1 이 보이지 않아 등록을 받지 않는다.",
                    "manual_count": manual_entry.count(conn),
                }
            finally:
                conn.close()
            return TEMPLATES.TemplateResponse(request, "entry.html", ctx)
        try:
            registered = manual_entry.register(conn, question=question, answer=answer)
            ctx = shell() | {
                "registered": registered,
                "manual_count": manual_entry.count(conn),
            }
        except manual_entry.EmptyEntry as exc:
            ctx = shell() | {"error": str(exc), "manual_count": manual_entry.count(conn)}
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(request, "entry.html", ctx)

    @app.get("/queues/Q2")
    def q2(request: Request, outcome: str = ""):  # noqa: ANN201
        """검수 대기열 — **판정 화면이다** (§6.4.4, FR-45).

        상태 기계가 없다. 보고 누르면 끝난다.
        """
        board, conn = dashboard()
        try:
            unsettled = publication.unsettled(conn)
            rows = draft_store.pending(conn)
            sources = _source_text(cfg, conn, rows)
            unmatched = {
                d.id: review_domain.unmatched_terms(
                    review_domain.ReviewInput(
                        draft_body=d.body, grounding=d.grounding, source_text=sources
                    )
                )[:12]
                for d in rows
            }
            dist = review_domain.distribution(conn, reviewed_by="human")
            agent_dist = review_domain.distribution(conn, reviewed_by="agent")
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(
            request,
            "q2.html",
            shell()
            | {
                "outcome": outcome,
                "unsettled": unsettled,
                "rows": rows,
                "sources": sources,
                "unmatched": unmatched,
                "dist": dist,
                "agent_dist": agent_dist,
                "reasons": [(str(r), review_domain.DESCRIPTIONS[r]) for r in review_domain.Reject],
            },
        )

    @app.post("/queues/Q2/{draft_id}/decide")
    def q2_decide(  # noqa: ANN201
        draft_id: str,
        approved: str = Form(...),
        reason: str = Form(""),
        detail: str = Form(""),
    ):
        """승인하거나 반려한다. **반려에는 사유가 필요하다** (§5.5.6).

        승인은 곧 **게재**다 (FR-24). 여기서 바로 내보내는 이유는, 게재를 배치로
        미루면 승인과 게재 사이에 사람이 볼 수 없는 구간이 생기고 **누른 사람이 결과를
        모르는 채로 화면을 떠나기** 때문이다 — 게재는 되돌리기 어려운 행위이므로
        (§5.2) 실패했을 때 그 자리에 있어야 할 사람이 누른 그 사람이다.
        """
        board, conn = dashboard()
        outcome = ""
        try:
            is_approved = approved == "1"
            picked = None
            if not is_approved:
                try:
                    picked = review_domain.Reject(reason)
                except ValueError:
                    # 사유 없는 반려는 기록으로 쓸 수 없다. 판정을 받지 않는다.
                    return RedirectResponse("/queues/Q2", status_code=303)
            draft = draft_store.get(conn, draft_id)
            sources = _source_text(cfg, conn, [draft] if draft else [])
            draft_store.decide(
                conn,
                draft_id,
                approved=is_approved,
                reason=picked,
                detail=detail,
                source_text=sources,
            )
            if is_approved:
                outcome = _publish(cfg, conn, parent_system, draft_id)
            elif draft is not None:
                # **반려는 버리는 것이 아니라 사람에게 보내는 것이다.** 여기서 티켓을
                # 열지 않으면 반려된 초안이 어느 대기열에도 없이 사라진다 — 실패는
                # 사람의 대기열로 수렴해야 한다 (§5.1).
                intake.reopen_for_rejected_draft(conn, draft.qna_item_id)
        finally:
            conn.close()
        suffix = f"?outcome={quote(outcome)}" if outcome else ""
        return RedirectResponse(f"/queues/Q2{suffix}", status_code=303)

    @app.post("/queues/Q1/{ticket_id}/answer")
    def q1_answer(ticket_id: str, answer: str = Form(...)):  # noqa: ANN201
        """파이프라인이 답하지 못한 건에 담당자 답변을 적는다 (WBS-4.5.2).

        **여기서 LLM 을 부르지 않는다.** 초안은 배치가 만든다 — 등록 화면과 같은
        이유로, 적는 행위를 LLM 호출만큼 붙들면 적지 않게 된다 (§1.4.4).
        """
        board, conn = dashboard()
        try:
            detail = board.ticket_detail(ticket_id)
            if detail is None or not detail.needs_answer or not detail.question:
                return RedirectResponse(f"/queues/Q1/{ticket_id}", status_code=303)
            try:
                manual_entry.attach(
                    conn,
                    qna_item_id=detail.item.ticket.qna_item_id,
                    question=detail.question,
                    answer=answer,
                )
            except manual_entry.EmptyEntry:
                # 빈 답변은 재료가 되지 않는다. 적지 않은 것과 같으므로 되돌린다.
                pass
        finally:
            conn.close()
        return RedirectResponse(f"/queues/Q1/{ticket_id}", status_code=303)

    @app.get("/queues/Q8")
    def q8(request: Request):  # noqa: ANN201
        board, conn = dashboard()
        try:
            rows = board.knowledge_gaps()
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(request, "q8.html", shell() | {"rows": rows})

    return app


def _q1_visible(stage: str) -> bool:
    return any(q.id == "Q1" for q in queues_for_stage(stage))


def _transitions(detail) -> list[tuple[str, str]]:  # noqa: ANN001
    """지금 갈 수 있는 곳. **종결은 여기 없다** — 무효화 조건을 채우는 행위가 곧 종결이다."""
    labels = {
        ticket_domain.State.OPEN: "열림으로 되돌린다",
        ticket_domain.State.IN_PROGRESS: "손대기 시작한다",
        ticket_domain.State.HELD: "보류 — 질문자 응답을 기다린다",
    }
    allowed = ticket_domain.TRANSITIONS[detail.item.ticket.state]
    return [(str(s), labels[s]) for s in labels if s in allowed]


def _chosen_invalidation(  # noqa: ANN001
    resolution, choice: str, kind: str, refs: str, period_days: str
) -> Invalidation:
    """사람이 고른 무효화 조건.

    후보를 고르든 직접 쓰든 **선택은 사람의 행위**다 (§5.6.4). 기본값이 없으므로
    아무것도 고르지 않으면 여기서 막힌다.
    """
    if choice != "custom":
        try:
            return resolution.invalidation_candidates[int(choice)]
        except (ValueError, IndexError):
            raise ValueError("고른 후보를 찾을 수 없다") from None

    parsed = InvalidationKind(kind)
    if parsed is InvalidationKind.LINKED:
        items = tuple(r.strip() for r in refs.split(",") if r.strip())
        if not items:
            raise ValueError("연결형에는 묶을 대상이 필요하다 — 경로를 쉼표로 적는다")
        return Invalidation(kind=parsed, refs=items)

    if not period_days.strip().isdigit() or int(period_days) <= 0:
        raise ValueError("주기형에는 재확인 주기(일수)가 필요하다")
    return Invalidation(kind=parsed, period_days=int(period_days))


def _publish(cfg, conn, parent_system, draft_id: str) -> str:  # noqa: ANN001
    """게재하고 결과를 한 줄로 돌려준다.

    **예외를 화면 밖으로 내보내지 않는다.** 여기서 500 이 나면 운영자는 승인이
    반영됐는지조차 모른 채 남겨지는데, 그때 남은 `in_flight` 기록은 **사람이
    확인해야만 닫히는 것**이라 그 사실이 화면에 닿아야 한다 (§5.2).

    어댑터를 만드는 것까지 이 안에서 하는 이유도 같다 — 연동이 설정되지 않았을 때
    (`NotConfigured`) 그것은 게재하지 못한 **이유**이지 화면이 깨질 일이 아니다.
    """
    accounts = frozenset(a.strip() for a in cfg.bot_accounts.split(",") if a.strip())
    try:
        result = publication.publish(
            conn,
            parent_system(),
            draft_id,
            bot_accounts=accounts,
            repo=KnowledgeRepository(cfg.knowledge_dir),
        )
    except Exception as exc:  # noqa: BLE001 — 게재 실패는 화면에 보여야 한다
        return f"게재하지 못했다: {exc}. 나갔는지 확인이 필요할 수 있다."
    if isinstance(result, publication.Refused):
        return f"게재하지 않았다 — {result.reason} ({result.detail})"
    return "게재했다."


def _source_text(cfg, conn, drafts) -> dict[str, str]:  # noqa: ANN001
    """초안이 가리키는 지식 항목의 원문.

    **없을 수 있다.** 초안을 만든 뒤 항목이 지워지거나 이름이 바뀌었을 수 있고,
    그때 화면은 "원문을 찾을 수 없다"고 말해야 한다 — 조용히 빈칸으로 두면 검수자가
    근거가 없는 줄 모르고 승인한다.
    """
    wanted = {g for d in drafts for g in d.grounding}
    if not wanted:
        return {}
    repo = KnowledgeRepository(cfg.knowledge_dir)
    if not repo.root.exists():
        return {}
    return {
        s.item.id: f"{s.item.title}\n\n{s.item.body}"
        for s in repo.scan()[0]
        if s.item.id in wanted
    }
