"""배치 루프.

지금은 골격이다 — 주기만 돌고 아무것도 하지 않는다. 실제 작업은 단계에 따라 붙는다.

    S0 : 소스·QnA 수집 → 산출물 필터 → Ingest → Lint   (WBS-4.2)
    S4~: 콘텐츠 제작                                   (WBS-4.6~4.7)
"""

from __future__ import annotations

import signal
import time
from types import FrameType

from agentic_service_desk.config import Settings


class BatchRunner:
    """주기 실행기. **중단 신호를 받으면 현재 청크를 마치고 멈춘다.**

    즉시 죽이지 않는 이유는 ingest 중간에 끊기면 지식 항목이 반만 쓰인 채 남을 수
    있기 때문이다.
    """

    def __init__(self, settings: Settings) -> None:
        self._cfg = settings
        self._stopping = False

    def request_stop(self, signum: int, frame: FrameType | None) -> None:
        print(f"[worker] 중단 요청 (signal={signum}). 현재 청크를 마치고 멈춘다.")
        self._stopping = True

    def run(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        interval = self._cfg.poll_interval_seconds
        print(f"[worker] stage={self._cfg.stage} interval={interval}s — 배치 경로")
        while not self._stopping:
            self._tick()
            for _ in range(interval):
                if self._stopping:
                    break
                time.sleep(1)
        print("[worker] 멈췄다.")

    def _tick(self) -> None:
        """한 주기. 단계에 따라 할 일이 붙는다 (WBS-4.2 부터)."""
