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
from agentic_service_desk.ingest.source import MirrorNotReady, SourceMirror
from agentic_service_desk.operations.checkpoint import SOURCE, get_cursor
from agentic_service_desk.operations.schema import connect, initialize


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
        """한 주기. 단계에 따라 할 일이 붙는다."""
        self._sync_source()

    def _sync_source(self) -> None:
        """소스 저장소를 갱신하고 **무엇이 바뀌었는지**만 알아 둔다 (WBS-4.2.1).

        여기서는 아직 ingest 하지 않는다 — 그것은 WBS-4.2.4 다. **커서도 옮기지
        않는다**: ingest 가 실제로 끝난 뒤에만 옮겨야 하기 때문이다. 먼저 옮기면
        중단됐을 때 그 구간을 건너뛰고, 그러면 **지식에 구멍이 생기는데 아무도
        알아채지 못한다.**
        """
        if not self._cfg.parent_repo_url:
            return
        mirror = SourceMirror(self._cfg.parent_repo_url, self._cfg.source_mirror_dir)
        try:
            mirror.ensure_cloned()
            mirror.fetch()
        except (MirrorNotReady, RuntimeError) as exc:
            print(f"[worker] 소스 동기화 실패: {exc}")
            return

        conn = connect(self._cfg.operations_db)
        initialize(conn)
        cursor = get_cursor(conn, SOURCE)
        changed = mirror.changed_paths_since(cursor)
        commits = mirror.commits_since(cursor)
        conn.close()

        if not changed and not commits:
            return
        scope = "전체(최초)" if cursor is None else f"{cursor[:8]}..HEAD"
        print(f"[worker] 소스 변경 {scope} — 커밋 {len(commits)}건, 경로 {len(changed)}개")
