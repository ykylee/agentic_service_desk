"""모델 호출의 단일 통로.

호출부가 특정 런타임에 묶이지 않게 한 겹을 둔다 — 하드웨어와 모델은 바뀐다.

우선순위 규칙은 단순하다 (ADR-005). **온라인 요청이 오면 배치는 청크 경계에서
양보한다.** 정교한 스케줄러를 지금 만들지 않는 이유는 충돌 확률 자체가 낮기
때문이다(§1.3.1).
"""

from __future__ import annotations

import enum
from typing import Protocol


class EmbeddingPurpose(enum.StrEnum):
    """임베딩을 어느 쪽으로 쓰는가 (ADR-004).

    지식 항목을 **색인**할 때와 질문으로 **질의**할 때는 다른 일이다.
    """

    INDEX = "index"
    """지식 항목을 검색 대상으로 넣는다."""

    QUERY = "query"
    """질문으로 찾는다."""


class Priority(enum.IntEnum):
    """낮은 값이 먼저다."""

    ONLINE = 0
    """답변 생성·검수. 지연이 곧 채택 실패(W4)로 이어진다."""

    BATCH = 1
    """ingest · 콘텐츠 · Lint. 중단되어도 다음 주기에 이어서 한다."""


class LlmGateway(Protocol):
    """생성과 임베딩의 단일 통로."""

    def generate(self, prompt: str, *, priority: Priority = Priority.ONLINE) -> str:
        """텍스트 생성.

        **검수 호출은 생성과 같은 맥락을 공유하지 않는다** (D21). 검수의 입력은
        초안과 근거 원문뿐이며, 질문 의도나 생성 과정을 넣지 않는다 — 의도를 알면
        "그럴 만했다"로 기울고, 모르면 글에 적힌 것만 보게 된다.
        """
        ...

    def embed(
        self,
        texts: list[str],
        *,
        purpose: EmbeddingPurpose,
        priority: Priority = Priority.BATCH,
    ) -> list[list[float]]:
        """임베딩. 검색(ADR-004)의 두 축 중 하나다.

        `purpose` 를 받는 이유는 **색인과 질의가 다른 일**이기 때문이다 — 같은 문장이라도
        찾히는 쪽으로 넣을 때와 찾는 쪽으로 넣을 때 최적 표현이 다를 수 있다.

        OpenAI 형식은 이 구분을 노출하지 않지만 일부 제공자는 요구한다. 제공자가
        무시하더라도 **인터페이스에는 두는 것이 맞다** — 우리 설계(ADR-004)에 이미
        있는 구분이고, 나중에 넣으려면 호출부를 전부 고쳐야 한다.
        """
        ...
