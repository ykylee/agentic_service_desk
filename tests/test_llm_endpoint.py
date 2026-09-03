"""WBS-5.8.1 — 모델 연결을 화면에서 정한다 (FR-62, ADR-009 개정).

여기서 지키는 것은 넷.

    1. **행이 없으면 `.env` 가 씨앗**이다 — 이행 하나로 도는 구성이 달라지지 않는다
    2. 화면에서 바꾼 값이 **재기동 없이** 다음 생성에 쓰인다
    3. **검문소는 옮기지 않았다** — 거부되면 DB 도 `models.json` 도 바뀌지 않는다
    4. 키는 화면을 지나지 않는다 (ADR-009)

가장 중요한 것은 3 이다. 화면이 생겼다고 NFR-1 을 우회하는 문이 열리면, 그 문은
설계가 구조적으로 불가능하게 만들어 둔 조합("실제 모 시스템에 붙은 채 외부 LLM")을
버튼 하나로 되살린다.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.llm.policy import RemoteEndpointRejected
from agentic_service_desk.operations import llm_endpoint
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.web.app import create_app

LOCAL = "http://127.0.0.1:8080/v1"
REMOTE = "https://api.minimax.io/v1"


def _settings(tmp_path, **over):  # noqa: ANN001, ANN202
    base = dict(
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
        llm_base_url=LOCAL,
        llm_model="seed-model",
        parent_adapter="mock",
        bot_accounts="svc-agentic-desk",
    )
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


class TestSeed:
    def test_행이_없으면_env_가_씨앗이다(self, tmp_path) -> None:
        cfg = _settings(tmp_path)
        conn = _conn(tmp_path)
        try:
            endpoint = llm_endpoint.current(conn, cfg)
        finally:
            conn.close()

        assert endpoint.base_url == LOCAL
        assert endpoint.model == "seed-model"
        assert endpoint.source == llm_endpoint.SEED

    def test_저장하면_출처가_화면이_된다(self, tmp_path) -> None:
        cfg = _settings(tmp_path)
        conn = _conn(tmp_path)
        try:
            llm_endpoint.save(
                conn,
                cfg,
                llm_endpoint.Endpoint(base_url=LOCAL, model="정한-모델"),
                models_json_path=tmp_path / "models.json",
            )
            endpoint = llm_endpoint.current(conn, cfg)
        finally:
            conn.close()

        assert endpoint.model == "정한-모델"
        assert endpoint.source == llm_endpoint.DASHBOARD
        # **씨앗을 고쳐도 따라가지 않는다** — SSOT 가 옮겨 갔다는 것의 뜻이다.
        assert llm_endpoint.current(_conn(tmp_path), _settings(tmp_path, llm_model="딴것")).model == (
            "정한-모델"
        )

    def test_설정을_갈아_끼운다(self, tmp_path) -> None:
        # 호출부를 고치지 않기 위한 장치다 — 진입 지점에서 한 번 입힌다.
        cfg = _settings(tmp_path)
        applied = llm_endpoint.apply(
            cfg, llm_endpoint.Endpoint(base_url=LOCAL, model="다른-모델", max_output_tokens=4096)
        )
        assert applied.llm_model == "다른-모델"
        assert applied.llm_max_output_tokens == 4096
        # 나머지는 그대로다.
        assert applied.knowledge_dir == cfg.knowledge_dir


class TestPolicyStillGuards:
    def test_실데이터에_닿으면_원격을_저장할_수_없다(self, tmp_path) -> None:
        # 어댑터가 실제이므로 플래그를 켜도 열리지 않는다 (NFR-1, ADR-005 결정 5).
        cfg = _settings(tmp_path, parent_adapter="http", parent_api_base_url="https://parent")
        conn = _conn(tmp_path)
        try:
            with pytest.raises(RemoteEndpointRejected):
                llm_endpoint.save(
                    conn,
                    cfg,
                    llm_endpoint.Endpoint(base_url=REMOTE, model="m", allow_remote=True),
                    models_json_path=tmp_path / "models.json",
                )
            # **아무것도 바뀌지 않았다.**
            assert llm_endpoint.current(conn, cfg).base_url == LOCAL
        finally:
            conn.close()
        assert not (tmp_path / "models.json").exists()

    def test_임베딩_주소도_검문을_받는다(self, tmp_path) -> None:
        # 나가는 것은 지식 본문 그 자체다 — 채팅만 보면 그 문은 열린 채로 남는다.
        cfg = _settings(tmp_path, parent_repo_url="git@internal:team/parent.git")
        conn = _conn(tmp_path)
        try:
            with pytest.raises(RemoteEndpointRejected):
                llm_endpoint.save(
                    conn,
                    cfg,
                    llm_endpoint.Endpoint(
                        base_url=LOCAL,
                        model="m",
                        embedding_base_url=REMOTE,
                        allow_remote=True,
                    ),
                    models_json_path=tmp_path / "models.json",
                )
        finally:
            conn.close()

    def test_개발_구성에서는_원격이_열린다(self, tmp_path) -> None:
        # mock 어댑터 + 소스 없음 + 명시적 허용 — 셋이 다 맞을 때만이다.
        cfg = _settings(tmp_path)
        conn = _conn(tmp_path)
        try:
            saved = llm_endpoint.save(
                conn,
                cfg,
                llm_endpoint.Endpoint(base_url=REMOTE, model="m", allow_remote=True),
                models_json_path=tmp_path / "models.json",
            )
        finally:
            conn.close()
        assert saved.endpoint.base_url == REMOTE
        assert saved.harness_error == ""


class TestHarnessConfig:
    def test_저장이_models_json_을_다시_쓴다(self, tmp_path) -> None:
        # ADR-009 — pi 는 우리 정책을 지나지 않으므로 설정을 만드는 시점이 검문소다.
        cfg = _settings(tmp_path)
        target = tmp_path / "models.json"
        conn = _conn(tmp_path)
        try:
            saved = llm_endpoint.save(
                conn,
                cfg,
                llm_endpoint.Endpoint(base_url=LOCAL, model="새-모델", max_output_tokens=1234),
                models_json_path=target,
            )
        finally:
            conn.close()

        assert saved.models_json == target
        payload = json.loads(target.read_text(encoding="utf-8"))
        provider = payload["providers"]["asd"]
        assert provider["baseUrl"] == LOCAL
        assert provider["models"][0]["id"] == "새-모델"
        assert provider["models"][0]["maxTokens"] == 1234
        # **키는 파일에 박히지 않는다** — 참조만 남는다.
        assert provider["apiKey"] == "$ASD_LLM_API_KEY"


class TestScreen:
    def test_화면이_지금_연결을_말한다(self, tmp_path) -> None:
        client = TestClient(create_app(_settings(tmp_path)))
        body = client.get("/settings/llm").text
        assert "seed-model" in body
        assert llm_endpoint.SEED in body

    def test_모델이_없으면_안_붙었다고_말한다(self, tmp_path) -> None:
        # **모델 미설정과 지식 부족은 다른 상태다.** 화면이 가르지 못하면 대응이 갈린다.
        client = TestClient(create_app(_settings(tmp_path, llm_base_url="", llm_model="")))
        assert "안 붙었다" in client.get("/settings/llm").text

    def test_화면에서_저장하면_다음_요청이_그것을_쓴다(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "agentic_service_desk.llm.harness.DEFAULT_MODELS_JSON", tmp_path / "models.json"
        )
        client = TestClient(create_app(_settings(tmp_path)))
        response = client.post(
            "/settings/llm",
            data={
                "base_url": LOCAL,
                "model": "화면이-정한-모델",
                "max_output_tokens": "2048",
            },
        )
        assert response.status_code == 200
        assert "화면이-정한-모델" in client.get("/settings/llm").text

        conn = _conn(tmp_path)
        try:
            assert llm_endpoint.current(conn, _settings(tmp_path)).model == "화면이-정한-모델"
        finally:
            conn.close()

    def test_거부되면_이유가_화면에_남는다(self, tmp_path) -> None:
        # 조용히 옛 값으로 돌아가면 운영자는 바꿨다고 믿는다.
        cfg = _settings(tmp_path, parent_adapter="http", parent_api_base_url="https://parent")
        client = TestClient(create_app(cfg))
        response = client.post(
            "/settings/llm",
            data={"base_url": REMOTE, "model": "m", "max_output_tokens": "2048",
                  "allow_remote": "1"},
        )
        assert response.status_code == 200
        assert "원격 LLM 을 쓸 수 없다" in response.text
        # 입력한 값이 화면에 남아 있다 — 다시 치게 하지 않는다.
        assert REMOTE in response.text

        conn = _conn(tmp_path)
        try:
            assert llm_endpoint.current(conn, cfg).base_url == LOCAL
        finally:
            conn.close()

    def test_출력_한도가_숫자가_아니면_거부한다(self, tmp_path) -> None:
        client = TestClient(create_app(_settings(tmp_path)))
        response = client.post(
            "/settings/llm", data={"base_url": LOCAL, "model": "m", "max_output_tokens": "0"}
        )
        assert "출력 한도는 1 이상의 정수다" in response.text
