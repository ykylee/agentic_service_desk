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
        return WorkItem(
            queue=QUEUES[queue_id],
            ref=t.id,
            href=f"/queues/Q1/{t.id}",
            label=QUEUES[queue_id].title,
            age_hours=hours,
            ticket=t,
        )

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


class TestJudgementQueuesRank:
    """판정 대기열도 같은 자에 오른다 (§8.2, D32, FR-46).

    §8.2 가 "영원히 밀린다"고 걱정한 것은 Q6·Q7·Q8 이다 — 방치해도 사고가 나지
    않아서 밀리고, 밀리면 **아무 신호 없이 지식이 자라기를 멈춘다.** 곱셈 순위가
    그 대응인데 티켓 있는 것만 세우면 대응이 정작 그쪽에 닿지 않는다.

    여기서 지키는 것은 셋.

        1. 티켓 없는 대기열도 순위에 **오른다**
        2. 판정은 **대기열당 한 줄**이다 — 목록 화면 하나를 가리키므로 여럿을
           올리면 같은 곳으로 가는 줄이 다섯 자리를 채운다
        3. FR-59 는 여기에도 걸린다 — 켜지지 않은 단계의 판정은 빠진다
    """

    def _qna(self, conn, qid: str, *, state: str, grade: str | None, days: float):  # noqa: ANN001, ANN202
        when = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        conn.execute(
            "INSERT INTO raw_question (id, body, asker_account, created_at, collected_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (qid, f"{qid} 의 질문 원문", "u-1", when, when),
        )
        conn.execute(
            "INSERT INTO qna_item (id, parent_question_id, state, resolution_grade, "
            " opened_at, closed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"qi-{qid}", qid, state, grade, when, when),
        )
        conn.commit()

    def _gap(self, conn, qid: str, days: float) -> None:  # noqa: ANN001
        from agentic_service_desk.operations import qna_state

        self._qna(conn, qid, state=qna_state.UNRESOLVED_CLOSED, grade=None, days=days)

    def _implicit(self, conn, qid: str, days: float) -> None:  # noqa: ANN001
        from agentic_service_desk.operations import qna_state

        self._qna(
            conn, qid, state=qna_state.RESOLVED, grade=qna_state.IMPLICIT, days=days
        )

    def test_티켓_없는_판정도_순위에_오른다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._gap(conn, "Q-old", days=60)
        _registered(conn)  # 방금 만든 Q1 티켓 (방치 비용 높음)

        ranked = _board(tmp_path, conn).next_up("S3")
        assert [i.queue.id for i in ranked][0] == "Q8"  # 낮은 비용이지만 두 달 묵었다

    def test_판정은_대기열당_한_줄이다(self, tmp_path) -> None:
        # 다섯 자리를 같은 목록으로 가는 줄로 채우면 순위가 아무것도 고르지 못한
        # 것과 같고, 각자 다른 화면을 가진 작업 항목이 그 뒤로 밀려난다.
        conn = _conn(tmp_path)
        for n in range(4):
            self._gap(conn, f"Q-{n}", days=10 + n)

        rows = [i for i in _board(tmp_path, conn).next_up("S3") if i.queue.id == "Q8"]
        assert len(rows) == 1
        assert rows[0].backlog == 3  # 나머지는 수로 말한다
        assert rows[0].ref == "qi-Q-3"  # 오르는 것은 **가장 오래된 건**이다
        assert rows[0].href == "/queues/Q8"  # 판정에는 상세 화면이 없다 (FR-45)

    def test_같은_나이의_판정끼리는_방치_비용이_가른다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._gap(conn, "Q-gap", days=5)
        self._implicit(conn, "Q-imp", days=5)

        ranked = _board(tmp_path, conn).next_up("S3")
        assert {i.queue.id for i in ranked} == {"Q6", "Q8"}

    def test_켜지지_않은_단계의_판정은_빠진다(self, tmp_path) -> None:
        # FR-59 — S1 에는 Q6 가 없다. 목록에 없는 것이 순위에 뜨면 갈 곳이 없다.
        conn = _conn(tmp_path)
        self._implicit(conn, "Q-imp", days=30)
        assert _board(tmp_path, conn).next_up("S1") == []

    def test_무엇에_대한_것인지_함께_준다(self, tmp_path) -> None:
        # id 만 늘어놓으면 열어 보기 전에는 알 수 없다.
        conn = _conn(tmp_path)
        self._gap(conn, "Q-1", days=3)
        assert "질문 원문" in _board(tmp_path, conn).next_up("S3")[0].label

    def test_Q2_초안도_오른다(self, tmp_path) -> None:
        # **방치 비용이 높다** — 사람이 답을 기다리고 있고, 밀리면 그 지연이 곧
        # 채택 실패다 (W4, §8.6.3).
        from agentic_service_desk.pipeline.answer import Confidence, Draft, Statement
        from agentic_service_desk.pipeline import draft_store

        conn = _conn(tmp_path)
        draft_store.save(
            conn,
            question="결재 한도는 무엇으로 정해지나요?",
            draft=Draft(
                statements=(Statement("부서 등급으로 정해진다.", Confidence.CONFIRMED, ("k-1",)),),
                grounding=("k-1",),
            ),
        )
        conn.execute(
            "UPDATE answer_draft SET created_at = ?",
            ((datetime.now(UTC) - timedelta(days=2)).isoformat(),),
        )
        conn.commit()

        rows = [i for i in _board(tmp_path, conn).next_up("S3") if i.queue.id == "Q2"]
        assert len(rows) == 1
        assert rows[0].href == "/queues/Q2"
        assert "결재 한도" in rows[0].label

    def test_Q7_은_자동_종결_시각부터_잰다(self, tmp_path) -> None:
        # 발행 시각이 아니다 — 자동 처리가 끝나야 승격 판정거리가 되므로, 그
        # 전까지 세면 파이프라인이 오래 걸린 건이 더 밀린 것처럼 보인다.
        conn = _conn(tmp_path)
        self._implicit(conn, "Q-p", days=30)
        opened = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        closed = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        t = ticket.issue(
            conn, source=ticket.Source.QNA, qna_item_id="qi-Q-p", state=ticket.State.AUTO_CLOSED
        )
        conn.execute(
            "UPDATE ticket SET opened_at = ?, state_at = ? WHERE id = ?",
            (opened, closed, t.id),
        )
        conn.commit()
        resolution.draft(
            conn,
            ticket_id=t.id,
            generalized_question="결재 한도는 무엇으로 결정되는가",
            answer="부서 등급으로 결정된다.",
            grounding=(Ground(kind=GroundKind.CODE, ref="src/approval/limit.py"),),
        )

        rows = [i for i in _board(tmp_path, conn).next_up("S3") if i.queue.id == "Q7"]
        assert len(rows) == 1
        assert 9 * 24 < rows[0].age_hours < 11 * 24  # 열흘 — 서른 날이 아니다
