"""티켓 도메인 (FR-12·56, D15·D33, §6.7).

**티켓은 "사람 손이 필요한 일"이 아니라 처리 하나의 작업 기록 단위다** (§6.4.3-1).
그래서 자동으로 답해 곧바로 닫힌 건도 티켓을 갖는다 — 그러지 않으면 자동 처리는
얇게, 수동 처리는 두껍게 남아 **통계와 승격 재료가 비대칭이 된다.**

상태 집합은 **조직 규모가 아니라 작업 리듬을 따른다** (§6.7.2). 1인 겸업이면
상태를 줄여도 될 것 같지만 겸업이라는 점이 반대로 작용한다 — 며칠 뒤에 돌아오면
무엇을 보다 말았는지 기억나지 않으므로 **자기 자신에게 남기는 표시**가 필요하고,
질문자 응답을 기다리는 건이 대기열에 남아 있으면 열어 볼 때마다 **다시 판단하게
된다.** 1인 겸업에게 가장 비싼 것은 반복되는 재판단이다.

**없는 것이 있는 것만큼 중요하다** (§6.7).

| 덜어낸 것 | 이유 |
|---|---|
| 담당자 배정 | 받을 사람이 하나뿐이다 |
| 우선순위 필드 | 대기열이 **방치 비용 × 경과 시간**으로 정렬한다 |
| 마감 · SLA | 기한을 걸지 않는다. **경과 시간만 본다** — 약속이 아니라 관측이다 |
| 외부 이관 | 운영자가 개발자이므로 코드 수정도 여기서 끝난다 |

> **QnA 항목과는 별개다** (D15, FR-56). 서로 다른 질문에 답하므로 동기화 대상이
> 아니다 — QnA 항목은 "이용자에게 이 질문이 어떻게 되었는가", 티켓은 "우리에게
> 무슨 일이 남았는가". 한쪽이 닫혀도 다른 쪽은 열려 있을 수 있고, QnA 하나가
> 여러 티켓을 낳을 수 있다.
"""

from __future__ import annotations

import enum
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


class State(enum.StrEnum):
    """다섯 상태 (D33, §6.7.1)."""

    AUTO_CLOSED = "auto_closed"
    """파이프라인이 끝까지 처리했다. **기록으로만 남고 대기열에 뜨지 않는다.**"""

    OPEN = "open"
    """사람이 봐야 한다. Q1 에 뜬다."""

    IN_PROGRESS = "in_progress"
    """사람이 손대기 시작했다. **1인이라 배정은 필요 없어도 자기 자신에게 남기는
    표시는 필요하다** — 며칠 뒤에 돌아오면 무엇을 보다 말았는지 기억나지 않는다."""

    HELD = "held"
    """외부 응답을 기다린다 — 질문자에게 추가 정보를 요청한 상태.

    **대기열에서 빠진다.** 응답 대기 건이 남아 있으면 열어 볼 때마다 다시 판단하게
    되고, 겸업에게 가장 비싼 것이 그 반복되는 재판단이다. 응답이 오면 자동으로 열린다.
    """

    CLOSED = "closed"
    """종결 기록(§6.5)을 남기고 닫혔다."""


class Source(enum.StrEnum):
    """티켓의 출처는 넷이다 (§6.4.3). **QnA 만이 아니다.**

    이것이 QnA 항목과 티켓을 나눈 이유 중 하나다 — 콘텐츠 검수 반려, 지식 모순,
    정정 후보는 QnA 항목이 아니므로 하나로 합쳤다면 담을 곳이 없다 (§6.4.1).
    """

    QNA = "qna"
    CONTENT = "content"
    CONTRADICTION = "contradiction"
    CORRECTION = "correction"


#: 허용되는 전이. **자동종결과 종결은 종점이다.**
#:
#: 닫힌 티켓을 다시 열지 않는 이유가 있다 — 같은 질문이 또 오면 그것은 **새로운
#: 처리**이고, QnA 하나가 여러 티켓을 낳을 수 있다는 것(FR-56)이 그 자리를 이미
#: 마련해 두었다. 되살리면 한 티켓에 여러 처리가 섞여 통계가 무너진다.
TRANSITIONS: dict[State, frozenset[State]] = {
    State.OPEN: frozenset({State.IN_PROGRESS, State.HELD, State.CLOSED}),
    State.IN_PROGRESS: frozenset({State.OPEN, State.HELD, State.CLOSED}),
    State.HELD: frozenset({State.OPEN, State.IN_PROGRESS, State.CLOSED}),
    State.AUTO_CLOSED: frozenset(),
    State.CLOSED: frozenset(),
}

#: Q1 대기열에 뜨는 상태 (§6.7.1). **보류가 빠져 있는 것이 요점이다.**
QUEUE_VISIBLE = frozenset({State.OPEN, State.IN_PROGRESS})


class InvalidTransition(RuntimeError):
    """허용되지 않은 상태 전이다."""


class ResolutionRequired(RuntimeError):
    """종결 기록이 없거나 아직 승인되지 않았다 (§6.4.5, FR-13).

    1국면에서는 **티켓 해결의 승격이 지식 성장의 주 경로**다. 그런데 티켓을 "완료"
    체크로 닫는 순간 **승격할 재료가 사라진다** — 그래서 닫는 행위 자체에 기록을
    묶었다. 기록의 형식은 `operations.resolution` 이 정한다.
    """


class TicketNotFound(KeyError):
    pass


@dataclass(frozen=True)
class Ticket:
    """티켓 하나. **담당자도 마감도 우선순위도 없다** (§6.7)."""

    id: str
    source: Source
    state: State
    opened_at: str
    state_at: str
    """지금 상태가 된 시각. 보류 해제 판정이 이것에 걸린다 (§6.7.1)."""

    qna_item_id: str | None = None
    """QnA 유래일 때만. **없어도 된다** — 티켓 출처가 넷이기 때문이다 (§6.4.3)."""

    closed_at: str | None = None

    def age(self, *, now: datetime | None = None) -> float:
        """열린 뒤 흐른 시간(시간 단위). **경과 시간이 SLA 를 대신한다** (§6.7.3).

        기한을 걸지 않되 오래된 것은 드러난다. 약속이 아니라 **관측**이므로 넘겨도
        위반이 아니라 신호다.
        """
        opened = datetime.fromisoformat(self.opened_at)
        return ((now or _now()) - opened).total_seconds() / 3600

    @property
    def in_queue(self) -> bool:
        return self.state in QUEUE_VISIBLE


def _now() -> datetime:
    return datetime.now(UTC)


def issue(
    conn: sqlite3.Connection,
    *,
    source: Source | str,
    qna_item_id: str | None = None,
    state: State | str = State.OPEN,
) -> Ticket:
    """티켓을 발행한다.

    **발행과 대기열 진입은 다른 일이다** (§6.4.3-1). 자동 처리 건은 발행되고 곧바로
    `auto_closed` 가 되어 대기열에 뜨지 않는다 — 그래도 기록·추적·통계에는 남는다.
    """
    state = State(state)
    now = _now().isoformat()
    ticket = Ticket(
        id=f"t-{uuid.uuid4().hex[:12]}",
        source=Source(source),
        state=state,
        opened_at=now,
        state_at=now,
        qna_item_id=qna_item_id,
        closed_at=now if state in (State.AUTO_CLOSED, State.CLOSED) else None,
    )
    conn.execute(
        "INSERT INTO ticket (id, source, qna_item_id, state, opened_at, state_at, closed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            ticket.id,
            str(ticket.source),
            ticket.qna_item_id,
            str(ticket.state),
            ticket.opened_at,
            ticket.state_at,
            ticket.closed_at,
        ),
    )
    conn.commit()
    return ticket


def get(conn: sqlite3.Connection, ticket_id: str) -> Ticket:
    row = conn.execute("SELECT * FROM ticket WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise TicketNotFound(ticket_id)
    return _from_row(row)


def transition(conn: sqlite3.Connection, ticket_id: str, to: State | str) -> Ticket:
    """상태를 옮긴다. **허용되지 않은 전이는 거부한다.**

    종결(`closed`)은 종결 기록이 있어야 한다 — 없이 닫으면 승격할 재료가 사라진다
    (§6.4.5).
    """
    to = State(to)
    ticket = get(conn, ticket_id)
    if to not in TRANSITIONS[ticket.state]:
        allowed = ", ".join(sorted(TRANSITIONS[ticket.state])) or "(종점이다)"
        raise InvalidTransition(
            f"{ticket.state} → {to} 는 허용되지 않는다. 갈 수 있는 곳: {allowed}"
        )
    if to is State.CLOSED and not has_resolution(conn, ticket_id):
        raise ResolutionRequired(
            f"티켓 {ticket_id} 를 닫을 수 없다 — 종결 기록이 없거나 **무효화 조건이 "
            "비어 있다.** 닫는 순간 승격할 재료가 사라지고(§6.4.5, FR-13), 무효화 "
            "조건이 없으면 그 지식은 영영 낡지 않는다 (FR-14, §6.5.3)"
        )

    now = _now().isoformat()
    conn.execute(
        "UPDATE ticket SET state = ?, state_at = ?, closed_at = ? WHERE id = ?",
        (str(to), now, now if to is State.CLOSED else None, ticket_id),
    )
    conn.commit()
    return get(conn, ticket_id)


def has_resolution(conn: sqlite3.Connection, ticket_id: str) -> bool:
    """닫아도 되는 종결 기록이 있는가.

    **초안만으로는 부족하다.** 무효화 조건이 비어 있으면 그 기록은 아직 사람의
    승인을 지나지 않았고(§5.6.4), 승인 없이 닫으면 승격 대기로 갈 수 없는 기록만
    남는다. 형식은 `operations.resolution` 이 정한다.
    """
    from agentic_service_desk.operations import resolution

    return resolution.is_confirmed(conn, ticket_id)


def queue(conn: sqlite3.Connection) -> list[Ticket]:
    """Q1 대기열 — **열림과 진행중만** (§6.7.1).

    오래된 것이 먼저 온다. 기한이 없으므로 **경과 시간이 정렬의 축**이다 (§6.7.3).
    """
    rows = conn.execute(
        "SELECT * FROM ticket WHERE state IN (?, ?) ORDER BY opened_at",
        (str(State.OPEN), str(State.IN_PROGRESS)),
    ).fetchall()
    return [_from_row(r) for r in rows]


def for_qna(conn: sqlite3.Connection, qna_item_id: str) -> list[Ticket]:
    """이 QnA 가 낳은 티켓들. **하나가 아닐 수 있다** (FR-56).

    질문 하나가 답변도 필요하고, 코드 버그도 드러내고, 가이드 갱신도 요구할 수
    있다 — 1:1 구조로는 표현되지 않는다 (§6.4.1).
    """
    rows = conn.execute(
        "SELECT * FROM ticket WHERE qna_item_id = ? ORDER BY opened_at", (qna_item_id,)
    ).fetchall()
    return [_from_row(r) for r in rows]


def release_held_with_response(conn: sqlite3.Connection) -> list[str]:
    """외부 응답이 온 보류 티켓을 다시 연다 (§6.7.1).

    **사람이 다시 열지 않는다.** 보류는 "질문자에게 물어 두고 잊는" 상태이므로,
    응답을 알아채는 일까지 사람 몫이면 보류의 값이 사라진다 — 열어 볼 때마다
    다시 판단하지 않으려고 뺀 것을 다시 들여다봐야 하기 때문이다.

    응답의 신호는 **후속 답글**이다 (D9). **보류로 바뀐 뒤에 수집된 것만 센다** —
    그 전에 온 후속은 우리가 묻기도 전의 것이라 기다리던 응답이 아니다.

    비교의 양쪽을 모두 *우리* 시계로 맞췄다. 후속의 `created_at` 은 모 시스템의
    시계이므로 우리 `state_at` 과 섞으면 시계 차이만큼 어긋난다 — 폴링이 분 단위라
    `collected_at` 쪽의 오차가 더 작고 한계도 분명하다.
    """
    rows = conn.execute(
        "SELECT t.id FROM ticket t "
        "JOIN qna_item q ON q.id = t.qna_item_id "
        "WHERE t.state = ? AND EXISTS ("
        "  SELECT 1 FROM raw_followup f "
        "  WHERE f.question_id = q.parent_question_id AND f.collected_at > t.state_at"
        ")",
        (str(State.HELD),),
    ).fetchall()
    reopened = [row["id"] for row in rows]
    for ticket_id in reopened:
        transition(conn, ticket_id, State.OPEN)
    return reopened


def _from_row(row: sqlite3.Row) -> Ticket:
    return Ticket(
        id=row["id"],
        source=Source(row["source"]),
        state=State(row["state"]),
        opened_at=row["opened_at"],
        state_at=row["state_at"],
        qna_item_id=row["qna_item_id"],
        closed_at=row["closed_at"],
    )
