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
from agentic_service_desk.operations import manual_entry
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
                "Q4": status.open_contradictions,
                "Q8": len(board.knowledge_gaps()),
            }
        finally:
            conn.close()
        ctx = shell()
        # 아직 자료가 없는 대기열은 0 으로 둔다 — 화면에 뜨는 것과 셀 수 있는 것은 다르다.
        ctx |= {"s": status, "counts": {q.id: counts.get(q.id, 0) for q in ctx["queues"]}}
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
        return TEMPLATES.TemplateResponse(request, "entry.html", ctx)

    @app.post("/entry")
    def entry_submit(  # noqa: ANN201
        request: Request, question: str = Form(...), answer: str = Form(...)
    ):
        """등록한다. **여기서 LLM 을 부르지 않는다** — 초안은 배치가 채운다.

        수십 초 걸리는 호출로 응답을 붙들면 부담이 되돌아와 §1.4.4 가 무너진다.
        """
        _, conn = dashboard()
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
