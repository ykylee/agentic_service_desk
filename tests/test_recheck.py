"""WBS-4.8.4 — 표본 재검증 (FR-50, §5.6.7 · §5.6.2 · §6.8.4-a).

§5.6 이 경계한 고장은 **검증 라벨만 붙는 것**이다. 대책 셋은 일어나기 전에 막고,
이것은 **일어난 뒤에 재는** 유일한 장치다. 여기서 지키는 것은 여섯.

    1. **위험도 순으로 뽑는다** — 자동 승격분이 가장 앞이다 (§6.8.4-a)
    2. **같은 건을 두 번 뽑지 않는다** — 분모만 부풀고 새로 아는 것은 없다
    3. **뽑기만 하고 보지 않은 것은 분모가 아니다** — 부하와 품질을 섞지 않는다
    4. **다르다면 사유를 받는다** — 사유 없는 불일치는 기록으로 쓸 수 없다
    5. **Q6 클릭이 기록된다** — 남지 않으면 재는 장치가 재야 할 것을 못 본다
    6. **대기열이 아니다** — 대기열에 넣으면 이 장치가 재려던 고장이 여기서 일어난다
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.operations import qna_state, recheck, tracking
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.web import metrics
from agentic_service_desk.web.app import create_app


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _settings(tmp_path, **over):  # noqa: ANN001, ANN202
    base = dict(
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
        stage="S3",
    )
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _ticket(conn, tid: str, *, promoted_by: str | None, at: str) -> None:  # noqa: ANN001
    conn.execute(
        "INSERT INTO ticket (id, source, state, opened_at, state_at) "
        "VALUES (?, 'qna', 'closed', ?, ?)",
        (tid, at, at),
    )
    conn.execute(
        "INSERT INTO ticket_resolution (ticket_id, generalized_question, answer, "
        "grounding, invalidation, confirmed_at, promoted_item_id, promoted_by) "
        "VALUES (?, '결재 한도는 어떻게 정해지나', '부서 등급으로 정해진다', "
        "'[\"k-1\"]', '{\"kind\": \"periodic\"}', ?, ?, ?)",
        (tid, at, f"item-{tid}" if promoted_by else None, promoted_by),
    )
    conn.commit()


def _review(conn, rid: str, *, kind: str, by: str, outcome: str, at: str) -> None:  # noqa: ANN001
    conn.execute(
        "INSERT INTO review (id, qna_item_id, kind, outcome, draft_body, grounding, "
        "reviewed_by, reviewed_at) VALUES (?, NULL, ?, ?, '통과시킨 본문', '[]', ?, ?)",
        (rid, kind, outcome, by, at),
    )
    conn.commit()


def _implicit(conn, qid: str, at: str) -> None:  # noqa: ANN001
    conn.execute(
        "INSERT INTO raw_question (id, title, body, asker_account, created_at, collected_at) "
        "VALUES (?, '', '휴가 신청은 며칠 전에 올려야 하나요', 'user', ?, ?)",
        (f"pq-{qid}", at, at),
    )
    conn.execute(
        "INSERT INTO qna_item (id, parent_question_id, origin, state, resolution_grade, "
        "opened_at, closed_at) VALUES (?, ?, 'parent', ?, ?, ?, ?)",
        (qid, f"pq-{qid}", qna_state.RESOLVED, qna_state.IMPLICIT, at, at),
    )
    conn.commit()


NOW = "2026-08-29T09:00:00+00:00"


class TestRiskOrder:
    """§5.6.2 의 위험도 순으로 뽑는다."""

    def test_자동_승격이_가장_먼저다(self, tmp_path) -> None:
        """사람이 본 적이 없으므로 재검증이 **유일한 안전망**이다 (§6.8.4-a)."""
        conn = _conn(tmp_path)
        _review(conn, "r-1", kind="answer", by="human", outcome="passed", at=NOW)
        _ticket(conn, "t-human", promoted_by="human", at=NOW)
        _ticket(conn, "t-gate", promoted_by="gate", at=NOW)

        [first, *_] = recheck.candidates(conn)
        assert first.point == recheck.AUTO_PROMOTION
        assert first.subject_id == "t-gate"
        conn.close()

    def test_반려는_뽑지_않는다(self, tmp_path) -> None:
        """반려에는 사유가 붙고, 사유를 쓰려면 읽어야 한다 — 형식적이기 어렵다."""
        conn = _conn(tmp_path)
        _review(conn, "r-1", kind="answer", by="human", outcome="rejected", at=NOW)
        _review(conn, "r-2", kind="answer", by="agent", outcome="passed", at=NOW)
        assert recheck.candidates(conn) == []
        conn.close()

    def test_같은_티켓이_두_지점에서_뽑히지_않는다(self, tmp_path) -> None:
        """한 번의 재검토가 분모를 둘 늘리면 **본 만큼이 아니라 센 만큼** 좋아진다."""
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="human", at=NOW)
        points = [c.point for c in recheck.candidates(conn) if c.subject_id == "t-1"]
        assert points == [recheck.PROMOTION]
        conn.close()

    def test_승격되지_않은_종결_기록도_대상이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by=None, at=NOW)
        assert [c.point for c in recheck.candidates(conn)] == [recheck.RESOLUTION]
        conn.close()

    def test_같은_위험도면_최근_것부터다(self, tmp_path) -> None:
        """오래된 것부터 보면 **지금의 승인 습관이 아니라 몇 달 전의 습관**을 잰다."""
        conn = _conn(tmp_path)
        _review(conn, "r-old", kind="answer", by="human", outcome="passed",
                at="2026-01-01T00:00:00+00:00")
        _review(conn, "r-new", kind="answer", by="human", outcome="passed", at=NOW)
        assert [c.subject_id for c in recheck.candidates(conn)] == ["r-new", "r-old"]
        conn.close()


class TestSelection:
    """뽑은 것은 남고, 두 번 뽑히지 않는다."""

    def test_뽑은_것은_표에_남는다(self, tmp_path) -> None:
        """매번 새로 뽑으면 **사람이 집어 든 건이 사라진다.**"""
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="gate", at=NOW)
        [sample] = recheck.select(conn, size=1)
        assert recheck.pending(conn)[0].id == sample.id
        conn.close()

    def test_같은_건은_다시_뽑히지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="gate", at=NOW)
        recheck.select(conn, size=5)
        assert recheck.select(conn, size=5) == []
        assert len(recheck.pending(conn)) == 1
        conn.close()

    def test_주기가_되기_전에는_뽑지_않는다(self, tmp_path) -> None:
        """배치 주기(분 단위)로 뽑으면 재검증이 **이중 작업**이 된다."""
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="gate", at=NOW)
        now = datetime.now(UTC)
        assert recheck.due(conn, period_days=7, now=now)
        recheck.select(conn, size=1, now=now)
        assert not recheck.due(conn, period_days=7, now=now + timedelta(days=3))
        assert recheck.due(conn, period_days=7, now=now + timedelta(days=8))
        conn.close()

    def test_주기가_0_이면_돌지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        assert not recheck.due(conn, period_days=0)
        conn.close()


class TestVerdict:
    """다시 본 결과."""

    def test_다르다면_사유가_필요하다(self, tmp_path) -> None:
        """사유 없는 불일치는 **무엇을 고쳐야 하는지**를 가리키지 못한다."""
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="gate", at=NOW)
        [sample] = recheck.select(conn, size=1)
        with pytest.raises(recheck.NotPending):
            recheck.decide(conn, sample.id, agreed=False, note="   ")
        assert recheck.pending(conn)  # 아직 판정되지 않았다
        conn.close()

    def test_같다에는_사유가_없어도_된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="gate", at=NOW)
        [sample] = recheck.select(conn, size=1)
        assert recheck.decide(conn, sample.id, agreed=True).state == recheck.AGREED
        conn.close()

    def test_한_번_판정한_표본은_다시_누를_수_없다(self, tmp_path) -> None:
        """뒤집을 수 있게 두면 **일치율이 조용히 달라진다.**"""
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="gate", at=NOW)
        [sample] = recheck.select(conn, size=1)
        recheck.decide(conn, sample.id, agreed=True)
        with pytest.raises(recheck.NotPending):
            recheck.decide(conn, sample.id, agreed=False, note="다시 보니 틀렸다")
        conn.close()


class TestAgreement:
    """FR-50 의 일치율."""

    def test_판정한_것만_분모다(self, tmp_path) -> None:
        """대기 중인 표본을 넣으면 **밀릴수록 일치율이 떨어진다** — 부하의 신호다."""
        conn = _conn(tmp_path)
        for i in range(3):
            _ticket(conn, f"t-{i}", promoted_by="gate", at=NOW)
        samples = recheck.select(conn, size=3)
        recheck.decide(conn, samples[0].id, agreed=True)
        recheck.decide(conn, samples[1].id, agreed=False, note="근거가 답을 받치지 않는다")

        result = recheck.agreement(conn)
        assert result.decided == 2
        assert result.waiting == 1
        assert result.rate == pytest.approx(0.5)
        conn.close()

    def test_판정한_것이_없으면_0퍼센트가_아니라_없음이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="gate", at=NOW)
        recheck.select(conn, size=1)
        assert recheck.agreement(conn).rate is None
        rows = dict(metrics.agent_status(conn).rows)
        assert "아직 판정한 표본이 없다" in rows["표본 재검증 일치율"]
        conn.close()

    def test_일치율이_현황에_뜬다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="gate", at=NOW)
        [sample] = recheck.select(conn, size=1)
        recheck.decide(conn, sample.id, agreed=True)
        rows = dict(metrics.agent_status(conn).rows)
        assert "100%" in rows["표본 재검증 일치율"]
        conn.close()


class TestUpgradeIsRecorded:
    """§5.6.2 가 최고 위험으로 꼽은 클릭이 남는다."""

    def test_Q6_상향이_기록되어_표본에_들어온다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _implicit(conn, "q-1", NOW)
        assert recheck.candidates(conn) == []  # 아직 아무도 누르지 않았다

        tracking.upgrade(conn, "q-1")
        [candidate] = recheck.candidates(conn)
        assert candidate.point == recheck.UPGRADE
        assert candidate.subject_id == "q-1"
        conn.close()

    def test_이용자의_해결_표시는_재검증_대상이_아니다(self, tmp_path) -> None:
        """우리의 판정이 아니라 이용자의 표시라 **재검증할 클릭이 없다.**"""
        conn = _conn(tmp_path)
        _implicit(conn, "q-1", NOW)
        conn.execute(
            "UPDATE qna_item SET resolution_grade = ? WHERE id = ?",
            (qna_state.EXPLICIT, "q-1"),
        )
        conn.commit()
        assert recheck.candidates(conn) == []
        conn.close()


class TestContext:
    """다시 보려면 무엇을 읽어야 하는가."""

    def test_그때_본_것을_함께_보여_준다(self, tmp_path) -> None:
        """원래 판정만 보여 주면 재검증이 아니라 **확인**이 된다."""
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="gate", at=NOW)
        [sample] = recheck.select(conn, size=1)
        rows = dict(recheck.context(conn, sample))
        assert rows["일반화된 질문"] == "결재 한도는 어떻게 정해지나"
        assert rows["승격된 지식 항목"] == "item-t-1"
        conn.close()

    def test_검수_승인은_통과시킨_본문을_보여_준다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _review(conn, "r-1", kind="content", by="human", outcome="passed", at=NOW)
        [sample] = recheck.select(conn, size=1)
        assert sample.point == recheck.CONTENT_REVIEW
        assert dict(recheck.context(conn, sample))["통과시킨 본문"] == "통과시킨 본문"
        conn.close()


class TestScreen:
    """화면 — **대기열이 아니다** (§8.1)."""

    def test_대기열_목록에_들어가지_않는다(self, tmp_path) -> None:
        """아홉째 대기열이 되면 밀린 손이 여기까지 와서 형식적으로 누른다."""
        client = TestClient(create_app(_settings(tmp_path)))
        index = client.get("/").text
        assert "/recheck" not in index
        assert "/recheck" in client.get("/status").text

    def test_다시_보고_누르면_일치율이_움직인다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="gate", at=NOW)
        [sample] = recheck.select(conn, size=1)
        conn.close()

        client = TestClient(create_app(_settings(tmp_path)))
        body = client.get("/recheck").text
        assert "사람이 본 적이 없다" in body
        assert "결재 한도는 어떻게 정해지나" in body

        client.post(f"/recheck/{sample.id}", data={"verdict": "agreed"},
                    follow_redirects=False)
        conn = _conn(tmp_path)
        assert recheck.agreement(conn).rate == pytest.approx(1.0)
        conn.close()

    def test_사유_없는_불일치는_화면이_거절한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _ticket(conn, "t-1", promoted_by="gate", at=NOW)
        [sample] = recheck.select(conn, size=1)
        conn.close()

        client = TestClient(create_app(_settings(tmp_path)))
        page = client.post(
            f"/recheck/{sample.id}", data={"verdict": "disagreed", "note": ""}
        )
        conn = _conn(tmp_path)
        assert recheck.agreement(conn).decided == 0
        conn.close()
        # **거절은 화면이 말한다** — 조용히 아무 일도 없으면 사람은 눌렀다고 믿는다.
        assert "사유가 필요하다" in page.text
