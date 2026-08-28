"""모 시스템 API 가 주고받는 자료 형태 (ADR-008).

**여기서 정한 형태가 모 시스템에 만들 API 의 명세다.** 모 시스템에 아직 이 API 가
없으므로 우리가 정의한다 — 운영자가 곧 모 시스템 개발자이므로 가능하다(D30).

형태를 고를 때의 기준은 하나다. **각 필드가 어느 결정을 떠받치는가.**
그 근거가 없는 필드는 넣지 않았다.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class ResolutionMethod(enum.StrEnum):
    """모 시스템이 알려줄 수 있는 해결 표시의 종류.

    **암묵적 해결은 여기 없다.** 그것은 모 시스템이 아는 사실이 아니라 우리가
    타임아웃으로 판정하는 것이기 때문이다(§5.3.1). 모 시스템은 *사람이 표시했는가*
    만 알려주면 된다.
    """

    USER_MARKED = "user_marked"
    """이용자가 해결 표시를 눌렀다. **명시적 해결의 1차 신호다** (D35)."""

    OPERATOR_CLOSED = "operator_closed"
    """운영자가 확인해 종결했다. 이것도 명시적 해결이다."""


@dataclass(frozen=True)
class Question:
    """XR-1 — 질문 하나."""

    id: str
    body: str
    asker_account: str
    """질문자. 사내 인증으로 이미 식별된다(D13).

    **지식 항목과 콘텐츠로는 넘어가지 않는다** (PO-3) — 종결 기록의 첫 필드가
    *일반화된 질문*인 것이 그 집행 지점이다.
    """
    created_at: str
    title: str | None = None


@dataclass(frozen=True)
class Answer:
    """XR-2 — 게재된 답변.

    **`author_account` 가 이 계약에서 가장 중요한 필드다.** 없으면 봇과 사람을 가릴
    수 없고, 그러면 §5.3 되먹임 차단이 통째로 무너진다 — 대안이 없는 유일한 항목이다.
    """

    id: str
    question_id: str
    body: str
    author_account: str
    """누가 올렸는가. **계정 단위 식별**이 되먹임 차단의 기준이다(D7).

    내용 판별이나 신뢰도 점수 같은 흐릿한 기준을 쓰지 않는 이유는, 이것이
    기계적으로 확정 가능한 사실이기 때문이다.
    """
    created_at: str
    revised_at: str | None = None
    """정정된 적이 있는가 (PO-1)."""


@dataclass(frozen=True)
class Followup:
    """XR-3 — 후속 답글.

    답변과 나눠 두는 이유는 **의미가 다르기** 때문이다. 후속은 파이프라인 재실행의
    트리거이고(D9), 답변은 우리가 만든 산출물이다. 실제 게시판에서 같은 테이블에
    살더라도 계약에서는 구분한다.
    """

    id: str
    question_id: str
    body: str
    author_account: str
    created_at: str


@dataclass(frozen=True)
class Resolution:
    """XR-4 — 해결 표시 상태.

    **ingest 자격과 승격이 모두 여기서 갈린다**(§5.3.1, §6.8).
    """

    question_id: str
    resolved: bool
    method: ResolutionMethod | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None

    @property
    def is_explicit(self) -> bool:
        """명시적 해결인가.

        모 시스템이 알려주는 해결은 **전부 명시적**이다 — 사람이 실제로 표시했거나
        종결한 것이기 때문이다. 암묵적 해결은 우리가 타임아웃으로 판정한다.
        """
        return self.resolved and self.method is not None
