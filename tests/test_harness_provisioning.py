"""pi 하네스 설정 생성 (ADR-009).

**pi 는 우리 코드가 아니다** — 우리 게이트웨이는 NFR-1 정책을 지나지만 pi 는 지나지
않는다. 그런데 지식 구축이야말로 소스코드를 직접 읽는 경로다.

여기서 지키는 것은 **설정 생성 시점이 검문소로 작동하는가**다.
"""

from __future__ import annotations

import json

import pytest

from agentic_service_desk.config import Settings
from agentic_service_desk.llm.harness import PROVIDER_NAME, render_models_json, write_models_json
from agentic_service_desk.llm.policy import RemoteEndpointRejected

REMOTE = "https://api.minimax.io/v1"
LOCAL = "http://gpu-box.local:8000/v1"


def _cfg(**over: object) -> Settings:
    base: dict = dict(llm_model="M", parent_adapter="mock", parent_repo_url="")
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


class TestRender:
    def test_로컬은_언제나_생성된다(self) -> None:
        out = render_models_json(_cfg(llm_base_url=LOCAL, parent_adapter="http"))
        assert out["providers"][PROVIDER_NAME]["baseUrl"] == LOCAL

    def test_제공자_이름은_고정이다(self) -> None:
        # 어느 제공자를 쓰든 pi 입장에서는 같은 이름이다 — 바꿔도 pi 설정이 안 흔들린다.
        for url in (LOCAL,):
            out = render_models_json(_cfg(llm_base_url=url, parent_adapter="http"))
            assert list(out["providers"]) == ["asd"]

    def test_키를_파일에_박지_않는다(self) -> None:
        # ADR-009 — pi 가 $VAR 참조를 지원한다.
        out = render_models_json(_cfg(llm_base_url=LOCAL, parent_adapter="http",
                                      llm_api_key="비밀값"))
        assert out["providers"][PROVIDER_NAME]["apiKey"] == "$ASD_LLM_API_KEY"
        assert "비밀값" not in json.dumps(out, ensure_ascii=False)

    def test_모델이_반영된다(self) -> None:
        out = render_models_json(_cfg(llm_base_url=LOCAL, llm_model="MiniMax-M3",
                                      parent_adapter="http"))
        assert out["providers"][PROVIDER_NAME]["models"] == [
            {"id": "MiniMax-M3", "maxTokens": 32_768}
        ]

    def test_출력_한도를_비워_두지_않는다(self) -> None:
        """**정하지 않으면 pi 기본값 16,384 가 쓰인다.**

        사고를 길게 하는 모델에서는 그 예산이 사고로 다 나가 `stop=length` 로
        잘리고, 잘린 자리가 사고 안이면 `strip_thinking()` 뒤에 본문이 빈
        문자열이 된다. 2026-08~09 부트스트랩 실패 62건 중 59건이 이것이었다.
        """
        out = render_models_json(
            _cfg(llm_base_url=LOCAL, llm_model="m", parent_adapter="http",
                 llm_max_output_tokens=65_536)
        )
        model = out["providers"][PROVIDER_NAME]["models"][0]
        assert model["maxTokens"] == 65_536

    def test_설정이_비면_거부한다(self) -> None:
        with pytest.raises(ValueError):
            render_models_json(_cfg(llm_base_url=LOCAL, llm_model="", parent_adapter="http"))


class TestPolicyGate:
    """**이 절이 이 모듈의 존재 이유다.**"""

    def test_개발_환경에서는_원격이_생성된다(self) -> None:
        out = render_models_json(_cfg(llm_base_url=REMOTE, llm_allow_remote=True))
        assert out["providers"][PROVIDER_NAME]["baseUrl"] == REMOTE

    def test_플래그가_없으면_원격_설정을_만들지_않는다(self) -> None:
        with pytest.raises(RemoteEndpointRejected):
            render_models_json(_cfg(llm_base_url=REMOTE, llm_allow_remote=False))

    def test_실제_어댑터면_플래그를_켜도_거부한다(self) -> None:
        with pytest.raises(RemoteEndpointRejected):
            render_models_json(
                _cfg(llm_base_url=REMOTE, llm_allow_remote=True, parent_adapter="http")
            )

    def test_실제_저장소가_있으면_플래그를_켜도_거부한다(self) -> None:
        # 지식 구축은 소스코드를 직접 읽는다. pi 만 원격을 가리키면 NFR-1 이 뚫린다.
        with pytest.raises(RemoteEndpointRejected):
            render_models_json(
                _cfg(
                    llm_base_url=REMOTE,
                    llm_allow_remote=True,
                    parent_repo_url="git@internal:team/parent.git",
                )
            )

    def test_거부되면_파일이_생기지_않는다(self, tmp_path) -> None:
        target = tmp_path / "models.json"
        with pytest.raises(RemoteEndpointRejected):
            write_models_json(
                _cfg(llm_base_url=REMOTE, llm_allow_remote=True, parent_adapter="http"), target
            )
        assert not target.exists()


class TestWrite:
    def test_파일로_쓴다(self, tmp_path) -> None:
        target = write_models_json(
            _cfg(llm_base_url=LOCAL, parent_adapter="http"), tmp_path / "a" / "models.json"
        )
        assert json.loads(target.read_text(encoding="utf-8"))["providers"][PROVIDER_NAME]

    def test_생성물이므로_덮어쓴다(self, tmp_path) -> None:
        # ADR-009 — 손으로 고치면 다음 생성에서 덮인다. .env 가 단일 출처다.
        target = tmp_path / "models.json"
        target.write_text('{"providers": {"손으로_고침": {}}}', encoding="utf-8")
        write_models_json(_cfg(llm_base_url=LOCAL, parent_adapter="http"), target)
        assert "손으로_고침" not in target.read_text(encoding="utf-8")


class TestCli:
    def test_dry_run_은_쓰지_않는다(self, monkeypatch, capsys, tmp_path) -> None:
        from agentic_service_desk import cli

        monkeypatch.setenv("ASD_PARENT_ADAPTER", "mock")
        # **개발자의 `.env` 에 기대지 않는다.** `load_settings()` 는 그 파일을 읽으므로,
        # 검증 실행 등으로 저장소가 채워져 있으면 이 시험이 그 설정을 따라 흔들린다.
        monkeypatch.setenv("ASD_PARENT_REPO_URL", "")
        monkeypatch.setenv("ASD_SIMULATED_SOURCE", "false")
        monkeypatch.setenv("ASD_LLM_ALLOW_REMOTE", "true")
        monkeypatch.setenv("ASD_LLM_BASE_URL", REMOTE)
        monkeypatch.setenv("ASD_LLM_MODEL", "MiniMax-M3")
        monkeypatch.setattr(cli, "write_models_json", lambda *a, **k: pytest.fail("쓰면 안 된다"))
        assert cli.main(["sync-harness", "--dry-run"]) == 0
        assert "MiniMax-M3" in capsys.readouterr().out

    def test_거부되면_1을_돌려준다(self, monkeypatch, capsys) -> None:
        from agentic_service_desk import cli

        monkeypatch.setenv("ASD_PARENT_ADAPTER", "http")
        monkeypatch.setenv("ASD_PARENT_REPO_URL", "")
        monkeypatch.setenv("ASD_SIMULATED_SOURCE", "false")
        monkeypatch.setenv("ASD_LLM_ALLOW_REMOTE", "true")
        monkeypatch.setenv("ASD_LLM_BASE_URL", REMOTE)
        monkeypatch.setenv("ASD_LLM_MODEL", "M")
        assert cli.main(["sync-harness", "--dry-run"]) == 1
        assert "NFR-1" in capsys.readouterr().err
