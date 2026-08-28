"""WBS-4.3.3 — 질문 직접 등록 (FR-10·11, D43·D44, ADR-007 결정 4).

**1국면에 QnA 는 메신저를 이길 수 없다.** 경쟁하지 않고 흡수한다 — 담당자가 메신저로
받은 질문을 직접 기록하고, 그 티켓은 A 경로(티켓 해결 → 지식)를 그대로 탄다.

여기서 지키는 것은 넷.

    1. **두 칸이면 끝난다** — 등록 부담이 크면 유인이 상쇄된다 (§1.4.4)
    2. 등록이 LLM 을 기다리지 않는다. 초안은 배치가 채운다
    3. 초안이 만들어지되 **무효화 조건 칸은 비어 있다** (FR-11, §5.6.4)
    4. 모델이 확정값을 보내도 **후보로만 내려앉는다**
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind
from agentic_service_desk.operations import manual_entry, resolution, ticket
from agentic_service_desk.operations.drafter import Drafter, build_prompt, parse_draft
from agentic_service_desk.operations.manual_entry import EmptyEntry
from agentic_service_desk.operations.resolution import GroundKind
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.web.app import create_app

from conftest import FakeHarness

DRAFT = """
{"generalized_question": "승인 한도는 무엇으로 결정되는가",
 "answer": "부서 등급으로 결정된다.",
 "grounding": [{"kind": "person", "ref": "인사팀 확인"}],
 "invalidation_candidates": [{"kind": "periodic", "period_days": 180}],
 "cause": "개인별 지정이라 인사이동마다 손이 갔다"}
"""


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _register(conn, question="김OO 사번 12345 인데 승인 한도가 왜 300만인가요?",  # noqa: ANN001
              answer="부서 등급에 따라 정해집니다."):
    return manual_entry.register(conn, question=question, answer=answer, registered_by="emp-1")


def _settings(tmp_path, **over):  # noqa: ANN001, ANN202
    base = dict(operations_db=tmp_path / "ops.sqlite3", knowledge_dir=tmp_path / "knowledge")
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


class TestTwoFields:
    """§1.4.4 — 붙여넣기로 끝나야 한다."""

    def test_두_칸이면_등록된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _register(conn)
        assert r.qna_item_id and r.ticket_id

    def test_질문이_비면_거부한다(self, tmp_path) -> None:
        with pytest.raises(EmptyEntry, match="질문"):
            manual_entry.register(_conn(tmp_path), question="  ", answer="답")

    def test_답변이_비면_거부한다(self, tmp_path) -> None:
        # 답 없는 등록은 지식이 되지 못한다 — 기록만 남고 A 경로를 못 탄다.
        with pytest.raises(EmptyEntry, match="답변"):
            manual_entry.register(_conn(tmp_path), question="질문", answer="")


class TestWhatRegistrationCreates:
    def test_모_시스템_질문_id_가_비어_있다(self, tmp_path) -> None:
        # 메신저로 온 것이라 모 시스템을 거치지 않았다.
        conn = _conn(tmp_path)
        r = _register(conn)
        row = conn.execute(
            "SELECT parent_question_id, origin FROM qna_item WHERE id = ?", (r.qna_item_id,)
        ).fetchone()
        assert row["parent_question_id"] is None
        assert row["origin"] == manual_entry.MANUAL

    def test_수동_등록_여럿이_부딪히지_않는다(self, tmp_path) -> None:
        # parent_question_id 가 UNIQUE 인데 NULL 이 여럿이어야 한다.
        conn = _conn(tmp_path)
        _register(conn)
        _register(conn, question="다른 질문")
        assert manual_entry.count(conn) == 2

    def test_티켓이_열린다(self, tmp_path) -> None:
        # 답은 이미 나갔지만 우리 쪽에 남은 일이 있다 — 무효화 조건을 채우는 것.
        conn = _conn(tmp_path)
        r = _register(conn)
        t = ticket.get(conn, r.ticket_id)
        assert t.state is ticket.State.OPEN
        assert t.qna_item_id == r.qna_item_id

    def test_원문이_그대로_남는다(self, tmp_path) -> None:
        # 일반화된 질문은 이것을 가공한 결과다 — 지우면 초안을 다시 만들 수 없다.
        conn = _conn(tmp_path)
        r = _register(conn)
        entry = manual_entry.get_entry(conn, r.qna_item_id)
        assert "사번 12345" in entry.question

    def test_등록_건수가_지표가_된다(self, tmp_path) -> None:
        # W4 의 유일한 간접 지표다 (§1.4.6). 0 에 머물면 질문이 여전히 사라지고 있다.
        conn = _conn(tmp_path)
        assert manual_entry.count(conn) == 0
        _register(conn)
        assert manual_entry.count(conn) == 1


class TestRegistrationDoesNotWaitForTheModel:
    def test_등록은_초안을_만들지_않는다(self, tmp_path) -> None:
        # 수십 초 걸리는 호출로 응답을 붙들면 부담이 되돌아온다 (§1.4.4).
        conn = _conn(tmp_path)
        r = _register(conn)
        assert resolution.get(conn, r.ticket_id) is None
        assert len(manual_entry.awaiting_draft(conn)) == 1


class TestDrafting:
    """FR-11 — 초안이 자동 생성되고 무효화 조건 칸은 비어 있다."""

    def test_초안을_채운다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _register(conn)
        report = Drafter(FakeHarness(DRAFT)).run(conn)

        assert report.drafted == [r.ticket_id]
        drafted = resolution.get(conn, r.ticket_id)
        assert drafted.generalized_question == "승인 한도는 무엇으로 결정되는가"
        assert drafted.cause

    def test_무효화_조건은_비어_있다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _register(conn)
        Drafter(FakeHarness(DRAFT)).run(conn)

        drafted = resolution.get(conn, r.ticket_id)
        assert drafted.invalidation is None
        assert not drafted.confirmed
        assert len(drafted.invalidation_candidates) == 1

    def test_초안만으로는_티켓이_닫히지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _register(conn)
        Drafter(FakeHarness(DRAFT)).run(conn)

        with pytest.raises(ticket.ResolutionRequired):
            ticket.transition(conn, r.ticket_id, ticket.State.CLOSED)

    def test_사람이_채우면_닫히고_승격_자격이_생긴다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _register(conn)
        Drafter(FakeHarness(DRAFT)).run(conn)
        resolution.confirm(
            conn, r.ticket_id,
            invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=180),
        )

        assert resolution.get(conn, r.ticket_id).promotable
        assert ticket.transition(conn, r.ticket_id, ticket.State.CLOSED).state is ticket.State.CLOSED

    def test_이미_초안이_있으면_다시_만들지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _register(conn)
        Drafter(FakeHarness(DRAFT)).run(conn)

        second = FakeHarness(DRAFT)
        assert Drafter(second).run(conn).drafted == []
        assert second.prompts == []

    def test_하나가_실패해도_나머지는_간다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _register(conn)
        _register(conn, question="다른 질문")
        report = Drafter(FakeHarness("JSON 이 아니다", DRAFT)).run(conn)

        assert len(report.failures) == 1
        assert len(report.drafted) == 1


class TestModelCannotDecideInvalidation:
    """§5.6.4 — 기본값을 미리 채워 두면 강제 입력 지점의 효과가 사라진다."""

    def test_확정값을_보내도_후보로_내려앉는다(self, tmp_path) -> None:
        # 형식을 어긴 쪽이 조용히 이기지 않게 하는 자리다.
        text = (
            '{"generalized_question": "가", "answer": "나",'
            ' "grounding": [{"kind": "person", "ref": "확인"}],'
            ' "invalidation": {"kind": "periodic", "period_days": 30}}'
        )
        fields = parse_draft(text)
        assert "invalidation" not in fields
        assert fields["invalidation_candidates"][0].period_days == 30

    def test_근거를_못_뽑아도_지어내지_않는다(self) -> None:
        # 남아 있는 사실 하나를 쓴다 — **담당자가 그렇게 답했다.**
        fields = parse_draft('{"generalized_question": "가", "answer": "나", "grounding": []}')
        assert fields["grounding"][0].kind is GroundKind.PERSON

    def test_형식을_어긴_후보는_버리고_나머지를_쓴다(self) -> None:
        text = (
            '{"generalized_question": "가", "answer": "나",'
            ' "grounding": [{"kind": "person", "ref": "확인"}],'
            ' "invalidation_candidates": [{"kind": "없는종류"}, {"kind": "periodic", "period_days": 90}]}'
        )
        assert len(parse_draft(text)["invalidation_candidates"]) == 1

    def test_일반화된_질문이_비면_실패한다(self) -> None:
        from agentic_service_desk.ingest.agent import AgentOutputError

        with pytest.raises(AgentOutputError):
            parse_draft('{"generalized_question": "", "answer": "나"}')

    def test_프롬프트가_후보만_요구한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _register(conn)
        prompt = build_prompt(manual_entry.get_entry(conn, r.qna_item_id))
        assert "후보를 제시만 한다" in prompt
        assert "고르는 것은 사람" in prompt

    def test_프롬프트가_근거를_추측하지_말라고_한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _register(conn)
        assert "추측해서 code 라고 하지 않는다" in build_prompt(
            manual_entry.get_entry(conn, r.qna_item_id)
        )


class TestForm:
    def test_두_칸이_뜬다(self, tmp_path) -> None:
        html = TestClient(create_app(_settings(tmp_path))).get("/entry").text
        assert 'name="question"' in html
        assert 'name="answer"' in html

    def test_등록하면_티켓이_생긴다(self, tmp_path) -> None:
        client = TestClient(create_app(_settings(tmp_path)))
        html = client.post(
            "/entry", data={"question": "승인 한도가 왜 이런가요?", "answer": "부서 등급입니다."}
        ).text
        assert "등록했다" in html

        conn = _conn(tmp_path)
        assert manual_entry.count(conn) == 1
        assert len(ticket.queue(conn)) == 1

    def test_빈_칸은_거부되고_화면이_말해_준다(self, tmp_path) -> None:
        client = TestClient(create_app(_settings(tmp_path)))
        html = client.post("/entry", data={"question": "  ", "answer": "답"}).text
        assert "질문 원문이 비었다" in html
        assert manual_entry.count(_conn(tmp_path)) == 0

    def test_등록_건수를_보여준다(self, tmp_path) -> None:
        # 유인은 시스템이 아니라 담당자 자신의 이익이다 (§1.4.4).
        conn = _conn(tmp_path)
        _register(conn)
        conn.close()
        html = TestClient(create_app(_settings(tmp_path))).get("/entry").text
        assert "<strong>1건</strong> 기록됐다" in html
