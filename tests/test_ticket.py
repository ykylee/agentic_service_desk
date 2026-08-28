"""WBS-4.3.1 — 티켓 도메인 (FR-12·56, D15·D33, §6.7).

**상태 집합은 조직 규모가 아니라 작업 리듬을 따른다.** 1인 겸업이면 상태를 줄여도
될 것 같지만 겸업이라는 점이 반대로 작용한다 — 접속이 띄엄띄엄하기 때문에
"진행 중"(자기 자신에게 남기는 표시)과 "보류"(재판단을 피하는 표시)가 필요하다.

여기서 지키는 것은 넷.

    1. **다섯 상태뿐이고 담당자·마감·우선순위가 없다** (FR-12)
    2. 허용되지 않은 전이는 거부된다. **닫힌 티켓은 되살아나지 않는다**
    3. **QnA 항목과 별개다** — 하나가 여러 티켓을 낳고 한쪽이 닫혀도 다른 쪽은 열려 있다 (FR-56)
    4. **보류는 대기열에서 빠지고, 응답이 오면 사람 없이 다시 열린다** (§6.7.1)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic_service_desk.adapters.mock import MockParentSystem
from agentic_service_desk.ingest.qna import QnaCollector
from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind
from agentic_service_desk.operations import resolution, ticket
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.operations.ticket import (
    InvalidTransition,
    ResolutionRequired,
    Source,
    State,
)


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _qna(conn, qna_id: str = "q-1", question_id: str = "Q-1") -> str:  # noqa: ANN001
    """QnA 항목 하나. 티켓이 그것을 가리키려면 먼저 있어야 한다 (외래키)."""
    conn.execute(
        "INSERT INTO qna_item (id, parent_question_id, state, opened_at) "
        "VALUES (?, ?, '접수', '2026-08-28')",
        (qna_id, question_id),
    )
    conn.commit()
    return qna_id


def _resolution(conn, ticket_id: str) -> None:  # noqa: ANN001
    """닫아도 되는 종결 기록. **초안을 만들고 사람이 무효화 조건을 채운다** (§5.6.4)."""
    resolution.draft(
        conn,
        ticket_id=ticket_id,
        generalized_question="권한 요청은 어디서 하는가",
        answer="설정 > 권한에서 신청한다.",
        grounding=[resolution.Ground(kind=resolution.GroundKind.PERSON, ref="담당자 확인")],
    )
    resolution.confirm(
        conn,
        ticket_id,
        invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=180),
    )


class TestShape:
    """FR-12 — 있는 것과 **없는 것** 둘 다 요구사항이다."""

    def test_다섯_상태뿐이다(self) -> None:
        assert {str(s) for s in State} == {
            "auto_closed", "open", "in_progress", "held", "closed"
        }

    def test_담당자_마감_우선순위_필드가_없다(self, tmp_path) -> None:
        # 받을 사람이 하나뿐이고, 기한을 걸지 않고, 정렬은 방치 비용 × 경과 시간이다.
        cols = {
            r["name"] for r in _conn(tmp_path).execute("PRAGMA table_info(ticket)")
        }
        assert not cols & {"assignee", "owner", "due_at", "deadline", "priority", "sla"}

    def test_출처가_넷이다(self) -> None:
        # QnA 만이 아니라는 것이 티켓을 별개 엔터티로 둔 이유다 (§6.4.1).
        assert {str(s) for s in Source} == {"qna", "content", "contradiction", "correction"}


class TestIssue:
    def test_발행하면_열려_있다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA, qna_item_id=_qna(conn))
        assert t.state is State.OPEN
        assert t.in_queue

    def test_자동_종결은_대기열에_뜨지_않는다(self, tmp_path) -> None:
        # 발행과 대기열 진입은 다른 일이다 (§6.4.3-1).
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA, state=State.AUTO_CLOSED)
        assert not t.in_queue
        assert ticket.queue(conn) == []

    def test_자동_종결도_기록으로는_남는다(self, tmp_path) -> None:
        # 자동 처리는 얇게, 수동 처리는 두껍게 남는 비대칭이 없어야 한다.
        conn = _conn(tmp_path)
        ticket.issue(conn, source=Source.QNA, qna_item_id=_qna(conn), state=State.AUTO_CLOSED)
        assert len(ticket.for_qna(conn, "q-1")) == 1

    def test_QnA_밖_출처는_qna_id_가_비어_있다(self, tmp_path) -> None:
        t = ticket.issue(_conn(tmp_path), source=Source.CONTRADICTION)
        assert t.qna_item_id is None


class TestTransitions:
    def test_열림에서_진행중으로_간다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA)
        assert ticket.transition(conn, t.id, State.IN_PROGRESS).state is State.IN_PROGRESS

    def test_진행중에서_열림으로_되돌릴_수_있다(self, tmp_path) -> None:
        # 보다 말고 손을 뗄 수 있다 — 겸업이므로 실제로 일어난다.
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA)
        ticket.transition(conn, t.id, State.IN_PROGRESS)
        assert ticket.transition(conn, t.id, State.OPEN).state is State.OPEN

    def test_종결은_종점이다(self, tmp_path) -> None:
        # 같은 질문이 또 오면 그것은 **새로운 처리**이고, QnA 하나가 여러 티켓을
        # 낳을 수 있다는 것이 그 자리를 이미 마련해 뒀다. 되살리면 한 티켓에 여러
        # 처리가 섞여 통계가 무너진다.
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA)
        _resolution(conn, t.id)
        ticket.transition(conn, t.id, State.CLOSED)

        with pytest.raises(InvalidTransition):
            ticket.transition(conn, t.id, State.OPEN)

    def test_자동_종결도_종점이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA, state=State.AUTO_CLOSED)
        with pytest.raises(InvalidTransition):
            ticket.transition(conn, t.id, State.OPEN)

    def test_거부_메시지가_갈_수_있는_곳을_알려준다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA, state=State.AUTO_CLOSED)
        with pytest.raises(InvalidTransition, match="종점"):
            ticket.transition(conn, t.id, State.IN_PROGRESS)

    def test_닫는_시각이_남는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA)
        _resolution(conn, t.id)
        assert ticket.transition(conn, t.id, State.CLOSED).closed_at


class TestClosingNeedsARecord:
    """§6.4.5 — "완료" 체크로 닫는 순간 승격할 재료가 사라진다."""

    def test_종결_기록이_없으면_닫히지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA)
        with pytest.raises(ResolutionRequired):
            ticket.transition(conn, t.id, State.CLOSED)
        assert ticket.get(conn, t.id).state is State.OPEN

    def test_기록이_있으면_닫힌다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA)
        _resolution(conn, t.id)
        assert ticket.transition(conn, t.id, State.CLOSED).state is State.CLOSED


class TestIndependentFromQna:
    """FR-56 — 상태가 서로를 결정하지 않는다."""

    def test_QnA_하나가_여러_티켓을_낳는다(self, tmp_path) -> None:
        # 질문 하나가 답변도 필요하고, 코드 버그도 드러내고, 가이드 갱신도 요구할 수 있다.
        conn = _conn(tmp_path)
        _qna(conn)
        ticket.issue(conn, source=Source.QNA, qna_item_id="q-1")
        ticket.issue(conn, source=Source.QNA, qna_item_id="q-1")

        assert len(ticket.for_qna(conn, "q-1")) == 2

    def test_한쪽이_닫혀도_다른_쪽은_열려_있다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _qna(conn)
        first = ticket.issue(conn, source=Source.QNA, qna_item_id="q-1")
        second = ticket.issue(conn, source=Source.QNA, qna_item_id="q-1")
        _resolution(conn, first.id)
        ticket.transition(conn, first.id, State.CLOSED)

        states = {t.state for t in ticket.for_qna(conn, "q-1")}
        assert states == {State.CLOSED, State.OPEN}
        assert ticket.queue(conn) == [ticket.get(conn, second.id)]


class TestHeld:
    """§6.7.1·6.7.2 — 겸업에게 가장 비싼 것은 반복되는 재판단이다."""

    def _with_qna(self, tmp_path):  # noqa: ANN001, ANN202
        conn = _conn(tmp_path)
        QnaCollector(MockParentSystem(), conn).collect()
        _qna(conn, "q-5", "Q-5")
        return conn

    def test_보류는_대기열에서_빠진다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA)
        ticket.transition(conn, t.id, State.HELD)

        assert not ticket.get(conn, t.id).in_queue
        assert ticket.queue(conn) == []

    def test_응답이_오면_사람_없이_다시_열린다(self, tmp_path) -> None:
        # 응답을 알아채는 일까지 사람 몫이면 보류의 값이 사라진다.
        conn = self._with_qna(tmp_path)
        t = ticket.issue(conn, source=Source.QNA, qna_item_id="q-5")
        ticket.transition(conn, t.id, State.HELD)

        # Q-5 에는 시드에 후속이 하나 있다. 보류로 바뀐 뒤 다시 수집되게 한다.
        conn.execute("UPDATE raw_followup SET collected_at = ? WHERE question_id = 'Q-5'",
                     (datetime.now(UTC).isoformat(),))
        conn.commit()

        assert ticket.release_held_with_response(conn) == [t.id]
        assert ticket.get(conn, t.id).state is State.OPEN

    def test_보류_전에_온_후속은_기다리던_응답이_아니다(self, tmp_path) -> None:
        # opened_at 으로 재면 이것까지 센다 — 우리가 묻기도 전의 후속이다.
        conn = self._with_qna(tmp_path)
        t = ticket.issue(conn, source=Source.QNA, qna_item_id="q-5")
        ticket.transition(conn, t.id, State.HELD)

        old = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        conn.execute("UPDATE raw_followup SET collected_at = ? WHERE question_id = 'Q-5'", (old,))
        conn.commit()

        assert ticket.release_held_with_response(conn) == []
        assert ticket.get(conn, t.id).state is State.HELD

    def test_후속이_없으면_보류인_채로_있다(self, tmp_path) -> None:
        conn = self._with_qna(tmp_path)
        conn.execute("DELETE FROM raw_followup")
        conn.commit()
        t = ticket.issue(conn, source=Source.QNA, qna_item_id="q-5")
        ticket.transition(conn, t.id, State.HELD)

        assert ticket.release_held_with_response(conn) == []


class TestAge:
    """§6.7.3 — 경과 시간이 SLA 를 대신한다. 약속이 아니라 관측이다."""

    def test_열린_시각에서_파생된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = ticket.issue(conn, source=Source.QNA)
        later = datetime.now(UTC) + timedelta(hours=30)
        assert 29 < t.age(now=later) < 31

    def test_대기열은_오래된_것부터다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        first = ticket.issue(conn, source=Source.QNA)
        second = ticket.issue(conn, source=Source.CONTENT)
        assert [t.id for t in ticket.queue(conn)] == [first.id, second.id]
