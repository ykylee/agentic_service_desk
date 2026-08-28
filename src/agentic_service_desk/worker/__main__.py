"""`asd-worker` — 배치 프로세스."""

from __future__ import annotations

from agentic_service_desk.config import load_settings
from agentic_service_desk.worker.runner import BatchRunner


def main() -> None:
    BatchRunner(load_settings()).run()


if __name__ == "__main__":
    main()
