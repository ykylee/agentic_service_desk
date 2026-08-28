"""유입 처리 — 전건 티켓 발행 (FR-27, D19, §6.4.3-1).

수집이 Raw Layer 에 담아 둔 질문을 **처리 단위**로 옮긴다. 질문 하나가 `qna_item`
(대외 관점 — 이용자에게 이 질문이 어떻게 되었는가)이 되고, 처리 한 번이 `ticket`
(내부 관점 — 우리에게 무슨 일이 남았는가)이 된다 (D15).

## 모든 QnA 가 티켓을 발행한다

> 티켓은 "사람 손이 필요한 일"이 아니라 **처리 하나의 작업 기록 단위**다 (§6.4.3-1).

그래서 자동으로 답한 건도 티켓을 갖는다. **발행과 대기열 진입은 다른 일이다** —
자동 처리분은 발행되고 곧바로 `auto_closed` 가 되어 Q1 에 뜨지 않지만 기록·추적·
통계에는 남는다. 이렇게 하면 자동 처리는 얇게 수동 처리는 두껍게 남는 **기록의
비대칭이 사라지고**, 처리량·자동화율이 티켓 한 축에서 나온다.

## 티켓은 파이프라인이 돈 **뒤에** 발행된다

FR-27 은 자동 처리 건이 "발행 **즉시** 자동 종결"된다고 했다. 발행 시점에 결과를
알고 있어야 성립하는 문장이므로, 발행은 처리 뒤다. 상태는 결과가 정한다.

    초안이 나왔다  → auto_closed — 파이프라인이 제 몫을 끝냈다. 남은 판정은 Q2 가 든다
    멈췄다         → open        — 실패는 사람의 대기열로 수렴한다 (§5.1)

초안이 Q2 에 걸려 있는데 티켓까지 Q1 에 띄우면 **같은 일이 두 대기열에 뜬다.**
§6.4.4 가 작업(티켓)과 판정(Q2)을 가른 것이 바로 그것을 막기 위해서다.

Q2 에서 반려되면 **새 티켓**이 열린다 — 닫힌 티켓을 되살리지 않는다(§6.7.1 종점).
"QnA 하나가 여러 티켓을 낳을 수 있다"(FR-56)가 그 자리를 이미 마련해 두었다.

## 경과 시간은 **물은 시각**부터 잰다

`qna_item.opened_at` 은 모 시스템의 질문 생성 시각이지 우리가 수집한 시각이 아니다.
경과 시간이 SLA 를 대신하는데(§6.7.3) 우리 폴링 주기가 거기 섞이면, 늦게 가져온 것이
방금 온 것처럼 보여 **오래된 질문이 대기열 아래로 숨는다.**
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from agentic_service_desk.ingest.agent import AgentOutputError, Harness
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations import resolution as resolution_domain
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations.drafter import DraftReport, parse_draft
from agentic_service_desk.operations.resolution import Ground, GroundKind
from agentic_service_desk.pipeline import draft_store
from agentic_service_desk.pipeline.answer import AnswerPipeline, Halt, Outcome
from agentic_service_desk.pipeline.review import Reviewer, ReviewInput, Verdict

ORIGIN_PARENT = "parent"
"""`qna_item.origin` — 모 시스템을 거쳐 들어왔다. 수동 등록(§1.4.3)과 가른다.

**수동 등록 건수가 W4(질문이 기록되지 않는다)의 유일한 간접 지표다**(§1.4.6) —
그러려면 둘이 구분돼 있어야 한다.
"""

STATE_RECEIVED = "접수"
"""유입 직후. 아직 아무것도 판정되지 않았다."""

STATE_AWAITING_HUMAN = "사람대기"
"""사람의 판정이나 손질을 기다린다 — Q2 검수 대기이거나 Q1 티켓이다.

수동 등록의 같은 상태(`manual_entry.STATE_HUMAN_ANSWERED`)와 글자는 같지만 사연이
다르다. 저쪽은 *답은 이미 나갔고* 무효화 조건만 남은 것이고, 이쪽은 *답이 아직
나가지 않은* 것이다. 이용자에게는 둘 다 "우리 쪽에 일이 남았다"로 같다.
"""


@dataclass(frozen=True)
class Admission:
    """유입 하나를 처리한 결과."""

    qna_item_id: str
    parent_question_id: str
    ticket_id: str
    ticket_state: ticket_domain.State
    draft_id: str | None = None
    halted: Halt | None = None

    @property
    def auto_closed(self) -> bool:
        return self.ticket_state is ticket_domain.State.AUTO_CLOSED


@dataclass
class IntakeReport:
    admitted: list[Admission] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.admitted or self.failures)

    @property
    def auto_closed(self) -> int:
        return sum(1 for a in self.admitted if a.auto_closed)

    @property
    def to_human(self) -> int:
        return len(self.admitted) - self.auto_closed


def pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """아직 처리 단위를 갖지 못한 질문.

    **Raw Layer 를 본다.** 수집이 이미 원문을 담았으므로 어댑터를 다시 부르지
    않는다 — 그리고 Raw Layer 는 거르지 않으므로(FR-52) 전건이 여기 있다.
    거르는 자리는 ingest 입구 하나뿐이고(NFR-4), **거기서 빠지는 것과 처리되지
    않는 것은 다르다**: 봇이 답한 미해결 질문도 처리 단위는 가져야 한다.
    """
    return conn.execute(
        "SELECT r.* FROM raw_question r "
        "LEFT JOIN qna_item q ON q.parent_question_id = r.id "
        "WHERE q.id IS NULL ORDER BY r.created_at"
    ).fetchall()


def admit(conn: sqlite3.Connection, question: sqlite3.Row) -> str:
    """질문 하나를 `qna_item` 으로 들인다. 이미 있으면 그 id 를 돌려준다.

    **두 번 들이지 않는다.** `qna_item.parent_question_id` 의 UNIQUE 제약이 함께
    지킨다 — 재실행이 중복 발행으로 이어지면 통계가 조용히 부풀고, 부푼 통계는
    틀렸다고 아무도 알려주지 않는다.
    """
    existing = conn.execute(
        "SELECT id FROM qna_item WHERE parent_question_id = ?", (question["id"],)
    ).fetchone()
    if existing is not None:
        return existing["id"]

    qna_item_id = f"q-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO qna_item (id, parent_question_id, origin, asker_id, state, opened_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            qna_item_id,
            question["id"],
            ORIGIN_PARENT,
            question["asker_account"],
            STATE_RECEIVED,
            # 우리가 가져온 시각(`collected_at`)이 아니다. 위 모듈 설명 참조.
            question["created_at"],
        ),
    )
    conn.commit()
    return qna_item_id


def run(
    conn: sqlite3.Connection,
    *,
    pipeline: AnswerPipeline | None = None,
    reviewer: Reviewer | None = None,
) -> IntakeReport:
    """유입분을 처리한다. 한 건이 실패해도 나머지는 계속 간다.

    `pipeline` 이 없으면(설정이 아직 비어 있다) **그래도 발행한다.** 티켓은 `open` 이
    되고 사람이 본다 — 질문이 왔는데 아무 기록도 없는 것보다 낫다. 유입 자체가
    W4(질문이 기록되지 않는다) 관측의 재료이기 때문이다 (§1.4.6).
    """
    report = IntakeReport()
    for row in pending(conn):
        try:
            report.admitted.append(_admit_one(conn, row, pipeline, reviewer))
        except Exception as exc:  # noqa: BLE001 — 한 건의 실패가 주기를 세우지 않는다
            report.failures.append(f"{row['id']}: {exc}")
    return report


def _admit_one(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    pipeline: AnswerPipeline | None,
    reviewer: Reviewer | None,
) -> Admission:
    qna_item_id = admit(conn, row)
    outcome = pipeline.run(row["body"]) if pipeline is not None else None

    if outcome is not None and outcome.analysis is not None:
        conn.execute(
            "UPDATE qna_item SET language = ? WHERE id = ?",
            (outcome.analysis.language, qna_item_id),
        )

    draft_id = None
    if outcome is not None and outcome.draft is not None:
        draft_id = _queue(conn, row["body"], outcome, qna_item_id, reviewer)

    state = (
        ticket_domain.State.AUTO_CLOSED
        if draft_id is not None
        else ticket_domain.State.OPEN
    )
    ticket = ticket_domain.issue(
        conn, source=ticket_domain.Source.QNA, qna_item_id=qna_item_id, state=state
    )
    conn.execute(
        "UPDATE qna_item SET state = ? WHERE id = ?",
        (STATE_AWAITING_HUMAN, qna_item_id),
    )
    conn.commit()
    return Admission(
        qna_item_id=qna_item_id,
        parent_question_id=row["id"],
        ticket_id=ticket.id,
        ticket_state=state,
        draft_id=draft_id,
        halted=outcome.halted if outcome else Halt.GENERATION_FAILED,
    )


def _queue(
    conn: sqlite3.Connection,
    question: str,
    outcome: Outcome,
    qna_item_id: str,
    reviewer: Reviewer | None,
) -> str:
    """초안을 4단계에 태우고 Q2 에 올린다.

    **검수를 건너뛰지 않는다.** 단계 자체가 필수이고, 누가 어느 강도로 보는지만
    국면에 따라 달라진다 (§5.1). 검수기가 없으면 판정 없이 올린다 — **검수가 없는
    것과 통과한 것은 다르므로**(§5.6.1) `verdict` 를 비워 두어 그 차이를 남긴다.
    """
    verdict: Verdict | None = None
    if reviewer is not None:
        verdict = reviewer.review(ReviewInput.of(outcome.draft, outcome.hits))
    return draft_store.save(
        conn,
        question=question,
        draft=outcome.draft,
        verdict=verdict,
        qna_item_id=qna_item_id,
    )


def reopen_for_rejected_draft(
    conn: sqlite3.Connection, qna_item_id: str | None
) -> str | None:
    """반려된 초안을 사람의 작업 대기열로 보낸다 (§5.1).

    **닫힌 티켓을 되살리지 않고 새로 연다.** 되살리면 한 티켓에 여러 처리가 섞여
    통계가 무너진다(§6.7.1) — 자동 처리 한 번과 사람 처리 한 번은 **두 번의 처리**다.
    """
    if not qna_item_id:
        return None
    ticket = ticket_domain.issue(
        conn,
        source=ticket_domain.Source.QNA,
        qna_item_id=qna_item_id,
        state=ticket_domain.State.OPEN,
    )
    conn.execute(
        "UPDATE qna_item SET state = ? WHERE id = ?",
        (STATE_AWAITING_HUMAN, qna_item_id),
    )
    conn.commit()
    return ticket.id


# --- 자동 처리 건의 종결 기록 (FR-27 확인 조건) ------------------------------
#
# §6.4.3-1 이 전건 발행에서 얻는다고 한 셋 중 세 번째다. 자동 처리 건에 종결 기록이
# 없으면 §5.3 이 허용한 "명시적 해결된 봇 답변"에 **승격할 형식이 존재하지 않는다** —
# 규칙은 있는데 올릴 물건이 없는 상태였다.
#
# **게재된 건에만 만든다.** 반려된 초안은 그 답이 틀렸다고 판정된 것이므로 승격
# 재료가 아니다 — 기록으로 남기면 틀린 답이 승격 후보로 줄을 선다.


@dataclass(frozen=True)
class Handled:
    """자동으로 처리되어 게재까지 간 건. 종결 기록의 재료다."""

    ticket_id: str
    qna_item_id: str
    question: str
    answer: str
    grounding_ids: tuple[str, ...]


def awaiting_resolution_draft(conn: sqlite3.Connection) -> list[Handled]:
    """종결 기록이 아직 없는 자동 처리 건."""
    rows = conn.execute(
        "SELECT t.id AS ticket_id, t.qna_item_id, d.question, d.statements, d.grounding "
        "FROM ticket t "
        "JOIN answer_draft d ON d.qna_item_id = t.qna_item_id "
        "JOIN answer_record a ON a.draft_id = d.id AND a.state = 'published' "
        "LEFT JOIN ticket_resolution r ON r.ticket_id = t.id "
        "WHERE t.state = ? AND d.state = ? AND r.ticket_id IS NULL "
        "ORDER BY t.opened_at",
        (str(ticket_domain.State.AUTO_CLOSED), draft_store.APPROVED),
    ).fetchall()
    return [
        Handled(
            ticket_id=r["ticket_id"],
            qna_item_id=r["qna_item_id"],
            question=r["question"],
            answer="\n\n".join(s["text"] for s in json.loads(r["statements"])),
            grounding_ids=tuple(json.loads(r["grounding"])),
        )
        for r in rows
    ]


def grounding_of(repo: KnowledgeRepository, item_ids: Sequence[str]) -> list[Ground]:
    """근거를 **지식 항목이 아니라 그 항목의 출처로** 적는다.

    지식 항목을 가리키면 한 다리 건너가 되고, 그 항목이 갱신되면 이 기록의 근거가
    무엇이었는지 흔들린다. 항목의 출처(커밋·경로)를 옮겨 적으면 **코드에 직접
    닿아** stale 자동 판정이 성립한다 (§6.5.3).

    **모델에게 묻지 않는다.** 우리는 파이프라인이 어느 항목을 썼는지 이미 알고
    있고, 그 항목이 자기 출처를 들고 있다 — 지어낸 경로를 받을 이유가 없다 (FR-4).
    """
    grounds: list[Ground] = []
    seen: set[tuple[str, str]] = set()
    for item_id in item_ids:
        stored = repo.find(item_id)
        if stored is None:
            continue
        for prov in stored.item.provenance:
            for kind, ref in (
                (GroundKind.CODE, prov.path),
                (GroundKind.COMMIT, prov.commit),
            ):
                if ref and (str(kind), ref) not in seen:
                    seen.add((str(kind), ref))
                    grounds.append(Ground(kind=kind, ref=ref))
    return grounds


_RESOLUTION_PROMPT = """이 시스템이 자동으로 만들어 게재한 답변이다. 이것을 **지식 항목의
초안**으로 옮긴다.

규칙:
1. **일반화된 질문** — 원 질문에서 개인·상황 특정 요소(이름·사번·건 번호·날짜·금액)를
   걷어낸 형태로 다시 쓴다. 같은 유형의 질문 전체를 대표해야 한다.
2. **답** — 재사용 가능한 진술로 다시 쓴다. **게재된 답변에 없는 내용을 지어내지 않는다.**
3. **무효화 조건 후보** — "무엇이 바뀌면 이 답이 틀려지는가"의 후보를 나열만 한다.
   고르는 것은 사람이다. 연결형은 {"kind":"linked","refs":["경로"]},
   주기형은 {"kind":"periodic","period_days":180}.
4. 원인·적용 범위·재발 가능성은 아는 만큼만 쓰고 모르면 비운다.

**근거는 쓰지 않는다.** 어느 지식 항목을 썼는지는 우리가 이미 알고 있다.

출력은 **JSON 하나만** 낸다. 설명이나 사고 과정을 붙이지 않는다.

{
  "generalized_question": "...",
  "answer": "...",
  "invalidation_candidates": [{"kind": "periodic", "period_days": 180}],
  "cause": null,
  "scope": null,
  "recurrence": null
}"""


def build_resolution_prompt(handled: Handled) -> str:
    return "\n".join(
        [
            _RESOLUTION_PROMPT,
            "",
            f"질문 원문:\n{handled.question}",
            "",
            f"게재된 답변:\n{handled.answer}",
        ]
    )


def draft_resolutions(
    conn: sqlite3.Connection, harness: Harness, repo: KnowledgeRepository
) -> DraftReport:
    """자동 처리 건의 종결 기록 초안을 채운다.

    **무효화 조건은 비워 둔다** — 수동 등록 건과 같다 (§5.6.4). `resolution.draft()`
    가 애초에 그 인자를 받지 않으므로 여기서 실수할 여지도 없다.
    """
    report = DraftReport()
    for handled in awaiting_resolution_draft(conn):
        grounding = grounding_of(repo, handled.grounding_ids)
        if not grounding:
            # 출처를 못 찾았다 — 항목이 지워졌거나 출처가 비었다. **지어내지 않고**
            # 넘긴다. 출처 없는 항목은 어느 경로로도 만들어지지 않는다 (FR-4).
            report.failures.append(f"{handled.ticket_id}: 근거 항목의 출처를 찾지 못했다")
            continue
        try:
            fields = parse_draft(harness.run(build_resolution_prompt(handled)).text)
        except (AgentOutputError, RuntimeError) as exc:
            report.failures.append(f"{handled.ticket_id}: {exc}")
            continue
        fields["grounding"] = grounding  # 모델이 무엇을 보냈든 코드가 정한다
        resolution_domain.draft(conn, ticket_id=handled.ticket_id, **fields)
        report.drafted.append(handled.ticket_id)
    return report
