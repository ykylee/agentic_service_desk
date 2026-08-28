"""WBS-4.3.5 — Q1 티켓 대기열 (FR-45·46, D32, §8.6.3).

**시스템이 다음에 볼 것을 제시한다.** 여덟을 나란히 두면 1인 겸업은 매번 전부
훑어야 하고, 그러면 실제로 밀린 것이 그 사이에 묻힌다.

여기서 지키는 것은 셋.

    1. 정렬이 **방치 비용 × 경과 시간** 두 축이다 — 곱셈이라 **낮은 비용도 오래되면
       올라온다** (§8.2 의 "영원히 밀리는" 문제에 대한 대응이 그 형태 자체에 있다)
    2. **작업 화면은 판정 화면과 다르다** — 상세와 상태 전이가 있다 (FR-45)
    3. 무효화 조건을 채우는 행위가 곧 승인이고, **채우면 곧바로 닫힌다**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind
from agentic_service_desk.operations import manual_entry, resolution, ticket
from agentic_service_desk.operations.drafter import Drafter
from agentic_service_desk.operations.resolution import Ground, GroundKind
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.web.app import create_app
from agentic_service_desk.web.dashboard import QUEUES, Dashboard, WorkItem
from agentic_service_desk.knowledge.repository import KnowledgeRepository

from conftest import FakeHarness

DRAFT = """
{"generalized_question": "승인 한도는 무엇으로 결정되는가",
 "answer": "부서 등급으로 결정된다.",
 "grounding": [{"kind": "person", "ref": "인사팀 확인"}],
 "invalidation_candidates": [{"kind": "periodic", "period_days": 180},
                             {"kind": "linked", "refs": ["src/approval/limit.py"]}]}
"""


def _settings(tmp_path, **over):  # noqa: ANN001, ANN202
    base = dict(
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
        stage="S1",
    )
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _board(tmp_path, conn):  # noqa: ANN001, ANN202
    return Dashboard(repo=KnowledgeRepository(tmp_path / "knowledge"), conn=conn)


def _registered(conn, drafted: bool = True):  # noqa: ANN001
    r = manual_entry.register(
        conn, question="제 승인 한도가 왜 300만원인가요?", answer="부서 등급으로 정해집니다."
    )
    if drafted:
        Drafter(FakeHarness(DRAFT)).run(conn)
    return r


class TestOrdering:
    """D32 — 방치 비용 × 경과 시간."""

    def _item(self, queue_id: str, hours: float) -> WorkItem:
        opened = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        t = ticket.Ticket(
            id=f"t-{queue_id}",
            source=ticket.Source(QUEUES[queue_id].source),
            state=ticket.State.OPEN,
            opened_at=opened,
            state_at=opened,
        )
        return WorkItem(ticket=t, queue=QUEUES[queue_id], age_hours=hours)

    def test_같은_나이면_방치_비용이_높은_쪽이_먼저다(self) -> None:
        assert self._item("Q4", 10).score > self._item("Q3", 10).score

    def test_낮은_비용도_오래되면_올라온다(self) -> None:
        # **곱셈이라 그렇다.** 덧셈이면 낮은 비용은 아무리 오래돼도 넘지 못해
        # §8.2 가 걱정한 "영원히 밀리는" 문제가 남는다.
        assert self._item("Q3", 100).score > self._item("Q4", 10).score

    def test_다음에_볼_것이_점수_순이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        old = ticket.issue(conn, source=ticket.Source.CONTENT)
        conn.execute(
            "UPDATE ticket SET opened_at = ? WHERE id = ?",
            ((datetime.now(UTC) - timedelta(days=10)).isoformat(), old.id),
        )
        conn.commit()
        _registered(conn)  # 방금 만든 Q1 티켓

        ranked = _board(tmp_path, conn).next_up("S4")
        assert ranked[0].ticket.id == old.id  # 낮은 비용이지만 열흘 묵었다

    def test_켜지지_않은_단계의_것은_빠진다(self, tmp_path) -> None:
        # FR-59 는 목록만이 아니라 제안에도 걸린다.
        conn = _conn(tmp_path)
        ticket.issue(conn, source=ticket.Source.CONTENT)  # Q3 — S4 부터
        assert _board(tmp_path, conn).next_up("S1") == []

    def test_대기열_정의에_방치_비용_수치가_있다(self) -> None:
        assert QUEUES["Q4"].weight > QUEUES["Q8"].weight


class TestWorkScreenIsDifferent:
    """FR-45 — 작업은 상세와 상태 전이, 판정은 목록과 버튼."""

    def test_대기열_종류가_구분된다(self) -> None:
        assert QUEUES["Q1"].kind == "작업"
        assert QUEUES["Q8"].kind == "판정"

    def test_상세가_원문과_초안을_함께_준다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn)
        d = _board(tmp_path, conn).ticket_detail(r.ticket_id)

        assert "300만원" in d.entry.question
        assert d.resolution.generalized_question == "승인 한도는 무엇으로 결정되는가"

    def test_지금_할_일을_말해_준다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn, drafted=False)
        assert "초안" in _board(tmp_path, conn).ticket_detail(r.ticket_id).next_step

        Drafter(FakeHarness(DRAFT)).run(conn)
        assert "무효화 조건" in _board(tmp_path, conn).ticket_detail(r.ticket_id).next_step

    def test_닫힌_뒤에는_다음_행선지를_말한다(self, tmp_path) -> None:
        # "닫을 수 있다" 를 계속 말하면 화면이 거짓말을 한다.
        conn = _conn(tmp_path)
        r = _registered(conn)
        resolution.confirm(
            conn, r.ticket_id,
            invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90),
        )
        ticket.transition(conn, r.ticket_id, ticket.State.CLOSED)

        assert "승격" in _board(tmp_path, conn).ticket_detail(r.ticket_id).next_step

    def test_보류_중이면_기다리는_중이라고_말한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn)
        ticket.transition(conn, r.ticket_id, ticket.State.HELD)
        assert "응답을 기다린다" in _board(tmp_path, conn).ticket_detail(r.ticket_id).next_step

    def test_없는_티켓은_None_이다(self, tmp_path) -> None:
        assert _board(tmp_path, _conn(tmp_path)).ticket_detail("t-없음") is None


class TestScreens:
    def test_목록이_뜬다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn)
        conn.close()

        html = TestClient(create_app(_settings(tmp_path))).get("/queues/Q1").text
        assert r.ticket_id in html

    def test_상세가_초안과_후보를_보여준다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn)
        conn.close()

        html = TestClient(create_app(_settings(tmp_path))).get(f"/queues/Q1/{r.ticket_id}").text
        assert "승인 한도는 무엇으로 결정되는가" in html
        assert "180일마다 재확인" in html
        assert "무효화 조건이 유일한 stale 장치다" in html  # 근거가 person 이라

    def test_상태를_바꾼다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn)
        conn.close()

        client = TestClient(create_app(_settings(tmp_path)))
        client.post(f"/queues/Q1/{r.ticket_id}/state", data={"to": "in_progress"})
        assert ticket.get(_conn(tmp_path), r.ticket_id).state is ticket.State.IN_PROGRESS

    def test_종결_버튼은_상태_전이에_없다(self, tmp_path) -> None:
        # 닫는 길은 무효화 조건을 채우는 것 하나뿐이다.
        conn = _conn(tmp_path)
        r = _registered(conn)
        conn.close()

        html = TestClient(create_app(_settings(tmp_path))).get(f"/queues/Q1/{r.ticket_id}").text
        assert 'value="closed"' not in html


class TestConfirmClosesTheLoop:
    """등록 → 초안 → 무효화 조건 → 종결. **채우는 행위가 곧 승인이다.**"""

    def test_후보를_고르면_닫힌다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn)
        conn.close()

        client = TestClient(create_app(_settings(tmp_path)))
        client.post(f"/queues/Q1/{r.ticket_id}/confirm", data={"choice": "0"})

        conn = _conn(tmp_path)
        assert resolution.get(conn, r.ticket_id).invalidation.period_days == 180
        assert ticket.get(conn, r.ticket_id).state is ticket.State.CLOSED

    def test_직접_쓴_연결형도_받는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn)
        conn.close()

        TestClient(create_app(_settings(tmp_path))).post(
            f"/queues/Q1/{r.ticket_id}/confirm",
            data={"choice": "custom", "kind": "linked", "refs": "src/a.py, src/b.py"},
        )
        inv = resolution.get(_conn(tmp_path), r.ticket_id).invalidation
        assert inv.kind is InvalidationKind.LINKED
        assert inv.refs == ("src/a.py", "src/b.py")

    def test_연결형인데_대상이_없으면_막고_이유를_말한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn)
        conn.close()

        html = TestClient(create_app(_settings(tmp_path))).post(
            f"/queues/Q1/{r.ticket_id}/confirm",
            data={"choice": "custom", "kind": "linked", "refs": "  "},
        ).text
        assert "묶을 대상이 필요하다" in html
        assert ticket.get(_conn(tmp_path), r.ticket_id).state is ticket.State.OPEN

    def test_주기형인데_주기가_없으면_막는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn)
        conn.close()

        html = TestClient(create_app(_settings(tmp_path))).post(
            f"/queues/Q1/{r.ticket_id}/confirm",
            data={"choice": "custom", "kind": "periodic", "period_days": "0"},
        ).text
        assert "재확인 주기" in html

    def test_닫힌_뒤에는_승격_자격이_생긴다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn)
        conn.close()
        TestClient(create_app(_settings(tmp_path))).post(
            f"/queues/Q1/{r.ticket_id}/confirm", data={"choice": "1"}
        )
        assert resolution.get(_conn(tmp_path), r.ticket_id).promotable

    def test_닫힌_티켓은_대기열에서_빠진다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _registered(conn)
        conn.close()
        client = TestClient(create_app(_settings(tmp_path)))
        client.post(f"/queues/Q1/{r.ticket_id}/confirm", data={"choice": "0"})

        assert "열린 티켓이 없다" in client.get("/queues/Q1").text
