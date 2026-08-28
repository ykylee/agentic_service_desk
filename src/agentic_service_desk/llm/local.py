"""LLM 클라이언트 (ADR-005).

**OpenAI 호환 chat completions** 로 말한다. 로컬 런타임(ollama · vLLM · llama.cpp)과
대부분의 원격 제공자가 이 형태를 지원하므로, 런타임을 바꿔도 호출부가 그대로다
(ADR-005 결정 4).

엔드포인트 허용 여부는 이 모듈이 정하지 않는다 — `policy` 가 정한다. 정책과 전송을
나눈 이유는 **정책이 시험 가능해야** 하기 때문이다.
"""

from __future__ import annotations

import httpx

from agentic_service_desk.llm.arbiter import YieldSignal
from agentic_service_desk.llm.embeddings import EmbeddingProvider, build_embedding_provider
from agentic_service_desk.llm.gateway import EmbeddingPurpose, Priority
from agentic_service_desk.llm.policy import (
    DataExposure,
    RemoteEndpointRejected,
    assert_endpoint_allowed,
    is_local_endpoint,
)

__all__ = [
    "ChatLlmGateway",
    "RemoteEndpointRejected",
    "assert_endpoint_allowed",
    "is_local_endpoint",
]


class ChatLlmGateway:
    """OpenAI 호환 엔드포인트에 대고 말하는 게이트웨이."""

    def __init__(
        self,
        base_url: str,
        model: str,
        arbiter: YieldSignal,
        *,
        embeddings: EmbeddingProvider | None = None,
        api_key: str = "",
        allow_remote: bool = False,
        exposure: DataExposure | None = None,
        timeout: float = 120.0,
    ) -> None:
        assert_endpoint_allowed(
            base_url,
            allow_remote=allow_remote,
            exposure=exposure or DataExposure(adapter="http", source_repo_url=""),
        )
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._embeddings = embeddings
        self._arbiter = arbiter
        self._timeout = timeout
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    # --- 생성 -------------------------------------------------------------

    def generate(self, prompt: str, *, priority: Priority = Priority.ONLINE) -> str:
        """텍스트 생성.

        온라인 요청은 **표시를 세우고** 부른다 — 그동안 배치가 청크 경계에서 비켜 준다.
        배치는 표시를 세우지 않는다. 자기가 양보 대상이기 때문이다.
        """
        if priority is Priority.ONLINE:
            with self._arbiter.online_in_use():
                return self._chat(prompt)
        return self._chat(prompt)

    def _chat(self, prompt: str) -> str:
        with httpx.Client(timeout=self._timeout) as client:
            res = client.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers,
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"]

    # --- 임베딩 -----------------------------------------------------------

    def embed(
        self,
        texts: list[str],
        *,
        purpose: EmbeddingPurpose,
        priority: Priority = Priority.BATCH,
    ) -> list[list[float]]:
        """임베딩 (ADR-004 의 두 축 중 하나).

        제공자에 위임한다 — **채팅과 임베딩은 같은 형식이 아닐 수 있다.**
        """
        if self._embeddings is None:
            raise NotImplementedError("임베딩 제공자가 설정되지 않았다")
        if priority is Priority.ONLINE:
            with self._arbiter.online_in_use():
                return self._embeddings.embed(texts, purpose)
        return self._embeddings.embed(texts, purpose)


def build_gateway(settings, arbiter: YieldSignal) -> ChatLlmGateway:  # noqa: ANN001
    """설정에서 게이트웨이를 만든다. 허용 판정이 여기서 한 번에 일어난다."""
    embed_url = settings.embedding_base_url or settings.llm_base_url
    return ChatLlmGateway(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        embeddings=build_embedding_provider(
            settings.embedding_provider,
            embed_url,
            settings.llm_embedding_model,
            settings.llm_api_key,
        ),
        api_key=settings.llm_api_key,
        arbiter=arbiter,
        allow_remote=settings.llm_allow_remote,
        exposure=DataExposure(
            adapter=settings.parent_adapter,
            source_repo_url=settings.parent_repo_url,
        ),
    )
