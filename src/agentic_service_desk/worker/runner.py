"""배치 루프.

지금은 골격이다 — 주기만 돌고 아무것도 하지 않는다. 실제 작업은 단계에 따라 붙는다.

    S0 : 소스·QnA 수집 → 산출물 필터 → Ingest → Lint   (WBS-4.2)
    S4~: 콘텐츠 제작                                   (WBS-4.6~4.7)
"""

from __future__ import annotations

import signal
import time
from types import FrameType

from agentic_service_desk.adapters.factory import build_parent_system
from agentic_service_desk.adapters.parent_system import NotConfigured
from agentic_service_desk.config import Settings
from agentic_service_desk.ingest.agent import IngestAgent
from agentic_service_desk.ingest.harness_runner import HarnessError, PiHarness
from agentic_service_desk.ingest.output_filter import (
    BotAccountsNotConfigured,
    OutputFilter,
    build_output_filter,
)
from agentic_service_desk.ingest.qna import QnaCollector
from agentic_service_desk.ingest.run import IngestRun
from agentic_service_desk.ingest.source import MirrorNotReady, SourceMirror
from agentic_service_desk.knowledge.repository import KnowledgeRepoError, KnowledgeRepository
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
        self._filter: OutputFilter | None = None

    def request_stop(self, signum: int, frame: FrameType | None) -> None:
        print(f"[worker] 중단 요청 (signal={signum}). 현재 청크를 마치고 멈춘다.")
        self._stopping = True

    def run(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        interval = self._cfg.poll_interval_seconds
        print(f"[worker] stage={self._cfg.stage} interval={interval}s — 배치 경로")
        # **설정 누락은 기동에서 드러나야 한다.** 배치 한복판에서 터지면 그때까지
        # 수집한 것과 못 한 것이 섞이고, 로그를 뒤져야 원인을 안다.
        try:
            self._output_filter()
        except BotAccountsNotConfigured as exc:
            print(f"[worker] ingest 를 건너뛴다 — {exc}")
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
        self._sync_qna()
        self._ingest()

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

    def _sync_qna(self) -> None:
        """QnA 이력을 Raw Layer 로 가져온다 (WBS-4.2.2, FR-52).

        **주기가 곧 유입 감지 지연이다** (NFR-7). 분 단위로 도는 것은 여기가 답변
        파이프라인의 출발점이기 때문이다 — 주기를 늘리면 그만큼 답변이 늦어진다.

        소스 쪽(`_sync_source`)과 달리 여기서는 **커서를 옮긴다.** 저쪽 커서는 ingest
        지점이라 ingest 가 끝나야 옮길 수 있지만, 이쪽 커서는 **수집 지점**이고
        수집은 방금 끝났다. 원문이 Raw Layer 에 남아 있으므로 ingest 는 자기 진행
        지점을 따로 들고 여기서 다시 읽는다 (WBS-4.2.4).
        """
        if not self._cfg.parent_api_base_url and self._cfg.parent_adapter != "mock":
            return

        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            parent = build_parent_system(self._cfg)
            report = QnaCollector(parent, conn).collect()
        except (NotConfigured, RuntimeError) as exc:
            print(f"[worker] QnA 수집 실패: {exc}")
            return
        finally:
            conn.close()

        if not report.changed:
            return
        print(
            f"[worker] QnA 수집 — 새 질문 {report.new_questions}건, "
            f"답변 {report.answers}건, 후속 {report.followups}건, "
            f"명시적 해결 상향 {report.upgraded}건 (다시 훑음 {report.refreshed_questions}건)"
        )

    def _output_filter(self) -> OutputFilter:
        """되먹임 차단 필터. **한 번 만들어 재사용한다** (NFR-4)."""
        if self._filter is None:
            self._filter = build_output_filter(self._cfg.bot_accounts)
        return self._filter

    def _ingest(self) -> None:
        """원천을 읽어 지식을 짓는다 (WBS-4.2.4, FR-3).

        **여기서 소스 커서가 옮겨진다** — `_sync_source` 가 옮기지 않고 남겨 둔 이유가
        이것이다. ingest 가 끝나야 그 구간을 처리했다고 말할 수 있다.
        """
        if not self._cfg.llm_base_url or not self._cfg.llm_model:
            return
        try:
            output_filter = self._output_filter()
        except BotAccountsNotConfigured:
            return  # 기동에서 이미 알렸다. 매 주기 같은 말을 반복하지 않는다.

        harness = PiHarness(self._cfg.llm_model, self._cfg.llm_api_key)
        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        mirror = (
            SourceMirror(self._cfg.parent_repo_url, self._cfg.source_mirror_dir)
            if self._cfg.parent_repo_url
            else None
        )

        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            result = IngestRun(
                repo=repo,
                agent=IngestAgent(harness),
                conn=conn,
                output_filter=output_filter,
                mirror=mirror,
            ).run()
        except (HarnessError, KnowledgeRepoError, RuntimeError) as exc:
            print(f"[worker] ingest 실패: {exc}")
            return
        finally:
            conn.close()

        for note in _ingest_notes(result):
            print(f"[worker] {note}")


def _ingest_notes(result) -> list[str]:  # noqa: ANN001
    """무엇을 알릴 것인가. **조용히 넘어가는 것을 만들지 않는다.**"""
    notes = []
    if result.changed:
        notes.append(f"ingest — {result.summary()} (커밋 {(result.commit or '없음')[:8]})")
    if result.held_for_human:
        notes.append(
            f"사람이 고친 항목 {len(result.held_for_human)}건은 덮어쓰지 않았다 "
            f"— 모순 대기열은 WBS-4.2.5 다"
        )
    if result.dropped_config_paths:
        notes.append(f"설정 파일 {len(result.dropped_config_paths)}개를 원천에서 뺐다 (FR-9)")
    if result.omitted_messages:
        notes.append(
            f"커밋 메시지 {result.omitted_messages}건이 한도를 넘어 실리지 않았다 "
            f"— 그만큼 '왜'가 빠졌다"
        )
    if result.broken_items:
        notes.append(f"읽을 수 없는 지식 파일 {len(result.broken_items)}개")
    for failure in result.failures:
        notes.append(f"ingest 실패 — {failure}")
    return notes
