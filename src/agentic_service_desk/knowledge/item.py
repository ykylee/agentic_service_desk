"""지식 항목 — 파일 하나가 개념 하나 (ADR-002 · ADR-003).

frontmatter 가 OKF 형식을 만족하면서(FR-55) 우리에게 필요한 것을 함께 담는다.

    ---
    type: knowledge            # OKF 필수
    id: k-7f3a...              # 불변. 경로가 아니다 (ADR-002)
    title: 결재 한도가 결정되는 규칙
    provenance:                # 출처는 1급 시민 (D3)
      - commit: a1b2c3d
        path: src/approval/limit.py
      - qna: Q-1042
    invalidation:              # 무엇이 바뀌면 틀려지는가 (§6.5.3)
      kind: linked             # linked | periodic
      refs: [src/approval/limit.py]
    stale: false
    edited_by_human: false     # 에이전트가 덮어쓰지 않게 (D38)
    ---

**불변 id 를 두는 이유**: OKF 는 파일 경로를 개념 ID 로 보지만, 입도 조정(ADR-003)
으로 파일은 실제로 이동한다. 경로가 바뀌면 답변 이력의 근거 링크가 끊긴다(D20).
OKF 는 미지의 frontmatter 키를 거부하지 않으므로(consumer MUST NOT reject)
형식 준수가 깨지지 않는다.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field


class InvalidationKind(enum.StrEnum):
    """무효화 조건의 형태 (§6.5.3).

    **둘 중 하나는 반드시 있어야 승격된다.** 없으면 그 지식은 영영 stale 판정을
    받지 못하고, 운영 문서를 원천에서 배제한 이유(§2.2.3)가 승격이라는 뒷문으로
    되돌아온다.
    """

    LINKED = "linked"
    """코드·설정에 묶인다. 그것이 바뀌면 **자동으로** stale 후보가 된다. 원칙이다."""

    PERIODIC = "periodic"
    """묶을 대상이 없을 때의 대비책. 재확인 주기가 지나면 Q5 로 올라온다."""


@dataclass(frozen=True)
class Provenance:
    """이 지식이 어디서 왔는가 (D3)."""

    commit: str | None = None
    """소스 저장소 커밋 해시. 버전 고정의 단위다 (ADR-002)."""

    path: str | None = None
    """그 커밋에서의 파일 경로. 참고용이며 식별자가 아니다."""

    qna: str | None = None
    """QnA 항목 id. 티켓 승격으로 온 지식이면 여기가 채워진다."""

    def __post_init__(self) -> None:
        if not (self.commit or self.qna):
            raise ValueError("출처는 커밋이나 QnA 중 하나는 있어야 한다 (D3)")


@dataclass(frozen=True)
class Invalidation:
    """언제 이 지식이 틀려지는가."""

    kind: InvalidationKind
    refs: tuple[str, ...] = ()
    """linked 면 코드·설정 경로. periodic 이면 비어 있고 `period_days` 를 쓴다."""

    period_days: int | None = None

    def __post_init__(self) -> None:
        if self.kind is InvalidationKind.LINKED and not self.refs:
            raise ValueError("linked 무효화 조건에는 묶을 대상이 필요하다")
        if self.kind is InvalidationKind.PERIODIC and not self.period_days:
            raise ValueError("periodic 무효화 조건에는 재확인 주기가 필요하다")


@dataclass
class KnowledgeItem:
    """지식 항목 하나.

    **개념 단위**다 (ADR-003) — 파일이나 함수가 아니다. 근거가 여러 파일·여러
    커밋에 걸칠 수 있으므로 출처는 단수가 아니라 목록이다.
    """

    title: str
    body: str
    provenance: list[Provenance]
    invalidation: Invalidation
    id: str = field(default_factory=lambda: f"k-{uuid.uuid4().hex[:12]}")
    stale: bool = False
    edited_by_human: bool = False
    """사람이 고쳤다는 표시 (D37 조건 3).

    이것이 없으면 다음 ingest 가 **자기가 이전에 쓴 것으로 착각하고 그냥 갱신**한다.
    표시가 있으면 에이전트는 덮어쓰지 않고 모순으로 올린다 (D38).
    """

    okf_type: str = "knowledge"
    """OKF 필수 필드 (FR-55)."""

    def can_be_promoted(self) -> bool:
        """승격 자격. 무효화 조건이 없으면 승격하지 않는다 (FR-14).

        검증할 수 없는 지식을 들이는 것보다 공백으로 두는 편이 낫다.
        """
        return self.invalidation is not None
