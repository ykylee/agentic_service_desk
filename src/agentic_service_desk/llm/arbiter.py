"""온라인·배치 사이의 양보 조정 (ADR-005).

ADR-005 는 "LLM 게이트웨이를 두고 요청에 우선순위를 준다"고 했다. 그런데 온라인과
배치는 **다른 프로세스**다(NFR-6) — 메모리를 공유하지 않으므로 **한 프로세스 안의
우선순위 큐로는 조정되지 않는다.**

그래서 실제 형태는 우선순위 큐가 아니라 **프로세스 간 양보 신호**다. 온라인이
LLM 을 쓰는 동안 표시를 남기고, 배치는 **청크 경계에서 그것을 확인해 기다린다.**

정교한 스케줄러를 만들지 않는 이유는 충돌 확률 자체가 낮기 때문이다(§1.3.1) —
질문은 일 단위로 소수다. 이 단순한 규칙으로 "대규모 ingest 중 답변이 굶는" 상황
(O32 가 걱정한 것)을 막기에 충분하다.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

#: 표시가 이보다 오래되면 무시한다. 온라인 프로세스가 죽어 표시를 지우지 못한 경우,
#: 배치가 영원히 기다리는 것을 막는다.
STALE_AFTER_SECONDS = 120


class YieldSignal:
    """온라인이 LLM 을 쓰는 중임을 알리는 파일 표시.

    파일을 쓰는 이유는 1인 운영에서 **조정용 서버를 하나 더 띄우지 않기 위해서**다
    (CO-7). 두 프로세스가 같은 호스트에 있으므로 이것으로 충분하다.
    """

    def __init__(self, marker_path: Path) -> None:
        self._path = marker_path

    # --- 온라인 쪽 -------------------------------------------------------

    @contextmanager
    def online_in_use(self) -> Iterator[None]:
        """온라인 요청이 진행되는 동안 표시를 세운다."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(f"{os.getpid()} {time.time()}\n", encoding="utf-8")
        try:
            yield
        finally:
            self._path.unlink(missing_ok=True)

    # --- 배치 쪽 ---------------------------------------------------------

    def online_is_waiting(self) -> bool:
        """온라인이 쓰는 중인가. 표시가 낡았으면 아니라고 본다."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return False
        try:
            stamped = float(raw.split()[-1])
        except (IndexError, ValueError):
            return False
        return (time.time() - stamped) < STALE_AFTER_SECONDS

    def yield_if_needed(self, *, poll_seconds: float = 0.5, max_wait: float = 30.0) -> bool:
        """청크 경계에서 호출한다. 온라인이 쓰는 중이면 비켜 준다.

        `max_wait` 를 두는 이유는 배치가 무한정 굶지 않게 하기 위해서다 — 지식이
        안 자라는 것도 실패다(§8.2). 돌려주는 값은 실제로 기다렸는지 여부다.
        """
        if not self.online_is_waiting():
            return False
        waited = 0.0
        while self.online_is_waiting() and waited < max_wait:
            time.sleep(poll_seconds)
            waited += poll_seconds
        return True
