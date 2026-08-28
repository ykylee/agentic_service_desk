"""임베딩 제공자 (ADR-004 · ADR-005 결정 4).

**채팅과 임베딩은 같은 형식이 아니다.** 채팅은 OpenAI 호환이 사실상 표준이 됐지만
임베딩은 제공자마다 다르다 — 2026-08-28 조사에서 MiniMax 가 그 예임을 확인했다.

여기서 지키는 것은 **형식 차이가 호출부로 새지 않는가**다.
"""

from __future__ import annotations

import json

import httpx
import pytest

_REAL_CLIENT = httpx.Client
"""원본을 먼저 잡아 둔다 — 안 그러면 monkeypatch 가 자기를 다시 불러 재귀한다."""


def _patch(monkeypatch, handler) -> None:  # noqa: ANN001
    """httpx.Client 를 가짜 전송으로 갈아 끼운다."""
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler))
    )

from agentic_service_desk.llm.embeddings import (
    MiniMaxEmbeddings,
    OpenAiCompatibleEmbeddings,
    build_embedding_provider,
)
from agentic_service_desk.llm.gateway import EmbeddingPurpose


class TestOpenAiCompatible:
    def test_input_을_보내고_data_embedding_을_읽는다(self, monkeypatch) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

        _patch(monkeypatch, handler)
        provider = OpenAiCompatibleEmbeddings("http://local/v1", "m")
        assert provider.embed(["가"], EmbeddingPurpose.INDEX) == [[0.1, 0.2]]
        assert seen["input"] == ["가"]
        assert "texts" not in seen

    def test_purpose_를_무시한다(self, monkeypatch) -> None:
        # 이 형식에는 색인/질의 구분이 없다. 인터페이스에는 두되 여기서는 버린다.
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"data": [{"embedding": [0.0]}]})

        _patch(monkeypatch, handler)
        OpenAiCompatibleEmbeddings("http://local/v1", "m").embed(["가"], EmbeddingPurpose.QUERY)
        assert "type" not in seen


class TestMiniMax:
    def _provider(self, monkeypatch, handler) -> MiniMaxEmbeddings:  # noqa: ANN001
        _patch(monkeypatch, handler)
        return MiniMaxEmbeddings("https://api.minimax.io/v1", "key")

    def test_texts_와_type_을_보내고_vectors_를_읽는다(self, monkeypatch) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"vectors": [[0.3]], "base_resp": {"status_code": 0}})

        provider = self._provider(monkeypatch, handler)
        assert provider.embed(["가"], EmbeddingPurpose.INDEX) == [[0.3]]
        assert seen["texts"] == ["가"]      # OpenAI 는 input
        assert "input" not in seen

    def test_색인과_질의가_다른_type_으로_간다(self, monkeypatch) -> None:
        # MiniMax 의 요구가 우리 설계(ADR-004)의 구분과 일치한다.
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"vectors": [[0.0]], "base_resp": {"status_code": 0}})

        provider = self._provider(monkeypatch, handler)
        provider.embed(["가"], EmbeddingPurpose.INDEX)
        assert seen["type"] == "db"
        provider.embed(["가"], EmbeddingPurpose.QUERY)
        assert seen["type"] == "query"

    def test_HTTP_200_이어도_본문_오류를_잡는다(self, monkeypatch) -> None:
        # **이것이 이 어댑터의 핵심이다.** MiniMax 는 실패를 HTTP 상태가 아니라
        # base_resp.status_code 로 알린다 — raise_for_status() 만으로는 놓친다.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"base_resp": {"status_code": 1004, "status_msg": "invalid key"}}
            )

        provider = self._provider(monkeypatch, handler)
        with pytest.raises(RuntimeError, match="1004"):
            provider.embed(["가"], EmbeddingPurpose.INDEX)


class TestFactory:
    def test_기본은_OpenAI_호환이다(self) -> None:
        # 로컬 런타임 대부분이 이 형식이다.
        assert isinstance(
            build_embedding_provider("openai_compatible", "http://local/v1", "m", ""),
            OpenAiCompatibleEmbeddings,
        )

    def test_minimax_를_고를_수_있다(self) -> None:
        p = build_embedding_provider("minimax", "https://api.minimax.io/v1", "", "key")
        assert isinstance(p, MiniMaxEmbeddings)

    def test_minimax_모델_기본값은_embo_01_이다(self) -> None:
        p = build_embedding_provider("minimax", "https://api.minimax.io/v1", "", "key")
        assert p._model == "embo-01"
