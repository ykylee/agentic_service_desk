"""WBS-4.4.5 — Q2 답변 검수 대기열 (FR-45, §6.4.4, §5.5.6, §5.6.5).

**Q2 는 판정 대기열이지 작업 대기열이 아니다.** 조사·수정이 필요한 일이 아니라 보고
누르면 끝나는 일이므로 상태 기계도 진행 중 표시도 없다 — 화면이 달라야 하는 이유다.

여기서 지키는 것은 넷.

    1. 판정 화면은 **상태 전이가 없다** (FR-45)
    2. **어디를 먼저 볼지** 화면이 말해 준다 (§5.6.5)
    3. **반려에는 사유가 필요하다** — 사유 없는 반려는 기록으로 쓸 수 없다 (§5.5.6)
    4. 에이전트가 반려한 것도 사람이 뒤집을 수 있다 (§5.5.3)
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline import draft_store
from agentic_service_desk.pipeline.answer import Confidence, Draft, Statement
from agentic_service_desk.pipeline.review import Reject, Verdict, distribution
from agentic_service_desk.web.app import create_app

SOURCE = "결재 한도는 부서 등급으로 정해진다."


def _settings(tmp_path, **over):  # noqa: ANN001, ANN202
    base = dict(
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
        stage="S2",
    )
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _item(tmp_path) -> KnowledgeItem:
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    item = KnowledgeItem(
        title="결재 한도 결정 규칙",
        body=SOURCE,
        provenance=[Provenance(commit="a" * 40)],
        invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90),
    )
    repo.save(item)
    return item


def _draft(item_id: str, weak: bool = True) -> Draft:
    statements = [
        Statement(text="결재 한도는 부서 등급으로 정해진다.",
                  confidence=Confidence.CONFIRMED, grounding=(item_id,))
    ]
    if weak:
        statements.append(
            Statement(text="따라서 인사이동 시 한도가 바뀔 수 있다.",
                      confidence=Confidence.INFERRED, grounding=(item_id,))
        )
    return Draft(
        statements=tuple(statements),
        grounding=(item_id,),
        unanswered=("현재 값은 조회 대상이 아니다",),
    )


def _queued(tmp_path, *, verdict=None, weak=True):  # noqa: ANN001, ANN202
    item = _item(tmp_path)
    conn = _conn(tmp_path)
    draft_id = draft_store.save(
        conn,
        question="제 결재 한도가 왜 이런가요?",
        draft=_draft(item.id, weak=weak),
        verdict=verdict,
    )
    conn.close()
    return draft_id, item


class TestJudgementScreen:
    """FR-45 — 판정은 목록과 버튼이면 된다."""

    def test_상태_전이가_없다(self, tmp_path) -> None:
        # 작업 화면과 달리 진행 중·보류가 없다. 보고 누르면 끝난다.
        _queued(tmp_path)
        html = TestClient(create_app(_settings(tmp_path))).get("/queues/Q2").text
        assert "승인" in html and "반려" in html
        assert "진행중" not in html and "보류" not in html

    def test_S1_에는_뜨지_않는다(self, tmp_path) -> None:
        # 답변 초안은 S2 부터다 (FR-59).
        _queued(tmp_path)
        html = TestClient(create_app(_settings(tmp_path, stage="S1"))).get("/").text
        assert "Q2" not in html

    def test_비어_있으면_그렇게_말한다(self, tmp_path) -> None:
        _conn(tmp_path).close()
        html = TestClient(create_app(_settings(tmp_path))).get("/queues/Q2").text
        assert "검수를 기다리는 초안이 없다" in html


class TestWhereToLookFirst:
    """§5.6.5 — 사람은 약한 지점만 집중해 보면 된다."""

    def test_에이전트가_반려했으면_그곳을_먼저_보라고_한다(self, tmp_path) -> None:
        _queued(tmp_path, verdict=Verdict(passed=False, reason=Reject.P1, detail="수치가 없다"))
        conn = _conn(tmp_path)
        d = draft_store.pending(conn)[0]
        assert "P1" in d.look_here_first

    def test_약한_지점이_있으면_그것을_가리킨다(self, tmp_path) -> None:
        _queued(tmp_path, verdict=Verdict(passed=True))
        conn = _conn(tmp_path)
        d = draft_store.pending(conn)[0]
        assert "약한 진술 1개" in d.look_here_first

    def test_다_확인됐으면_그렇게_말한다(self, tmp_path) -> None:
        _queued(tmp_path, verdict=Verdict(passed=True), weak=False)
        conn = _conn(tmp_path)
        assert "약한 지점도 없다" in draft_store.pending(conn)[0].look_here_first

    def test_진술별_강도가_화면에_보인다(self, tmp_path) -> None:
        # 본문만 남기면 어디를 먼저 볼지가 사라진다.
        _queued(tmp_path)
        html = TestClient(create_app(_settings(tmp_path))).get("/queues/Q2").text
        assert "확인됨" in html and "추론" in html

    def test_근거_원문이_함께_보인다(self, tmp_path) -> None:
        _queued(tmp_path)
        html = TestClient(create_app(_settings(tmp_path))).get("/queues/Q2").text
        assert SOURCE in html

    def test_근거가_사라졌으면_그렇게_말한다(self, tmp_path) -> None:
        # 조용히 빈칸으로 두면 검수자가 근거가 없는 줄 모르고 승인한다.
        conn = _conn(tmp_path)
        KnowledgeRepository(tmp_path / "knowledge").ensure_initialized()
        draft_store.save(conn, question="질문", draft=_draft("k-사라짐"))
        conn.close()

        html = TestClient(create_app(_settings(tmp_path))).get("/queues/Q2").text
        assert "원문을 찾을 수 없다" in html

    def test_모른다고_밝힌_부분이_보인다(self, tmp_path) -> None:
        _queued(tmp_path)
        html = TestClient(create_app(_settings(tmp_path))).get("/queues/Q2").text
        assert "현재 값은 조회 대상이 아니다" in html


class TestDecide:
    def test_승인하면_대기열에서_빠진다(self, tmp_path) -> None:
        draft_id, _ = _queued(tmp_path)
        client = TestClient(create_app(_settings(tmp_path)))
        client.post(f"/queues/Q2/{draft_id}/decide", data={"approved": "1"})

        conn = _conn(tmp_path)
        assert draft_store.pending(conn) == []
        assert draft_store.get(conn, draft_id).state == draft_store.APPROVED

    def test_반려하면_사유가_기록된다(self, tmp_path) -> None:
        # FR-22 — 반려 사유별 분포를 집계할 수 있다.
        draft_id, _ = _queued(tmp_path)
        client = TestClient(create_app(_settings(tmp_path)))
        client.post(
            f"/queues/Q2/{draft_id}/decide",
            data={"approved": "0", "reason": "P2", "detail": "항상 이라고 단정"},
        )

        conn = _conn(tmp_path)
        assert distribution(conn).by_reason["P2"] == 1
        assert draft_store.get(conn, draft_id).state == draft_store.REJECTED

    def test_사유_없는_반려는_받지_않는다(self, tmp_path) -> None:
        # 사유 없는 반려는 기록으로 쓸 수 없다 (§5.5.6).
        draft_id, _ = _queued(tmp_path)
        client = TestClient(create_app(_settings(tmp_path)))
        client.post(f"/queues/Q2/{draft_id}/decide", data={"approved": "0", "reason": ""})

        conn = _conn(tmp_path)
        assert draft_store.get(conn, draft_id).state == draft_store.PENDING
        assert distribution(conn).total == 0

    def test_사람_판정임이_남는다(self, tmp_path) -> None:
        # 에이전트 판정과 사람 판정이 섞이면 자동 검수의 학습 자료가 오염된다.
        draft_id, _ = _queued(tmp_path)
        TestClient(create_app(_settings(tmp_path))).post(
            f"/queues/Q2/{draft_id}/decide", data={"approved": "1"}
        )
        conn = _conn(tmp_path)
        assert conn.execute("SELECT reviewed_by FROM review").fetchone()["reviewed_by"] == "human"

    def test_두_번_판정하지_않는다(self, tmp_path) -> None:
        draft_id, _ = _queued(tmp_path)
        client = TestClient(create_app(_settings(tmp_path)))
        client.post(f"/queues/Q2/{draft_id}/decide", data={"approved": "1"})
        client.post(f"/queues/Q2/{draft_id}/decide", data={"approved": "0", "reason": "P1"})

        assert distribution(_conn(tmp_path)).total == 1


class TestAgentRejectionIsNotFinal:
    """§5.5.3 — 1국면 사람 판정 기록이 2국면 자동 검수의 기준이 된다."""

    def test_에이전트가_반려해도_대기열에_오른다(self, tmp_path) -> None:
        # 버리면 사람이 뒤집을 기회가 사라지고, 자동 판정이 틀렸을 때 알 길이 없어진다.
        _queued(tmp_path, verdict=Verdict(passed=False, reason=Reject.P1))
        conn = _conn(tmp_path)
        assert len(draft_store.pending(conn)) == 1

    def test_사람이_뒤집을_수_있다(self, tmp_path) -> None:
        draft_id, _ = _queued(tmp_path, verdict=Verdict(passed=False, reason=Reject.P1))
        TestClient(create_app(_settings(tmp_path))).post(
            f"/queues/Q2/{draft_id}/decide", data={"approved": "1"}
        )
        conn = _conn(tmp_path)
        assert draft_store.get(conn, draft_id).state == draft_store.APPROVED
        assert distribution(conn).passed == 1


class TestDistributionIsSplitByReviewer:
    """§8.3·§5.5.6 — 섞으면 읽을 수 없다."""

    def test_주체별로_가른다(self, tmp_path) -> None:
        """반려율이 신뢰 지표인 것은 **사람 판정**일 때이고, 자동 검수의 학습 자료가
        되는 것도 사람 판정 기록이다.

        에이전트 판정은 초안을 큐에 올릴 때 **함께 기록된다** — 초안에만 적어 두면
        판정이 *물건*에는 있고 *사건*에는 없어 분포가 영원히 비어 보인다
        (WBS-4.5.8 의 라이브 지표가 잡았다).
        """
        draft_id, _ = _queued(tmp_path, verdict=Verdict(passed=False, reason=Reject.P1))
        conn = _conn(tmp_path)
        assert distribution(conn, reviewed_by="agent").rejected == 1
        conn.close()

        TestClient(create_app(_settings(tmp_path))).post(
            f"/queues/Q2/{draft_id}/decide", data={"approved": "1"}
        )
        conn = _conn(tmp_path)
        assert distribution(conn, reviewed_by="human").passed == 1
        assert distribution(conn, reviewed_by="agent").rejected == 1
        assert distribution(conn).total == 2

    def test_화면이_둘을_나눠_보여준다(self, tmp_path) -> None:
        draft_id, _ = _queued(tmp_path)
        client = TestClient(create_app(_settings(tmp_path)))
        client.post(f"/queues/Q2/{draft_id}/decide", data={"approved": "1"})
        html = client.get("/queues/Q2").text
        assert "내 판정" in html
        assert "내가 에이전트를 얼마나 믿는가" in html


class TestDistributionOnScreen:
    def test_근거_부족_신호가_화면에_뜬다(self, tmp_path) -> None:
        # P1·P5 가 몰리면 근거가 부족하다는 뜻이다 (§5.5.6).
        draft_id, _ = _queued(tmp_path)
        client = TestClient(create_app(_settings(tmp_path)))
        client.post(
            f"/queues/Q2/{draft_id}/decide", data={"approved": "0", "reason": "P1", "detail": "x"}
        )
        html = client.get("/queues/Q2").text
        assert "P1·P5 가" in html
        assert "근거가 부족하다는 뜻" in html
