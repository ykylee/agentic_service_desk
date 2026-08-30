"""한 번의 ingest — 원천을 읽고 지식을 바꾸고 **한 커밋으로 남긴다** (FR-5).

llm-wiki 의 운영 모델을 그대로 따른다 (§4).

    1. 원천 읽기 — 변경된 소스 범위와 그 커밋 메시지, 또는 신규 QnA 묶음
    2. 대상 항목 식별 — 새로 만들 것과 갱신할 것
    3. 다중 항목 갱신
    4. 로그 적재 — `log.md` 에 한 줄, **1 회 ingest = 1 커밋**

순서에 걸린 것이 하나 있다. **진행 표시는 커밋이 끝난 뒤에 옮긴다.** 먼저 옮기면
중단됐을 때 그 구간을 건너뛰고, 그러면 지식에 구멍이 생기는데 아무도 알아채지
못한다. 반대 순서의 대가는 같은 원천을 한 번 더 읽는 것뿐이고, 갱신은 기존 항목의
`id` 로 이뤄지므로 중복이 생기지 않는다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

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
    omitted_messages: int = 0
    failures: list[str] = field(default_factory=list)
    """에이전트 호출이 실패한 묶음. 하나가 터져도 나머지는 간다."""

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated)

    def summary(self) -> str:
        return f"신규 {self.created} · 갱신 {self.updated}"


class IngestRun:
    """원천 → 지식. 한 번 돌면 커밋 하나가 생긴다."""

    def __init__(
        self,
        *,
        repo: KnowledgeRepository,
        agent: IngestAgent,
        conn: sqlite3.Connection,
        output_filter: OutputFilter,
        mirrors: Sequence[SourceMirror] = (),
        max_chars: int = MAX_CHARS_PER_CALL,
    ) -> None:
        self._repo = repo
        self._agent = agent
        self._conn = conn
        self._filter = output_filter
        self._mirrors = list(mirrors)
        self._max_chars = max_chars

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

        material, dropped = prepare_source_material(head, messages, files)
        result.dropped_config_paths.extend(dropped)
        if not material.files and not material.messages:
            return head
        # 읽을 것이 남아 있으면 아래에서 묶음마다 돈다.

        live: dict[str, bool] = {}
        for chunk in _chunks(material, self._max_chars):
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
        return None if len(result.failures) > failures_before else head

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
    return out


def _with_files(material: SourceMaterial, files: list[tuple[str, str]]) -> SourceMaterial:
    return SourceMaterial(
        commit=material.commit, messages=material.messages, files=tuple(files)
    )
