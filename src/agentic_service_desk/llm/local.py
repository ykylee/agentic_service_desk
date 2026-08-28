"""로컬 LLM 구현.

**NFR-1 은 원칙이 아니라 여기서 강제된다.** 소스코드 파생 내용을 외부로 보내는 것은
곧 소스코드 반출이므로, 설정에 외부 주소가 들어오면 **거부한다.** 원칙을 문서에만
두면 언젠가 실수로 외부 엔드포인트가 들어가고, 그때는 이미 나간 뒤다.

로컬 런타임(ollama · vLLM · llama.cpp 등)이 대체로 OpenAI 호환 API 를 제공하므로
그 형태로 말한다 — 런타임을 바꿔도 호출부가 그대로다(ADR-005 결정 4).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx

from agentic_service_desk.llm.arbiter import YieldSignal
from agentic_service_desk.llm.gateway import Priority


class RemoteEndpointRejected(RuntimeError):
    """외부 엔드포인트가 설정됐다 — NFR-1 위반이다."""


def assert_local_endpoint(base_url: str) -> None:
    """로컬이 아니면 거부한다.

    허용하는 것은 loopback · 사설 대역 · `.local` 뿐이다. 판정이 애매하면
    **거부하는 쪽**으로 기운다 — 반출은 되돌릴 수 없기 때문이다.
    """
    host = urlparse(base_url).hostname
    if not host:
        raise RemoteEndpointRejected(f"호스트를 알 수 없다: {base_url!r}")
    if host in {"localhost"} or host.endswith(".local"):
        return
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise RemoteEndpointRejected(
            f"로컬로 확인되지 않는 주소다: {host!r}. "
            "NFR-1 — 소스코드 파생 내용을 외부로 보낼 수 없다."
        ) from exc
    if not (ip.is_loopback or ip.is_private):
        raise RemoteEndpointRejected(
            f"외부 주소다: {host!r}. NFR-1 — 소스코드 파생 내용을 외부로 보낼 수 없다."
        )


class LocalLlmGateway:
    """로컬 런타임에 대고 말하는 게이트웨이."""

    def __init__(
        self,
        base_url: str,
        model: str,
        embedding_model: str,
        arbiter: YieldSignal,
        *,
        timeout: float = 120.0,
    ) -> None:
        assert_local_endpoint(base_url)
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._embedding_model = embedding_model
        self._arbiter = arbiter
        self._timeout = timeout

    def generate(self, prompt: str, *, priority: Priority = Priority.ONLINE) -> str:
        """텍스트 생성.

        온라인 요청은 **표시를 세우고** 부른다 — 그동안 배치가 청크 경계에서 비켜 준다.
        배치 요청은 표시를 세우지 않는다. 자기가 양보 대상이기 때문이다.
        """
        if priority is Priority.ONLINE:
            with self._arbiter.online_in_use():
                return self._chat(prompt)
        return self._chat(prompt)

    def embed(
        self, texts: list[str], *, priority: Priority = Priority.BATCH
    ) -> list[list[float]]:
        """임베딩. 이것도 로컬이어야 한다 — 지식 항목은 소스코드 파생이다."""
        raise NotImplementedError("WBS-4.4.1 검색에서 구현한다")

    def _chat(self, prompt: str) -> str:
        raise NotImplementedError("WBS-4.2.4 Ingest 에이전트에서 구현한다")
