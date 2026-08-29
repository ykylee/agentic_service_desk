"""WBS-4.8.2 — 알림 웹훅 (ADR-007 결정 2, O28, §8.2).

1인 겸업이 대시보드를 매일 연다는 보장이 없다면 **Q4·Q5 가 쌓여도 아무도 모른다.**
여기서 지키는 것은 여섯.

    1. **시간으로 잰다** — 위험은 몇 건인가가 아니라 얼마나 오래 노출됐는가다
    2. **같은 상황으로 도배하지 않는다** — 지문이 같으면 다시 보내지 않는다
    3. **실패하면 보냈다고 적지 않는다** — 침묵보다 중복이 낫다
    4. **세는 것만 보낸다** — 질문 원문도 지식 본문도 싣지 않는다 (PO-3)
    5. **배너는 웹훅이 있어도 뜬다** — 침묵이 안전으로 읽히지 않게
    6. **알림이 못 가도 배치는 돈다** — 부수적인 것이 본체를 세우지 않는다
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.operations import alert
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.web.app import create_app

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


def _contradiction(conn, cid: str, *, days_ago: float) -> None:  # noqa: ANN001
    at = (NOW - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO contradiction (id, knowledge_item_id, proposed_title, "
        "proposed_body, provenance, detected_at, state) "
        "VALUES (?, 'k-1', '결재 한도 결정 규칙', "
        "'결재 한도는 부서 등급으로 정해진다', '[]', ?, 'open')",
        (cid, at),
    )
    conn.commit()


def _stale_answer(conn, key: str, *, days_ago: float) -> None:  # noqa: ANN001
    at = (NOW - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO lint_finding (key, kind, subject, detail, first_seen, state) "
        "VALUES (?, 'stale_answer', 'rec-1', '근거가 낡았다', ?, 'open')",
        (key, at),
    )
    conn.commit()


def _regression(conn) -> None:  # noqa: ANN001
    conn.execute(
        "INSERT INTO phase_decision (from_phase, to_phase, decided_by, reason, decided_at) "
        "VALUES (3, 2, 'system', '역행 신호: stale 비율 급등', ?)",
        (NOW.isoformat(),),
    )
    conn.commit()


class FakePost:
    """웹훅 자리에 놓는다. **보낸 것을 그대로 들고 있는다.**"""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._fail = fail

    def __call__(self, url, *, json, timeout):  # noqa: ANN001, ANN204, A002
        self.calls.append((url, json))
        if self._fail:
            raise httpx.ConnectError("연결할 수 없다")
        return httpx.Response(200, request=httpx.Request("POST", url))


class TestAgeNotCount:
    """건수가 아니라 시간으로 잰다."""

    def test_임계를_넘긴_것이_없으면_조용하다(self, tmp_path) -> None:
        """하루 만에 다섯 건이 잡혔다고 울리면 **볼 시간도 없었는데** 울린 것이다."""
        conn = _conn(tmp_path)
        for i in range(5):
            _contradiction(conn, f"c-{i}", days_ago=0.5)
        assert alert.pending(conn, neglect_hours=72, now=NOW) == []
        conn.close()

    def test_한_건이라도_오래되면_알린다(self, tmp_path) -> None:
        """건수로 잡으면 **한 건짜리 모순이 한 달 방치돼도 조용하다.**"""
        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=30)
        [found] = alert.pending(conn, neglect_hours=72, now=NOW)
        assert found.kind == alert.RISK_QUEUE
        assert "30일 전" in found.body
        conn.close()

    def test_Q4_와_Q5_를_한_알림으로_묶는다(self, tmp_path) -> None:
        """사람이 할 일이 같다 — 나눠 보내면 같은 아침에 두 번 울린다."""
        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=10)
        _stale_answer(conn, "f-1", days_ago=5)
        found = alert.pending(conn, neglect_hours=72, now=NOW)
        assert len(found) == 1
        assert "Q4 모순 1건" in found[0].body
        assert "Q5 정정 후보 1건" in found[0].body
        conn.close()

    def test_임계가_0_이면_돌지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=30)
        assert alert.pending(conn, neglect_hours=0, now=NOW) == []
        conn.close()


class TestNoSpam:
    """지문이 같으면 다시 보내지 않는다."""

    def test_같은_상황은_한_번만_간다(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=10)
        post = FakePost()
        monkeypatch.setattr(httpx, "post", post)

        for _ in range(3):
            waiting = alert.unsent(conn, alert.pending(conn, neglect_hours=72, now=NOW))
            alert.dispatch(conn, url="https://hook.example/x", alerts=waiting)
        assert len(post.calls) == 1
        conn.close()

    def test_건수가_늘어도_다시_보내지_않는다(self, tmp_path) -> None:
        """그 사이 사람이 할 수 있는 일이 달라지지 않는다."""
        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=10)
        [first] = alert.pending(conn, neglect_hours=72, now=NOW)
        alert.mark_sent(conn, first)

        _contradiction(conn, "c-2", days_ago=9)
        assert alert.unsent(conn, alert.pending(conn, neglect_hours=72, now=NOW)) == []
        conn.close()

    def test_가장_오래된_것이_처리되면_다시_알린다(self, tmp_path) -> None:
        """지문은 **가장 오래 밀린 것**이다 — 그것이 바뀌면 새 상황이다."""
        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=10)
        _contradiction(conn, "c-2", days_ago=9)
        [first] = alert.pending(conn, neglect_hours=72, now=NOW)
        alert.mark_sent(conn, first)

        conn.execute("UPDATE contradiction SET state = 'resolved' WHERE id = 'c-1'")
        conn.commit()
        [again] = alert.unsent(conn, alert.pending(conn, neglect_hours=72, now=NOW))
        assert again.fingerprint == "Q4:c-2"
        conn.close()


class TestRegression:
    """국면 자동 후퇴 (§1.3.3-c)."""

    def test_자동_후퇴가_알림으로_나간다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _regression(conn)
        [found] = alert.pending(conn, neglect_hours=72, now=NOW)
        assert found.kind == alert.PHASE_REGRESSION
        assert "3 → 2" in found.title
        assert "검수 강도" in found.body
        conn.close()

    def test_오래된_후퇴는_배너에서_내린다(self, tmp_path) -> None:
        """**후퇴는 사건이지 상태다가 아니다** — 늘 떠 있는 경고는 배경이 된다."""
        conn = _conn(tmp_path)
        conn.execute(
            "INSERT INTO phase_decision (from_phase, to_phase, decided_by, reason, "
            "decided_at) VALUES (3, 2, 'system', '역행 신호: stale 비율 급등', ?)",
            ((NOW - timedelta(days=alert.REGRESSION_DAYS + 1)).isoformat(),),
        )
        conn.commit()
        assert alert.pending(conn, neglect_hours=72, now=NOW) == []
        conn.close()

    def test_되돌린_뒤에는_알리지_않는다(self, tmp_path) -> None:
        """운영자가 다시 올렸다면 후퇴는 **이미 다뤄진 일**이다."""
        conn = _conn(tmp_path)
        _regression(conn)
        conn.execute(
            "INSERT INTO phase_decision (from_phase, to_phase, decided_by, reason, "
            "decided_at) VALUES (2, 3, 'operator', '세 축이 함께 올랐다', ?)",
            (NOW.isoformat(),),
        )
        conn.commit()
        assert alert.pending(conn, neglect_hours=72, now=NOW) == []
        conn.close()

    def test_사람이_올린_것은_알리지_않는다(self, tmp_path) -> None:
        """본인이 눌렀다 — 알릴 것이 없다."""
        conn = _conn(tmp_path)
        conn.execute(
            "INSERT INTO phase_decision (from_phase, to_phase, decided_by, reason, "
            "decided_at) VALUES (1, 2, 'operator', '세 축이 함께 올랐다', ?)",
            (NOW.isoformat(),),
        )
        conn.commit()
        assert alert.pending(conn, neglect_hours=72, now=NOW) == []
        conn.close()


class TestDelivery:
    """보내기."""

    def test_세는_것만_보낸다(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """**질문 원문도 지식 본문도 싣지 않는다** — 채널이 어디로 가는지 모른다."""
        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=10)
        post = FakePost()
        monkeypatch.setattr(httpx, "post", post)

        alert.dispatch(
            conn,
            url="https://hook.example/x",
            alerts=alert.pending(conn, neglect_hours=72, now=NOW),
        )
        [(url, payload)] = post.calls
        assert url == "https://hook.example/x"
        assert "결재 한도는 부서 등급으로 정해진다" not in payload["text"]
        assert "결재 한도 결정 규칙" not in payload["text"]
        assert "Q4 모순 1건" in payload["text"]
        conn.close()

    def test_실패하면_보냈다고_적지_않는다(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """적으면 그 경고는 **영영 가지 않는다.**"""
        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=10)
        monkeypatch.setattr(httpx, "post", FakePost(fail=True))
        sent, failures = alert.dispatch(
            conn,
            url="https://hook.example/x",
            alerts=alert.pending(conn, neglect_hours=72, now=NOW),
        )
        assert sent == []
        assert failures

        ok = FakePost()
        monkeypatch.setattr(httpx, "post", ok)
        waiting = alert.unsent(conn, alert.pending(conn, neglect_hours=72, now=NOW))
        alert.dispatch(conn, url="https://hook.example/x", alerts=waiting)
        assert len(ok.calls) == 1  # 다음 주기가 다시 시도했다
        conn.close()

    def test_웹훅이_없으면_아무것도_보내지_않는다(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=10)
        post = FakePost()
        monkeypatch.setattr(httpx, "post", post)
        sent, failures = alert.dispatch(
            conn, url="", alerts=alert.pending(conn, neglect_hours=72, now=NOW)
        )
        assert (sent, failures, post.calls) == ([], [], [])
        conn.close()

    def test_알림이_못_가도_배치는_돈다(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """부수적인 것이 본체를 세우지 않는다."""
        from agentic_service_desk.worker.runner import BatchRunner

        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=10)
        conn.close()
        monkeypatch.setattr(httpx, "post", FakePost(fail=True))
        BatchRunner(
            _settings(tmp_path, alert_webhook_url="https://hook.example/x")
        )._notify()  # 예외가 새어 나오지 않는다


class TestBanner:
    """웹훅이 없어도 대시보드는 말한다."""

    def test_배너가_대기열_화면에_뜬다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=30)
        conn.close()
        client = TestClient(create_app(_settings(tmp_path)))
        body = client.get("/").text
        assert "위험 대기열이 밀려 있다" in body

    def test_웹훅이_있어도_배너는_뜬다(self, tmp_path) -> None:
        """알림이 도착하지 않은 것과 경고가 없는 것을 화면에서 구분할 수 없다."""
        conn = _conn(tmp_path)
        _contradiction(conn, "c-1", days_ago=30)
        conn.close()
        client = TestClient(
            create_app(_settings(tmp_path, alert_webhook_url="https://hook.example/x"))
        )
        assert "위험 대기열이 밀려 있다" in client.get("/").text

    def test_경고가_없으면_배너도_없다(self, tmp_path) -> None:
        client = TestClient(create_app(_settings(tmp_path)))
        assert "위험 대기열이 밀려 있다" not in client.get("/").text
