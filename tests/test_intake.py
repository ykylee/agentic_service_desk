"""WBS-4.5.2 — 전건 티켓 발행 (FR-27, D19, §6.4.3-1).

유입된 질문이 처리 단위를 갖는다. 여기서 지키는 것은 여섯.

    1. **모든 QnA 가 티켓을 발행한다** — 자동 처리 건도 (FR-27)
    2. **발행과 대기열 진입은 다른 일이다** — 자동 처리분은 Q1 에 뜨지 않는다
    3. **멈춘 건은 사람의 대기열로 수렴한다** (§5.1)
    4. **두 번 들이지 않는다** — 재실행이 통계를 부풀리지 않는다
    5. **경과 시간은 물은 시각부터 잰다** — 폴링 주기가 섞이지 않는다 (§6.7.3)
    6. **자동 처리 건에도 종결 기록이 있다** — 승격할 형식이 존재하게 된다
"""

from __future__ import annotations

import json
import sqlite3

from agentic_service_desk.ingest.qna import QnaStore
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.search import Search
from agentic_service_desk.operations import intake, resolution as resolution_domain
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline import draft_store
from agentic_service_desk.pipeline.answer import AnswerPipeline
from agentic_service_desk.pipeline.review import Reviewer

from conftest import FakeHarness

SOURCE = "결재 승인 한도는 신청자의 부서 등급으로 결정된다."
ASKED_AT = "2026-08-20T09:00:00+00:00"


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _raw_question(
    conn: sqlite3.Connection,
    qid: str = "Q-1",
    body: str = "결재 승인 한도는 어떻게 정해지나요?",
    *,
    created_at: str = ASKED_AT,
) -> None:
    """수집이 Raw Layer 에 담아 둔 상태를 만든다."""
    conn.execute(
        "INSERT INTO raw_question (id, title, body, asker_account, created_at, collected_at) "
        "VALUES (?, NULL, ?, ?, ?, ?)",
        (qid, body, "emp-100", created_at, "2026-08-28T00:00:00+00:00"),
    )
    conn.commit()


def _item(tmp_path) -> KnowledgeItem:
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    item = KnowledgeItem(
        title="결재 승인 한도가 결정되는 규칙",
        body=SOURCE,
        provenance=[Provenance(commit="a" * 40, path="approval/limit.py")],
        invalidation=Invalidation(
            kind=InvalidationKind.LINKED, refs=("approval/limit.py",)
        ),
    )
    repo.save(item)
    repo.commit("시험용 지식 항목")
    return item


def _pipeline(tmp_path, conn, item_id: str, *, harness=None):  # noqa: ANN001, ANN202
    return AnswerPipeline(
        search=Search(repo=KnowledgeRepository(tmp_path / "knowledge"), conn=conn),
        conn=conn,
        harness=harness
        or FakeHarness(
            json.dumps(
                {
                    "answerable": True,
                    "statements": [
                        {
                            "text": "결재 승인 한도는 부서 등급으로 결정됩니다.",
                            "confidence": "확인됨",
                            "grounding": [item_id],
                        }
                    ],
                    "unanswered": [],
                },
                ensure_ascii=False,
            )
        ),
    )


class TestEveryQnaGetsTicket:
    """FR-27 — 모든 QnA 가 티켓을 발행한다."""

    def test_초안이_나오면_자동_종결된다(self, tmp_path) -> None:
        """**발행과 대기열 진입은 다른 일이다** (§6.4.3-1).

        파이프라인이 제 몫을 끝냈으므로 티켓은 기록으로만 남는다. 남은 판정은
        Q2 가 들고 있고, 여기서 Q1 에도 띄우면 같은 일이 두 대기열에 뜬다.
        """
        conn = _conn(tmp_path)
        item = _item(tmp_path)
        _raw_question(conn)

        report = intake.run(conn, pipeline=_pipeline(tmp_path, conn, item.id))
        assert len(report.admitted) == 1
        admission = report.admitted[0]
        assert admission.ticket_state is ticket_domain.State.AUTO_CLOSED
        assert admission.draft_id is not None

        # 티켓은 있고, Q1 에는 없다.
        assert ticket_domain.get(conn, admission.ticket_id).in_queue is False
        assert ticket_domain.queue(conn) == []
        # 초안은 Q2 에 있다.
        assert [d.id for d in draft_store.pending(conn)] == [admission.draft_id]
        conn.close()

    def test_멈추면_사람의_대기열로_간다(self, tmp_path) -> None:
        """§5.1 — 실패는 사람의 대기열로 수렴한다.

        근거가 0건이면 생성 단계에 가지도 않는다 (FR-18). 그때 답이 없는 것이
        아니라 **사람이 볼 일이 하나 생긴 것**이다.
        """
        conn = _conn(tmp_path)
        _item(tmp_path)  # 있지만 질문과 겹치지 않는다
        _raw_question(conn, body="회의실 예약은 어디서 하나요?")

        report = intake.run(
            conn, pipeline=_pipeline(tmp_path, conn, "k-none")
        )
        admission = report.admitted[0]
        assert admission.ticket_state is ticket_domain.State.OPEN
        assert admission.draft_id is None
        assert [t.id for t in ticket_domain.queue(conn)] == [admission.ticket_id]
        conn.close()

    def test_파이프라인이_없어도_발행한다(self, tmp_path) -> None:
        """설정이 아직 비어 있어도 **질문이 왔다는 사실은 남는다.**

        유입 자체가 W4(질문이 기록되지 않는다) 관측의 재료다 (§1.4.6).
        """
        conn = _conn(tmp_path)
        _raw_question(conn)
        report = intake.run(conn)
        assert report.admitted[0].ticket_state is ticket_domain.State.OPEN
        assert len(ticket_domain.queue(conn)) == 1
        conn.close()

    def test_수집한_전건이_들어온다(self, tmp_path) -> None:
        """**Raw Layer 를 본다** — 거기에 전건이 있다 (FR-52).

        봇이 답한 미해결 질문도 처리 단위는 가져야 한다. ingest 입구에서 빠지는
        것과 처리되지 않는 것은 다르다.
        """
        from agentic_service_desk.adapters.mock import MockParentSystem
        from agentic_service_desk.ingest.qna import QnaCollector

        conn = _conn(tmp_path)
        QnaCollector(MockParentSystem(), conn).collect()
        collected = conn.execute("SELECT count(*) c FROM raw_question").fetchone()["c"]

        report = intake.run(conn)
        assert len(report.admitted) == collected == 5
        assert conn.execute("SELECT count(*) c FROM ticket").fetchone()["c"] == 5
        conn.close()


class TestIdempotence:
    """재실행이 통계를 부풀리지 않는다."""

    def test_두_번_들이지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _raw_question(conn)
        assert len(intake.run(conn).admitted) == 1
        assert intake.run(conn).admitted == []
        assert conn.execute("SELECT count(*) c FROM qna_item").fetchone()["c"] == 1
        assert conn.execute("SELECT count(*) c FROM ticket").fetchone()["c"] == 1
        conn.close()

    def test_스키마가_중복_유입을_거부한다(self, tmp_path) -> None:
        # `qna_item.parent_question_id` 의 UNIQUE 가 함께 지킨다.
        conn = _conn(tmp_path)
        _raw_question(conn)
        intake.run(conn)
        try:
            conn.execute(
                "INSERT INTO qna_item (id, parent_question_id, origin, state, opened_at) "
                "VALUES ('q-dup', 'Q-1', 'parent', '접수', '2026-08-28T00:00:00Z')"
            )
        except sqlite3.IntegrityError:
            pass
        else:  # pragma: no cover
            raise AssertionError("중복 유입이 통과했다")
        conn.close()


class TestAge:
    """§6.7.3 — 경과 시간이 SLA 를 대신한다."""

    def test_물은_시각부터_잰다(self, tmp_path) -> None:
        """수집 시각이 아니다.

        우리 폴링 주기가 경과 시간에 섞이면 **늦게 가져온 것이 방금 온 것처럼
        보여** 오래된 질문이 대기열 아래로 숨는다.
        """
        conn = _conn(tmp_path)
        _raw_question(conn, created_at=ASKED_AT)
        intake.run(conn)
        opened = conn.execute("SELECT opened_at FROM qna_item").fetchone()["opened_at"]
        assert opened == ASKED_AT
        conn.close()


class TestRejectionOpensNewTicket:
    """§6.7.1 — 닫힌 티켓을 되살리지 않는다."""

    def test_반려는_새_티켓을_연다(self, tmp_path) -> None:
        """자동 처리 한 번과 사람 처리 한 번은 **두 번의 처리**다.

        되살리면 한 티켓에 여러 처리가 섞여 통계가 무너진다. FR-56 이 "QnA 하나가
        여러 티켓을 낳을 수 있다"고 한 자리가 여기다.
        """
        conn = _conn(tmp_path)
        item = _item(tmp_path)
        _raw_question(conn)
        admission = intake.run(
            conn, pipeline=_pipeline(tmp_path, conn, item.id)
        ).admitted[0]

        reopened = intake.reopen_for_rejected_draft(conn, admission.qna_item_id)
        assert reopened != admission.ticket_id
        assert ticket_domain.get(conn, admission.ticket_id).state is (
            ticket_domain.State.AUTO_CLOSED
        )
        assert [t.id for t in ticket_domain.queue(conn)] == [reopened]
        conn.close()


class TestReviewRuns:
    """§5.1 — 검수 단계 자체는 필수다."""

    def test_검수기가_있으면_판정이_붙는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        item = _item(tmp_path)
        _raw_question(conn)
        verdict_json = json.dumps(
            {"passed": True, "reason": None, "detail": "근거와 일치한다"},
            ensure_ascii=False,
        )
        intake.run(
            conn,
            pipeline=_pipeline(tmp_path, conn, item.id),
            reviewer=Reviewer(FakeHarness(verdict_json)),
        )
        assert draft_store.pending(conn)[0].agent_outcome == "passed"
        conn.close()

    def test_검수기가_없으면_판정을_비워_둔다(self, tmp_path) -> None:
        """**검수가 없는 것과 통과한 것은 다르다** (§5.6.1).

        비워 두지 않고 통과로 적으면 "검증했다"는 라벨이 남는데, 형식적 승인은
        무검증보다 나쁘다.
        """
        conn = _conn(tmp_path)
        item = _item(tmp_path)
        _raw_question(conn)
        intake.run(conn, pipeline=_pipeline(tmp_path, conn, item.id))
        assert draft_store.pending(conn)[0].agent_outcome is None
        conn.close()


class TestResolutionForAutoHandled:
    """FR-27 확인 — 자동 처리 건에도 종결 기록이 있다."""

    def _published(self, tmp_path, conn, item):  # noqa: ANN001, ANN202
        from agentic_service_desk.adapters.mock import BOT_ACCOUNT, MockParentSystem
        from agentic_service_desk.pipeline import publication

        admission = intake.run(
            conn, pipeline=_pipeline(tmp_path, conn, item.id)
        ).admitted[0]
        draft_store.decide(conn, admission.draft_id, approved=True)
        result = publication.publish(
            conn,
            MockParentSystem(),
            admission.draft_id,
            bot_accounts=frozenset({BOT_ACCOUNT}),
            repo=KnowledgeRepository(tmp_path / "knowledge"),
        )
        assert isinstance(result, publication.Published)
        return admission

    def test_게재된_자동_처리_건에_종결_기록이_생긴다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        item = _item(tmp_path)
        _raw_question(conn)
        admission = self._published(tmp_path, conn, item)

        report = intake.draft_resolutions(
            conn,
            FakeHarness(
                json.dumps(
                    {
                        "generalized_question": "결재 승인 한도는 무엇으로 결정되는가",
                        "answer": "신청자의 부서 등급으로 결정된다.",
                        "invalidation_candidates": [
                            {"kind": "linked", "refs": ["approval/limit.py"]}
                        ],
                    },
                    ensure_ascii=False,
                )
            ),
            KnowledgeRepository(tmp_path / "knowledge"),
        )
        assert report.drafted == [admission.ticket_id]

        record = resolution_domain.get(conn, admission.ticket_id)
        assert record is not None
        # **무효화 조건은 비어 있는 것이 초안의 정상 상태다** (§5.6.4).
        assert record.invalidation is None
        assert record.invalidation_candidates
        conn.close()

    def test_근거는_모델이_아니라_코드가_정한다(self, tmp_path) -> None:
        """**출처를 모델에게 묻지 않는다** (FR-4).

        어느 항목을 썼는지 우리가 이미 알고, 그 항목이 자기 출처를 들고 있다.
        지식 항목이 아니라 **그 항목의 출처**를 적으므로 코드에 직접 닿는다.
        """
        conn = _conn(tmp_path)
        item = _item(tmp_path)
        _raw_question(conn)
        admission = self._published(tmp_path, conn, item)

        intake.draft_resolutions(
            conn,
            FakeHarness(
                json.dumps(
                    {
                        "generalized_question": "결재 승인 한도는 무엇으로 결정되는가",
                        "answer": "신청자의 부서 등급으로 결정된다.",
                        # 모델이 지어낸 근거를 보내도 값이 되지 않는다.
                        "grounding": [{"kind": "person", "ref": "누가 그랬다"}],
                    },
                    ensure_ascii=False,
                )
            ),
            KnowledgeRepository(tmp_path / "knowledge"),
        )
        record = resolution_domain.get(conn, admission.ticket_id)
        refs = {g.ref for g in record.grounding}
        assert "approval/limit.py" in refs
        assert "누가 그랬다" not in refs
        # 코드에 닿으므로 stale 자동 판정이 성립한다 (§6.5.3).
        assert record.code_traceable
        conn.close()

    def test_게재되지_않은_건에는_만들지_않는다(self, tmp_path) -> None:
        """반려된 초안은 **그 답이 틀렸다고 판정된 것**이다.

        기록으로 남기면 틀린 답이 승격 후보로 줄을 선다.
        """
        conn = _conn(tmp_path)
        item = _item(tmp_path)
        _raw_question(conn)
        intake.run(conn, pipeline=_pipeline(tmp_path, conn, item.id))
        assert intake.awaiting_resolution_draft(conn) == []
        conn.close()

    def test_두_번_만들지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        item = _item(tmp_path)
        _raw_question(conn)
        self._published(tmp_path, conn, item)
        payload = json.dumps(
            {
                "generalized_question": "결재 승인 한도는 무엇으로 결정되는가",
                "answer": "신청자의 부서 등급으로 결정된다.",
            },
            ensure_ascii=False,
        )
        repo = KnowledgeRepository(tmp_path / "knowledge")
        assert intake.draft_resolutions(conn, FakeHarness(payload), repo).drafted
        assert intake.draft_resolutions(conn, FakeHarness(payload), repo).drafted == []
        conn.close()


def test_qna_store_와_같은_원문을_본다(tmp_path) -> None:
    """`pending` 이 보는 표가 수집이 쓰는 표와 같은지 — 배선이 어긋나면 조용하다."""
    conn = _conn(tmp_path)
    assert QnaStore(conn) is not None
    _raw_question(conn)
    assert [r["id"] for r in intake.pending(conn)] == ["Q-1"]
    conn.close()


class TestHaltedTicketIsActionable:
    """멈춘 건이 **손댈 수 있는** 티켓이 되는가 (WBS-4.5.2).

    대기열에 넣기만 하고 화면이 아무것도 말하지 못하면 그 대기열은 소화되지 않는다.
    """

    def _app(self, tmp_path, **over):  # noqa: ANN001, ANN202
        from agentic_service_desk.config import Settings
        from agentic_service_desk.web.app import create_app

        base = dict(
            operations_db=tmp_path / "ops.sqlite3",
            knowledge_dir=tmp_path / "knowledge",
            stage="S1",
        )
        base.update(over)
        return create_app(Settings(_env_file=None, **base))  # type: ignore[arg-type]

    def test_화면이_유입_원문을_보여준다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        conn = _conn(tmp_path)
        _item(tmp_path)
        _raw_question(conn, body="회의실 예약은 어디서 하나요?")
        ticket_id = intake.run(conn).admitted[0].ticket_id
        conn.close()

        html = TestClient(self._app(tmp_path)).get(f"/queues/Q1/{ticket_id}").text
        assert "회의실 예약은 어디서 하나요?" in html

    def test_오지_않을_초안을_기다리라고_하지_않는다(self, tmp_path) -> None:
        """**어떤 배치도 이것을 채우지 않는다.**

        담당자 답변이 없으면 종결 기록의 재료가 없다. 기다리라고 하면 대기열이
        조용히 막힌다.
        """
        from fastapi.testclient import TestClient

        conn = _conn(tmp_path)
        _item(tmp_path)
        _raw_question(conn, body="회의실 예약은 어디서 하나요?")
        ticket_id = intake.run(conn).admitted[0].ticket_id
        conn.close()

        html = TestClient(self._app(tmp_path)).get(f"/queues/Q1/{ticket_id}").text
        assert "다음 배치 주기에 만들어진다" not in html
        assert "직접 답하고" in html

    def test_답을_적으면_초안의_재료가_생긴다(self, tmp_path) -> None:
        """적은 뒤에야 배치가 초안을 만들 수 있다 — 고리가 이어진다."""
        from fastapi.testclient import TestClient

        from agentic_service_desk.operations import manual_entry

        conn = _conn(tmp_path)
        _item(tmp_path)
        _raw_question(conn, body="회의실 예약은 어디서 하나요?")
        ticket_id = intake.run(conn).admitted[0].ticket_id
        conn.close()

        client = TestClient(self._app(tmp_path))
        client.post(
            f"/queues/Q1/{ticket_id}/answer",
            data={"answer": "설비 시스템의 예약 메뉴에서 합니다."},
        )

        conn = _conn(tmp_path)
        awaiting = manual_entry.awaiting_draft(conn)
        assert [e.ticket_id for e in awaiting] == [ticket_id]
        assert awaiting[0].question == "회의실 예약은 어디서 하나요?"
        conn.close()

    def test_빈_답변은_재료가_되지_않는다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from agentic_service_desk.operations import manual_entry

        conn = _conn(tmp_path)
        _item(tmp_path)
        _raw_question(conn, body="회의실 예약은 어디서 하나요?")
        ticket_id = intake.run(conn).admitted[0].ticket_id
        conn.close()

        client = TestClient(self._app(tmp_path))
        client.post(f"/queues/Q1/{ticket_id}/answer", data={"answer": "   "})

        conn = _conn(tmp_path)
        assert manual_entry.awaiting_draft(conn) == []
        conn.close()
