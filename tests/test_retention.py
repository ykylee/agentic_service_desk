"""WBS-4.8.3 — 보존 만료 (FR-51, PO-4).

전건 티켓 발행(D19)이라 기록이 무한정 쌓이는데 **사내에 정해진 보존 정책이 없다.**
그래서 기본은 무제한이되 그것을 *정책 부재*가 아니라 **결정**으로 기록한다.
여기서 지키는 것은 다섯.

    1. **기본은 무제한** — 설정이 없으면 아무것도 지우지 않는다
    2. **만료는 원문을 지우는 것이지 셈을 지우는 것이 아니다** — 통계의 분모가 남는다
    3. **지식 항목과 답변 이력은 만료되지 않는다** — 감사와 정정 추적이 끊기지 않게
    4. **사람이 아직 봐야 할 것은 만료하지 않는다** — 손안에서 사라지지 않게
    5. **되돌릴 수 없는 쪽으로 기울지 않는다** — 이상한 설정에는 지우지 않는다
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.operations import qna_state, recheck, retention
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.web import metrics
from agentic_service_desk.web.app import create_app
from agentic_service_desk.worker.runner import BatchRunner

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


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


def _closed(  # noqa: ANN202
    conn,  # noqa: ANN001
    qid: str,
    *,
    days_ago: float,
    grade: str | None = qna_state.EXPLICIT,
    state: str = qna_state.RESOLVED,
) -> None:
    at = (NOW - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO raw_question (id, title, body, asker_account, created_at, collected_at) "
        "VALUES (?, '', '결재 한도는 어떻게 정해지나요 (사번 12345)', 'user', ?, ?)",
        (f"pq-{qid}", at, at),
    )
    conn.execute(
        "INSERT INTO raw_answer (id, question_id, body, author_account, created_at, "
        "collected_at) VALUES (?, ?, '부서 등급으로 정해집니다', 'staff', ?, ?)",
        (f"ra-{qid}", f"pq-{qid}", at, at),
    )
    conn.execute(
        "INSERT INTO raw_followup (id, question_id, body, author_account, created_at, "
        "collected_at) VALUES (?, ?, '감사합니다', 'user', ?, ?)",
        (f"rf-{qid}", f"pq-{qid}", at, at),
    )
    conn.execute(
        "INSERT INTO raw_resolution (question_id, resolved, grade, resolved_at, "
        "collected_at) VALUES (?, 1, 'explicit', ?, ?)",
        (f"pq-{qid}", at, at),
    )
    conn.execute(
        "INSERT INTO qna_item (id, parent_question_id, origin, asker_id, state, "
        "resolution_grade, opened_at, closed_at) VALUES (?, ?, 'parent', 'emp-77', ?, ?, ?, ?)",
        (qid, f"pq-{qid}", state, grade, at, at),
    )
    conn.commit()


def _manual(conn, qid: str, *, days_ago: float) -> None:  # noqa: ANN001
    at = (NOW - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO qna_item (id, origin, asker_id, state, resolution_grade, "
        "opened_at, closed_at) VALUES (?, 'manual', 'emp-77', ?, ?, ?, ?)",
        (qid, qna_state.RESOLVED, qna_state.EXPLICIT, at, at),
    )
    conn.execute(
        "INSERT INTO manual_entry (qna_item_id, question, answer, registered_by, "
        "registered_at) VALUES (?, '제 결재가 왜 반려됐나요 (사번 12345)', "
        "'부서 등급 때문입니다', 'ops', ?)",
        (qid, at),
    )
    conn.commit()


def _count(conn, sql: str, params: tuple = ()) -> int:  # noqa: ANN001
    return conn.execute(sql, params).fetchone()[0]


class TestDefaultIsUnlimited:
    """**기본은 무제한이고, 그것이 결정이다** (PO-4)."""

    def test_설정이_없으면_아무것도_지우지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=3650)
        conn.close()

        BatchRunner(_settings(tmp_path))._expire()

        conn = _conn(tmp_path)
        assert _count(conn, "SELECT count(*) FROM raw_question") == 1
        assert _count(conn, "SELECT count(*) FROM retention_run") == 0
        conn.close()

    def test_기본값이_무제한이다(self) -> None:
        assert Settings(_env_file=None).retention_days is None

    def test_화면이_무제한을_결정으로_말한다(self, tmp_path) -> None:
        """설정 줄이 빈 것과 두지 않기로 정한 것이 화면에서 같아 보이면 안 된다."""
        conn = _conn(tmp_path)
        rows = dict(metrics.qna_status(conn, retention_days=None).rows)
        assert "두지 않기로 정했다" in rows["보존"]
        conn.close()

    def test_0_이하는_거부한다(self, tmp_path) -> None:
        """'즉시 지운다'를 설정으로 표현할 수 있게 두면 **오타 하나가 원문을 지운다.**"""
        conn = _conn(tmp_path)
        with pytest.raises(retention.InvalidRetention):
            retention.expire(conn, retention_days=0, now=NOW)
        conn.close()

    def test_이상한_설정에도_배치는_지우지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=3650)
        conn.close()
        BatchRunner(_settings(tmp_path, retention_days=0))._expire()
        conn = _conn(tmp_path)
        assert _count(conn, "SELECT count(*) FROM raw_question") == 1
        conn.close()


class TestWhatGoes:
    """원문은 지운다."""

    def test_기간이_지난_원문이_사라진다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        report = retention.expire(conn, retention_days=365, now=NOW)

        assert report.questions == 1
        assert (report.answers, report.followups, report.resolutions) == (1, 1, 1)
        assert _count(conn, "SELECT count(*) FROM raw_question") == 0
        assert _count(conn, "SELECT count(*) FROM raw_answer") == 0
        conn.close()

    def test_수동_등록_원문도_사라진다(self, tmp_path) -> None:
        """붙여넣은 메신저 문의는 **개인 사정이 가장 많이 남는 자리**다."""
        conn = _conn(tmp_path)
        _manual(conn, "q-1", days_ago=400)
        report = retention.expire(conn, retention_days=365, now=NOW)
        assert report.manual_entries == 1
        assert _count(conn, "SELECT count(*) FROM manual_entry") == 0
        conn.close()

    def test_식별자가_지워진다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        retention.expire(conn, retention_days=365, now=NOW)
        assert _count(conn, "SELECT count(*) FROM qna_item WHERE asker_id IS NOT NULL") == 0
        conn.close()

    def test_기간_안의_것은_남는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=100)
        assert retention.expire(conn, retention_days=365, now=NOW).changed is False
        assert _count(conn, "SELECT count(*) FROM raw_question") == 1
        conn.close()

    def test_닫힌_시각으로_잰다(self, tmp_path) -> None:
        """들어온 시각으로 재면 **오래 끌던 건이 해결되자마자 만료된다.**"""
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        conn.execute(
            "UPDATE qna_item SET closed_at = ? WHERE id = 'q-1'", (NOW.isoformat(),)
        )
        conn.commit()
        assert retention.expirable(conn, retention_days=365, now=NOW) == []
        conn.close()

    def test_열려_있는_건은_만료되지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400, grade=None, state=qna_state.PUBLISHED)
        assert retention.expirable(conn, retention_days=365, now=NOW) == []
        conn.close()


class TestWhatStays:
    """셈과 지식은 남는다 — **만료는 원문을 지우는 것이다.**"""

    def test_통계의_분모가_남는다(self, tmp_path) -> None:
        """`qna_item` 을 지우면 **작년 해결률이 사라진다** — 그것은 만료가 아니라 소실이다."""
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        retention.expire(conn, retention_days=365, now=NOW)

        assert _count(conn, "SELECT count(*) FROM qna_item") == 1
        by_label = {m.label: m for m in metrics.core(conn)}
        assert by_label["명시적 해결률"].value == pytest.approx(1.0)
        assert by_label["명시적 해결률"].denominator == 1
        conn.close()

    def test_티켓과_종결_기록은_남는다(self, tmp_path) -> None:
        """PO-3 가 이미 일반화한 형태다 — 지울 개인 요소가 없다."""
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        at = (NOW - timedelta(days=400)).isoformat()
        conn.execute(
            "INSERT INTO ticket (id, source, qna_item_id, state, opened_at, state_at) "
            "VALUES ('t-1', 'qna', 'q-1', 'closed', ?, ?)",
            (at, at),
        )
        conn.execute(
            "INSERT INTO ticket_resolution (ticket_id, generalized_question, answer, "
            "grounding, confirmed_at, promoted_item_id, promoted_by) "
            "VALUES ('t-1', '결재 한도는 어떻게 정해지나', '부서 등급', '[\"k-1\"]', "
            "?, 'item-1', 'human')",
            (at,),
        )
        conn.commit()

        retention.expire(conn, retention_days=365, now=NOW)
        assert _count(conn, "SELECT count(*) FROM ticket") == 1
        assert _count(conn, "SELECT count(*) FROM ticket_resolution") == 1
        conn.close()

    def test_답변_이력은_남는다(self, tmp_path) -> None:
        """**무엇을 근거로 답했는가**(D20) — 지우면 감사와 정정 추적이 끊긴다."""
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        conn.execute(
            "INSERT INTO answer_record (id, qna_item_id, body, author_kind, state) "
            "VALUES ('rec-1', 'q-1', '부서 등급으로 정해진다', 'bot', 'published')"
        )
        conn.commit()
        retention.expire(conn, retention_days=365, now=NOW)
        assert _count(conn, "SELECT count(*) FROM answer_record") == 1
        conn.close()


class TestNotYours:
    """사람이 아직 봐야 할 것은 만료하지 않는다."""

    def test_Q6_확인_대기는_남는다(self, tmp_path) -> None:
        """원문이 없으면 **확인할 수 없다** (§5.3.2)."""
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400, grade=qna_state.IMPLICIT)
        assert retention.expirable(conn, retention_days=365, now=NOW) == []
        conn.close()

    def test_Q2_검수_대기는_남는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        conn.execute(
            "INSERT INTO answer_draft (id, qna_item_id, question, statements, grounding, "
            "unanswered, state, created_at) VALUES ('d-1', 'q-1', '물음', '[]', '[]', "
            "'[]', 'pending', ?)",
            ((NOW - timedelta(days=400)).isoformat(),),
        )
        conn.commit()
        assert retention.expirable(conn, retention_days=365, now=NOW) == []
        conn.close()

    def test_Q7_승격_대기는_남는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        at = (NOW - timedelta(days=400)).isoformat()
        conn.execute(
            "INSERT INTO ticket (id, source, qna_item_id, state, opened_at, state_at) "
            "VALUES ('t-1', 'qna', 'q-1', 'auto_closed', ?, ?)",
            (at, at),
        )
        conn.execute(
            "INSERT INTO ticket_resolution (ticket_id, generalized_question, answer, "
            "grounding, confirmed_at) VALUES ('t-1', '일반화', '답', '[\"k-1\"]', ?)",
            (at,),
        )
        conn.commit()
        assert retention.expirable(conn, retention_days=365, now=NOW) == []
        conn.close()

    def test_재검증_표본은_남는다(self, tmp_path) -> None:
        """다시 볼 것이 손안에 있는데 사라지면 **재검증이 성립하지 않는다** (§5.6.7)."""
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        conn.execute(
            "INSERT INTO recheck (id, point, subject_id, original_by, original_at, "
            "selected_at, state) VALUES ('rc-1', ?, 'q-1', 'human', ?, ?, ?)",
            (
                recheck.UPGRADE,
                (NOW - timedelta(days=400)).isoformat(),
                NOW.isoformat(),
                recheck.PENDING,
            ),
        )
        conn.commit()
        assert retention.expirable(conn, retention_days=365, now=NOW) == []
        conn.close()


class TestRecord:
    """지운 뒤에는 건수가 남는다."""

    def test_만료가_이력에_남는다(self, tmp_path) -> None:
        """무엇이 몇 건 사라졌는지가 없으면 **"원래 없었다"와 "지웠다"를 구분할 수 없다.**"""
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        retention.expire(conn, retention_days=365, now=NOW)
        run = retention.last_run(conn)
        assert run["questions"] == 1
        assert run["anonymized"] == 1
        conn.close()

    def test_지울_것이_없으면_이력도_없다(self, tmp_path) -> None:
        """이력은 "무엇이 사라졌는가"의 기록이지 "배치가 돌았는가"의 기록이 아니다."""
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=10)
        retention.expire(conn, retention_days=365, now=NOW)
        assert retention.last_run(conn) is None
        conn.close()

    def test_이미_만료된_건은_다시_대상이_되지_않는다(self, tmp_path) -> None:
        """**라이브에서 밟았다.** `qna_item` 은 남기므로 조건이 계속 참이었고, 배치가
        돌 때마다 0건짜리 만료가 기록돼 **진짜 만료가 그 사이에 묻혔다.**
        """
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        first = retention.expire(conn, retention_days=365, now=NOW)
        assert first.changed

        assert retention.expirable(conn, retention_days=365, now=NOW) == []
        assert retention.expire(conn, retention_days=365, now=NOW).changed is False
        assert _count(conn, "SELECT count(*) FROM retention_run") == 1
        conn.close()

    def test_화면이_설정과_마지막_만료를_말한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        retention.expire(conn, retention_days=365, now=NOW)
        rows = dict(metrics.qna_status(conn, retention_days=365).rows)
        assert "365일" in rows["보존"]
        assert "마지막 만료" in rows["보존"]
        conn.close()

    def test_배치가_돌면_원문이_사라진다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _closed(conn, "q-1", days_ago=400)
        conn.close()

        BatchRunner(_settings(tmp_path, retention_days=365))._expire()

        conn = _conn(tmp_path)
        assert _count(conn, "SELECT count(*) FROM raw_question") == 0
        assert _count(conn, "SELECT count(*) FROM qna_item") == 1
        conn.close()

    def test_현황_화면이_뜬다(self, tmp_path) -> None:
        client = TestClient(create_app(_settings(tmp_path, retention_days=365)))
        assert "원문" in client.get("/status").text
