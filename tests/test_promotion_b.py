"""WBS-4.5.6 — 승격 경로 B (FR-33, D41·D42, §6.8).

**명시적 해결은 필요조건이지 충분조건이 아니다** (§6.8.2). 이용자의 해결 표시는
*"나에게 유효했다"는 증언*이지 *"이것이 일반적으로 옳다"는 판정*이 아니다. 그러나
전부 사람이 보면 성장이 멈춘다 (§6.8.3) — 여기가 그 둘 사이의 답이다.

여기서 지키는 것은 여섯.

    1. **세 조건을 모두 만족해야 자동이다** (§6.8.4)
    2. **1국면에는 자동 승격이 없다** — 판단 기준 자체가 없다 (§6.8.4-b)
    3. **경로 A 는 Q7 을 거치지 않는다** — 이중 승인이 곧 대기열 정체다 (§6.8.1)
    4. **무효화 조건은 코드에서 도출한다** — 조건 1 이 판정을 대신한다 (§6.8.4-c)
    5. **기각도 판정이다** — 남기지 않으면 매 주기 다시 뜬다
    6. **누가 올렸는지 남는다** — 자동 승격분은 표본 재검증 우선순위가 높다 (§6.8.4-a)
"""

from __future__ import annotations

import sqlite3

from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations import promotion, qna_state
from agentic_service_desk.operations import resolution as resolution_domain
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations.resolution import Ground, GroundKind
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline.review import ReviewInput, Verdict, record

CODE_GROUND = [Ground(kind=GroundKind.CODE, ref="approval/limit.py")]
PERSON_GROUND = [Ground(kind=GroundKind.PERSON, ref="담당자 확인")]


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _repo(tmp_path) -> KnowledgeRepository:
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    return repo


def _handled(
    conn: sqlite3.Connection,
    *,
    qid: str = "q-1",
    grounding=CODE_GROUND,  # noqa: ANN001
    clean_review: bool = True,
    explicit: bool = True,
) -> str:
    """자동 처리되어 종결 기록까지 남은 건을 만든다 (WBS-4.5.2 의 산출물)."""
    conn.execute(
        "INSERT INTO qna_item (id, parent_question_id, origin, state, resolution_grade, opened_at) "
        "VALUES (?, ?, 'parent', ?, ?, 'x')",
        (
            qid,
            f"Q-{qid}",
            qna_state.RESOLVED if explicit else qna_state.PUBLISHED,
            qna_state.EXPLICIT if explicit else None,
        ),
    )
    conn.commit()
    t = ticket_domain.issue(
        conn,
        source=ticket_domain.Source.QNA,
        qna_item_id=qid,
        state=ticket_domain.State.AUTO_CLOSED,
    )
    resolution_domain.draft(
        conn,
        ticket_id=t.id,
        generalized_question="결재 승인 한도는 무엇으로 결정되는가",
        answer="신청자의 부서 등급으로 결정된다.",
        grounding=list(grounding),
        invalidation_candidates=[
            Invalidation(kind=InvalidationKind.PERIODIC, period_days=180)
        ],
    )
    _review(conn, qid, passed=True)
    if not clean_review:
        _review(conn, qid, passed=False)
    return t.id


def _review(conn: sqlite3.Connection, qna_item_id: str, *, passed: bool) -> None:
    from agentic_service_desk.pipeline.review import Reject

    record(
        conn,
        review=ReviewInput(draft_body="본문", grounding=("k-1",), source_text={}),
        verdict=Verdict(
            passed=passed,
            reason=None if passed else Reject.P1,
            detail="",
            checked_by="agent",
        ),
        qna_item_id=qna_item_id,
    )


class TestConditions:
    """§6.8.4 — 세 조건. 하나라도 어긋나면 Q7 로 간다."""

    def test_셋을_다_채우면_자격이_있다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        ticket_id = _handled(conn)
        c = promotion.assess(conn, ticket_id)
        assert c.eligible
        assert c.reason == "세 조건을 모두 만족한다."
        conn.close()

    def test_코드에_묶이지_않으면_자동이_아니다(self, tmp_path) -> None:
        """**§6.8.2 가 경계한 것이 이것이다.**

        코드에 묶인다는 것은 그 답이 *이용자에게 유효했다*를 넘어 **코드가 실제로
        그렇게 동작한다**는 뜻이고, 우회책은 코드에 묶이지 않는다.
        """
        conn = _conn(tmp_path)
        ticket_id = _handled(conn, grounding=PERSON_GROUND)
        c = promotion.assess(conn, ticket_id)
        assert promotion.Condition.CODE_LINKED in c.missing
        assert c.derived is None
        conn.close()

    def test_반려_이력이_있으면_자동이_아니다(self, tmp_path) -> None:
        # 첫 시도에 통과하지 못했다는 사실은 나중에 뒤집혀도 남는다.
        conn = _conn(tmp_path)
        ticket_id = _handled(conn, clean_review=False)
        assert promotion.Condition.CLEAN_REVIEW in promotion.assess(conn, ticket_id).missing
        conn.close()

    def test_명시적_해결이_아니면_자동이_아니다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        ticket_id = _handled(conn, explicit=False)
        assert (
            promotion.Condition.EXPLICIT_RESOLUTION
            in promotion.assess(conn, ticket_id).missing
        )
        conn.close()

    def test_모_시스템_해결_표시도_명시적이다(self, tmp_path) -> None:
        """**출처가 둘이다** (§5.3.1-1) — 이용자의 표시와 운영자의 확인 종결."""
        conn = _conn(tmp_path)
        ticket_id = _handled(conn, explicit=False)
        conn.execute(
            "INSERT INTO raw_question (id, body, asker_account, created_at, collected_at) "
            "VALUES ('Q-q-1', '질문', 'emp-1', 'x', 'x')"
        )
        conn.execute(
            "INSERT INTO raw_resolution (question_id, resolved, grade, collected_at) "
            "VALUES ('Q-q-1', 1, ?, 'x')",
            (qna_state.EXPLICIT,),
        )
        conn.commit()
        assert (
            promotion.Condition.EXPLICIT_RESOLUTION
            not in promotion.assess(conn, ticket_id).missing
        )
        conn.close()


class TestDerivedInvalidation:
    """§6.8.4-c — 조건 1 이 판정을 대신한다."""

    def test_코드_경로에서_연결형을_도출한다(self, tmp_path) -> None:
        """**에이전트가 기본값을 미리 채우는 것과 다르다.**

        여기서 쓰는 것은 종결 기록에 이미 적힌 코드 경로 그 자체이고, 그것이
        바뀌면 이 지식이 틀려진다는 것은 판단이 아니라 사실이다.
        """
        conn = _conn(tmp_path)
        ticket_id = _handled(conn)
        derived = promotion.assess(conn, ticket_id).derived
        assert derived.kind is InvalidationKind.LINKED
        assert derived.refs == ("approval/limit.py",)
        conn.close()

    def test_커밋만_있으면_도출하지_않는다(self, tmp_path) -> None:
        """커밋은 *그때 그 커밋*을 가리킬 뿐 **무엇이 바뀌면 틀려지는가**를 말해
        주지 않는다."""
        conn = _conn(tmp_path)
        ticket_id = _handled(
            conn, grounding=[Ground(kind=GroundKind.COMMIT, ref="a" * 40)]
        )
        c = promotion.assess(conn, ticket_id)
        assert c.derived is None
        assert promotion.Condition.CODE_LINKED in c.missing
        conn.close()


class TestPhaseGate:
    """§6.8.4-b — 자동화는 시간이 아니라 국면을 기준으로 올린다."""

    def test_1국면에는_올리지_않는다(self, tmp_path) -> None:
        """지식베이스가 얇아 판단 기준 자체가 없고, 이 시기 승격의 주 경로는 A 다."""
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _handled(conn)
        report = promotion.run_auto(conn, repo, phase=1)
        assert report.promoted == []
        assert len(report.to_queue) == 1  # Q7 에는 뜬다
        conn.close()

    def test_2국면부터_자동이다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        ticket_id = _handled(conn)
        report = promotion.run_auto(conn, repo, phase=2)
        assert [p.ticket_id for p in report.promoted] == [ticket_id]
        assert report.to_queue == []
        conn.close()

    def test_조건_미충족은_국면과_무관하게_Q7_이다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _handled(conn, grounding=PERSON_GROUND)
        report = promotion.run_auto(conn, repo, phase=3)
        assert report.promoted == []
        assert len(report.to_queue) == 1
        conn.close()

    def test_완화는_3국면에서_조건_2_만_푼다(self, tmp_path) -> None:
        """**완화할 수 있는 것은 조건 2 하나뿐이다.**

        조건 1 은 §6.8.4-c 가 "판정을 대신한다"고 못 박았으므로 풀면 §6.8.2 의
        우려로 되돌아가고, 조건 3 은 §5.3 의 배제를 푸는 조건이라 풀 수 없다.
        """
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _handled(conn, qid="q-a", clean_review=False)
        _handled(conn, qid="q-b", grounding=PERSON_GROUND)
        _handled(conn, qid="q-c", explicit=False)

        report = promotion.run_auto(conn, repo, phase=3, relax_clean_review=True)
        assert len(report.promoted) == 1  # 반려 이력만 풀린다
        assert len(report.to_queue) == 2
        conn.close()

    def test_완화는_2국면에서_듣지_않는다(self, tmp_path) -> None:
        # **완화는 운영자 승인 사항**이고 3국면의 것이다 (§1.3.3).
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _handled(conn, clean_review=False)
        report = promotion.run_auto(conn, repo, phase=2, relax_clean_review=True)
        assert report.promoted == []
        conn.close()


class TestPromotedRecord:
    """§6.8.4-a — 자동 승격분은 사람이 본 적이 없다."""

    def test_누가_올렸는지_남는다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        ticket_id = _handled(conn)
        promotion.run_auto(conn, repo, phase=2)

        record_row = resolution_domain.get(conn, ticket_id)
        assert record_row.promoted_by == "gate"
        assert record_row.promoted_item_id
        conn.close()

    def test_도출한_무효화_조건이_지식_항목에_간다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _handled(conn)
        item = promotion.run_auto(conn, repo, phase=2).promoted[0].item
        assert item.invalidation.kind is InvalidationKind.LINKED
        assert item.invalidation.refs == ("approval/limit.py",)
        conn.close()

    def test_두_번_올리지_않는다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _handled(conn)
        assert len(promotion.run_auto(conn, repo, phase=2).promoted) == 1
        assert promotion.run_auto(conn, repo, phase=2).promoted == []
        conn.close()


class TestDecline:
    """기각도 판정이다."""

    def test_기각하면_다시_뜨지_않는다(self, tmp_path) -> None:
        """방치해도 사고가 나지 않는 대기열에 같은 것이 쌓이면 **실제로 볼 것이
        그 사이에 묻힌다** (§8.2)."""
        conn = _conn(tmp_path)
        ticket_id = _handled(conn, grounding=PERSON_GROUND)
        assert len(promotion.awaiting_decision(conn)) == 1

        assert resolution_domain.decline_promotion(conn, ticket_id)
        assert promotion.awaiting_decision(conn) == []
        conn.close()

    def test_기각한_건은_승격되지_않는다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        ticket_id = _handled(conn)
        resolution_domain.decline_promotion(conn, ticket_id)
        assert promotion.run_auto(conn, repo, phase=2).promoted == []
        conn.close()

    def test_이미_승격된_건은_기각할_수_없다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        ticket_id = _handled(conn)
        promotion.run_auto(conn, repo, phase=2)
        assert resolution_domain.decline_promotion(conn, ticket_id) is False
        conn.close()


class TestPathAStaysOut:
    """§6.8.1 — 경로 A 는 Q7 을 거치지 않는다."""

    def test_사람이_닫은_티켓은_Q7_에_없다(self, tmp_path) -> None:
        """사람이 무효화 조건을 채운 것이 곧 승격 승인이다. 여기 또 승인을 붙이면
        **이중 승인**이고, 1인 겸업에게 그 중복이 곧 대기열 정체다."""
        from agentic_service_desk.operations import manual_entry

        conn = _conn(tmp_path)
        r = manual_entry.register(conn, question="질문", answer="답")
        resolution_domain.draft(
            conn,
            ticket_id=r.ticket_id,
            generalized_question="일반화된 질문",
            answer="답",
            grounding=CODE_GROUND,
        )
        assert promotion.awaiting_decision(conn) == []
        conn.close()

    def test_모순_티켓은_Q7_에_없다(self, tmp_path) -> None:
        # 모순 판정과 Lint 처리는 새 지식을 만드는 일이 아니다 (§6.4.3).
        conn = _conn(tmp_path)
        t = ticket_domain.issue(
            conn,
            source=ticket_domain.Source.CONTRADICTION,
            state=ticket_domain.State.AUTO_CLOSED,
        )
        resolution_domain.draft(
            conn,
            ticket_id=t.id,
            generalized_question="판정",
            answer="kept_human",
            grounding=CODE_GROUND,
        )
        assert promotion.awaiting_decision(conn) == []
        conn.close()


class TestQ7Screen:
    """§6.4.4 — 보고 **지정하면** 끝나는 판정 화면."""

    def _app(self, tmp_path, **over):  # noqa: ANN001, ANN202
        from agentic_service_desk.config import Settings
        from agentic_service_desk.web.app import create_app

        base = dict(
            operations_db=tmp_path / "ops.sqlite3",
            knowledge_dir=tmp_path / "knowledge",
            stage="S3",
        )
        base.update(over)
        return create_app(Settings(_env_file=None, **base))  # type: ignore[arg-type]

    def test_왜_자동이_아닌지_말해_준다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        conn = _conn(tmp_path)
        _repo(tmp_path)
        _handled(conn, grounding=PERSON_GROUND)
        conn.close()

        html = TestClient(self._app(tmp_path)).get("/queues/Q7").text
        assert "못 채운 조건" in html
        assert "근거가 소스코드에 직접 연결된다" in html

    def test_도출된_조건으로_승격한다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        conn = _conn(tmp_path)
        _repo(tmp_path)
        ticket_id = _handled(conn)
        conn.close()

        client = TestClient(self._app(tmp_path))
        client.post(f"/queues/Q7/{ticket_id}/promote", data={"choice": "derived"})

        conn = _conn(tmp_path)
        row = resolution_domain.get(conn, ticket_id)
        assert row.promoted_item_id
        assert row.promoted_by == "human"  # 사람이 지정했다
        conn.close()

    def test_기각_버튼이_판정을_남긴다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        conn = _conn(tmp_path)
        _repo(tmp_path)
        ticket_id = _handled(conn, grounding=PERSON_GROUND)
        conn.close()

        client = TestClient(self._app(tmp_path))
        client.post(f"/queues/Q7/{ticket_id}/decline")

        conn = _conn(tmp_path)
        assert resolution_domain.get(conn, ticket_id).promotion_declined_at
        assert promotion.awaiting_decision(conn) == []
        conn.close()

    def test_1국면임을_화면이_밝힌다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        conn = _conn(tmp_path)
        _repo(tmp_path)
        _handled(conn)
        conn.close()

        html = TestClient(self._app(tmp_path, phase=1)).get("/queues/Q7").text
        assert "1국면이라 자동 승격이 없다" in html
