"""WBS-5.8.2 — 지식베이스 구축의 현황과 제어 (FR-63).

여기서 지키는 것은 넷.

    1. **워커가 살아 있는가**를 화면이 말한다 — 진행 0 이 "할 일이 없다"인지
       "아무도 안 돈다"인지 구분되지 않는 것이 이 화면이 생긴 이유다
    2. 진행이 **디스크에** 남는다 — 부트스트랩이 반나절이면 물을 곳이 화면뿐이다
    3. **중단은 신호가 아니라 상태다** — 다음 주기에 되살아나면 멈춘 것이 아니다
    4. **재구축은 런이 도는 중에 거부된다** — 런이 끝나며 커서를 다시 쓰기 때문이다
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.operations import build
from agentic_service_desk.operations.checkpoint import set_cursor
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.web.app import create_app


def _settings(tmp_path, **over):  # noqa: ANN001, ANN202
    base = dict(
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
        bot_accounts="svc-agentic-desk",
    )
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


class TestHeartbeat:
    def test_심박이_없으면_살아_있다고_하지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        try:
            beat = build.heartbeat(conn)
        finally:
            conn.close()
        assert not beat.seen
        assert not beat.alive
        assert "심박을 남기지 않았다" in beat.label

    def test_방금_찍은_심박은_살아_있다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        try:
            build.beat(conn, stage="S1", doing="지식 구축")
            beat = build.heartbeat(conn)
        finally:
            conn.close()
        assert beat.alive
        assert "지식 구축" in beat.label

    def test_오래된_심박은_죽은_것으로_본다(self, tmp_path) -> None:
        old = (datetime.now(UTC) - timedelta(seconds=build.HEARTBEAT_STALE_SECONDS + 60))
        conn = _conn(tmp_path)
        try:
            conn.execute(
                "INSERT INTO worker_heartbeat (id, beat_at, stage, doing) VALUES (1, ?, 'S1', '')",
                (old.isoformat(timespec="seconds"),),
            )
            conn.commit()
            beat = build.heartbeat(conn)
        finally:
            conn.close()
        assert beat.seen
        assert not beat.alive
        assert "떠 있지 않다" in beat.label


class TestRun:
    def test_진행이_디스크에_남는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        try:
            run_id = build.start_run(conn)
            build.note_chunk(conn, run_id, repo_url="git@x:parent.git", done=43, total=116)
            run = build.running(conn)
        finally:
            conn.close()
        assert run is not None
        assert (run.chunks_done, run.chunks_total) == (43, 116)
        assert run.running
        assert 0.37 < run.ratio < 0.38

    def test_남은_시간은_묶음이_끝나야_잰다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        try:
            run_id = build.start_run(conn)
            assert build.running(conn).remaining_label == ""
            build.note_chunk(conn, run_id, repo_url="r", done=1, total=10)
            run = build.running(conn)
        finally:
            conn.close()
        # 묶음 하나가 지났으므로 이제 평균이 있다 — 값 자체는 시각에 달렸다.
        assert run.seconds_per_chunk is not None
        assert "남은 시간" in run.remaining_label

    def test_완주와_중단이_이력에서_갈린다(self, tmp_path) -> None:
        # 커밋 목록만으로는 갈리지 않는다 — 그것이 이 표가 있는 이유다.
        conn = _conn(tmp_path)
        try:
            done = build.start_run(conn)
            build.end_run(conn, done, outcome=build.COMPLETED, note="항목 12개")
            stopped = build.start_run(conn)
            build.end_run(conn, stopped, outcome=build.STOPPED)
            rows = build.recent(conn)
        finally:
            conn.close()
        assert [r.outcome for r in rows] == [build.STOPPED, build.COMPLETED]
        assert "커서를 옮기지 않았다" in rows[0].outcome_label

    def test_읽은_것이_없는_런은_이력에_남지_않는다(self, tmp_path) -> None:
        # 주기마다 확인은 하므로 남기면 하루에 1,440줄이 "바뀐 것이 없다"로 쌓이고,
        # 실제로 읽은 런이 그 사이에 묻힌다. 확인했다는 사실은 심박이 진다.
        conn = _conn(tmp_path)
        try:
            run_id = build.start_run(conn)
            build.discard_run(conn, run_id)
            assert build.recent(conn) == []
            assert build.running(conn) is None
        finally:
            conn.close()

    def test_죽으며_남긴_런을_다음_기동이_닫는다(self, tmp_path) -> None:
        # 남겨 두면 화면이 영원히 "도는 중"을 보여 준다 — 그것이 곧 거짓말이다.
        conn = _conn(tmp_path)
        try:
            build.start_run(conn)
            closed = build.abandon_stale(conn)
            assert closed == 1
            assert build.running(conn) is None
            assert build.recent(conn)[0].outcome == build.FAILED
        finally:
            conn.close()


class TestControl:
    def test_중단은_상태로_남는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        try:
            build.request_stop(conn)
            assert build.control(conn).paused
            # 깨우기 표시를 집어도 멈춤은 남는다 — 둘은 다른 축이다.
            build.take_wake(conn)
            assert build.control(conn).paused
        finally:
            conn.close()

    def test_시작은_멈춤을_풀고_깨운다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        try:
            build.request_stop(conn)
            build.request_start(conn)
            assert not build.control(conn).paused
            assert build.take_wake(conn) is True
            # **집으면 내린다** — 한 번만 깨운다.
            assert build.take_wake(conn) is False
        finally:
            conn.close()


class TestRebuild:
    def test_커서를_지운다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        try:
            set_cursor(conn, "source:parent", "abc123")
            assert build.rebuild(conn) == 1
            assert build.cursors(conn) == []
        finally:
            conn.close()

    def test_런이_도는_중에는_거부한다(self, tmp_path) -> None:
        # 런이 끝나며 커서를 다시 쓰므로, 지금 지우면 조용히 되살아난다.
        conn = _conn(tmp_path)
        try:
            set_cursor(conn, "source:parent", "abc123")
            build.start_run(conn)
            with pytest.raises(build.RebuildRefused):
                build.rebuild(conn)
            assert len(build.cursors(conn)) == 1
        finally:
            conn.close()


class TestScreen:
    def test_워커가_안_돌면_화면이_그렇게_말한다(self, tmp_path) -> None:
        client = TestClient(create_app(_settings(tmp_path)))
        body = client.get("/build").text
        assert "안 돈다" in body

    def test_진행이_화면에_보인다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        try:
            build.beat(conn, stage="S1", doing="지식 구축")
            run_id = build.start_run(conn)
            build.note_chunk(conn, run_id, repo_url="git@x:parent.git", done=43, total=116)
        finally:
            conn.close()

        body = TestClient(create_app(_settings(tmp_path))).get("/build").text
        assert "묶음 43/116" in body
        assert "돈다" in body

    def test_중단_버튼이_상태를_세운다(self, tmp_path) -> None:
        client = TestClient(create_app(_settings(tmp_path)))
        response = client.post("/build/stop", follow_redirects=False)
        assert response.status_code == 303

        conn = _conn(tmp_path)
        try:
            assert build.control(conn).paused
        finally:
            conn.close()
        assert "멈춤" in client.get("/build").text

    def test_시작_버튼이_깨운다(self, tmp_path) -> None:
        client = TestClient(create_app(_settings(tmp_path)))
        client.post("/build/stop")
        client.post("/build/start")

        conn = _conn(tmp_path)
        try:
            control = build.control(conn)
        finally:
            conn.close()
        assert not control.paused
        assert control.wake

    def test_재구축은_확인_없이는_돌지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        try:
            set_cursor(conn, "source:parent", "abc123")
        finally:
            conn.close()

        client = TestClient(create_app(_settings(tmp_path)))
        client.post("/build/rebuild", data={"confirm": ""})

        conn = _conn(tmp_path)
        try:
            assert len(build.cursors(conn)) == 1
        finally:
            conn.close()

    def test_재구축이_커서를_지우고_다시_시작한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        try:
            set_cursor(conn, "source:parent", "abc123")
        finally:
            conn.close()

        client = TestClient(create_app(_settings(tmp_path)))
        client.post("/build/rebuild", data={"confirm": "재구축"})

        conn = _conn(tmp_path)
        try:
            assert build.cursors(conn) == []
            assert build.control(conn).wake
        finally:
            conn.close()


class TestWorkerWiring:
    def test_한_주기가_심박을_남긴다(self, tmp_path) -> None:
        # 워커가 살아 있다는 사실은 **워커만 말할 수 있다.**
        from agentic_service_desk.worker.runner import BatchRunner

        BatchRunner(_settings(tmp_path, stage="S0"))._tick()

        conn = _conn(tmp_path)
        try:
            assert build.heartbeat(conn).alive
        finally:
            conn.close()

    def test_멈춤이_서_있으면_구축이_돌지_않는다(self, tmp_path) -> None:
        # **중단은 상태다** — 다음 주기에 되살아나면 멈춘 것이 아니다.
        from agentic_service_desk.worker.runner import BatchRunner

        conn = _conn(tmp_path)
        try:
            build.request_stop(conn)
        finally:
            conn.close()

        cfg = _settings(
            tmp_path,
            llm_base_url="http://127.0.0.1:8080/v1",
            llm_model="m",
            parent_repo_url="git@internal:team/parent.git",
            simulated_source=True,
        )
        BatchRunner(cfg)._ingest()

        conn = _conn(tmp_path)
        try:
            # 런이 아예 열리지 않았다 — 열고 닫으면 이력이 빈 런으로 찬다.
            assert build.recent(conn) == []
            assert "쉰다" in build.heartbeat(conn).doing
        finally:
            conn.close()

    def test_주기마다_연결을_DB_에서_읽는다(self, tmp_path) -> None:
        # FR-62 — 화면에서 바꾼 값이 워커에 닿지 않으면 지식 구축만 옛 모델로 돈다.
        from agentic_service_desk.operations import llm_endpoint
        from agentic_service_desk.worker.runner import BatchRunner

        cfg = _settings(tmp_path, llm_base_url="http://127.0.0.1:8080/v1", llm_model="씨앗")
        conn = _conn(tmp_path)
        try:
            llm_endpoint.save(
                conn,
                cfg,
                llm_endpoint.Endpoint(base_url="http://127.0.0.1:9999/v1", model="화면이-정한"),
                models_json_path=tmp_path / "models.json",
            )
        finally:
            conn.close()

        runner = BatchRunner(cfg)
        runner._refresh_endpoint()
        assert runner._cfg.llm_model == "화면이-정한"
        assert runner._cfg.llm_base_url == "http://127.0.0.1:9999/v1"
