"""배치 루프.

    S0 : 소스·QnA 수집 → Ingest → Lint → 임베딩 색인      (WBS-4.2, 4.4.1)
    S3 : 유입 처리 → 답변 파이프라인 → 종결 기록 초안      (WBS-4.5)
    S4~: 콘텐츠 제작                                       (WBS-4.6~4.7)

**한 주기의 순서에는 이유가 있다.** 수집 다음에 유입 처리가 오는 것은 여기가 답변
파이프라인의 출발점이라 사이가 벌어지면 그만큼 답변이 늦기 때문이고(NFR-7),
색인이 맨 뒤인 것은 방금 바뀐 지식이 반영돼야 하기 때문이다.
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
from agentic_service_desk.knowledge.lint import Lint
from agentic_service_desk.knowledge.search import Search, rebuild_embedding_index
from agentic_service_desk.pipeline.answer import AnswerPipeline
from agentic_service_desk.pipeline.review import Reviewer
from agentic_service_desk.llm.embeddings import build_embedding_provider
from agentic_service_desk.knowledge.repository import KnowledgeRepoError, KnowledgeRepository
from agentic_service_desk.operations import intake as intake_domain
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations.drafter import Drafter
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
        self._intake()
        self._release_held_tickets()
        self._draft_resolutions()
        self._ingest()
        self._lint()
        self._reindex()

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

    def _intake(self) -> None:
        """유입된 질문을 처리 단위로 옮긴다 (WBS-4.5.2, FR-27).

        **수집 직후에 돈다.** 여기가 답변 파이프라인의 출발점이므로, 수집과 사이가
        벌어지면 그만큼 답변이 늦어진다 (NFR-7).

        **LLM 이 없어도 돈다.** 그때 파이프라인 없이 티켓만 발행하고 사람에게
        넘긴다 — 질문이 왔는데 아무 기록도 없는 것보다 낫고, 유입 자체가
        W4(질문이 기록되지 않는다) 관측의 재료다.
        """
        if not self._cfg.operations_db.exists():
            return
        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            report = intake_domain.run(
                conn,
                pipeline=self._answer_pipeline(conn),
                reviewer=self._reviewer(),
            )
        finally:
            conn.close()

        if not report.changed:
            return
        print(
            f"[worker] 유입 {len(report.admitted)}건 — 자동 종결 {report.auto_closed}건, "
            f"사람 대기열 {report.to_human}건"
        )
        for failure in report.failures:
            print(f"[worker] 유입 처리 실패 — {failure}")

    def _answer_pipeline(self, conn) -> AnswerPipeline | None:  # noqa: ANN001
        """답변 파이프라인. **없으면 없는 대로 간다.**

        생성기가 없으면 3단계에서 멈추고 그 건은 사람에게 간다 — 그것이 이 시스템의
        정상 결과다 (§5.1). 억지로 답을 만들지 않는 것이 요점이므로 여기서 조용히
        건너뛰는 대신 **파이프라인을 통째로 빼서** 티켓만 남긴다.
        """
        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        if not repo.root.exists():
            return None
        return AnswerPipeline(
            search=Search(repo=repo, conn=conn),
            conn=conn,
            harness=self._harness(),
            # 모델 식별자는 **설정에서 온다** — 하네스에게 되물으면 실행기마다 답이
            # 달라진다. 이 값이 답변 이력의 생성 주체가 된다 (§6.6.1 필드 5).
            generated_by=self._cfg.llm_model,
        )

    def _reviewer(self) -> Reviewer | None:
        """4단계 검수기. **검수를 건너뛰는 것과 통과시키는 것은 다르다** (§5.6.1).

        모델이 없으면 기계적 검사(P4·P1)만 돈다 — `Reviewer` 가 그 경우를 이미
        안다. 검수기 자체를 빼지 않는 이유가 그것이다.
        """
        return Reviewer(self._harness())

    def _harness(self) -> PiHarness | None:
        if not self._cfg.llm_base_url or not self._cfg.llm_model:
            return None
        return PiHarness(self._cfg.llm_model, self._cfg.llm_api_key)

    def _release_held_tickets(self) -> None:
        """응답이 온 보류 티켓을 다시 연다 (§6.7.1).

        **수집 직후에 돈다.** 보류를 푸는 신호가 후속 답글이므로, 방금 들어온 후속을
        보고 판단해야 다음 주기까지 기다리지 않는다.

        사람이 다시 열게 하지 않는 이유는 보류의 값이 거기 있기 때문이다 — 응답을
        알아채는 일까지 사람 몫이면 결국 열어 보게 되고, 그것이 보류로 피하려던
        **반복되는 재판단**이다 (§6.7.2).
        """
        if not self._cfg.operations_db.exists():
            # 아직 아무것도 돌지 않았다. 여기서 DB 를 만들면 **설정이 비어 있는
            # 워커가 파일을 남기고**, 그것이 "연동이 없다"와 "아직 아무 일도 없다"를
            # 구분하기 어렵게 만든다.
            return
        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            reopened = ticket_domain.release_held_with_response(conn)
        finally:
            conn.close()
        if reopened:
            print(f"[worker] 응답이 와 보류를 푼 티켓 {len(reopened)}건 — Q1 로 돌아간다")

    def _output_filter(self) -> OutputFilter:
        """되먹임 차단 필터. **한 번 만들어 재사용한다** (NFR-4)."""
        if self._filter is None:
            self._filter = build_output_filter(self._cfg.bot_accounts)
        return self._filter

    def _draft_resolutions(self) -> None:
        """종결 기록 초안을 채운다 — **두 원천이다** (FR-11, FR-27).

        수동 등록 건(WBS-4.3.3)과 **자동 처리 건**(WBS-4.5.2)이 같은 형식으로 남는다.
        후자가 없으면 §5.3 이 허용한 "명시적 해결된 봇 답변"에 승격할 물건이 없다 —
        규칙만 있고 올릴 것이 없는 상태였다 (§6.4.3-1).

        **등록은 온라인에서 즉시 끝나고 초안은 여기서 만들어진다.** 등록 응답을
        LLM 호출만큼 붙들면 부담이 되돌아와 §1.4.4 의 유인이 상쇄된다.

        무효화 조건은 여기서 채우지 않는다 — 사람 몫이다 (§5.6.4).
        """
        harness = self._harness()
        if harness is None:
            return
        if not self._cfg.operations_db.exists():
            return

        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            report = Drafter(harness).run(conn)
            if repo.root.exists():
                auto = intake_domain.draft_resolutions(conn, harness, repo)
                report.drafted.extend(auto.drafted)
                report.failures.extend(auto.failures)
        except (HarnessError, RuntimeError) as exc:
            print(f"[worker] 종결 기록 초안 실패: {exc}")
            return
        finally:
            conn.close()

        if report.drafted:
            print(
                f"[worker] 종결 기록 초안 {len(report.drafted)}건 — "
                f"**무효화 조건은 비워 뒀다.** 사람이 채워야 닫힌다 (§5.6.4)"
            )
        for failure in report.failures:
            print(f"[worker] 초안 실패 — {failure}")

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


    def _lint(self) -> None:
        """지식베이스 정합성을 훑는다 (WBS-4.2.6, FR-7).

        **ingest 뒤에 돈다.** 방금 만든 항목까지 포함해 보아야 하고, stale 표시가
        같은 주기 안에서 최신 상태를 반영한다.

        LLM 을 쓰지 않으므로 `_ingest` 와 달리 **모델 설정이 없어도 돈다** — 지식이
        이미 쌓여 있는 환경에서 검사만 돌리는 경우가 있다.
        """
        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        if not repo.root.exists():
            return
        mirror = (
            SourceMirror(self._cfg.parent_repo_url, self._cfg.source_mirror_dir)
            if self._cfg.parent_repo_url
            else None
        )
        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        except (KnowledgeRepoError, RuntimeError) as exc:
            print(f"[worker] lint 실패: {exc}")
            return
        finally:
            conn.close()

        for note in _lint_notes(report):
            print(f"[worker] {note}")


    def _reindex(self) -> None:
        """임베딩 인덱스를 다시 만든다 (WBS-4.4.1, ADR-004).

        **ingest·Lint 뒤에 돈다** — 방금 바뀐 지식이 반영돼야 한다. 통째로 다시
        만드는 것은 항목이 수백~수천이라 부담되지 않기 때문이고, 부분 갱신은 어느
        항목이 낡은 벡터를 들고 있는지를 계속 추적해야 한다.

        **실패해도 배치를 세우지 않는다.** 개발 환경에서 이 경로가 레이트 리밋으로
        막혀 있고(O57), 임베딩 없이도 검색은 키워드·표현 사전으로 돈다. 다만
        조용히 넘어가지는 않는다 — 인덱스가 없는 것과 검색이 임베딩을 안 쓰는 것은
        로그에서 구분돼야 한다.
        """
        if not self._cfg.llm_embedding_model:
            return
        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        if not repo.root.exists():
            return

        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            indexed = rebuild_embedding_index(
                conn,
                repo,
                build_embedding_provider(
                    self._cfg.embedding_provider,
                    self._cfg.embedding_base_url or self._cfg.llm_base_url,
                    self._cfg.llm_embedding_model,
                    self._cfg.llm_api_key,
                ),
                self._cfg.llm_embedding_model,
            )
        except Exception as exc:  # noqa: BLE001 — 제공자가 어떤 오류를 낼지 우리가 정하지 않는다
            print(f"[worker] 임베딩 인덱스 실패: {exc}")
            print("[worker]   검색은 키워드·표현 사전으로 계속 돈다 (ADR-004)")
            return
        finally:
            conn.close()
        if indexed:
            print(f"[worker] 임베딩 인덱스 {indexed}건 재생성")


def _lint_notes(report) -> list[str]:  # noqa: ANN001
    """무엇을 알릴 것인가. **깨끗하면 조용하다** — 매 주기 '이상 없음'을 찍으면
    실제 소견이 그 사이에 묻힌다."""
    notes = []
    if report.marked_stale:
        notes.append(
            f"stale {len(report.marked_stale)}건을 표시했다 — 삭제하지 않는다 (FR-8)"
        )
    if report.newly_opened:
        notes.append(f"lint 소견 {report.newly_opened}건을 Q5 로 올렸다")
    if report.open_contradictions:
        notes.append(f"미해결 모순 {report.open_contradictions}건 (Q4)")
    if report.broken_files:
        notes.append(f"읽을 수 없는 지식 파일 {len(report.broken_files)}개")
    if report.indexed:
        notes.append(f"번들 목록에 {report.indexed}건을 새로 등재했다")
    return notes


def _ingest_notes(result) -> list[str]:  # noqa: ANN001
    """무엇을 알릴 것인가. **조용히 넘어가는 것을 만들지 않는다.**"""
    notes = []
    if result.changed:
        notes.append(f"ingest — {result.summary()} (커밋 {(result.commit or '없음')[:8]})")
    if result.held_for_human:
        notes.append(
            f"사람이 고친 항목 {len(result.held_for_human)}건은 덮어쓰지 않았다 "
            f"— 모순 {result.contradictions_opened}건을 Q4 로 올렸다 (FR-6)"
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
