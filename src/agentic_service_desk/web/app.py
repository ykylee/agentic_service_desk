"""웹 진입점 — 운영자 대시보드 (§8, FR-44·59).

**최종 이용자 대면 UI 가 아니다.** 그것은 모 시스템이 렌더링한다(§13). 여기 오는
사람은 운영자 하나뿐이며, 그는 **루프의 일부**다 — 여기서 처리되지 않으면 시스템은
사고 없이 조용히 성장을 멈춘다 (§8.4).

지금 켜진 것은 S0 이라 **Q4(모순)·Q8(지식 공백)과 지식베이스 현황**뿐이다.
나머지 대기열은 그 기능이 켜지는 단계에 맞춰 붙는다 (FR-59).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from agentic_service_desk import __version__
from agentic_service_desk.config import Settings, load_settings
from agentic_service_desk.web.dashboard import Dashboard, queues_for_stage
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind
from agentic_service_desk.operations import manual_entry
from agentic_service_desk.operations import promotion as promotion_domain
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
