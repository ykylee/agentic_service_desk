"""웹 진입점 — 운영자 대시보드 (§8, FR-44·59).

**최종 이용자 대면 UI 가 아니다.** 그것은 모 시스템이 렌더링한다(§13). 여기 오는
사람은 운영자 하나뿐이며, 그는 **루프의 일부**다 — 여기서 처리되지 않으면 시스템은
사고 없이 조용히 성장을 멈춘다 (§8.4).

지금 켜진 것은 S0 이라 **Q4(모순)·Q8(지식 공백)과 지식베이스 현황**뿐이다.
나머지 대기열은 그 기능이 켜지는 단계에 맞춰 붙는다 (FR-59).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from agentic_service_desk import __version__
from agentic_service_desk.config import Settings, load_settings
from agentic_service_desk.content import registry as content_registry
from agentic_service_desk.content import production as content_production
from agentic_service_desk.content import publication as content_publication
from agentic_service_desk.content import review as content_review
from agentic_service_desk.content import store as content_store
from agentic_service_desk.web import auth, metrics
from agentic_service_desk.web.dashboard import Dashboard, queues_for_stage
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind
from agentic_service_desk.operations import alert as alert_domain
from agentic_service_desk.operations import intake
from agentic_service_desk.operations import manual_entry
from agentic_service_desk.operations import phase as phase_domain
from agentic_service_desk.knowledge.search import Search
from agentic_service_desk.operations import promotion as promotion_domain
from agentic_service_desk.operations import recheck as recheck_domain
from agentic_service_desk.adapters.factory import build_parent_system
from agentic_service_desk.adapters.parent_system import NotConfigured
from agentic_service_desk.pipeline import draft_store, review as review_domain
from agentic_service_desk.ingest.harness_runner import PiHarness
from agentic_service_desk.pipeline import audience as audience_domain
from agentic_service_desk.pipeline.answer import AnswerPipeline
from agentic_service_desk.pipeline.review import ReviewInput
from agentic_service_desk.pipeline import correction, publication
from agentic_service_desk.operations import resolution as resolution_domain
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations import tracking
from agentic_service_desk.operations.schema import connect, initialize

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(settings: Settings | None = None) -> FastAPI:
    """앱을 만든다. 설정을 인자로 받는 이유는 테스트에서 갈아 끼우기 위해서다."""
    cfg = settings or load_settings()
    app = FastAPI(title="Agentic Service Desk", version=__version__)

    # **선언은 기동 때 한 번 읽고 검사한다** (FR-42, §7.5). 요청마다 읽으면 잘못된
    # 선언이 화면 하나에서만 터져, 어느 요청이 그것을 밟는지에 따라 있다가 없어진다.
    content_types = content_registry.load(cfg.content_types_path)

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

    def harness():  # noqa: ANN202
        """생성에 쓸 모델. **없으면 없는 대로 간다.**

        모델이 없으면 파이프라인이 3단계에서 멈추고 조회 결과만 남는다 — 그것도
        답이다: "지식베이스가 이만큼은 갖고 있다"를 보여 준다. 배치 쪽과 같은
        판단이다 (`worker.runner._harness`).
        """
        if not cfg.llm_base_url or not cfg.llm_model:
            return None
        return PiHarness(cfg.llm_model, cfg.llm_api_key)

    def shell() -> dict:
        """모든 화면이 함께 쓰는 것 — 켜진 단계와 그 단계의 대기열."""
        return {
            "stage": cfg.stage,
            "queues": queues_for_stage(cfg.stage),
            # 인증을 켜지 않았으면 "나간다"를 보여 줄 이유가 없다 — 나갈 곳이 없다.
            "authenticated": bool(cfg.web_password),
        }

    # **암호가 선언됐을 때만 건다** (WBS-5.2.2). 선언하지 않은 구성에 인증을
    # 강제하는 자리는 여기가 아니라 기동이다 (`preflight.check_live_exposure`).
    if cfg.web_password:
        app.add_middleware(
            auth.RequireLogin,
            password=cfg.web_password,
            ttl_hours=cfg.web_session_hours,
        )

    @app.get("/login")
    def login_form(request: Request, next: str = "/"):  # noqa: ANN201, A002
        return TEMPLATES.TemplateResponse(
            request, "login.html", shell() | {"next": next}
        )

    @app.post("/login")
    def login_submit(  # noqa: ANN201
        request: Request, password: str = Form(""), next: str = Form("/")  # noqa: A002
    ):
        """암호가 맞으면 세션을 발급한다.

        **`next` 를 그대로 믿지 않는다.** 우리 화면 안의 절대 경로만 받는다 —
        로그인 뒤에 남의 주소로 보내는 문을 열어 줄 이유가 없다.
        """
        if not auth.matches(password, cfg.web_password):
            return TEMPLATES.TemplateResponse(
                request,
                "login.html",
                shell() | {"next": next, "error": "암호가 맞지 않는다"},
                status_code=401,
            )
        target = next if next.startswith("/") and not next.startswith("//") else "/"
        response = RedirectResponse(target, status_code=303)
        auth.set_cookie(
            response,
            auth.issue(cfg.web_password),
            ttl_hours=cfg.web_session_hours,
        )
        return response

    @app.post("/logout")
    def logout():  # noqa: ANN201
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(auth.COOKIE)
        return response

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
                # 화면(`/queues/Q3`)과 티켓 배선은 WBS-4.6.4 다. 그래도 **건수는
                # 지금 센다** — 초안이 쌓이는데 0 을 내면 화면이 거짓을 말한다.
                "Q3": len(content_store.pending(conn)),
                "Q4": status.open_contradictions,
                "Q5": len(correction.pending(conn)),
                "Q6": len(tracking.awaiting_confirmation(conn)),
                "Q7": len(promotion_domain.awaiting_decision(conn)),
                "Q8": len(board.knowledge_gaps()),
            }
            next_up = board.next_up(cfg.stage)
            # **배너는 웹훅이 있어도 뜬다** (ADR-007 결정 2). 알림이 도착하지 않은
            # 것과 경고가 없는 것을 화면에서 구분할 수 없으면 침묵이 안전으로 읽힌다.
            alerts = alert_domain.pending(
                conn, neglect_hours=cfg.alert_neglect_hours
            )
        finally:
            conn.close()
        ctx = shell()
        # 아직 자료가 없는 대기열은 0 으로 둔다 — 화면에 뜨는 것과 셀 수 있는 것은 다르다.
        ctx |= {
            "s": status,
            "counts": {q.id: counts.get(q.id, 0) for q in ctx["queues"]},
            "next_up": next_up,
            "alerts": alerts,
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

    @app.get("/status")
    def status(request: Request, phase: str = ""):  # noqa: ANN201
        """현황 다섯 종 + 핵심 지표 여섯 (FR-47·58).

        **대기열과 화면을 나눈다** (§8.1). 한 화면에 섞으면 숫자가 대기열을 밀어내고,
        그러면 운영자가 처리해야 할 것이 현황 사이에 묻힌다.
        """
        board, conn = dashboard()
        try:
            view = _phase_view(cfg, conn)
            ctx = {
                "core": metrics.core(conn),
                "screens": [
                    _knowledge_screen(board.knowledge_status()),
                    metrics.qna_status(conn, retention_days=cfg.retention_days),
                    metrics.content_status(conn, content_types),
                    metrics.agent_status(conn),
                    view.status,
                ],
                # **전진 제안은 대기열이 아니라 이 화면의 알림이다** (§8.3) —
                # 운영 전체에서 두어 번 일어나는 일이라 대기열을 하나 더 만들 일이
                # 아니다.
                "phase": view,
                "outcome": phase,
            }
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(request, "status.html", shell() | ctx)

    @app.get("/recheck")
    def recheck(request: Request, outcome: str = ""):  # noqa: ANN201
        """표본 재검증 (WBS-4.8.4, FR-50, §5.6.7).

        **대기열이 아니다.** Q1~Q8 옆에 세우면 밀린 대기열을 비우는 손이 여기까지
        와서, 이 화면이 재려던 바로 그 형식적 승인이 재검증에서 일어난다 — 그러면
        일치율은 100% 로 수렴하고 아무것도 재지 못한다. 그래서 현황에서 열고,
        순위 목록(§8.2)에도 넣지 않는다.
        """
        _, conn = dashboard()
        try:
            rows = [
                (sample, recheck_domain.context(conn, sample))
                for sample in recheck_domain.pending(conn)
            ]
            ctx = {
                "rows": rows,
                "agreement": recheck_domain.agreement(conn),
                "decided": recheck_domain.decided(conn),
                "outcome": outcome,
            }
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(request, "recheck.html", shell() | ctx)

    @app.post("/recheck/{sample_id}")
    def recheck_decide(  # noqa: ANN201
        sample_id: str, verdict: str = Form(...), note: str = Form("")
    ):
        """다시 본 결과를 남긴다. **다르다면 사유를 받는다** (§5.5.6 과 같은 이유)."""
        _, conn = dashboard()
        try:
            try:
                recheck_domain.decide(
                    conn, sample_id, agreed=verdict == "agreed", note=note
                )
                outcome = ""
            except recheck_domain.NotPending as exc:
                outcome = str(exc)
        finally:
            conn.close()
        suffix = f"?outcome={quote(outcome)}" if outcome else ""
        return RedirectResponse(f"/recheck{suffix}", status_code=303)

    @app.post("/phase/advance")
    def phase_advance(to: int = Form(...)):  # noqa: ANN201
        """전진 승인 — **운영자만 누른다** (§1.3.3-c, FR-49).

        **제안이 없으면 거부된다.** 이 버튼이 국면 다이얼이 되면 "지표가 제안하고
        운영자가 승인한다"가 "운영자가 정한다"가 되고, 그러면 검수를 느슨하게 한
        결정에 근거가 남지 않는다. 후퇴 버튼을 두지 않은 것도 같은 결정의 뒷면이다 —
        후퇴는 사람이 누르는 것이 아니라 시스템이 내리는 것이다.
        """
        _, conn = dashboard()
        try:
            view = _phase_view(cfg, conn)
            if view.judgment is None:
                return RedirectResponse("/status?phase=관측이 아직 없다", status_code=303)
            try:
                decision = phase_domain.advance(conn, to=to, judgment=view.judgment)
            except phase_domain.NotProposed as exc:
                return RedirectResponse(f"/status?phase={quote(str(exc))}", status_code=303)
        finally:
            conn.close()
        return RedirectResponse(
            f"/status?phase={quote(f'{decision.to_phase}국면으로 올렸다 — 검수 강도와 자동 승격 범위가 함께 넓어진다')}",
            status_code=303,
        )

    @app.get("/queues/Q3")
    def q3(request: Request, outcome: str = ""):  # noqa: ANN201
        """콘텐츠 검수 대기열 (FR-39·45).

        **작업 대기열이다** — 판정 화면과 달리 항목마다 상세가 있다. diff 를 읽어야
        누를 수 있는 버튼이고, 그 읽는 행위가 §5.6.1 이 말한 "형식적 승인"을 막는다.
        """
        _, conn = dashboard()
        try:
            rows = [
                _content_row(conn, content_types, d)
                for d in content_store.pending(conn)
            ]
            waiting = [
                _content_row(conn, content_types, d)
                for d in content_store.approved(conn)
                if content_types.get(d.type_id).review.final_check
                and content_publication.of_draft(conn, d.id) is None
            ]
            unsettled = content_publication.unsettled(conn, content_types)
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(
            request,
            "q3.html",
            shell()
            | {
                "rows": rows,
                "waiting": waiting,
                "unsettled": unsettled,
                "outcome": outcome,
            },
        )

    @app.post("/queues/Q3/{ticket_id}/publish")
    def q3_publish(ticket_id: str):  # noqa: ANN201
        """발행 직전 최종 확인 (§5.5.5, §7.3).

        **발행물에만 있다.** 되돌릴 수 없으므로 승인 위에 확인이 하나 더 있고,
        누르는 행위가 곧 그 확인이다 — 배치가 대신 누르지 않는다.
        """
        _, conn = dashboard()
        outcome = ""
        try:
            draft = content_store.by_ticket(conn, ticket_id)
            if draft is not None:
                outcome = _publish_content(
                    conn,
                    parent_system,
                    content_types.get(draft.type_id),
                    draft,
                    cfg=cfg,
                    final_check_by="human",
                )
        finally:
            conn.close()
        return RedirectResponse(f"/queues/Q3?outcome={quote(outcome)}", status_code=303)

    @app.get("/queues/Q3/{ticket_id}")
    def q3_detail(request: Request, ticket_id: str):  # noqa: ANN201
        """**티켓 id 로 연다.** 순위(`next_up`)가 가리키는 것이 티켓이므로 그 자리에서
        바로 열려야 한다."""
        _, conn = dashboard()
        try:
            detail = _content_detail(cfg, conn, content_types, ticket_id)
        finally:
            conn.close()
        if detail is None:
            return RedirectResponse("/queues/Q3", status_code=303)
        return TEMPLATES.TemplateResponse(
            request,
            "content.html",
            shell()
            | {
                "d": detail,
                "reasons": _reasons(
                    review_domain.ANSWER_REASONS
                    + tuple(
                        review_domain.Reject(r)
                        for r in detail.ctype.review.extra_rejections
                    )
                ),
            },
        )

    @app.post("/queues/Q3/{ticket_id}/decide")
    def q3_decide(  # noqa: ANN201
        ticket_id: str,
        approved: str = Form(...),
        reason: str = Form(""),
        detail: str = Form(""),
    ):
        """사람이 판정한다. **여기 자동 승인 경로는 없다** (FR-39)."""
        _, conn = dashboard()
        outcome = ""
        try:
            draft = content_store.by_ticket(conn, ticket_id)
            if draft is None:
                return RedirectResponse("/queues/Q3", status_code=303)
            is_approved = approved == "1"
            picked = None
            if not is_approved:
                try:
                    picked = review_domain.Reject(reason)
                except ValueError:
                    # 사유 없는 반려는 기록으로 쓸 수 없다 (§5.5.6) — 화면으로
                    # 되돌려 다시 고르게 한다.
                    return RedirectResponse(
                        f"/queues/Q3/{ticket_id}", status_code=303
                    )
            ctype = content_types.get(draft.type_id)
            content_review.decide(
                conn,
                ctype,
                draft,
                approved=is_approved,
                reason=picked,
                detail=detail,
            )
            # **승인과 게재는 다른 행위다.** 게재는 모 시스템에 닿아야 하므로
            # 실패할 수 있고, 실패해도 승인은 남아 다음 배치가 다시 시도한다 —
            # 살아있는 문서는 멱등해서 그 재시도가 안전하다 (D46).
            # 발행물은 여기서 나가지 않는다: 발행 직전 최종 확인이 남았다 (§5.5.5).
            if is_approved and not ctype.review.final_check:
                # **판정 뒤의 초안을 다시 읽는다.** 손에 든 것은 `pending` 이던
                # 시점의 값이라, 그대로 넘기면 게재 관문이 "승인되지 않았다"며
                # 막는다 — 방금 승인한 사람에게는 이유를 알 수 없는 거절이다.
                outcome = _publish_content(
                    conn, parent_system, ctype, content_store.get(conn, draft.id), cfg=cfg
                )
        finally:
            conn.close()
        return RedirectResponse(f"/queues/Q3?outcome={quote(outcome)}", status_code=303)

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

    @app.get("/ask")
    def ask_form(request: Request):  # noqa: ANN201
        """지식베이스에 직접 묻는 자리 (FR-60, WBS-5.7.1).

        §8.1 은 대시보드가 "조회 화면이 아니라 작업대"라고 못 박았지만, 그 절이
        경계한 것은 **차트 구경 화면이 되는 것**이다. 같은 절이 이렇게도 적었다 —
        *"여기서 할 수 없는 일은 아무도 할 수 없는 일이 된다."* 지식베이스에
        무언가를 물어볼 수 있는 사람이 **아무도 없었다**: 답변 파이프라인은 모
        시스템에서 온 질문에만 걸린다.

        **단계를 보지 않는다.** 지식베이스는 S0 부터 있고 이 화면은 아무것도
        내보내지 않으므로, 대기열처럼 점증시킬 이유가 없다 (FR-59 는 *내보내는*
        기능의 대기열을 다룬다).
        """
        return TEMPLATES.TemplateResponse(request, "ask.html", shell())

    @app.post("/ask")
    def ask_submit(request: Request, question: str = Form("")):  # noqa: ANN201
        """물어보고 결과를 펼친다. **아무것도 저장하지 않는다.**

        `AnswerPipeline` 은 그 자체로는 무엇도 쓰지 않는다 — 초안 보관·티켓·답변
        이력·게재는 전부 유입 처리(`operations.intake`)가 이어 붙이는 것이다.
        여기서는 그 배선을 붙이지 않는 것이 요구의 절반이다 (FR-60): 검수를 지나지
        않은 산출이 답변 이력에 남으면 지표가 오염되고, **게재 출구가 하나라는
        규약**(NFR-3)이 흐려진다.

        **동기로 답한다.** 40~60초가 걸리지만 이것은 대기열이 아니라 도구다 —
        배치로 미루면 확인하러 다시 와야 하고, 그 순간 대기열이 하나 더 느는 것과
        같아진다 (§8.6 이 경계한 바로 그것이다).
        """
        question = question.strip()
        ctx = shell() | {"question": question}
        if not question:
            return TEMPLATES.TemplateResponse(
                request, "ask.html", ctx | {"error": "물어볼 것을 적는다."}
            )
        repo = KnowledgeRepository(cfg.knowledge_dir)
        if not repo.root.exists():
            return TEMPLATES.TemplateResponse(
                request, "ask.html", ctx | {"error": "지식베이스가 아직 없다."}
            )
        _, conn = dashboard()
        try:
            outcome = AnswerPipeline(
                search=Search(repo=repo, conn=conn),
                conn=conn,
                harness=harness(),
                generated_by=cfg.llm_model,
            ).run(question)
        except Exception as exc:  # noqa: BLE001 — 모델이 터져도 화면은 서야 한다
            return TEMPLATES.TemplateResponse(
                request, "ask.html", ctx | {"error": f"물어보다 터졌다 — {exc}"}
            )
        finally:
            conn.close()
        ctx |= {"outcome": outcome}
        if outcome.draft is not None:
            ctx |= {"renderings": _renderings(question, outcome, harness())}
        return TEMPLATES.TemplateResponse(request, "ask.html", ctx)

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
                # P6~P8 은 칼럼 전용이라 여기 두지 않는다 — 고를 수 없는 선택지가
                # 화면에 늘면 무엇이 이 화면의 사유인지가 흐려진다 (§7.6.4).
                "reasons": _reasons(review_domain.ANSWER_REASONS),
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

    @app.get("/queues/Q5")
    def q5(request: Request):  # noqa: ANN201
        """정정 후보 — **작업 화면이다** (§6.4.4).

        조사와 수정이 필요한 일이므로 목록과 버튼만으로 끝나지 않는다. 다만 정정
        자체는 배치가 초안까지 만들어 두므로, 여기서 사람이 하는 판단은 **"무시할
        것인가"** 하나다.
        """
        board, conn = dashboard()
        try:
            repo = KnowledgeRepository(cfg.knowledge_dir)
            rows = correction.pending(conn)
            ready = {c.record_id for c in correction.ready(conn, repo)}
            drafting = _correcting(conn)
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(
            request,
            "q5.html",
            shell() | {"rows": rows, "ready": ready, "drafting": drafting},
        )

    @app.post("/queues/Q5/{record_id}/ignore")
    def q5_ignore(record_id: str):  # noqa: ANN201
        """근거는 낡았지만 답변은 여전히 맞다 (§8.2 의 "무시")."""
        board, conn = dashboard()
        try:
            correction.ignore(conn, record_id)
        finally:
            conn.close()
        return RedirectResponse("/queues/Q5", status_code=303)

    @app.get("/queues/Q6")
    def q6(request: Request):  # noqa: ANN201
        """암묵적 해결 확인 — **판정 화면이다** (§6.4.4).

        보고 누르면 끝난다. 상태 기계도 상세도 없다.
        """
        board, conn = dashboard()
        try:
            rows = tracking.awaiting_confirmation(conn)
            grades = tracking.grades(conn)
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(
            request, "q6.html", shell() | {"rows": rows, "grades": grades}
        )

    @app.post("/queues/Q6/{qna_item_id}/confirm")
    def q6_confirm(qna_item_id: str):  # noqa: ANN201
        """운영자가 확인해 명시적으로 올린다 (FR-32).

        **이것이 §5.3.1-1 이 꼽은 명시적 해결 신호 둘 중 하나다** — 다른 하나인
        이용자의 해결 표시와 달리 이 신호는 모 시스템이 아니라 여기서 나온다.
        """
        board, conn = dashboard()
        try:
            tracking.upgrade(conn, qna_item_id)
        finally:
            conn.close()
        return RedirectResponse("/queues/Q6", status_code=303)

    @app.get("/queues/Q7")
    def q7(request: Request):  # noqa: ANN201
        """승격 후보 — **판정 화면이다** (§6.4.4).

        보고 무효화 조건을 지정하면 끝난다. "지정"이 이 대기열의 판정 형태다.
        """
        board, conn = dashboard()
        try:
            rows = promotion_domain.awaiting_decision(conn)
            # **설정이 아니라 DB 다** (WBS-4.8.1) — 화면이 말하는 국면과 자동 승격이
            # 보는 국면이 다르면, 역행 뒤에도 화면은 "자동으로 올라간다"고 적는다.
            current_phase = phase_domain.current(conn, seed=cfg.phase)
        finally:
            conn.close()
        return TEMPLATES.TemplateResponse(
            request, "q7.html", shell() | {"rows": rows, "phase": current_phase}
        )

    @app.post("/queues/Q7/{ticket_id}/promote")
    def q7_promote(  # noqa: ANN201
        ticket_id: str,
        choice: str = Form(...),
        kind: str = Form("linked"),
        refs: str = Form(""),
        period_days: str = Form(""),
    ):
        """사람이 무효화 조건을 지정해 승격한다 (경로 B, §6.8.1).

        **경로 A 와 같은 행위다** — 무효화 조건을 채우는 것이 곧 승격 승인이다.
        다른 것은 여기서는 그 앞에 Q7 이라는 판정 한 겹이 있다는 점뿐이고, 그것은
        이 경로가 **지식으로서 판정된 적이 없기** 때문이다 (§6.8.2).
        """
        board, conn = dashboard()
        try:
            candidate = promotion_domain.assess(conn, ticket_id)
            if candidate is None:
                return RedirectResponse("/queues/Q7", status_code=303)
            try:
                invalidation = (
                    candidate.derived
                    if choice == "derived" and candidate.derived
                    else _chosen_invalidation(
                        candidate.record, choice, kind, refs, period_days
                    )
                )
            except ValueError:
                # 아무것도 고르지 않았다 — **기본값이 없으므로 여기서 막힌다** (§5.6.4).
                return RedirectResponse("/queues/Q7", status_code=303)
            resolution_domain.confirm(conn, ticket_id, invalidation=invalidation)
            promotion_domain.promote_if_eligible(
                conn, KnowledgeRepository(cfg.knowledge_dir), ticket_id
            )
        finally:
            conn.close()
        return RedirectResponse("/queues/Q7", status_code=303)

    @app.post("/queues/Q7/{ticket_id}/decline")
    def q7_decline(ticket_id: str):  # noqa: ANN201
        """올리지 않는다. **기각도 판정이다** — 남기지 않으면 매 주기 다시 뜬다."""
        board, conn = dashboard()
        try:
            resolution_domain.decline_promotion(conn, ticket_id)
        finally:
            conn.close()
        return RedirectResponse("/queues/Q7", status_code=303)

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


def _renderings(question: str, outcome, harness):  # noqa: ANN001, ANN202
    """화면에 나란히 둘 두 글.

    **고객용은 다시 만들지 않는다** — 파이프라인 4단계가 이미 만들었고 그것이
    게재 본문이다(FR-61). 여기서 또 부르면 화면에 보이는 글과 나가는 글이 갈리고,
    호출도 하나 더 든다.

    개발자용만 부른다. 실패하면 **그 자리를 비운다** — 덤이 본체를 무너뜨리지 않는다.
    """
    from agentic_service_desk.ingest.agent import extract_json

    draft = outcome.draft
    review = ReviewInput.of(draft, outcome.hits)
    out = []
    customer = audience_domain.render_of(audience_domain.CUSTOMER, draft.body, review)
    if customer is not None:
        out.append(customer)
    if harness is None:
        return out
    try:
        payload = extract_json(
            harness.run(
                audience_domain.build_developer_prompt(
                    question, draft, outcome.hits, outcome.analysis.language
                )
            ).text
        )
        developer = audience_domain.render_of(
            audience_domain.DEVELOPER, str(payload.get("developer") or ""), review
        )
        if developer is not None:
            out.append(developer)
    except Exception:  # noqa: BLE001 — 덤이 본체를 무너뜨리지 않는다
        pass
    return out


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


def _phase_view(cfg, conn):  # noqa: ANN001
    """국면 화면과 판정을 한 셈에서 낸다 (WBS-4.8.1).

    표가 보는 판정과 버튼이 보는 판정이 갈리면, 화면은 "전진할 수 있다"고 적고
    버튼은 거부한다.
    """
    return metrics.phase_view(
        conn,
        stage=cfg.stage,
        seed=cfg.phase,
        thresholds_path=cfg.phase_thresholds_path,
        window_days=cfg.phase_window_days,
        min_sample=cfg.phase_min_sample,
    )


def _knowledge_screen(status) -> metrics.Status:  # noqa: ANN001
    """지식베이스 현황 (§8.3). 이미 있던 것을 다섯 종의 한 자리로 옮긴다."""
    return metrics.Status(
        title="지식베이스 현황",
        question="지식이 자라고 있는가, 썩고 있는가",
        rows=[
            ("지식 항목", f"{status.total}건"),
            (
                "출처 구성",
                f"소스코드 {status.from_source} · QnA {status.from_qna} — "
                "**한쪽으로 쏠리면 다른 쪽 수집이 막혔다는 신호**다 (D2)",
            ),
            (
                "stale 비율",
                f"{status.stale_ratio:.0%} ({status.stale}건) — 표시일 뿐 "
                "**삭제하지 않는다** (FR-8). 게재물로 번지는 것은 Q5 가 든다",
            ),
            ("미해결 모순", f"{status.open_contradictions}건 — Q4 가 그 목록이다"),
            (
                "최근 ingest",
                " / ".join(m for _, m in status.recent_ingests[:3]) or "아직 없다",
            ),
        ],
        note=(
            f"읽을 수 없는 파일 {len(status.broken_files)}건 — frontmatter 가 깨졌다."
            if status.broken_files
            else ""
        ),
    )


def _correcting(conn) -> set[str]:  # noqa: ANN001
    """정정 초안이 이미 검수를 기다리는 답변들.

    화면이 이것을 밝히지 않으면 **사람이 이미 처리 중인 건을 또 보게 된다** —
    Q5 는 방치 비용이 높은 대기열이라 그 낭비가 곧 다른 건의 지연이다.
    """
    rows = conn.execute(
        "SELECT DISTINCT corrects FROM answer_draft "
        "WHERE corrects IS NOT NULL AND state = ?",
        (draft_store.PENDING,),
    ).fetchall()
    return {r["corrects"] for r in rows}


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


def _reasons(codes) -> list[tuple[str, str]]:  # noqa: ANN001
    return [(str(r), review_domain.DESCRIPTIONS[r]) for r in codes]


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


@dataclass(frozen=True)
class _ContentRow:
    """Q3 목록 한 줄."""

    draft: object
    type_title: str
    nature: str
    age_hours: float
    summary: str

    @property
    def age_label(self) -> str:
        if self.age_hours < 1:
            return "방금"
        if self.age_hours < 48:
            return f"{int(self.age_hours)}시간"
        return f"{int(self.age_hours / 24)}일"


@dataclass(frozen=True)
class _ContentDetail:
    """콘텐츠 검수 화면이 필요한 전부."""

    draft: object
    ctype: object
    previous: object
    sources: dict[str, str]
    stale_ids: frozenset[str]
    findings: object
    diff: str
    churn: float
    age_hours: float

    @property
    def type_title(self) -> str:
        return self.ctype.title

    @property
    def nature(self) -> str:
        return "살아있는 문서" if self.ctype.living else "발행물"

    @property
    def destination(self) -> str:
        place = self.ctype.destination.place
        return f"{'문서 면' if self.ctype.living else '발행 면'}({place.operation})"

    @property
    def diff_review(self) -> bool:
        """변경분 검수인가 (§5.5.5). **선언이 정한다** (FR-42)."""
        return self.ctype.review.scope is content_registry.Scope.DIFF

    @property
    def facts_label(self) -> str:
        """이 타입에서 박은 사실을 뭐라고 부르는가.

        칼럼에서는 **관찰**(권고의 근거)이고 뉴스레터에서는 **집계**(그 기간에 센 것)다 —
        같은 자리에 담기지만 검수자가 볼 이유가 다르다.
        """
        return (
            "이번 기간에 센 것"
            if self.ctype.input is content_registry.Input.PERIOD_SUMMARY
            else "관찰"
        )

    @property
    def observations(self) -> list[tuple[bool, str]]:
        """그때 무엇을 관찰했는가 — **(본문이 밝혔는가, 문장)** (§7.6.2, FR-41).

        **초안에 박힌 것을 읽는다** — 지금 다시 세면 숫자가 달라져 검수자가 본문과
        대조할 수 없다. 발행물은 회수할 수 없으므로 그 대조가 마지막 기회다.

        **본 것을 전부 보여 주고 밝힌 것을 표시한다.** 전부를 봐야 지어낸 관찰을
        가려낼 수 있고, 표시가 있어야 "이 권고가 무엇에 기댔는가"를 한눈에 본다.
        """
        return [(f.cited, f.text) for f in content_store.facts_of(self.draft)]

    @property
    def awaiting_final_check(self) -> bool:
        return content_review.awaiting_final_check(self.ctype)

    @property
    def generated_by(self) -> str:
        return self.draft.generated_by

    @property
    def age_label(self) -> str:
        if self.age_hours < 1:
            return "방금"
        if self.age_hours < 48:
            return f"{int(self.age_hours)}시간"
        return f"{int(self.age_hours / 24)}일"


def _content_row(conn, types, draft) -> _ContentRow:  # noqa: ANN001
    ctype = types.get(draft.type_id)
    return _ContentRow(
        draft=draft,
        type_title=ctype.title,
        nature="살아있는 문서" if ctype.living else "발행물",
        age_hours=_hours_since(draft.created_at),
        summary=(
            "갱신 — 변경분을 본다" if draft.based_on else "첫 제작 — 전문을 본다"
        ),
    )


def _content_detail(cfg, conn, types, ticket_id):  # noqa: ANN001, ANN201
    """티켓에서 초안으로, 초안에서 근거·직전 판본·소견으로.

    **diff 와 변경 비율은 여기서 다시 센다.** 제작 시점에 저장해 두면 직전 판본이
    바뀌었을 때 화면이 옛 비교를 보여 주는데, 세는 비용이 낮아 저장할 이유가 없다.
    """
    draft = content_store.by_ticket(conn, ticket_id)
    if draft is None:
        return None
    ctype = types.get(draft.type_id)
    sources = _source_text(cfg, conn, [draft])
    stale = _stale_ids(cfg, draft)
    previous = content_store.get(conn, draft.based_on) if draft.based_on else None
    return _ContentDetail(
        draft=draft,
        ctype=ctype,
        previous=previous,
        sources=sources,
        stale_ids=stale,
        findings=content_review.inspect(
            ctype, draft, source_text=sources, stale_ids=stale
        ),
        diff=content_production.diff_of(previous.body, draft.body) if previous else "",
        churn=content_production.churn(previous.body, draft.body) if previous else 0.0,
        age_hours=_hours_since(draft.created_at),
    )


def _stale_ids(cfg, draft) -> frozenset[str]:  # noqa: ANN001
    """근거 중 낡은 것. **화면이 표시하고 P4 가 이것을 본다.**"""
    repo = KnowledgeRepository(cfg.knowledge_dir)
    if not repo.root.exists():
        return frozenset()
    return frozenset(
        s.item.id
        for s in repo.scan()[0]
        if s.item.id in set(draft.grounding) and s.item.stale
    )


def _hours_since(when: str) -> float:
    if not when:
        return 0.0
    try:
        at = datetime.fromisoformat(when)
    except ValueError:
        return 0.0
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - at).total_seconds() / 3600


def _publish_content(conn, parent_of, ctype, draft, *, cfg, final_check_by=None) -> str:  # noqa: ANN001
    """게재하고 **무슨 일이 있었는지 한 문장으로** 돌려준다.

    실패를 삼키지 않는다 — 승인은 남았는데 나가지 않았다는 사실이 화면에 보이지
    않으면, 운영자는 나갔다고 믿고 다음 일로 넘어간다. 살아있는 문서는 멱등하므로
    다음 배치가 다시 시도한다 (D46).

    **어댑터를 늦게 만든다.** 연동 전 단계에서도 승인은 되어야 하고, 모 시스템이
    설정되지 않았다는 이유로 **판정이 통째로 실패하면 대기열이 막힌다** — 승인은
    우리 안의 일이고 게재는 바깥으로 나가는 일이라 실패의 성격이 다르다.
    """
    try:
        record = content_publication.publish(
            conn,
            parent_of(),
            ctype,
            draft,
            # **근거 버전을 여기서도 박는다.** 화면에서 승인하는 것이 보통의
            # 경로인데 배치에서만 박으면 그 칸은 **실제로는 늘 비어 있다** —
            # 기록된 듯 보이지만 아무것도 재현할 수 없다.
            repo=KnowledgeRepository(cfg.knowledge_dir),
            final_check_by=final_check_by,
        )
    except (
        content_publication.FinalCheckMissing,
        content_publication.AlreadyPublished,
        content_publication.NotApproved,
    ) as exc:
        # **다시 시도한다고 말하지 않는다.** 셋 다 배치가 고칠 수 있는 것이 아니라
        # 지금 상태가 그렇다는 뜻이고, "다음 배치가 한다"고 적으면 오지 않을 것을
        # 기다리게 한다.
        return str(exc)
    except (NotConfigured, RuntimeError) as exc:
        return (
            f"게재하지 못했다 — {exc}. **승인은 남아 있고 다음 배치가 다시 시도한다** "
            "(문서 면은 멱등하다)"
            if ctype.living
            else f"게재하지 못했다 — {exc}. 발행 면이라 **사람이 확인해야 한다**"
        )
    where = "문서 면" if ctype.living else "발행 면"
    return f"{where}에 게재했다 — {record.parent_ref}"
