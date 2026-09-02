"""실운영 조합 기동 거부 (WBS-5.2.1).

**막는 것은 사람의 실수이고 그 실수는 조용하다** — 시뮬레이션 선언을 켠 채 진짜
저장소를 넣어도 아무것도 터지지 않는다. 그래서 기동에서 드러낸다.
"""

from __future__ import annotations

from agentic_service_desk.config import Settings
from agentic_service_desk.operations.preflight import check_live_exposure


def _settings(tmp_path, **over) -> Settings:  # noqa: ANN001, ANN003
    base = dict(
        _env_file=None,
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
    )
    return Settings(**(base | over))  # type: ignore[arg-type]


class TestNotLive:
    """연습 구성은 막지 않는다 — 지금의 개발 환경이 여기다."""

    def test_mock_이고_게재_전이면_통과한다(self, tmp_path) -> None:
        cfg = _settings(
            tmp_path, parent_adapter="mock", stage="S0", simulated_source=True,
            web_host="10.0.0.5",
        )
        assert check_live_exposure(cfg, binds_socket=True)

    def test_연습_구성에서는_화면이_넓게_열려도_기동한다(self, tmp_path) -> None:
        # S0 + mock 은 눌러도 나가지 않는다. 여기서 거부하면 **고칠 이유가 없는
        # 것을 고치게** 만들고, 그러면 진짜 거부도 함께 무시된다.
        cfg = _settings(tmp_path, parent_adapter="mock", stage="S0", web_host="0.0.0.0")
        assert check_live_exposure(cfg, binds_socket=True)


class TestSimulatedSourceInLive:
    """선언과 구성이 서로 모순되는 것은 코드가 안다."""

    def test_실어댑터인데_시뮬레이션_선언이_남아_있으면_거부한다(self, tmp_path) -> None:
        cfg = _settings(tmp_path, parent_adapter="http", stage="S0", simulated_source=True)
        assert check_live_exposure(cfg) is False

    def test_게재_단계인데_시뮬레이션_선언이_남아_있으면_거부한다(self, tmp_path) -> None:
        # 어댑터가 mock 이어도 게재 단계면 쓰는 것이 진짜 답변이다.
        cfg = _settings(tmp_path, parent_adapter="mock", stage="S3", simulated_source=True)
        assert check_live_exposure(cfg) is False

    def test_선언을_지우면_기동한다(self, tmp_path) -> None:
        cfg = _settings(tmp_path, parent_adapter="http", stage="S0", simulated_source=False)
        assert check_live_exposure(cfg)


class TestUnauthenticatedScreen:
    """인증 없는 화면 + 루프백 밖 + 실운영."""

    def test_실운영에서_루프백_밖이면_거부한다(self, tmp_path) -> None:
        cfg = _settings(tmp_path, parent_adapter="http", web_host="100.119.181.116")
        assert check_live_exposure(cfg, binds_socket=True) is False

    def test_루프백이면_기동한다(self, tmp_path) -> None:
        cfg = _settings(tmp_path, parent_adapter="http", web_host="127.0.0.1")
        assert check_live_exposure(cfg, binds_socket=True)

    def test_배치는_소켓을_열지_않으므로_화면_설정을_보지_않는다(self, tmp_path) -> None:
        # 같은 설정을 보고 배치까지 거부하면 배치 쪽에 고칠 것이 없다.
        cfg = _settings(tmp_path, parent_adapter="http", web_host="100.119.181.116")
        assert check_live_exposure(cfg)

    def test_0000_은_루프백이_아니다(self, tmp_path) -> None:
        # 이름과 달리 '내가 가진 모든 인터페이스'다.
        cfg = _settings(tmp_path, parent_adapter="http", web_host="0.0.0.0")
        assert check_live_exposure(cfg, binds_socket=True) is False


class TestMessage:
    """거부는 **무엇을 고쳐야 하는지**까지 말한다 — 둘이 겹치면 둘 다."""

    def test_둘_다_어긋나면_둘_다_적는다(self, tmp_path, capsys) -> None:  # noqa: ANN001
        cfg = _settings(
            tmp_path, parent_adapter="http", simulated_source=True, web_host="0.0.0.0"
        )
        assert check_live_exposure(cfg, binds_socket=True) is False
        err = capsys.readouterr().err
        assert "ASD_SIMULATED_SOURCE" in err
        assert "ASD_WEB_HOST" in err
