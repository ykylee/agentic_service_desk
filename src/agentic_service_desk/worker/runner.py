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
from agentic_service_desk.content import production as content_production
from agentic_service_desk.content import publication as content_publication
from agentic_service_desk.content import store as content_store
from agentic_service_desk.content import registry as content_registry
from agentic_service_desk.content import review as content_review
from agentic_service_desk.ingest.agent import IngestAgent
from agentic_service_desk.ingest.harness_runner import HarnessError, PiHarness
from agentic_service_desk.ingest.output_filter import (
    BotAccountsNotConfigured,
    OutputFilter,
    build_output_filter,
)
from agentic_service_desk.ingest.qna import QnaCollector
from agentic_service_desk.ingest.run import IngestRun
from agentic_service_desk.ingest.source import MirrorNotReady, MirrorSet, build_mirrors
from agentic_service_desk.knowledge.lint import Lint
from agentic_service_desk.knowledge.search import Search, rebuild_embedding_index
from agentic_service_desk.pipeline import correction as correction_domain
from agentic_service_desk.pipeline import draft_store
from agentic_service_desk.pipeline.answer import AnswerPipeline
from agentic_service_desk.pipeline.review import Reviewer
from agentic_service_desk.llm.embeddings import build_embedding_provider
from agentic_service_desk.knowledge.repository import KnowledgeRepoError, KnowledgeRepository
from agentic_service_desk.operations import alert as alert_domain
from agentic_service_desk.operations import intake as intake_domain
from agentic_service_desk.operations import phase as phase_domain
from agentic_service_desk.operations import promotion as promotion_domain
from agentic_service_desk.operations import recheck as recheck_domain
from agentic_service_desk.operations import retention as retention_domain
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations import tracking as tracking_domain
from agentic_service_desk.operations.drafter import Drafter
from agentic_service_desk.operations.checkpoint import get_cursor, source_key
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

    def _mirrors(self):  # noqa: ANN201
        """붙은 저장소마다 자기 칸을 가진 미러.

        **설정에서 매번 만든다** — 들고 있지 않는 이유는 저장소 목록이 배치가
        도는 중에도 바뀔 수 있고, 그때 옛 목록을 쓰면 방금 뗀 저장소를 계속
        읽거나 방금 붙인 저장소를 영영 읽지 않기 때문이다.
        """
        return build_mirrors(self._cfg.parent_repo_urls, self._cfg.source_mirror_dir)

    def _content_source_commit(self, conn) -> str | None:  # noqa: ANN001
        """콘텐츠에 박을 소스 커밋 (WBS-4.6~4.7).

        이 값은 **출처가 된다** — 제작 트리거이면서 만들어진 글의 provenance 다.

        저장소가 여럿이면 **어느 것을 골라도 틀린 출처**다. 틀린 출처는 붙어 있다는
        사실 때문에 오히려 그럴듯해지므로(§2.2.3) 그럴 때는 **박지 않는다.** 대가는
        소스 변경을 트리거로 쓰는 콘텐츠 타입이 그 신호를 잃는 것이고, 그것은
        조용하지 않다 — 제작기가 "만들 것이 없다"로 말한다.

        여러 저장소에 걸친 콘텐츠 출처는 아직 답이 없다. S4 의 물음이고, 지금은
        S0 이라 여기서 정하지 않는다.
        """
        urls = self._cfg.parent_repo_urls
        if len(urls) != 1:
            return None
        return get_cursor(conn, source_key(urls[0]))

    def request_stop(self, signum: int, frame: FrameType | None) -> None:
        """멈추라는 말을 **닿는 곳까지** 전한다.

        플래그를 세우는 것만으로는 부족하다는 것이 2026-08-30 에 드러났다 —
        `_ingest` 한 번이 묶음 수백 개를 도는데 플래그를 바깥 루프에서만 보고
        있어, SIGTERM 을 받고도 반나절을 더 돌았다. `pkill` 이 통하지 않아
        **워커 다섯이 같은 지식베이스에 동시에 쓰고 있었다.**

        나머지 tick 단계(콘텐츠 제작 등)는 아직 이 신호를 보지 않는다 — 그것들은
        한 번이 짧아 급하지 않다. 급한 것은 ingest 하나다.
        """
        print(f"[worker] 중단 요청 (signal={signum}). 묶음 경계에서 멈춘다.")
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
        self._judge_phase()
        self._sync_source()
        self._sync_qna()
        self._intake()
        self._track()
        self._promote()
        self._release_held_tickets()
        self._sample_recheck()
        self._draft_resolutions()
        self._ingest()
        self._lint()
        self._correct()
        self._produce_content()
        self._inspect_content()
        self._publish_content()
        self._reindex()
        self._expire()
        self._notify()

    def _judge_phase(self) -> None:
        """세 축을 관측하고, 필요하면 **국면을 내린다** (WBS-4.8.1, FR-49, §1.3.3).

        **주기의 맨 앞이다.** 이 값이 이번 주기의 게재 판정(FR-57)과 자동 승격
        범위(§6.8.4-b)를 정하므로, 뒤에 두면 역행이 잡힌 주기의 답변들이 **느슨한
        강도로 이미 나간 뒤에** 강도가 올라간다 — 강화는 지체하지 않는다는 결정이
        한 주기만큼 지체된다.

        **올리는 일은 여기서 하지 않는다.** 전진은 운영자 승인이고(§1.3.3-c) 배치가
        승인을 대신할 수는 없다. 배치가 하는 것은 제안의 재료를 남기는 것까지다.
        """
        if not self._cfg.operations_db.exists():
            return
        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            thresholds = phase_domain.load_thresholds(self._cfg.phase_thresholds_path)
            observation = phase_domain.observe(
                conn,
                repo=repo,
                window_days=self._cfg.phase_window_days,
                min_sample=self._cfg.phase_min_sample,
            )
            phase_domain.save(conn, observation)
            judgment = phase_domain.judge(
                observation,
                phase_domain.baseline(
                    conn,
                    before=observation.observed_on,
                    lookback_days=thresholds.regression.lookback_days,
                ),
                current=phase_domain.current(conn, seed=self._cfg.phase),
                thresholds=thresholds,
            )
            decision = None
            if judgment.regression is not None:
                decision = phase_domain.regress(
                    conn, to=judgment.regression, signals=judgment.signals
                )
        except phase_domain.InvalidThresholds as exc:
            print(f"[worker] 국면 판정을 건너뛴다 — 임계 선언이 성립하지 않는다: {exc}")
            return
        finally:
            conn.close()

        if decision is not None:
            print(
                f"[worker] 국면을 {decision.from_phase} → {decision.to_phase} 로 "
                f"**내렸다.** 승인을 기다리지 않는다 (§1.3.3-c) — {decision.reason}"
            )
        elif judgment.signals:
            print(f"[worker] 역행 신호 — {' · '.join(judgment.signals)}")
        if judgment.proposal is not None:
            print(
                f"[worker] {judgment.proposal}국면 전진이 제안됐다 — "
                f"**운영자가 승인해야 올라간다** (/status)"
            )

    def _sync_source(self) -> None:
        """소스 저장소를 갱신하고 **무엇이 바뀌었는지**만 알아 둔다 (WBS-4.2.1).

        여기서는 아직 ingest 하지 않는다 — 그것은 WBS-4.2.4 다. **커서도 옮기지
        않는다**: ingest 가 실제로 끝난 뒤에만 옮겨야 하기 때문이다. 먼저 옮기면
        중단됐을 때 그 구간을 건너뛰고, 그러면 **지식에 구멍이 생기는데 아무도
        알아채지 못한다.**
        """
        mirrors = self._mirrors()
        if not mirrors:
            return

        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            for mirror in mirrors:
                # **저장소 하나가 죽어도 나머지는 간다.** 붙은 것이 여럿일 때 하나의
                # 접속 실패로 전부를 멈추면, 멀쩡한 저장소의 변경분까지 밀린다.
                try:
                    mirror.ensure_cloned()
                    mirror.fetch()
                except (MirrorNotReady, RuntimeError) as exc:
                    print(f"[worker] 소스 동기화 실패 ({mirror.repo_url}): {exc}")
                    continue

                cursor = get_cursor(conn, source_key(mirror.repo_url))
                changed = mirror.changed_paths_since(cursor)
                commits = mirror.commits_since(cursor)
                if not changed and not commits:
                    continue
                scope = "전체(최초)" if cursor is None else f"{cursor[:8]}..HEAD"
                print(
                    f"[worker] 소스 변경 {scope} ({mirror.repo_url}) — "
                    f"커밋 {len(commits)}건, 경로 {len(changed)}개"
                )
        finally:
            conn.close()

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
                gate=self._gate(conn),
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

    def _gate(self, conn) -> intake_domain.Gate | None:  # noqa: ANN001
        """게재 판정에 필요한 것 (WBS-4.5.5). **못 갖추면 게재하지 않는다.**

        연동이나 봇 계정이 없으면 내보낼 수도, 누가 올리는지 대조할 수도 없다 —
        그때는 초안만 Q2 에 쌓이고 사람이 본다. S0~S2 의 동작과 같다(§1.5.3).
        """
        accounts = frozenset(
            a.strip() for a in self._cfg.bot_accounts.split(",") if a.strip()
        )
        if not accounts:
            return None
        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        if not repo.root.exists():
            return None
        try:
            parent = build_parent_system(self._cfg)
        except (NotConfigured, RuntimeError) as exc:
            print(f"[worker] 게재 판정을 건너뛴다 — {exc}")
            return None
        return intake_domain.Gate(
            parent=parent,
            repo=repo,
            bot_accounts=accounts,
            stage=self._cfg.stage,
            # **국면은 설정이 아니라 DB 에서 온다** (WBS-4.8.1). 역행이 자동인 이상
            # 이 값은 시스템이 내릴 수 있는 자리에 있어야 한다 (§1.3.3-c).
            phase=phase_domain.current(conn, seed=self._cfg.phase),
            sample_rate=self._cfg.review_sample_rate,
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

    def _track(self) -> None:
        """게재 뒤를 따라간다 (WBS-4.5.4, FR-29~32).

        **순서에 이유가 있다.** ① 모 시스템이 알려준 명시적 해결을 먼저 반영한다 —
        그러지 않으면 방금 해결 표시가 눌린 건을 아래에서 다시 판정한다.
        ② 후속을 재실행하고 ③ 조용해진 건을 닫는다. 재실행이 먼저인 것은, 닫기부터
        하면 **후속이 달린 오래된 건이 미해결로 닫혀** 답할 기회를 잃기 때문이다.
        """
        if not self._cfg.operations_db.exists():
            return
        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            adopted = tracking_domain.adopt_explicit(conn)
            reran = tracking_domain.rerun(
                conn,
                pipeline=self._answer_pipeline(conn),
                reviewer=self._reviewer(),
                gate=self._gate(conn),
            )
            settled = tracking_domain.settle_quiet(
                conn, quiet_hours=self._cfg.quiet_hours
            )
        finally:
            conn.close()

        if adopted:
            print(
                f"[worker] 명시적 해결 {len(adopted)}건 반영 — "
                f"그 봇 답변에 **ingest 자격이 생겼다** (§5.3)"
            )
        if reran.changed:
            print(f"[worker] 후속 재실행 {len(reran.reruns)}건")
            for failure in reran.failures:
                print(f"[worker] 재실행 실패 — {failure}")
        if settled.changed:
            print(
                f"[worker] 조용해서 닫은 건 {len(settled.settled)}건 — "
                f"암묵적 해결 {settled.implicit} · 미해결 종료 {settled.gaps}"
                f" (Q8 지식 공백)"
            )

    def _promote(self) -> None:
        """승격 경로 B — 조건을 채운 봇 답변이 지식이 된다 (WBS-4.5.6, FR-33).

        **추적 다음에 돈다.** 조건 셋 중 하나가 명시적 해결이고 그것을 방금
        `_track()` 이 반영했다 — 순서가 뒤집히면 오늘 해결 표시가 눌린 건이 하루 늦게
        승격된다.

        1국면에는 아무것도 올라가지 않는다 (§6.8.4-b). 그래도 후보는 Q7 에 쌓이고,
        **무엇이 승격을 기다리는지가 국면을 올릴 판단 재료**가 된다.
        """
        if not self._cfg.operations_db.exists():
            return
        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        if not repo.root.exists():
            return

        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            report = promotion_domain.run_auto(
                conn,
                repo,
                phase=phase_domain.current(conn, seed=self._cfg.phase),
                relax_clean_review=self._cfg.relax_promotion,
            )
        except KnowledgeRepoError as exc:
            print(f"[worker] 승격 실패: {exc}")
            return
        finally:
            conn.close()

        if report.promoted:
            print(
                f"[worker] 자동 승격 {len(report.promoted)}건 — "
                f"**사람이 본 적이 없다.** 표본 재검증의 우선순위가 높다 (§6.8.4-a)"
            )
        for failure in report.failures:
            print(f"[worker] 승격 실패 — {failure}")

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

    def _sample_recheck(self) -> None:
        """다시 볼 표본을 뽑는다 (WBS-4.8.4, FR-50, §5.6.7).

        **승격 다음에 돈다.** 방금 자동으로 올라간 건이 그 주기의 표본 후보에
        들어와야 한다 — 사람이 본 적 없는 것이 재검증 우선순위 1번이기 때문이다
        (§6.8.4-a).

        **뽑기만 한다.** 다시 보는 것은 사람 몫이고, 배치가 대신 판정하면 "사람
        승인이 실질인가"를 기계가 답하는 셈이 되어 재려던 것이 사라진다.
        """
        if not self._cfg.operations_db.exists():
            return
        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            if not recheck_domain.due(conn, period_days=self._cfg.recheck_period_days):
                return
            taken = recheck_domain.select(conn, size=self._cfg.recheck_sample_size)
        finally:
            conn.close()
        if taken:
            unseen = sum(1 for s in taken if s.unseen)
            print(
                f"[worker] 재검증 표본 {len(taken)}건을 뽑았다 (/recheck)"
                + (
                    f" — 그중 {unseen}건은 **사람이 본 적이 없다** (§6.8.4-a)"
                    if unseen
                    else ""
                )
            )

    def _expire(self) -> None:
        """보존 기간이 지난 원문을 지운다 (WBS-4.8.3, FR-51, PO-4).

        **기본은 무제한이라 대개 아무 일도 하지 않는다.** 그것이 정책 부재가 아니라
        결정이다 — 사내에 정해진 보존 정책이 없어 두지 않기로 정했고, 정책이 생기면
        `ASD_RETENTION_DAYS` 에 값만 넣으면 된다.

        **주기의 뒤쪽이다.** 이번 주기의 수집·파이프라인·ingest 가 원문을 다 쓰고 난
        뒤에 지운다 — 앞에 두면 방금 들어온 후속이 붙을 건의 원문이 같은 주기에
        사라질 수 있다.
        """
        if self._cfg.retention_days is None:
            return
        if not self._cfg.operations_db.exists():
            return
        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            report = retention_domain.expire(
                conn, retention_days=self._cfg.retention_days
            )
        except retention_domain.InvalidRetention as exc:
            # **설정이 이상하면 지우지 않는다.** 되돌릴 수 없는 쪽으로 기울지 않는다.
            print(f"[worker] 보존 만료를 건너뛴다 — {exc}")
            return
        finally:
            conn.close()

        if report.changed:
            print(
                f"[worker] 보존 만료 — {report.summary}. "
                f"**지식 항목과 통계는 남는다** (FR-51)"
            )

    def _notify(self) -> None:
        """대시보드를 열지 않아도 알게 한다 (WBS-4.8.2, ADR-007 결정 2).

        **주기의 맨 뒤다.** 이번 주기가 만든 것까지 세고 나서 알려야 한다 — 앞에 두면
        방금 Lint 가 찾은 모순이 다음 주기에나 알려진다.

        **웹훅이 없으면 아무 일도 하지 않는다.** 그때는 배너가 같은 것을 말하고,
        배너는 웹훅이 있어도 함께 뜬다.
        """
        if not self._cfg.alert_webhook_url:
            return
        if not self._cfg.operations_db.exists():
            return
        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            waiting = alert_domain.unsent(
                conn,
                alert_domain.pending(
                    conn, neglect_hours=self._cfg.alert_neglect_hours
                ),
            )
            sent, failures = alert_domain.dispatch(
                conn, url=self._cfg.alert_webhook_url, alerts=waiting
            )
        finally:
            conn.close()

        for alert in sent:
            print(f"[worker] 알림 — {alert.title}")
        for failure in failures:
            # **보냈다고 적지 않았다.** 다음 주기가 다시 시도한다.
            print(f"[worker] 알림 실패 — {failure}")

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

        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            result = IngestRun(
                repo=repo,
                agent=IngestAgent(harness, on_retry=_note_ingest_retry),
                conn=conn,
                output_filter=output_filter,
                mirrors=self._mirrors(),
                # **여기를 잇지 않으면 중단 신호가 ingest 에 닿지 않는다.**
                # 최초 부트스트랩은 묶음이 수백이라 한 tick 이 반나절인데, 정지
                # 플래그를 바깥 루프에서만 보면 그 반나절이 다 지나야 신호를
                # 쳐다본다 — 2026-08-30 에 워커 다섯이 동시에 도는 것으로 드러났다.
                should_stop=lambda: self._stopping,
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
        mirrors = self._mirrors()
        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            report = Lint(
                repo=repo, conn=conn, mirror=MirrorSet(mirrors) if mirrors else None
            ).run()
        except (KnowledgeRepoError, RuntimeError) as exc:
            print(f"[worker] lint 실패: {exc}")
            return
        finally:
            conn.close()

        for note in _lint_notes(report):
            print(f"[worker] {note}")


    def _correct(self) -> None:
        """stale 을 게재물까지 밀어내고, 만들 수 있는 정정 초안을 만든다
        (WBS-4.5.7, FR-34·35).

        **Lint 다음에 돈다.** stale 표시를 붙이는 것이 Lint 이므로 순서가 뒤집히면
        오늘 낡은 것이 하루 늦게 드러난다 — 그동안 틀린 내용이 계속 노출된다 (§8.2).

        **초안까지 배치가 만든다.** 사람이 누르기를 기다리면 그 대기 자체가 노출
        시간이고, 초안을 만들어도 나가지는 않는다 — 게재 판정과 Q2 가 그대로 걸린다.
        무시를 누르면 소견이 닫혀 더 만들지 않으므로 비용도 거기서 멈춘다.
        """
        if not self._cfg.operations_db.exists():
            return
        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        if not repo.root.exists():
            return

        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            report = correction_domain.propagate(conn, repo)
            drafted = self._draft_corrections(conn, repo)
        except KnowledgeRepoError as exc:
            print(f"[worker] 정정 전파 실패: {exc}")
            return
        finally:
            conn.close()

        if report.opened:
            print(
                f"[worker] 정정 후보 {len(report.opened)}건을 Q5 로 올렸다 — "
                f"**틀린 내용이 지금 노출되고 있다** (§8.2)"
            )
        if drafted:
            print(f"[worker] 정정 초안 {drafted}건 — 게재 판정과 검수를 그대로 지난다")

    def _draft_corrections(self, conn, repo) -> int:  # noqa: ANN001
        """근거가 따라잡은 건만 다시 만든다.

        파이프라인이 없으면 아무것도 만들지 않는다 — 그때 Q5 는 그대로 남아 사람이
        본다. **소견이 닫히지 않는 것이 요점이다**: 못 만든 것과 고친 것은 다르다.
        """
        pipeline = self._answer_pipeline(conn)
        if pipeline is None:
            return 0
        count = 0
        for candidate in correction_domain.ready(conn, repo):
            if _has_pending_correction(conn, candidate.record_id):
                continue
            if correction_domain.draft_correction(
                conn, candidate, pipeline=pipeline, reviewer=self._reviewer()
            ):
                count += 1
        return count

    def _produce_content(self) -> None:
        """콘텐츠를 만든다 (WBS-4.6.2, FR-36·43).

        **ingest·Lint 다음에 돈다.** 낡음 표시를 붙이는 것이 Lint 이고, 이 단계가
        낡은 근거를 빼는 판단을 그 표시로 한다 — 순서가 뒤집히면 어제의 낡음으로
        오늘의 문서를 쓴다.

        **단계가 켜져야 돈다** (FR-59, D49). S4 미만에서 콘텐츠를 만들면 앞 단계
        대기열이 밀려 있는데 새 대기열을 여는 것이고, 그것이 §1.5.4 가 금지한 것이다.
        """
        if self._cfg.stage not in content_production.CONTENT_STAGES:
            return
        if not self._cfg.operations_db.exists():
            return
        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        if not repo.root.exists():
            return

        try:
            types = content_registry.load(self._cfg.content_types_path)
        except content_registry.InvalidDeclaration as exc:
            print(f"[worker] 콘텐츠 타입 선언이 잘못됐다 — {exc}")
            return

        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            producer = content_production.ContentProducer(
                conn=conn,
                repo=repo,
                harness=self._harness(),
                generated_by=self._cfg.llm_model,
            )
            commit = self._content_source_commit(conn)
            for ctype in types.all():
                try:
                    result = producer.run(ctype, source_commit=commit)
                except (
                    content_production.UnsupportedInput,
                    content_production.UnknownThreshold,
                ) as exc:
                    # **조용히 건너뛰지 않는다.** 침묵은 "만들 것이 없다"와
                    # 구분되지 않아, 선언은 있는데 아무것도 안 나오는 타입이 생긴다.
                    print(f"[worker] 콘텐츠 건너뜀 — {exc}")
                    continue
                except KnowledgeRepoError as exc:
                    print(f"[worker] 콘텐츠 제작 실패 ({ctype.id}): {exc}")
                    continue
                _report_content(ctype, result)
        finally:
            conn.close()

    def _inspect_content(self) -> None:
        """대기 중인 콘텐츠 초안에 의미 판정을 붙인다 (WBS-4.7.2, §7.6.4, FR-40·41).

        **제작 다음이고 게재 앞이다.** 앞이면 판정할 초안이 없고, 뒤면 사람이 소견
        없이 본 초안이 이미 나간 뒤다.

        **선언이 정한 타입만 본다** (FR-42). P6·P7 을 들지 않은 타입에 붙이면
        가이드의 사용 설명이 전부 걸려 소견이 소음이 된다 — 그 타입의 초안은
        `agent_findings` 가 `NULL` 로 남고, 화면은 그것을 "아직 안 봤다"가 아니라
        "의미 판정 대상이 아니다"로 읽는다(`needs_semantic`).
        """
        if self._cfg.stage not in content_production.CONTENT_STAGES:
            return
        if not self._cfg.operations_db.exists():
            return
        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        if not repo.root.exists():
            return
        harness = self._harness()
        if harness is None:
            # **조용히 통과시키지 않는다.** 소견 없이 두면 화면이 "아직 안 봤다"고
            # 말하고, 그것이 사실이다 (§5.6.1).
            return

        try:
            types = content_registry.load(self._cfg.content_types_path)
        except content_registry.InvalidDeclaration as exc:
            print(f"[worker] 콘텐츠 타입 선언이 잘못됐다 — {exc}")
            return

        conn = connect(self._cfg.operations_db)
        initialize(conn)
        try:
            for draft in content_store.awaiting_inspection(conn):
                try:
                    ctype = types.get(draft.type_id)
                except content_registry.InvalidDeclaration:
                    continue
                if not content_review.needs_semantic(ctype):
                    continue
                findings = content_review.inspect_semantically(
                    conn,
                    ctype,
                    draft,
                    harness=harness,
                    source_text=_source_text_of(repo, draft),
                )
                if findings is None:
                    print(
                        f"[worker] {ctype.title} 의미 판정을 못 했다 — 다음 주기에 "
                        "다시 본다. **박지 않는다**: 빈 소견으로 박으면 돌지 않은 "
                        "판정이 통과한 것처럼 보인다"
                    )
                    continue
                print(
                    f"[worker] {ctype.title} 의미 판정 — 소견 {len(findings)}건. "
                    "**소견이 없다고 통과가 아니다** (FR-39)"
                )
        finally:
            conn.close()

    def _publish_content(self) -> None:
        """승인됐는데 아직 나가지 않은 콘텐츠를 내보낸다 (WBS-4.6.3, XR-6).

        **승인과 게재는 다른 행위다.** 승인 시점에 모 시스템이 닿지 않았을 수 있고,
        그때 승인은 남되 게재는 남지 않는다 — 여기가 그 차이를 메운다.

        **문서 면만 스스로 다시 시도한다** (D46). upsert 는 멱등해서 결과를 몰라도
        다시 보내면 되지만, 발행 면은 다시 보내면 **회차가 둘 생기고 우리는 그것을
        지울 수 없다.** 발행물은 최종 확인도 남아 있어(§5.5.5) 배치가 대신 누르지
        않는다 — 둘 다 사람의 자리다.
        """
        if self._cfg.stage not in content_production.CONTENT_STAGES:
            return
        if not self._cfg.operations_db.exists():
            return
        try:
            types = content_registry.load(self._cfg.content_types_path)
            parent = build_parent_system(self._cfg)
        except (content_registry.InvalidDeclaration, NotConfigured) as exc:
            print(f"[worker] 콘텐츠 게재를 건너뛴다 — {exc}")
            return

        conn = connect(self._cfg.operations_db)
        initialize(conn)
        repo = KnowledgeRepository(self._cfg.knowledge_dir)
        try:
            for draft in content_publication.retriable(conn, types):
                ctype = types.get(draft.type_id)
                try:
                    record = content_publication.publish(
                        conn, parent, ctype, draft, repo=repo
                    )
                except (content_publication.NotApproved, RuntimeError) as exc:
                    print(f"[worker] 콘텐츠 게재 실패 ({ctype.id}): {exc}")
                    continue
                print(
                    f"[worker] {ctype.title} 을 문서 면에 올렸다 — {record.parent_ref}"
                )
        finally:
            conn.close()

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


def _has_pending_correction(conn, record_id: str) -> bool:  # noqa: ANN001
    """이미 정정 초안이 대기 중인가.

    없으면 사람이 Q2 를 처리할 때까지 **매 주기 같은 정정 초안이 쌓인다** — 후속
    재실행(WBS-4.5.4)이 같은 이유로 막아 둔 것과 같은 함정이다.
    """
    return (
        conn.execute(
            "SELECT 1 FROM answer_draft WHERE corrects = ? AND state = ? LIMIT 1",
            (record_id, draft_store.PENDING),
        ).fetchone()
        is not None
    )


def _source_text_of(repo, draft) -> dict[str, str]:  # noqa: ANN001
    """초안이 가리키는 지식 항목의 원문. **없으면 없는 대로** — 화면과 같은 규칙이다."""
    wanted = set(draft.grounding)
    if not wanted:
        return {}
    return {
        s.item.id: f"{s.item.title}\n\n{s.item.body}"
        for s in repo.scan()[0]
        if s.item.id in wanted
    }


def _report_content(ctype, result) -> None:  # noqa: ANN001
    """무슨 일이 있었는지 말한다. **아무것도 안 만든 주기도 말한다** — 조용하면
    돌지 않은 것과 구분되지 않는다."""
    from agentic_service_desk.content.store import Outcome

    if result.outcome is Outcome.NOT_DUE:
        return
    if result.outcome is Outcome.PRODUCED:
        print(
            f"[worker] {ctype.title} 초안을 Q3 에 올렸다 — {result.detail}. "
            "**콘텐츠는 국면과 무관하게 전수 사람 승인이다** (FR-39)"
        )
    else:
        print(f"[worker] {ctype.title} — {result.outcome}: {result.detail}")
    _report_knowledge_gaps(ctype, result)


def _report_knowledge_gaps(ctype, result) -> None:  # noqa: ANN001
    """반복되는데 지식베이스가 답을 모르는 질문 (WBS-4.7.1, §6.2).

    **초안을 만들었을 때도 말한다.** FAQ 가 나갔다는 사실은 빠진 문항을 가리지
    못한다 — 자주 묻는데 답이 없는 자리가 곧 ingest 우선순위이고, 그것을 아는 유일한
    시점이 여기다.
    """
    if not result.uncovered:
        return
    print(
        f"[worker] {ctype.title} — 반복 질문 {len(result.uncovered)}건은 지식베이스가 "
        "답을 몰라 뺐다. **지식 공백이다** — 지어내지 않고 ingest 우선순위로 되먹인다:"
    )
    for question in result.uncovered[:5]:
        print(f"[worker]   · {question[:60]}")


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
    if result.stopped:
        # **맨 앞줄이어야 할 만큼 중요하지만 맨 뒤가 아니면 된다.** 이것이 없으면
        # 중단된 실행과 완주한 실행이 화면에서 같아 보이고, 사람이 "부트스트랩이
        # 끝났다"고 읽고 Lint 결과로 완주를 판정한다.
        notes.append(
            "ingest 를 **다 읽지 못하고 멈췄다** (중단 신호) — 읽다 만 저장소의 "
            "커서는 그대로 두었다. 다시 띄우면 그 구간부터 이어서 읽는다"
        )
    if result.held_for_human:
        notes.append(
            f"사람이 고친 항목 {len(result.held_for_human)}건은 덮어쓰지 않았다 "
            f"— 모순 {result.contradictions_opened}건을 Q4 로 올렸다 (FR-6)"
        )
    if result.dropped_config_paths:
        notes.append(f"설정 파일 {len(result.dropped_config_paths)}개를 원천에서 뺐다 (FR-9)")
    for dropped in result.dropped_dead_refs:
        # 무엇이 왜 떨어졌는지 보여야 경계가 잘못 잡혔을 때 알아챈다. 특히 이쪽은
        # **조용히 대비값으로 바뀌는** 자리라, 안 보이면 모든 항목이 주기형이 돼도
        # 그것이 정상인 줄 안다.
        notes.append(f"무효화 조건에서 죽은 경로를 뺐다 (FR-8) — {dropped}")
    for dropped in result.dropped_config_values:
        # **한 줄씩 다 보인다.** 건수만 세면 경계가 잘못 잡혀 멀쩡한 항목이
        # 막히고 있어도 숫자가 하나 늘 뿐이라 아무도 알아채지 못한다.
        notes.append(f"설정값을 옮겨 적어 받지 않았다 (FR-9) — {dropped}")
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


def _note_ingest_retry(attempt: int, exc: Exception) -> None:
    """형식을 어긴 출력을 다시 부르기 직전에 남긴다.

    **조용히 다시 부르면 재시도가 무엇을 덮고 있는지 안 보인다.** 실패율이
    올라가는 것은 성공 건수만 봐서는 드러나지 않고, 이 줄이 늘어나는 것으로
    먼저 나타난다.
    """
    print(f"[worker] ingest 출력이 형식을 어겼다 — 다시 부른다 ({attempt}회차): {exc}")
