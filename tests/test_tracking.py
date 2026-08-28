"""WBS-4.5.4 — QnA 추적 (FR-29~32, D8, D9, §6.1~6.3).

**게재는 끝이 아니다.** QnA 항목은 해결로 처리될 때까지 추적되는 상태를 가진
엔터티이며, 이것이 이 시스템을 "질문 하나에 답 하나"인 봇과 구분한다.

여기서 지키는 것은 여섯.

    1. **후속이 달리면 다시 돈다** — 이전 답변까지 입력에 넣는다 (FR-29, §6.2)
    2. **같은 건으로 초안을 쌓지 않는다** — Q2 가 한 질문으로 채워지면 안 된다
    3. **마지막으로 말한 쪽이 등급을 정한다** — 암묵적 해결과 미해결 종료를 가른다
    4. **상향만 있고 강등은 없다** (FR-32, §5.3.2)
    5. **상향하면 ingest 자격이 실제로 열린다** — 필터가 두 출처를 함께 본다
    6. **등급을 분리 집계한다** — 하나로 뭉치면 품질이 가려진다 (FR-30, §6.3)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from agentic_service_desk.ingest.output_filter import OutputFilter
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.search import Search
from agentic_service_desk.operations import intake, qna_state, tracking
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline import draft_store
from agentic_service_desk.pipeline.answer import AnswerPipeline

from conftest import FakeHarness

BOT = "svc-agentic-desk"
ASKED = "2026-08-01T09:00:00+00:00"
ANSWERED = "2026-08-01T10:00:00+00:00"
FOLLOWED = "2026-08-01T11:00:00+00:00"
NOW = datetime(2026, 8, 28, tzinfo=UTC)


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _raw_question(conn, qid="Q-1", body="결재 승인 한도는 어떻게 정해지나요?"):  # noqa: ANN001, ANN202
    conn.execute(
        "INSERT INTO raw_question (id, body, asker_account, created_at, collected_at) "
        "VALUES (?, ?, 'emp-100', ?, ?)",
        (qid, body, ASKED, ASKED),
    )
    conn.commit()


def _raw_answer(conn, qid="Q-1", body="부서 등급으로 정해집니다.", *, author=BOT, at=ANSWERED):  # noqa: ANN001, ANN202
    conn.execute(
        "INSERT INTO raw_answer (id, question_id, body, author_account, created_at, collected_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (f"A-{qid}-{at}-{author}", qid, body, author, at, at),
    )
    conn.commit()


def _raw_followup(conn, qid="Q-1", body="제 부서는 등급이 뭔가요?", *, at=FOLLOWED):  # noqa: ANN001, ANN202
    conn.execute(
        "INSERT INTO raw_followup (id, question_id, body, author_account, created_at, collected_at) "
        "VALUES (?, ?, ?, 'emp-100', ?, ?)",
        (f"F-{at}", qid, body, at, at),
    )
    conn.commit()


def _explicit(conn, qid="Q-1"):  # noqa: ANN001, ANN202
    conn.execute(
        "INSERT INTO raw_resolution "
        "(question_id, resolved, grade, method, resolved_at, collected_at) "
        "VALUES (?, 1, ?, 'user_marked', ?, ?)",
        (qid, qna_state.EXPLICIT, ANSWERED, ANSWERED),
    )
    conn.commit()


def _repo(tmp_path) -> KnowledgeRepository:
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    repo.save(
        KnowledgeItem(
            id="k-1",
            title="결재 승인 한도가 결정되는 규칙",
            body="결재 승인 한도는 신청자의 부서 등급으로 결정된다.",
            provenance=[Provenance(commit="a" * 40, path="approval/limit.py")],
            invalidation=Invalidation(
                kind=InvalidationKind.LINKED, refs=("approval/limit.py",)
            ),
        )
    )
    repo.commit("시험용 지식 항목")
    return repo


def _pipeline(tmp_path, conn):  # noqa: ANN001, ANN202
    payload = json.dumps(
        {
            "answerable": True,
            "statements": [
                {
                    "text": "결재 승인 한도는 부서 등급으로 결정됩니다.",
                    "confidence": "확인됨",
                    "grounding": ["k-1"],
                }
            ],
            "unanswered": [],
        },
        ensure_ascii=False,
    )
    return AnswerPipeline(
        search=Search(repo=KnowledgeRepository(tmp_path / "knowledge"), conn=conn),
        conn=conn,
        harness=FakeHarness(*[payload] * 6),
    )


def _admitted(tmp_path, conn):  # noqa: ANN001, ANN202
    """유입과 Q2 판정까지 끝난 상태를 만든다.

    **초안을 대기 상태로 두지 않는다.** 실제 흐름에서 후속은 답변이 게재된 뒤에
    달리므로, 그때 그 초안은 이미 판정을 받았다. 대기 상태로 두면 재실행 방지
    장치에 걸려 시험이 엉뚱한 것을 보게 된다.
    """
    _repo(tmp_path)
    admitted = intake.run(conn, pipeline=_pipeline(tmp_path, conn)).admitted[0]
    if admitted.draft_id:
        draft_store.decide(conn, admitted.draft_id, approved=True)
    return admitted


class TestRerun:
    """FR-29 · D9 — 후속이 달리면 파이프라인이 다시 돈다."""

    def test_후속에_아직_답하지_않은_건이_대상이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        assert tracking.awaiting_rerun(conn) == []

        _raw_answer(conn)
        _raw_followup(conn)
        assert [r["parent_question_id"] for r in tracking.awaiting_rerun(conn)] == ["Q-1"]
        conn.close()

    def test_후속_뒤에_답했으면_대상이_아니다(self, tmp_path) -> None:
        # 판정은 **시각 비교 하나**다 — 가장 늦은 후속이 가장 늦은 답변보다 뒤인가.
        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _raw_followup(conn)
        _raw_answer(conn, body="등급표는 여기 있습니다.", at="2026-08-01T12:00:00+00:00")
        assert tracking.awaiting_rerun(conn) == []
        conn.close()

    def test_초안이_대기_중이면_다시_돌지_않는다(self, tmp_path) -> None:
        """**Q2 가 같은 질문으로 채워지면 다른 질문이 영원히 밀린다.**

        사람이 처리할 때까지 매 주기 새 초안이 쌓이면 LLM 호출도 그만큼 버려진다.
        """
        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _raw_answer(conn)
        _raw_followup(conn)

        first = tracking.rerun(conn, pipeline=_pipeline(tmp_path, conn))
        assert len(first.reruns) == 1
        assert len(draft_store.pending(conn)) == 1  # 재실행분 하나

        second = tracking.rerun(conn, pipeline=_pipeline(tmp_path, conn))
        assert second.reruns == []
        assert len(draft_store.pending(conn)) == 1
        conn.close()

    def test_이전_답변과_후속이_입력에_들어간다(self, tmp_path) -> None:
        """§6.2 — 후속만 넘기면 **무엇에 대한 이의인지 모르는 채로** 검색에 걸린다."""
        conn = _conn(tmp_path)
        _raw_question(conn)
        _raw_answer(conn)
        _raw_followup(conn)

        text = tracking.build_rerun_input(conn, "Q-1")
        assert "결재 승인 한도는 어떻게 정해지나요?" in text
        assert "부서 등급으로 정해집니다." in text
        assert "제 부서는 등급이 뭔가요?" in text
        conn.close()

    def test_재실행도_티켓을_하나_더_발행한다(self, tmp_path) -> None:
        """**재실행은 새로운 처리이지 지난 처리의 연장이 아니다** (FR-56)."""
        conn = _conn(tmp_path)
        _raw_question(conn)
        admission = _admitted(tmp_path, conn)
        _raw_answer(conn)
        _raw_followup(conn)

        rerun = tracking.rerun(conn, pipeline=_pipeline(tmp_path, conn)).reruns[0]
        assert rerun.ticket_id != admission.ticket_id
        tickets = ticket_domain.for_qna(conn, admission.qna_item_id)
        assert len(tickets) == 2
        conn.close()

    def test_종점에_닿은_건은_다시_돌지_않는다(self, tmp_path) -> None:
        # 같은 질문이 또 오면 그것은 **새 질문**이다.
        conn = _conn(tmp_path)
        _raw_question(conn)
        admission = _admitted(tmp_path, conn)
        _raw_answer(conn)
        _raw_followup(conn)
        conn.execute(
            "UPDATE qna_item SET state = ? WHERE id = ?",
            (qna_state.RESOLVED, admission.qna_item_id),
        )
        conn.commit()
        assert tracking.awaiting_rerun(conn) == []
        conn.close()


class TestSettleQuiet:
    """FR-30 · O18 — 조용해진 건을 등급과 함께 닫는다."""

    def test_답이_나간_뒤의_침묵은_암묵적_해결이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _raw_answer(conn)

        report = tracking.settle_quiet(conn, quiet_hours=336, now=NOW)
        assert report.implicit == 1 and report.gaps == 0
        assert report.settled[0].state == qna_state.RESOLVED
        assert report.settled[0].grade == qna_state.IMPLICIT
        conn.close()

    def test_물음_뒤의_침묵은_미해결_종료다(self, tmp_path) -> None:
        """**지식 공백의 신호다** — 버려지는 데이터가 아니다 (§6.2)."""
        conn = _conn(tmp_path)
        _raw_question(conn, body="회의실 예약은 어디서 하나요?")
        _admitted(tmp_path, conn)  # 근거가 없어 초안이 안 나온다

        report = tracking.settle_quiet(conn, quiet_hours=336, now=NOW)
        assert report.gaps == 1 and report.implicit == 0
        assert report.settled[0].state == qna_state.UNRESOLVED_CLOSED
        assert report.settled[0].grade is None
        conn.close()

    def test_후속_뒤의_침묵도_미해결_종료다(self, tmp_path) -> None:
        # 답이 나갔더라도 그 뒤에 물음이 왔고 다시 답이 못 갔으면 풀리지 않은 것이다.
        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _raw_answer(conn)
        _raw_followup(conn)

        report = tracking.settle_quiet(conn, quiet_hours=336, now=NOW)
        assert report.settled[0].state == qna_state.UNRESOLVED_CLOSED
        conn.close()

    def test_아직_조용하지_않으면_두고_본다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _raw_answer(conn)

        soon = datetime.fromisoformat(ANSWERED) + timedelta(hours=1)
        assert tracking.settle_quiet(conn, quiet_hours=336, now=soon).settled == []
        conn.close()

    def test_명시적_해결은_건드리지_않는다(self, tmp_path) -> None:
        """**우리 타임아웃이 사람의 확인을 덮어쓰지 않는다** — 강등은 없다 (§5.3.2)."""
        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _raw_answer(conn)
        _explicit(conn)
        tracking.adopt_explicit(conn)

        assert tracking.settle_quiet(conn, quiet_hours=336, now=NOW).settled == []
        grade = conn.execute("SELECT resolution_grade FROM qna_item").fetchone()[0]
        assert grade == qna_state.EXPLICIT
        conn.close()

    def test_두_번_닫지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _raw_answer(conn)
        assert tracking.settle_quiet(conn, quiet_hours=336, now=NOW).settled
        assert tracking.settle_quiet(conn, quiet_hours=336, now=NOW).settled == []
        conn.close()


class TestAdoptExplicit:
    """D35 — 모 시스템이 알려준 해결 표시를 추적 상태에 잇는다."""

    def test_해결_표시가_추적_상태에_반영된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _explicit(conn)

        assert len(tracking.adopt_explicit(conn)) == 1
        row = conn.execute("SELECT state, resolution_grade FROM qna_item").fetchone()
        assert (row["state"], row["resolution_grade"]) == (
            qna_state.RESOLVED,
            qna_state.EXPLICIT,
        )
        conn.close()

    def test_암묵으로_닫힌_뒤에_눌러도_올라간다(self, tmp_path) -> None:
        """**이미 닫힌 건도 올린다** — 그때 ingest 자격이 열린다 (§5.3.2)."""
        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _raw_answer(conn)
        tracking.settle_quiet(conn, quiet_hours=336, now=NOW)

        _explicit(conn)
        assert len(tracking.adopt_explicit(conn)) == 1
        grade = conn.execute("SELECT resolution_grade FROM qna_item").fetchone()[0]
        assert grade == qna_state.EXPLICIT
        conn.close()


class TestUpgrade:
    """FR-32 — 운영자가 확인하면 명시적으로 상향된다."""

    def _implicit(self, tmp_path, conn):  # noqa: ANN001, ANN202
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _raw_answer(conn)
        return tracking.settle_quiet(conn, quiet_hours=336, now=NOW).settled[0]

    def test_확인하면_명시적이_된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        settled = self._implicit(tmp_path, conn)
        assert tracking.upgrade(conn, settled.qna_item_id)
        grade = conn.execute("SELECT resolution_grade FROM qna_item").fetchone()[0]
        assert grade == qna_state.EXPLICIT
        conn.close()

    def test_강등은_없다(self, tmp_path) -> None:
        """한 번 사람이 확인한 사실은 시간이 지나도 확인된 사실이다 (§5.3.2)."""
        conn = _conn(tmp_path)
        settled = self._implicit(tmp_path, conn)
        tracking.upgrade(conn, settled.qna_item_id)
        # 이미 명시적이므로 다시 불러도 아무 일도 일어나지 않는다.
        assert tracking.upgrade(conn, settled.qna_item_id) is False
        grade = conn.execute("SELECT resolution_grade FROM qna_item").fetchone()[0]
        assert grade == qna_state.EXPLICIT
        conn.close()

    def test_상향하면_ingest_자격이_열린다(self, tmp_path) -> None:
        """**FR-32 의 확인 조건 그대로.**

        운영자의 확인 종결은 §5.3.1-1 이 꼽은 명시적 해결 신호이지만 모 시스템이
        아니라 여기서 나온다. 필터가 두 출처를 함께 보지 않으면 **Q6 에서 확인한
        건이 끝내 ingest 되지 않아** 상향의 의미가 사라진다.
        """
        conn = _conn(tmp_path)
        settled = self._implicit(tmp_path, conn)
        filt = OutputFilter(frozenset({BOT}))
        assert filt.ingestible_answers(conn) == []

        tracking.upgrade(conn, settled.qna_item_id)
        ingestible = filt.ingestible_answers(conn)
        assert [a.question_id for a in ingestible] == ["Q-1"]
        assert ingestible[0].grade == qna_state.EXPLICIT
        conn.close()

    def test_아직_닫히지_않은_건은_올릴_수_없다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _raw_question(conn)
        admission = _admitted(tmp_path, conn)
        assert tracking.upgrade(conn, admission.qna_item_id) is False
        conn.close()


class TestGrades:
    """§6.3 — 하나의 숫자로 뭉치면 실제 품질이 가려진다."""

    def test_등급별로_나눠_센다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _repo(tmp_path)
        for qid, body in (
            ("Q-1", "결재 승인 한도는 어떻게 정해지나요?"),
            ("Q-2", "결재 승인 한도 기준이 뭔가요?"),
            ("Q-3", "회의실 예약은 어디서 하나요?"),
        ):
            _raw_question(conn, qid, body)
        for admitted in intake.run(conn, pipeline=_pipeline(tmp_path, conn)).admitted:
            if admitted.draft_id:
                draft_store.decide(conn, admitted.draft_id, approved=True)
        _raw_answer(conn, "Q-1")
        _raw_answer(conn, "Q-2")
        tracking.settle_quiet(conn, quiet_hours=336, now=NOW)

        g = tracking.grades(conn)
        assert (g.total, g.implicit, g.unresolved) == (3, 2, 1)
        assert g.implicit_rate > g.explicit_rate

        item_id = tracking.awaiting_confirmation(conn)[0]["id"]
        tracking.upgrade(conn, item_id)
        g = tracking.grades(conn)
        assert (g.explicit, g.implicit, g.unresolved) == (1, 1, 1)
        conn.close()

    def test_비어_있어도_나눗셈이_터지지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        g = tracking.grades(conn)
        assert (g.total, g.explicit_rate, g.implicit_rate) == (0, 0.0, 0.0)
        conn.close()


class TestQ6Screen:
    """§8.2 — 방치해도 사고가 나지 않아 영원히 밀리는 대기열이다."""

    def _app(self, tmp_path):  # noqa: ANN001, ANN202
        from agentic_service_desk.config import Settings
        from agentic_service_desk.web.app import create_app

        return create_app(
            Settings(  # type: ignore[arg-type]
                _env_file=None,
                operations_db=tmp_path / "ops.sqlite3",
                knowledge_dir=tmp_path / "knowledge",
                stage="S3",
                bot_accounts=BOT,
            )
        )

    def test_무엇을_얻는지_말해_준다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _raw_answer(conn)
        tracking.settle_quiet(conn, quiet_hours=336, now=NOW)
        conn.close()

        html = TestClient(self._app(tmp_path)).get("/queues/Q6").text
        assert "지식베이스가 자란다" in html
        assert "결재 승인 한도는 어떻게 정해지나요?" in html

    def test_확인_버튼이_상향한다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        conn = _conn(tmp_path)
        _raw_question(conn)
        _admitted(tmp_path, conn)
        _raw_answer(conn)
        item_id = tracking.settle_quiet(
            conn, quiet_hours=336, now=NOW
        ).settled[0].qna_item_id
        conn.close()

        client = TestClient(self._app(tmp_path))
        client.post(f"/queues/Q6/{item_id}/confirm")

        conn = _conn(tmp_path)
        grade = conn.execute("SELECT resolution_grade FROM qna_item").fetchone()[0]
        assert grade == qna_state.EXPLICIT
        assert tracking.awaiting_confirmation(conn) == []
        conn.close()


class TestIntakeSkipsAnswered:
    """유입 시 이미 사람이 답한 건에 답을 하나 더 올리지 않는다."""

    def test_이미_답이_있으면_파이프라인을_돌리지_않는다(self, tmp_path) -> None:
        """우리 봇 답변일 수는 없다 — 그랬다면 `qna_item` 이 있어 유입 대상에서
        빠졌을 것이다. 그러므로 **사람이 먼저 답한 것**이다."""
        conn = _conn(tmp_path)
        _repo(tmp_path)
        _raw_question(conn)
        _raw_answer(conn, author="emp-999")

        report = intake.run(conn, pipeline=_pipeline(tmp_path, conn))
        assert len(report.admitted) == 1
        assert report.admitted[0].draft_id is None
        assert draft_store.pending(conn) == []
        conn.close()

    def test_그래도_티켓은_발행된다(self, tmp_path) -> None:
        # 전건 발행이다 (FR-27). "할 일이 없었다"도 처리 결과다.
        conn = _conn(tmp_path)
        _repo(tmp_path)
        _raw_question(conn)
        _raw_answer(conn, author="emp-999")
        admission = intake.run(conn, pipeline=_pipeline(tmp_path, conn)).admitted[0]
        assert ticket_domain.get(conn, admission.ticket_id)
        conn.close()

    def test_조용해지면_암묵적_해결이_된다(self, tmp_path) -> None:
        # 마지막 사건이 답변이므로.
        conn = _conn(tmp_path)
        _repo(tmp_path)
        _raw_question(conn)
        _raw_answer(conn, author="emp-999")
        intake.run(conn, pipeline=_pipeline(tmp_path, conn))
        report = tracking.settle_quiet(conn, quiet_hours=336, now=NOW)
        assert report.implicit == 1
        conn.close()


class TestQueueVsClosure:
    """D15 — 티켓과 QnA 항목은 축이 다르다."""

    def test_검수_대기_초안이_있으면_닫지_않는다(self, tmp_path) -> None:
        """답이 이미 만들어졌고 나가기 직전이다.

        미해결로 닫으면 **사람이 Q2 에서 승인하려는 순간 그 QnA 는 이미 닫혀 있다.**
        """
        conn = _conn(tmp_path)
        _raw_question(conn)
        _repo(tmp_path)
        intake.run(conn, pipeline=_pipeline(tmp_path, conn))  # 초안이 Q2 에 남는다
        assert draft_store.pending(conn)

        assert tracking.settle_quiet(conn, quiet_hours=336, now=NOW).settled == []
        conn.close()

    def test_열린_티켓은_닫지_않을_이유가_아니다(self, tmp_path) -> None:
        """**우리 대기열이 밀렸다는 이유로 지식 공백을 지우지 않는다** (§6.2).

        티켓은 *우리에게 무슨 일이 남았는가*이고 QnA 항목은 *이용자에게 이 질문이
        어떻게 되었는가*다 — 파이프라인이 답을 못 만들어 Q1 에 걸린 건은 이용자
        관점에서 정말로 답이 못 간 것이다. 밀린 작업은 대기열의 경과 시간이 드러낸다.
        """
        conn = _conn(tmp_path)
        _raw_question(conn, body="회의실 예약은 어디서 하나요?")
        admission = _admitted(tmp_path, conn)
        assert ticket_domain.get(conn, admission.ticket_id).in_queue

        report = tracking.settle_quiet(conn, quiet_hours=336, now=NOW)
        assert report.gaps == 1
        # 티켓은 그대로 열려 있다 — 축이 다르므로 함께 닫히지 않는다.
        assert ticket_domain.get(conn, admission.ticket_id).in_queue
        conn.close()

    def test_이미_답이_있던_건의_티켓은_자동_종결이다(self, tmp_path) -> None:
        """**초안을 못 만든 것과 만들 일이 없던 것은 다르다.**

        섞으면 이미 답이 있는 질문이 Q1 에 떠서 "직접 답하고 적어라"고 말하게 된다.
        """
        conn = _conn(tmp_path)
        _repo(tmp_path)
        _raw_question(conn)
        _raw_answer(conn, author="emp-999")
        admission = intake.run(conn, pipeline=_pipeline(tmp_path, conn)).admitted[0]
        assert admission.ticket_state is ticket_domain.State.AUTO_CLOSED
        assert ticket_domain.queue(conn) == []
        conn.close()
