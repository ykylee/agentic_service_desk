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
    if cfg.web_host not in ("127.0.0.1", "localhost", "::1"):
        # **루프백 밖으로 나가는 것은 조용하면 안 된다.** 이 화면에는 인증이
        # 없고 승인·게재를 POST 로 실행한다 — 붙을 수 있는 사람은 곧 누를 수
        # 있는 사람이다.
        print(
            f"[web] **루프백 밖에 열린다: {cfg.web_host}:{cfg.web_port}** — "
            f"이 대시보드에는 인증이 없다. 승인·게재·해결 표시를 누구나 누를 수 있다"
        )
    uvicorn.run(
        "agentic_service_desk.web.app:create_app",
        factory=True,
        host=cfg.web_host,
        port=cfg.web_port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
