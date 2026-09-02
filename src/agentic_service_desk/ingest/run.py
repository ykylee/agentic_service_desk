"""한 번의 ingest — 원천을 읽고 지식을 바꾸고 **한 커밋으로 남긴다** (FR-5).

llm-wiki 의 운영 모델을 그대로 따른다 (§4).

    1. 원천 읽기 — 변경된 소스 범위와 그 커밋 메시지, 또는 신규 QnA 묶음
    2. 대상 항목 식별 — 새로 만들 것과 갱신할 것
    3. 다중 항목 갱신
    4. 로그 적재 — `log.md` 에 **런마다 한 줄**, 커밋은 **묶음마다** (FR-5)

순서에 걸린 것이 하나 있다. **진행 표시는 커밋이 끝난 뒤에 옮긴다.** 먼저 옮기면
중단됐을 때 그 구간을 건너뛰고, 그러면 지식에 구멍이 생기는데 아무도 알아채지
못한다. 반대 순서의 대가는 같은 원천을 한 번 더 읽는 것뿐이고, 갱신은 기존 항목의
`id` 로 이뤄지므로 중복이 생기지 않는다.
"""

from __future__ import annotations

import fnmatch
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from agentic_service_desk.ingest.agent import (
    AgentOutputError,
    IngestAgent,
    ProposedItem,
    QnaMaterial,
    SourceMaterial,
    prepare_source_material,
    qna_provenance,
    source_provenance,
    to_knowledge_item,
)
from agentic_service_desk.ingest.config_values import (
    declared_values,
    leaked_pairs,
    merge_declared,
)
from agentic_service_desk.ingest.output_filter import OutputFilter
from agentic_service_desk.ingest.source import SourceMirror
from agentic_service_desk.knowledge import contradiction
from agentic_service_desk.knowledge.repository import KnowledgeRepository, StoredItem
from agentic_service_desk.operations.checkpoint import get_cursor, set_cursor, source_key

MAX_CHARS_PER_CALL = 24_000
"""한 번의 에이전트 호출에 넣을 원천 크기. 넘으면 묶음을 나눈다.

파일 하나가 이보다 크면 **그 파일만 담은 묶음**이 된다 — 잘라 넣지 않는다.
반쪽짜리 코드에서 뽑은 개념은 틀리기 쉽고, 틀렸다는 것이 드러나지 않는다.

**줄이려다 실측을 보고 그만뒀다** (2026-08-31). 부트스트랩의 잘린 JSON 을 보고
묶음이 너무 커서라고 봤는데, 실제 원천으로 재 보니 24,000 · 12,000 · 6,000자가
각각 56 · 48 · 52초로 **거의 같았고 셋 다 완결된 JSON 이 왔다.** 소요는 입력이
아니라 생성량이 지배한다. 여기서 줄이면 호출당 시간은 그대로인 채 **호출 수만
늘어 부트스트랩이 느려진다** — 잘림의 원인은 다른 데 있다.
"""

MAX_COMMIT_MESSAGES = 50
"""한 묶음에 실을 커밋 메시지 수. 넘친 것은 **보고에 남긴다.**

메시지는 "왜 그렇게 정했는가"의 1차 출처이므로(§2.2.1) 잘리는 것이 손실이다.
증분 실행에서는 이 수에 닿지 않는다 — 닿는 것은 최초 부트스트랩뿐이다.
"""


@dataclass
class IngestResult:
    """한 번의 ingest 가 무엇을 했는가."""

    created: int = 0
    updated: int = 0
    commit: str | None = None
    """지식 저장소의 커밋 해시. 바뀐 것이 없으면 `None`."""

    held_for_human: list[str] = field(default_factory=list)
    """사람이 고친 항목이라 덮어쓰지 않고 **모순으로 올린 것** (FR-6, D38).

    덮어쓰지 않는 것과 없던 일로 하는 것은 다르다 — 에이전트의 판단은
    `contradiction` 에 남아 Q4 대기열로 간다.
    """

    contradictions_opened: int = 0
    """이번에 새로 연 모순. 같은 항목에 이미 열려 있으면 세지 않는다."""

    dropped_config_paths: list[str] = field(default_factory=list)
    dropped_config_values: list[str] = field(default_factory=list)
    """설정값을 옮겨 적어 받지 않은 제안 (FR-9, §2.2.2).

    경로 필터를 지나온 것들이다 — 값이 **코드 상수나 커밋 메시지**에 실려 있었다.
    무엇이 왜 걸렸는지 그대로 남긴다: 배제가 조용하면 경계가 잘못 잡혔을 때
    아무도 알아채지 못한다.
    """

    dropped_dead_refs: list[str] = field(default_factory=list)
    """무효화 조건에서 떨어낸 **나타날 수 없는 경로** (FR-8).

    모델이 낸 경로가 옮겨졌거나 다른 저장소 것이거나 문서의 자리표시자면, 그것을
    조건으로 두는 순간 **그 항목은 절대 stale 이 되지 않는다** — 조건이 붙어 있어
    살아 보이는데 아무것도 가리키지 못한다. 만들지 않는 것이 고치는 것보다 싸다.
    """

    broken_items: list[str] = field(default_factory=list)

    excluded_paths: list[str] = field(default_factory=list)
    """선언된 패턴으로 원천에서 뺀 경로 (`ASD_SOURCE_EXCLUDE`).

    **건수만 세지 않는다.** 이 배제는 코드가 아니라 사람이 정한 것이라, 패턴이
    너무 넓으면 **모 시스템의 진짜 코드가 조용히 빠진다** — 그때 지식베이스에는
    "없다"는 사실조차 남지 않는다.
    """

    unreadable_paths: list[str] = field(default_factory=list)
    """글자로 읽히지 않아 원천에서 뺀 경로 (zip·이미지 등).

    건수만 세지 않고 경로를 남긴다 — **여기 텍스트 파일이 섞여 들어오면 인코딩
    처리가 틀린 것**이고, 숫자만으로는 그것이 드러나지 않는다.
    """

    omitted_messages: int = 0
    failures: list[str] = field(default_factory=list)
    """에이전트 호출이 실패한 묶음. 하나가 터져도 나머지는 간다."""

    stopped: bool = False
    """중단 신호를 받고 **다 읽지 못한 채** 나왔는가.

    실패와 다르다 — 터진 것이 아니라 그만두라는 말을 들은 것이다. 그런데 커서
    처리는 같다: **읽다 만 저장소의 커서는 옮기지 않는다.** 옮기면 남은 구간을
    영영 건너뛰고 지식에 구멍이 생기는데 아무도 알아채지 못한다.

    보고에 반드시 실린다. 조용히 나가면 **다 읽은 것과 구분되지 않아**, 사람이
    "부트스트랩이 끝났다"고 읽고 Lint 결과로 완주를 판정하게 된다.
    """

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated)

    def summary(self) -> str:
        return f"신규 {self.created} · 갱신 {self.updated}"


class IngestRun:
    """원천 → 지식.

    **커밋은 묶음마다 남긴다** (WBS-5.6.1). 예전에는 런 끝에 한 번이었는데, 그러면
    긴 런 도중 **디스크와 커밋이 계속 어긋난다** — 질의는 작업 트리를 읽어 새 항목을
    바로 보지만 게재는 근거를 *커밋* 에 고정하므로(`pin()`), 그 사이에는 답변이
    하나도 나가지 못한다. 2026-09-03 실측에서 그 창이 **4.96시간**이었다.

    묶음마다 커밋하면 어긋남이 최대 한 묶음(중앙값 약 2분)이 된다. 대가는 이력이
    길어지는 것인데, **근거 버전 고정의 관점에서는 오히려 정밀해진다** — 답변이
    더 좁은 시점에 묶인다.

    로그(`log.md`)는 여전히 런 단위다 — "1 ingest = 1 항목"(llm-wiki)은 *무엇을 한
    런인가*의 기록이지 커밋의 단위가 아니다.
    """

    def __init__(
        self,
        *,
        repo: KnowledgeRepository,
        agent: IngestAgent,
        conn: sqlite3.Connection,
        output_filter: OutputFilter,
        mirrors: Sequence[SourceMirror] = (),
        max_chars: int = MAX_CHARS_PER_CALL,
        should_stop: Callable[[], bool] | None = None,
        on_chunk: Callable[[str, int, int], None] | None = None,
        exclude: Sequence[str] = (),
    ) -> None:
        self._repo = repo
        self._agent = agent
        self._conn = conn
        self._filter = output_filter
        self._should_stop = should_stop or (lambda: False)
        self._mirrors = list(mirrors)
        self._max_chars = max_chars
        self._on_chunk = on_chunk or (lambda *_: None)
        self._exclude = tuple(exclude)

    def run(self) -> IngestResult:
        self._repo.ensure_initialized()
        result = IngestResult()

        stored, result.broken_items = self._repo.scan()
        by_id = {s.item.id: s for s in stored}
        index = [(s.item.id, s.item.title) for s in stored]

        heads = self._ingest_source(result, by_id, index)
        answer_ids = self._ingest_qna(result, by_id, index)

        if result.changed:
            self._repo.append_log(
                f"{result.summary()} — {self._origin_note(heads, answer_ids)}"
            )
            result.commit = self._repo.commit(f"ingest: {result.summary()}")

        # **여기서부터가 진행 표시다.** 커밋이 끝난 뒤에만 옮긴다.
        #
        # 지식이 하나도 안 바뀌었어도 옮긴다. **읽은 것과 지식이 된 것은 다르다** —
        # 원천에 뽑을 개념이 없다는 판단도 처리 결과이고, 그것을 처리로 세지 않으면
        # 같은 원천을 매 주기 LLM 에 다시 태운다. 실제로 그런 원천이 있다
        # (내용 없는 봇 답변 등).
        #
        # 옮기지 않는 경우는 **실패했을 때뿐이다.** `heads` 에는 무사히 끝난
        # 저장소만 담기고, 답변 id 는 그 호출이 성공했을 때만 담긴다.
        #
        # **저장소마다 따로 옮긴다.** 하나가 실패했다고 다른 저장소까지 되감으면
        # 멀쩡히 읽은 구간을 다음 주기에 통째로 다시 태우게 된다.
        for repo_url, head in heads.items():
            set_cursor(self._conn, source_key(repo_url), head)
        self._mark_ingested(answer_ids, result.commit)
        return result

    # --- 소스 원천 -------------------------------------------------------

    def _ingest_source(
        self,
        result: IngestResult,
        by_id: dict[str, StoredItem],
        index: list[tuple[str, str]],
    ) -> dict[str, str]:
        """붙은 저장소들의 변경분을 읽는다.

        돌려주는 것은 **무사히 읽은 저장소의 `주소 → HEAD`** 다. 저장소 하나가
        실패하면 그 저장소만 빠진다 — 커서가 그 자리에 남아 다음 주기가 같은
        구간을 다시 읽는다. 옮겨 버리면 **그 구간의 개념이 영영 지식이 되지
        않는데 아무도 알아채지 못한다.**

        **지식베이스는 하나다.** `by_id` 와 `index` 를 저장소마다 새로 만들지 않고
        그대로 넘기는 것이 그 뜻이다 — 두 저장소가 같은 개념을 말하면 뒤엣것이
        앞엣것을 *갱신*해야지 항목을 하나 더 만들어서는 안 된다.
        """
        heads: dict[str, str] = {}
        for mirror in self._mirrors:
            if self._should_stop():
                # **아직 손대지 않은 저장소다.** 커서가 없으니 다음 주기가 처음부터
                # 읽고, 그것이 맞다.
                result.stopped = True
                break
            head = self._ingest_one_source(mirror, result, by_id, index)
            if head:
                heads[mirror.repo_url] = head
        return heads

    def _ingest_one_source(
        self,
        mirror: SourceMirror,
        result: IngestResult,
        by_id: dict[str, StoredItem],
        index: list[tuple[str, str]],
    ) -> str | None:
        """저장소 하나. 무사히 끝났을 때만 HEAD 를 돌려준다."""
        if not mirror.is_cloned:
            return None
        failures_before = len(result.failures)

        cursor = get_cursor(self._conn, source_key(mirror.repo_url))
        head = mirror.head()
        if cursor == head:
            return None

        changed = mirror.changed_paths_since(cursor)
        if self._exclude:
            # **읽기 전에 뺀다.** 읽고 나서 버리면 큰 저장소에서 그 값을 그대로
            # 치른다 — 배제의 요점이 시간과 오염 둘 다이므로 앞자리가 맞다.
            kept = [p for p in changed if not _excluded(p, self._exclude)]
            result.excluded_paths.extend(p for p in changed if p not in set(kept))
            changed = kept
        commits = mirror.commits_since(cursor)
        messages = [c.full_message for c in commits]
        if len(messages) > MAX_COMMIT_MESSAGES:
            result.omitted_messages += len(messages) - MAX_COMMIT_MESSAGES
            messages = messages[:MAX_COMMIT_MESSAGES]

        files: list[tuple[str, str]] = []
        for path in changed:
            try:
                files.append((path, mirror.read_file(path, at=head)))
            except RuntimeError:
                # 삭제된 경로다. 커밋 메시지에는 남아 있으므로 "왜 지웠는가"는 살아 있다.
                continue
            except UnicodeDecodeError:
                # **글자가 아닌 파일이다** (zip·이미지·바이너리). 개념을 뽑을
                # 것이 없으므로 건너뛴다.
                #
                # 따로 잡는 이유는 계보 때문이다 — `UnicodeDecodeError` 는
                # `ValueError` 라 위의 `RuntimeError` 에 걸리지 않고, `run()` 과
                # 워커 tick 의 `except (HarnessError, KnowledgeRepoError,
                # RuntimeError)` 도 지나쳐 **워커 프로세스를 죽인다.**
                # 죽는 자리가 커밋 앞이라 그 런의 수집분이 통째로 사라지고,
                # 커서는 그대로라 다시 띄워도 같은 자리에서 또 죽는다 —
                # 부트스트랩이 영영 완주하지 못한다. 2026-08-31 실측:
                # `standard_ai_workflow` 의 `tests/devhub_temp_source/*.zip`
                # 하나가 이 경로를 밟는다.
                result.unreadable_paths.append(path)
                continue

        material, dropped = prepare_source_material(head, messages, files)
        result.dropped_config_paths.extend(dropped)
        if not material.files and not material.messages:
            return head
        # 읽을 것이 남아 있으면 아래에서 묶음마다 돈다.

        live: dict[str, bool] = {}
        # **묶음 수를 미리 안다.** 최초 부트스트랩은 한 런이 하루를 넘기는데,
        # 진행을 남기지 않으면 "얼마나 남았는가"에 답할 방법이 항목 수를 세는
        # 것뿐이다 — 개념이 없는 묶음은 항목을 내지 않으므로 그것은 진행이
        # 아니다. 남은 시간을 알아야 기다릴지 끊을지 정할 수 있다.
        chunks = _chunks(material, self._max_chars)
        for done, chunk in enumerate(chunks, start=1):
            if self._should_stop():
                # **묶음 경계에서 나간다.** 여기가 나갈 수 있는 유일한 자리다 —
                # 한 묶음 안에서 끊기면 항목이 반만 쓰인 채 남는다.
                #
                # **`None` 을 돌려주는 것이 요점이다.** 여기까지 읽은 것을 근거로
                # 커서를 옮기면 나머지 구간이 영영 건너뛰어지고, 그 구간의 개념이
                # 지식이 되지 않는데 아무도 알아채지 못한다. 다시 읽는 값은
                # 치르지만 구멍은 만들지 않는다.
                result.stopped = True
                return None
            self._on_chunk(mirror.repo_url, done, len(chunks))
            before = (result.created, result.updated)
            try:
                proposals = self._agent.from_source(chunk, index)
            except (AgentOutputError, RuntimeError) as exc:
                result.failures.append(f"소스 {chunk.commit[:8]}: {exc}")
                continue
            declared = _declared_in(chunk)
            for proposal in proposals:
                leaks = leaked_pairs(proposal.body, declared)
                if leaks:
                    # **설정값은 지식이 아니라 현재 상태다** (FR-9, §2.2.2). 경로
                    # 필터를 지나온 값이라 여기가 마지막 자리다.
                    result.dropped_config_values.append(
                        f"{proposal.title} — {', '.join(leaks)}"
                    )
                    continue
                self._apply(
                    self._with_live_refs(proposal, mirror, result, live),
                    source_provenance(proposal, chunk),
                    by_id,
                    index,
                    result,
                )
            # **묶음 경계가 커밋 경계다.** 여기서 남기지 않으면 다음 묶음이 도는
            # 동안 방금 지은 항목이 디스크에만 있고, 그 상태로는 게재가 근거를
            # 고정할 수 없다 (`pin()` 은 커밋에 실재하는 것만 받는다).
            self._commit_chunk(
                mirror, done, len(chunks),
                result.created - before[0], result.updated - before[1],
            )
        return None if len(result.failures) > failures_before else head

    def _commit_chunk(
        self, mirror: SourceMirror, done: int, total: int, created: int, updated: int
    ) -> None:
        """묶음 하나의 결과를 커밋한다 (WBS-5.6.1).

        **진행을 메시지에 적는다.** 묶음마다 커밋하면 중간에 죽은 런의 결과도
        이력에 남는데, `43/116` 이 있으면 마지막 ingest 커밋만 보고 완주 여부를
        가릴 수 있다. 없으면 "다 읽은 것"과 "읽다 만 것"이 이력에서 같아 보인다.

        아무것도 바뀌지 않았으면 `commit()` 이 알아서 지나간다 — 빈 커밋을 만들지
        않는 규칙은 그대로다.
        """
        if not (created or updated):
            return
        name = Path(mirror.repo_url.rstrip("/")).name
        self._repo.commit(f"ingest: {name} {done}/{total} — 신규 {created} · 갱신 {updated}")

    def _with_live_refs(
        self,
        proposal: ProposedItem,
        mirror: SourceMirror,
        result: IngestResult,
        live: dict[str, bool],
    ) -> ProposedItem:
        """무효화 조건에 **나타날 수 없는 경로를 남기지 않는다** (FR-8).

        모델은 경로를 곧잘 어긋나게 짚는다 — 옮겨진 자리, 틀린 디렉터리 층, 다른
        저장소의 경로, 문서에서 베낀 `<branch>` 자리표시자. 2026-08-30 실저장소
        수집에서 ref 넷 중 하나가 그랬다.

        그것을 조건으로 두면 `refs & 바뀐_경로` 가 영원히 비어 **그 항목은 절대
        stale 이 되지 않는다.** 조건이 붙어 있어 살아 보이는 만큼 없는 것보다 나쁘다.

        **떨어내기만 하고 지어내지 않는다.** 남는 것이 없으면 `_invalidation_for`
        가 이미 가진 대비값으로 간다 — 근거로 쓴 경로에 묶고, 그것도 없으면
        주기형이다. 설계가 예비해 둔 자리가 여기서 쓰인다.

        **`used_paths` 도 함께 거른다.** 그 대비값이 쓰는 것이 이 목록이라, 여기를
        거르지 않으면 지어낸 경로가 대비값을 통해 그대로 조건이 된다 — 출처 쪽
        여과(`source_provenance`)는 이 항목에 닿지 않는다.

        Lint 의 같은 검사는 **없어지지 않는다.** 여기서 막는 것은 새로 만드는 것이고,
        Lint 가 잡는 것은 **저장소가 바뀌어 나중에 죽은 것**이다 — 만들 때 살아 있던
        경로가 강제 푸시나 이력 재작성으로 사라질 수 있다.
        """

        def alive(path: str) -> bool:
            if path not in live:
                live[path] = mirror.can_appear_in_diff(path)
            return live[path]

        refs = tuple(p for p in proposal.refs if alive(p))
        used = tuple(p for p in proposal.used_paths if alive(p))
        dropped = [p for p in (*proposal.refs, *proposal.used_paths) if not alive(p)]
        if not dropped:
            return proposal
        result.dropped_dead_refs.append(
            f"{proposal.title} — {', '.join(sorted(set(dropped)))}"
        )
        return replace(proposal, refs=refs, used_paths=used)

    # --- QnA 원천 --------------------------------------------------------

    def _ingest_qna(
        self,
        result: IngestResult,
        by_id: dict[str, StoredItem],
        index: list[tuple[str, str]],
    ) -> list[str]:
        """QnA 를 읽는다. **산출물 필터를 지난 것만 온다** (NFR-4)."""
        done = {
            row["answer_id"]
            for row in self._conn.execute("SELECT answer_id FROM ingested_answer")
        }
        ingested: list[str] = []
        for answer in self._filter.ingestible_answers(self._conn):
            if self._should_stop():
                # 여기서 나가도 **읽은 답변의 표시는 남는다** — 소스 커서와 달리
                # 답변은 하나씩 표시되므로 건너뛸 구간이라는 것이 없다.
                result.stopped = True
                break
            if answer.id in done:
                continue
            material = QnaMaterial(
                answer_id=answer.id,
                question_id=answer.question_id,
                question=answer.question_body,
                answer=answer.body,
            )
            try:
                proposals = self._agent.from_qna(material, index)
            except (AgentOutputError, RuntimeError) as exc:
                result.failures.append(f"QnA {answer.question_id}: {exc}")
                continue
            for proposal in proposals:
                self._apply(proposal, qna_provenance(material), by_id, index, result)
            # 제안이 없었어도 읽은 것은 읽은 것이다. 다시 읽어도 같은 결과이므로
            # 표시해 두지 않으면 매 주기 같은 답변에 LLM 을 태우게 된다.
            ingested.append(answer.id)
        return ingested

    # --- 적용 ------------------------------------------------------------

    def _apply(
        self,
        proposal: ProposedItem,
        provenance: list,
        by_id: dict[str, StoredItem],
        index: list[tuple[str, str]],
        result: IngestResult,
    ) -> None:
        """제안 하나를 반영한다. **사람이 고친 항목은 건드리지 않는다** (D38).

        덮어쓰는 대신 **양쪽을 남긴다** (FR-6) — 사람 쪽은 파일에 그대로 두고,
        에이전트 쪽은 모순 기록에 넣어 Q4 로 올린다. 판정은 사람이 다시 한다.
        """
        base = by_id.get(proposal.item_id) if proposal.item_id else None
        if base and base.item.edited_by_human:
            result.held_for_human.append(base.item.id)
            before = contradiction.open_for(self._conn, base.item.id)
            contradiction.record(
                self._conn,
                knowledge_item_id=base.item.id,
                proposed_title=proposal.title,
                proposed_body=proposal.body,
                provenance=provenance,
            )
            if before is None:
                result.contradictions_opened += 1
            return

        item = to_knowledge_item(
            proposal, provenance=provenance, base=base.item if base else None
        )
        path = self._repo.save(item, at=base.path if base else None)
        by_id[item.id] = StoredItem(item=item, path=path)
        if base:
            result.updated += 1
        else:
            # 같은 실행의 다음 묶음이 방금 만든 항목을 갱신 대상으로 볼 수 있게 한다 —
            # 없으면 한 번의 ingest 안에서 같은 개념이 여러 항목으로 갈린다.
            index.append((item.id, item.title))
            result.created += 1

    # --- 진행 표시 -------------------------------------------------------

    def _mark_ingested(self, answer_ids: list[str], commit: str | None) -> None:
        if not answer_ids:
            return
        now = datetime.now(UTC).isoformat()
        self._conn.executemany(
            "INSERT INTO ingested_answer (answer_id, knowledge_commit, ingested_at) "
            "VALUES (?, ?, ?) ON CONFLICT(answer_id) DO NOTHING",
            [(aid, commit, now) for aid in answer_ids],
        )
        self._conn.commit()

    @staticmethod
    def _origin_note(heads: dict[str, str], answer_ids: list[str]) -> str:
        parts = [f"소스 {head[:8]}" for head in heads.values()]
        if answer_ids:
            parts.append(f"QnA 답변 {len(answer_ids)}건")
        return ", ".join(parts) or "원천 없음"


def _excluded(path: str, patterns: Sequence[str]) -> bool:
    """이 경로가 선언된 배제 패턴에 걸리는가.

    디렉터리를 가리키는 뜻으로 `ai-workflow/*` 라고 쓰는 것이 자연스러운데
    `fnmatch` 의 `*` 는 `/` 도 먹으므로 그대로 깊은 경로까지 걸린다. 그 편이
    사람이 쓴 뜻에 가깝다 — 한 칸만 빼고 싶은 경우는 드물다.
    """
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _declared_in(chunk: SourceMaterial) -> dict[str, set[str]]:
    """이 묶음의 원천이 선언한 설정값 (FR-9).

    **커밋 메시지도 본다.** 값이 코드에서만 오는 것이 아니다 — "한도를 5_000 으로
    올렸다"는 메시지가 그대로 지식 본문이 되는 길이 실저장소에서 관측됐다.
    """
    declared: dict[str, set[str]] = {}
    for _, content in chunk.files:
        merge_declared(declared_values(content), declared)
    for message in chunk.messages:
        merge_declared(declared_values(message), declared)
    return declared


def _chunks(material: SourceMaterial, max_chars: int) -> list[SourceMaterial]:
    """원천을 프롬프트에 들어갈 크기로 나눈다.

    **파일을 쪼개지 않는다.** 파일 하나가 한도를 넘으면 그 파일만 담은 묶음이 된다 —
    반쪽짜리 코드에서 뽑은 개념은 틀리기 쉬운데, 틀렸다는 것이 드러나지 않는다.

    **커밋 메시지도 나눠 싣는다.** 예전에는 같은 메시지 묶음이 모든 묶음에 통째로
    실렸다. 2026-09-02 실측: 묶음 하나의 프롬프트 74,694자 중 메시지가 40,410자
    (54%)로 **원천 파일(20,170자)의 두 배**였고, 116묶음이면 같은 것을 4.6M자
    다시 보내는 셈이었다.

    메시지는 **맥락이 아니라 원천이다** (D16, §2.2.1) — "왜 그렇게 정했는가"의
    1차 출처라 그 자체가 개념이 된다. 원천이라면 **한 번 읽히면 족하다.** 파일을
    나눠 싣듯 메시지도 나눠 싣는 것이 같은 규칙의 적용이고, 그래야 전부가 꼭
    한 번씩 읽힌다.

    대가는 **짝이 어긋난다**는 것이다 — 어떤 묶음은 A 영역 코드와 B 영역 메시지를
    함께 보게 된다. 다만 메시지는 저장소 전체를 걸치고 묶음은 파일 일부라, 통째로
    실을 때에도 대부분은 이미 어긋나 있었다.
    """
    if not material.files:
        return [material]

    out: list[SourceMaterial] = []
    current: list[tuple[str, str]] = []
    size = 0
    for path, content in material.files:
        cost = len(path) + len(content)
        if current and size + cost > max_chars:
            out.append(_with_files(material, current))
            current, size = [], 0
        current.append((path, content))
        size += cost
    if current:
        out.append(_with_files(material, current))
    return _spread_messages(out, material.messages)


def _spread_messages(
    chunks: list[SourceMaterial], messages: tuple[str, ...]
) -> list[SourceMaterial]:
    """메시지를 묶음들에 고르게 나눈다. **하나도 빠뜨리지 않는다.**

    묶음보다 메시지가 적으면 앞쪽 묶음만 받는다 — 나머지는 코드만으로 읽힌다.
    반대로 많으면 나눠 담기며, 나누어떨어지지 않는 나머지는 앞쪽부터 하나씩 더
    받는다. **버리는 자리를 만들지 않는 것이 요점이다**: 메시지 하나가 사라지면
    그 결정의 "왜"가 지식이 될 기회를 잃는데, 그것은 조용하다.
    """
    if not messages or not chunks:
        return chunks
    n = len(chunks)
    out: list[SourceMaterial] = []
    for i, chunk in enumerate(chunks):
        share = messages[i::n]
        out.append(
            SourceMaterial(commit=chunk.commit, messages=share, files=chunk.files)
        )
    return out


def _with_files(material: SourceMaterial, files: list[tuple[str, str]]) -> SourceMaterial:
    return SourceMaterial(
        commit=material.commit, messages=material.messages, files=tuple(files)
    )
