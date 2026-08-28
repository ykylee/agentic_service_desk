"""웹 진입점.

지금은 골격이다 — 상태 확인과 켜진 단계 표시만 한다. 대기열 화면은 해당 기능이
켜지는 단계에 맞춰 늘어난다 (FR-59). 켜지지 않은 기능의 대기열은 만들지 않는다.
"""

from __future__ import annotations

from fastapi import FastAPI

from agentic_service_desk import __version__
from agentic_service_desk.config import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """앱을 만든다. 설정을 인자로 받는 이유는 테스트에서 갈아 끼우기 위해서다."""
    cfg = settings or load_settings()
    app = FastAPI(title="Agentic Service Desk", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        """살아 있는지."""
        return {"status": "ok", "version": __version__}

    @app.get("/")
    def index() -> dict[str, object]:
        """현재 켜진 단계와 아직 비어 있는 대기열.

        S0 에는 Q4(모순)·Q8(지식 공백)만 뜬다 — 나머지는 그 기능이 아직 없다.
        """
        return {
            "stage": cfg.stage,
            "queues": _queues_for_stage(cfg.stage),
            "note": "골격이다. 대기열 내용은 WBS-4.2.7 부터 채운다.",
        }

    return app


def _queues_for_stage(stage: str) -> list[str]:
    """단계별로 뜨는 대기열 (FR-59).

    켜지지 않은 기능의 대기열을 보여주면 1인 운영자가 매번 빈 화면을 훑게 된다.
    """
    by_stage: dict[str, list[str]] = {
        "S0": ["Q4", "Q8"],
        "S1": ["Q1", "Q4", "Q8"],
        "S2": ["Q1", "Q2", "Q4", "Q8"],
        "S3": ["Q1", "Q2", "Q4", "Q5", "Q6", "Q7", "Q8"],
        "S4": ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"],
        "S5": ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"],
    }
    return by_stage.get(stage, by_stage["S0"])
