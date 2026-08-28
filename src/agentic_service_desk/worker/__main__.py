"""`asd-worker` — 배치 프로세스."""

from __future__ import annotations

from agentic_service_desk.config import load_settings
from agentic_service_desk.operations.preflight import check_schema
from agentic_service_desk.worker.runner import BatchRunner


def main() -> int:
    cfg = load_settings()
    if not check_schema(cfg):
        return 1
    BatchRunner(cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
