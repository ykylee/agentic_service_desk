"""임베딩 제공자 (ADR-004 · ADR-005 결정 4).

**채팅과 임베딩은 같은 제공자가 아닐 수 있다.** 채팅은 OpenAI 호환이 사실상 표준이
됐지만 임베딩은 그렇지 않다 — 제공자마다 요청·응답 형태가 다르다.

ADR-005 결정 4("모델을 교체 가능하게 감싼다")가 예상한 경우가 바로 이것이다.
호출부는 `LlmGateway.embed()` 하나만 알고, 형태 차이는 여기서 흡수한다.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from agentic_service_desk.llm.gateway import EmbeddingPurpose


class EmbeddingProvider(Protocol):
    """텍스트를 벡터로."""

    def embed(self, texts: list[str], purpose: EmbeddingPurpose) -> list[list[float]]:
        ...


class OpenAiCompatibleEmbeddings:
    """`POST /embeddings` 에 `{model, input}` 을 보내고 `data[].embedding` 을 받는다.

    로컬 런타임(ollama · vLLM) 대부분이 이 형태다. `purpose` 는 무시한다 —
    이 형식에 그 구분이 없기 때문이다.
    """

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 120.0) -> None:
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._model = model
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._timeout = timeout

    def embed(self, texts: list[str], purpose: EmbeddingPurpose) -> list[list[float]]:
        with httpx.Client(timeout=self._timeout) as client:
            res = client.post(
                self._url, headers=self._headers, json={"model": self._model, "input": texts}
            )
            res.raise_for_status()
            return [row["embedding"] for row in res.json()["data"]]


class MiniMaxEmbeddings:
    """MiniMax `embo-01` — **OpenAI 형식이 아니다.**

    조사(2026-08-28)로 확인한 차이는 셋이다.

        요청  `texts`  (OpenAI 는 `input`)
        요청  `type`   — `db` | `query`. **필수다**
        응답  최상위 `vectors`  (OpenAI 는 `data[].embedding`)

    오류는 HTTP 상태가 아니라 `base_resp.status_code` 로 온다 (0 이 성공). 그래서
    `raise_for_status()` 만으로는 실패를 놓친다.

    `type` 이 우리에게 잡음이 아니라는 점이 흥미롭다 — 색인과 질의를 나누라는 요구는
    ADR-004 가 이미 갖고 있던 구분이다. 그래서 인터페이스에 `purpose` 를 두었다.

    **주의**: 실제 API 키가 없어 라이브 검증을 하지 못했다. 문서상의 형태를 따랐으며,
    실제 연결 시 어긋나면 여기를 고친다.
    """

    _PURPOSE_TO_TYPE = {
        EmbeddingPurpose.INDEX: "db",
        EmbeddingPurpose.QUERY: "query",
    }

    def __init__(self, base_url: str, api_key: str, model: str = "embo-01", timeout: float = 120.0) -> None:
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._model = model
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        self._timeout = timeout

    def embed(self, texts: list[str], purpose: EmbeddingPurpose) -> list[list[float]]:
        with httpx.Client(timeout=self._timeout) as client:
            res = client.post(
                self._url,
                headers=self._headers,
                json={
                    "model": self._model,
                    "texts": texts,
                    "type": self._PURPOSE_TO_TYPE[purpose],
                },
            )
            res.raise_for_status()
            payload = res.json()
            status = payload.get("base_resp", {}).get("status_code", 0)
            if status != 0:
                msg = payload.get("base_resp", {}).get("status_msg", "")
                raise RuntimeError(f"MiniMax 임베딩 실패 (status_code={status}): {msg}")
            return payload["vectors"]


def build_embedding_provider(
    kind: str, base_url: str, model: str, api_key: str
) -> EmbeddingProvider:
    """설정에서 제공자를 고른다."""
    if kind == "minimax":
        return MiniMaxEmbeddings(base_url, api_key, model or "embo-01")
    return OpenAiCompatibleEmbeddings(base_url, model, api_key)
