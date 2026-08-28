"""질문 직접 등록 — 메신저 문의를 흡수한다 (FR-10·11, D43·D44, §1.4.3).

**1국면에 QnA 는 메신저를 이길 수 없다.** 속도·맥락·부담 어느 항목에서도 메신저가
유리하므로, 이용자를 옮기려는 시도는 실패하기 쉽고 실패하면 시스템이 쓸모없다는
인상만 남긴다. 그래서 **경쟁하지 않고 흡수한다** — 담당자가 메신저로 받은 질문을
직접 기록한다.

W4("질문이 기록되지 않는다")의 실체는 인입 경로가 부족한 것이 아니라 **기록 경로가
없는 것**이다(D43). 대응은 경로를 늘리는 것이 아니라 기록할 자리를 만드는 것이고,
기록된 티켓은 §6.8 의 A 경로(티켓 해결 → 지식)를 그대로 탄다 — 새 메커니즘이
필요 없다.

## 두 칸에서 시작한다

**등록 부담이 크면 유인이 상쇄된다** (§1.4.4). 담당자가 메신저 답변을 마치고 곧바로
남길 수 있어야 하므로 입력은 **질문 원문과 자기 답변 둘뿐**이고 붙여넣기로 끝난다.
나머지는 시스템이 채운다 (ADR-007 결정 4).

| 항목 | 누가 |
|---|---|
| 질문 · 답변 | **사람** (붙여넣기) |
| 일반화된 질문 · 근거 추정 | 에이전트 초안 |
| **무효화 조건** | **사람** — 강제 입력 지점(§5.6.4). 이것만은 줄이지 않는다 |

## 초안은 배치가 만든다

등록 자체는 즉시 끝나야 하는데(붙여넣고 닫는다) 초안 생성은 LLM 호출이라 수십 초가
걸린다. 등록 응답을 그만큼 붙들면 **부담이 되돌아와 §1.4.4 가 무너진다.** 그래서
등록은 온라인에서 즉시 끝내고 초안은 배치가 채운다 — 온라인은 저지연, 배치는 정확도
우선이라는 실행 경로 분리(§9.5)와 같은 선이다.

> **QnA 게재 체크박스는 아직 만들 수 없다.** ADR-007 이 선택 항목으로 둔 "QnA 에도
> 게재"는 모 시스템에 **질문을 만드는 API 표면이 없어서**(§9.8.1 의 일곱은 답변
> 게재까지다) 성립하지 않는다. 다행히 이것은 없어도 되는 축이다 — 컨셉이 그 항목을
> 선택으로 둔 이유가 "**지식화가 QnA 게재 여부와 무관하게 성립**하기 때문"이고,
> 티켓만 있어도 지식은 자란다 (§1.4.3).
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_service_desk.operations import resolution as resolution_domain
from agentic_service_desk.operations import ticket as ticket_domain

MANUAL = "manual"
"""`qna_item.origin` 값. **이 건수가 W4 의 유일한 간접 지표다** (§1.4.6)."""

PARENT = "parent"

STATE_HUMAN_ANSWERED = "사람대기"
"""등록 직후의 QnA 상태.

이미 답은 나갔지만(담당자가 메신저로 답했다) **우리 쪽에 남은 일이 있다** —
무효화 조건을 채워 지식으로 만드는 것. 그래서 해결이 아니라 사람 대기다.
"""


class EmptyEntry(ValueError):
    """두 칸 중 하나가 비었다."""


@dataclass(frozen=True)
class Registration:
    """등록 결과."""

    qna_item_id: str
    ticket_id: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def register(
    conn: sqlite3.Connection, *, question: str, answer: str, registered_by: str = ""
) -> Registration:
    """질문과 답변을 기록한다. **여기서 LLM 을 부르지 않는다.**

    붙여넣고 닫는 것이 등록의 전부여야 한다. 초안은 배치가 채운다.
    """
    question, answer = question.strip(), answer.strip()
    if not question:
        raise EmptyEntry("질문 원문이 비었다")
    if not answer:
        raise EmptyEntry("담당자가 한 답변이 비었다")

    now = _now()
    qna_item_id = f"q-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO qna_item (id, parent_question_id, origin, asker_id, state, opened_at) "
        "VALUES (?, NULL, ?, ?, ?, ?)",
        # `parent_question_id` 가 비는 것이 정상이다 — 모 시스템을 거치지 않았다.
        (qna_item_id, MANUAL, registered_by or None, STATE_HUMAN_ANSWERED, now),
    )
    conn.execute(
        "INSERT INTO manual_entry (qna_item_id, question, answer, registered_by, registered_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (qna_item_id, question, answer, registered_by or None, now),
    )
    conn.commit()

    ticket = ticket_domain.issue(
        conn, source=ticket_domain.Source.QNA, qna_item_id=qna_item_id
    )
    return Registration(qna_item_id=qna_item_id, ticket_id=ticket.id)


@dataclass(frozen=True)
class Entry:
    """등록된 원문. 초안의 재료다."""

    qna_item_id: str
    ticket_id: str
    question: str
    answer: str


def awaiting_draft(conn: sqlite3.Connection) -> list[Entry]:
    """아직 종결 기록 초안이 없는 등록 건.

    티켓이 있는데 기록이 없는 상태다 — 배치가 채울 대상이며, 채워질 때까지 사람은
    무효화 조건을 쓸 수 없다.
    """
    rows = conn.execute(
        "SELECT m.qna_item_id, m.question, m.answer, t.id AS ticket_id "
        "FROM manual_entry m "
        "JOIN ticket t ON t.qna_item_id = m.qna_item_id "
        "LEFT JOIN ticket_resolution r ON r.ticket_id = t.id "
        "WHERE r.ticket_id IS NULL AND t.state NOT IN (?, ?) "
        "ORDER BY m.registered_at",
        (str(ticket_domain.State.CLOSED), str(ticket_domain.State.AUTO_CLOSED)),
    ).fetchall()
    return [
        Entry(
            qna_item_id=r["qna_item_id"],
            ticket_id=r["ticket_id"],
            question=r["question"],
            answer=r["answer"],
        )
        for r in rows
    ]


def get_entry(conn: sqlite3.Connection, qna_item_id: str) -> Entry | None:
    row = conn.execute(
        "SELECT m.qna_item_id, m.question, m.answer, t.id AS ticket_id "
        "FROM manual_entry m JOIN ticket t ON t.qna_item_id = m.qna_item_id "
        "WHERE m.qna_item_id = ?",
        (qna_item_id,),
    ).fetchone()
    if row is None:
        return None
    return Entry(
        qna_item_id=row["qna_item_id"],
        ticket_id=row["ticket_id"],
        question=row["question"],
        answer=row["answer"],
    )


def count(conn: sqlite3.Connection) -> int:
    """수동 등록 건수. **W4 의 유일한 간접 지표다** (§1.4.6, §8.3).

    이 수가 늘면 담당자가 기록하고 있다는 뜻이고, 0 에 머물면 **질문이 여전히
    메신저에서 사라지고 있다는 뜻**이다 — 시스템이 조용히 쓸모없어지는 방식이다.
    """
    return conn.execute(
        "SELECT COUNT(*) FROM qna_item WHERE origin = ?", (MANUAL,)
    ).fetchone()[0]


def awaiting_invalidation(conn: sqlite3.Connection) -> list[resolution_domain.Resolution]:
    """초안은 있는데 사람이 아직 무효화 조건을 채우지 않은 건 (§5.6.4)."""
    return resolution_domain.awaiting_invalidation(conn)
