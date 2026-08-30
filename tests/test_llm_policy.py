"""NFR-1 집행 정책 (ADR-005 §개발 환경).

NFR-1 의 실체는 "엔드포인트가 로컬이어야 한다"가 아니라 **"실제 소스코드가 밖으로
나가면 안 된다"** 이다. 여기서 지키는 것은 그 구분이 **정확히** 집행되는가다.

가장 중요한 시험은 **실제 데이터에 닿는 실행에서는 플래그를 켜도 원격이 열리지
않는다**는 것이다. 플래그 하나로 열리면 언젠가 그 조합이 만들어진다.
"""

from __future__ import annotations

import pytest

from agentic_service_desk.llm.policy import (
    DataExposure,
    RemoteEndpointRejected,
    assert_endpoint_allowed,
    is_local_endpoint,
)

MOCK = DataExposure(adapter="mock", source_repo_url="")
REAL_ADAPTER = DataExposure(adapter="http", source_repo_url="")
REAL_REPO = DataExposure(adapter="mock", source_repo_url="git@internal:team/parent.git")
BOTH_REAL = DataExposure(adapter="http", source_repo_url="git@internal:team/parent.git")
SIMULATED_REPO = DataExposure(
    adapter="mock", source_repo_url="/Users/me/repos/my-own", source_is_simulated=True
)
SIMULATED_BUT_REAL_QNA = DataExposure(
    adapter="http", source_repo_url="/Users/me/repos/my-own", source_is_simulated=True
)

REMOTE = "https://api.minimax.io/v1"
LOCAL = "http://gpu-box.local:8000/v1"


class TestLocalDetection:
    @pytest.mark.parametrize(
        "url",
        ["http://localhost:11434", "http://127.0.0.1:8080", "http://192.168.1.50:8000",
         "http://10.0.0.5:9000", "http://gpu-box.local:11434"],
    )
    def test_로컬을_알아본다(self, url: str) -> None:
        assert is_local_endpoint(url)

    @pytest.mark.parametrize(
        "url",
        ["https://api.minimax.io/v1", "https://api.openai.com/v1", "http://8.8.8.8:11434"],
    )
    def test_원격을_알아본다(self, url: str) -> None:
        assert not is_local_endpoint(url)

    def test_애매하면_로컬이_아니라고_본다(self) -> None:
        # 반출은 되돌릴 수 없으므로 모호할 때는 막는 쪽으로 기운다.
        assert not is_local_endpoint("http://some-internal-host:8000")
        assert not is_local_endpoint("")


class TestExposure:
    def test_mock_이고_저장소가_없으면_실제_데이터가_없다(self) -> None:
        assert MOCK.has_real_data is False

    def test_실제_어댑터면_실제_데이터다(self) -> None:
        assert REAL_ADAPTER.has_real_data is True

    def test_소스_저장소가_설정되면_실제_데이터다(self) -> None:
        # 어댑터가 mock 이어도 실제 코드가 로컬에 있으면 반출 위험이 있다.
        assert REAL_REPO.has_real_data is True

    def test_공백만_있는_저장소_설정은_없는_것으로_본다(self) -> None:
        assert DataExposure(adapter="mock", source_repo_url="   ").has_real_data is False

    def test_선언하지_않은_저장소는_모_시스템으로_본다(self) -> None:
        # 기본값이 거짓인 것이 요점이다 — 설정을 빠뜨린 실행이 조용히 열리면 안 된다.
        assert DataExposure(adapter="mock", source_repo_url="/repo").has_real_data is True

    def test_검증용이라고_선언된_저장소는_소스_위험이_없다(self) -> None:
        assert SIMULATED_REPO.has_real_data is False

    def test_저장소를_선언해도_실제_어댑터는_풀리지_않는다(self) -> None:
        # 저장소가 우리 것이어도 QnA 는 질문자의 말이다. 조건 2 는 별개다.
        assert SIMULATED_BUT_REAL_QNA.has_real_data is True


class TestPolicy:
    def test_로컬은_언제나_통과한다(self) -> None:
        for exposure in (MOCK, REAL_ADAPTER, REAL_REPO, BOTH_REAL):
            assert_endpoint_allowed(LOCAL, allow_remote=False, exposure=exposure)

    def test_개발_환경에서는_원격이_열린다(self) -> None:
        # 세 조건이 모두 참일 때만.
        assert_endpoint_allowed(REMOTE, allow_remote=True, exposure=MOCK)

    def test_플래그가_없으면_원격이_막힌다(self) -> None:
        with pytest.raises(RemoteEndpointRejected, match="명시적으로 허용"):
            assert_endpoint_allowed(REMOTE, allow_remote=False, exposure=MOCK)

    @pytest.mark.parametrize("exposure", [REAL_ADAPTER, REAL_REPO, BOTH_REAL])
    def test_실제_데이터에_닿으면_플래그를_켜도_막힌다(self, exposure: DataExposure) -> None:
        # **이것이 이 정책의 핵심이다.** 플래그 하나로 열리면 언젠가
        # "실제 모 시스템 + 외부 LLM" 조합이 만들어진다.
        with pytest.raises(RemoteEndpointRejected, match="실제 데이터"):
            assert_endpoint_allowed(REMOTE, allow_remote=True, exposure=exposure)

    def test_검증용_선언이_있으면_저장소가_붙어도_원격이_열린다(self) -> None:
        assert_endpoint_allowed(REMOTE, allow_remote=True, exposure=SIMULATED_REPO)

    def test_검증용_선언만으로는_열리지_않는다_플래그도_필요하다(self) -> None:
        # 두 선언은 서로 다른 사실이다 — 하나가 다른 하나를 대신하지 않는다.
        with pytest.raises(RemoteEndpointRejected, match="명시적으로 허용"):
            assert_endpoint_allowed(REMOTE, allow_remote=False, exposure=SIMULATED_REPO)

    def test_검증용_선언이_실제_어댑터를_풀지_않는다(self) -> None:
        with pytest.raises(RemoteEndpointRejected, match="실제 데이터"):
            assert_endpoint_allowed(
                REMOTE, allow_remote=True, exposure=SIMULATED_BUT_REAL_QNA
            )

    def test_거부_사유가_선언하는_법을_알려준다(self) -> None:
        with pytest.raises(RemoteEndpointRejected) as exc:
            assert_endpoint_allowed(REMOTE, allow_remote=True, exposure=REAL_REPO)
        assert "ASD_SIMULATED_SOURCE" in str(exc.value)

    def test_거부_사유가_무엇을_고쳐야_하는지_알려준다(self) -> None:
        with pytest.raises(RemoteEndpointRejected) as exc:
            assert_endpoint_allowed(REMOTE, allow_remote=True, exposure=REAL_REPO)
        assert "source_repo" in str(exc.value)


class TestGatewayConstruction:
    def test_실제_데이터_실행에서_원격_게이트웨이를_만들_수_없다(self) -> None:
        from agentic_service_desk.llm.arbiter import YieldSignal
        from agentic_service_desk.llm.local import ChatLlmGateway

        with pytest.raises(RemoteEndpointRejected):
            ChatLlmGateway(
                base_url=REMOTE,
                model="x",
                arbiter=YieldSignal.__new__(YieldSignal),
                allow_remote=True,
                exposure=BOTH_REAL,
            )

    def test_설정에서_게이트웨이를_만들_때도_같은_판정이_적용된다(self, tmp_path) -> None:
        from agentic_service_desk.config import Settings
        from agentic_service_desk.llm.arbiter import YieldSignal
        from agentic_service_desk.llm.local import build_gateway

        cfg = Settings(
            _env_file=None,  # type: ignore[arg-type]
            llm_base_url=REMOTE,
            llm_model="m",
            llm_allow_remote=True,
            parent_adapter="http",  # 실제 어댑터 — 막혀야 한다
        )
        with pytest.raises(RemoteEndpointRejected):
            build_gateway(cfg, YieldSignal(tmp_path / "m"))

    def test_임베딩_엔드포인트도_판정을_받는다(self, tmp_path) -> None:
        # 채팅이 로컬이어도 임베딩이 따로 원격을 가리킬 수 있다. 나가는 것은
        # 지식 본문 그 자체라 오히려 더 직접적인 반출 경로다.
        from agentic_service_desk.config import Settings
        from agentic_service_desk.llm.arbiter import YieldSignal
        from agentic_service_desk.llm.local import build_gateway

        cfg = Settings(
            _env_file=None,  # type: ignore[arg-type]
            llm_base_url=LOCAL,
            llm_model="m",
            llm_allow_remote=True,
            embedding_base_url=REMOTE,
            parent_repo_url="git@internal:team/parent.git",
        )
        with pytest.raises(RemoteEndpointRejected):
            build_gateway(cfg, YieldSignal(tmp_path / "m"))

    def test_검증용_선언이면_임베딩도_원격이_열린다(self, tmp_path) -> None:
        from agentic_service_desk.config import Settings
        from agentic_service_desk.llm.arbiter import YieldSignal
        from agentic_service_desk.llm.local import build_gateway

        cfg = Settings(
            _env_file=None,  # type: ignore[arg-type]
            llm_base_url=REMOTE,
            llm_model="m",
            llm_allow_remote=True,
            embedding_base_url=REMOTE,
            parent_adapter="mock",
            parent_repo_url="/Users/me/repos/my-own",
            simulated_source=True,
        )
        build_gateway(cfg, YieldSignal(tmp_path / "m"))
