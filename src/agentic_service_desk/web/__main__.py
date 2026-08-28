"""`asd-web` — 온라인 프로세스."""

from __future__ import annotations

import uvicorn

from agentic_service_desk.config import load_settings


def main() -> None:
    cfg = load_settings()
    print(f"[web] stage={cfg.stage} — 온라인 경로 (대시보드 · 답변)")
    uvicorn.run(
        "agentic_service_desk.web.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
    )


if __name__ == "__main__":
    main()
