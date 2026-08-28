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
from dataclasses import dataclass, field
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
from agentic_service_desk.ingest.output_filter import OutputFilter
from agentic_service_desk.ingest.source import SourceMirror
from agentic_service_desk.knowledge import contradiction
from agentic_service_desk.knowledge.repository import KnowledgeRepository, StoredItem
from agentic_service_desk.operations.checkpoint import SOURCE, get_cursor, set_cursor

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
        mirror: SourceMirror | None = None,
        max_chars: int = MAX_CHARS_PER_CALL,
    ) -> None:
        self._repo = repo
        self._agent = agent
        self._conn = conn
        self._filter = output_filter
        self._mirror = mirror
        self._max_chars = max_chars

    def run(self) -> IngestResult:
        self._repo.ensure_initialized()
        result = IngestResult()

        stored, result.broken_items = self._repo.scan()
        by_id = {s.item.id: s for s in stored}
        index = [(s.item.id, s.item.title) for s in stored]

        head = self._ingest_source(result, by_id, index)
        answer_ids = self._ingest_qna(result, by_id, index)

        if result.changed:
            self._repo.append_log(
                f"{result.summary()} — {self._origin_note(head, answer_ids)}"
            )
            result.commit = self._repo.commit(f"ingest: {result.summary()}")

        # **여기서부터가 진행 표시다.** 커밋이 끝난 뒤에만 옮긴다.
        #
        # 지식이 하나도 안 바뀌었어도 옮긴다. **읽은 것과 지식이 된 것은 다르다** —
        # 원천에 뽑을 개념이 없다는 판단도 처리 결과이고, 그것을 처리로 세지 않으면
        # 같은 원천을 매 주기 LLM 에 다시 태운다. 실제로 그런 원천이 있다
        # (내용 없는 봇 답변 등).
        #
        # 옮기지 않는 경우는 **실패했을 때뿐이다.** `head` 는 소스 단계가 무사히
        # 끝났을 때만 오고, 답변 id 는 그 호출이 성공했을 때만 담긴다.
        if head:
            set_cursor(self._conn, SOURCE, head)
        self._mark_ingested(answer_ids, result.commit)
        return result

    # --- 소스 원천 -------------------------------------------------------

    def _ingest_source(
        self,
        result: IngestResult,
        by_id: dict[str, StoredItem],
        index: list[tuple[str, str]],
    ) -> str | None:
        """소스 저장소의 변경분을 읽는다.

        돌려주는 것은 **이번 구간을 무사히 읽었을 때의 HEAD** 다. 한 묶음이라도
        실패하면 `None` 을 돌려준다 — 그래야 커서가 그 자리에 남고 다음 주기가
        같은 구간을 다시 읽는다. 옮겨 버리면 **그 구간의 개념이 영영 지식이 되지
        않는데 아무도 알아채지 못한다.**
        """
        if self._mirror is None or not self._mirror.is_cloned:
            return None
        failures_before = len(result.failures)

        cursor = get_cursor(self._conn, SOURCE)
        head = self._mirror.head()
        if cursor == head:
            return None

        changed = self._mirror.changed_paths_since(cursor)
        commits = self._mirror.commits_since(cursor)
        messages = [c.full_message for c in commits]
        if len(messages) > MAX_COMMIT_MESSAGES:
            result.omitted_messages = len(messages) - MAX_COMMIT_MESSAGES
            messages = messages[:MAX_COMMIT_MESSAGES]

        files: list[tuple[str, str]] = []
        for path in changed:
            try:
                files.append((path, self._mirror.read_file(path, at=head)))
            except RuntimeError:
                # 삭제된 경로다. 커밋 메시지에는 남아 있으므로 "왜 지웠는가"는 살아 있다.
                continue

        material, dropped = prepare_source_material(head, messages, files)
        result.dropped_config_paths.extend(dropped)
        if not material.files and not material.messages:
            return head
        # 읽을 것이 남아 있으면 아래에서 묶음마다 돈다.

        for chunk in _chunks(material, self._max_chars):
            try:
                proposals = self._agent.from_source(chunk, index)
            except (AgentOutputError, RuntimeError) as exc:
                result.failures.append(f"소스 {chunk.commit[:8]}: {exc}")
                continue
            for proposal in proposals:
                self._apply(
                    proposal,
                    source_provenance(proposal, chunk),
                    by_id,
                    index,
                    result,
                )
        return None if len(result.failures) > failures_before else head

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
    def _origin_note(head: str | None, answer_ids: list[str]) -> str:
        parts = []
        if head:
            parts.append(f"소스 {head[:8]}")
        if answer_ids:
            parts.append(f"QnA 답변 {len(answer_ids)}건")
        return ", ".join(parts) or "원천 없음"


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
