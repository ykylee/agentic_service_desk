"""LLM 엔드포인트 허용 정책 (NFR-1, ADR-005).

NFR-1 의 실체는 **"엔드포인트가 로컬이어야 한다"가 아니라 "실제 소스코드가 밖으로
나가면 안 된다"** 이다. 처음 구현에서 전자로 강제했는데, 그러면 **실제 데이터가
아예 없는 개발 환경**까지 막힌다.

정확히 강제한다 — **실제 데이터를 다루는 경로에서만** 로컬을 요구한다.

원격이 열리려면 **셋이 모두** 참이어야 한다.

    1. `llm_allow_remote` 가 명시적으로 켜져 있다
    2. 어댑터가 **mock** 이다        — 실제 QnA 가 없다
    3. 소스 저장소가 **비어 있다**    — 실제 코드가 로컬에 없다

2·3 이 핵심이다. **실제 모 시스템에 붙은 채로 외부 LLM 을 쓰는 조합이 구조적으로
불가능해진다** — 플래그 하나로 열리면 언젠가 그 조합이 만들어진다.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse


class RemoteEndpointRejected(RuntimeError):
    """외부 엔드포인트가 허용되지 않는 상황에서 설정됐다 — NFR-1 위반이다."""


@dataclass(frozen=True)
class DataExposure:
    """이 실행이 실제 데이터를 다루는가."""

    adapter: str
    """`mock` 이면 실제 QnA 가 없다."""

    source_repo_url: str
    """비어 있으면 실제 코드가 로컬에 없다."""

    @property
    def has_real_data(self) -> bool:
        """실제 모 시스템 데이터에 닿는가."""
        return self.adapter != "mock" or bool(self.source_repo_url.strip())


def is_local_endpoint(base_url: str) -> bool:
    """loopback · 사설 대역 · `.local` 인가.

    판정이 애매하면 **로컬이 아니라고 본다** — 반출은 되돌릴 수 없기 때문이다.
    """
    host = urlparse(base_url).hostname
    if not host:
        return False
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def assert_endpoint_allowed(
    base_url: str, *, allow_remote: bool, exposure: DataExposure
) -> None:
    """이 엔드포인트를 써도 되는가. 안 되면 이유를 밝히고 막는다."""
    if is_local_endpoint(base_url):
        return

    if exposure.has_real_data:
        raise RemoteEndpointRejected(
            f"원격 LLM 을 쓸 수 없다: {base_url!r}. "
            f"실제 데이터에 닿는 실행이다 (adapter={exposure.adapter!r}, "
            f"source_repo={'설정됨' if exposure.source_repo_url else '없음'}). "
            "NFR-1 — 모 시스템 소스코드 파생 내용을 외부로 보낼 수 없다."
        )

    if not allow_remote:
        raise RemoteEndpointRejected(
            f"원격 LLM 이 명시적으로 허용되지 않았다: {base_url!r}. "
            "실제 데이터가 없는 개발 환경이라면 ASD_LLM_ALLOW_REMOTE=true 를 켠다."
        )
