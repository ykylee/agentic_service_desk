"""`asd-web` — 온라인 프로세스."""

from __future__ import annotations

import uvicorn

from agentic_service_desk.config import load_settings
from agentic_service_desk.operations.preflight import check_schema


def main() -> int:
    cfg = load_settings()
    if not check_schema(cfg):
        return 1
    print(f"[web] stage={cfg.stage} — 온라인 경로 (대시보드 · 답변)")
    uvicorn.run(
        "agentic_service_desk.web.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
