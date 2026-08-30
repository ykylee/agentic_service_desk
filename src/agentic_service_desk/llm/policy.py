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

조건 3 에는 예외가 하나 있다. 붙은 저장소가 **모 시스템이 아니라고 선언된 경우**
(`source_is_simulated`) 다. 파이프라인을 실제 저장소로 검증하려면 코드가 로컬에
있어야 하는데, 그 저장소가 남의 것이 아니면 NFR-1 이 지키려던 것 자체가 없다.

**선언을 `llm_allow_remote` 와 합치지 않은 것이 요점이다.** 하나로 두면 "원격을
쓰겠다"는 뜻과 "이 소스는 모 시스템이 아니다"라는 뜻이 한 값에 섞이고, 그러면
개발 편의로 켜 둔 플래그가 **모 시스템이 붙는 날 조건 3 까지 함께 푼다.** 둘은
서로 다른 사실이므로 서로 다른 자리에서 선언한다. 그리고 이 예외로 **조건 2 는
풀리지 않는다** — 실제 QnA 에는 질문자의 말이 담기고, 그것은 우리 것이 아니다.
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

    source_is_simulated: bool = False
    """붙은 저장소가 **모 시스템이 아니라고 선언됐는가** (검증 실행).

    기본이 거짓인 이유는 하나다 — **선언하지 않은 저장소는 모 시스템으로 본다.**
    반대로 두면 설정을 빠뜨린 실행이 조용히 반출 가능한 상태가 된다.
    """

    @property
    def has_real_data(self) -> bool:
        """실제 모 시스템 데이터에 닿는가.

        선언된 검증용 저장소는 **소스 쪽 위험이 없다.** 어댑터는 그와 무관하게
        따로 본다 — 저장소가 우리 것이어도 QnA 는 질문자의 말이다.
        """
        real_source = bool(self.source_repo_url.strip()) and not self.source_is_simulated
        return self.adapter != "mock" or real_source


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
            "NFR-1 — 모 시스템 소스코드 파생 내용을 외부로 보낼 수 없다. "
            "붙은 저장소가 모 시스템이 아니라면 ASD_SIMULATED_SOURCE=true 로 "
            "**선언**한다 — 어댑터는 그 선언으로 풀리지 않는다."
        )

    if not allow_remote:
        raise RemoteEndpointRejected(
            f"원격 LLM 이 명시적으로 허용되지 않았다: {base_url!r}. "
            "실제 데이터가 없는 개발 환경이라면 ASD_LLM_ALLOW_REMOTE=true 를 켠다."
        )
