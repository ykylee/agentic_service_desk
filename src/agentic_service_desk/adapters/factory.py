"""어댑터 선택.

**mock 이 프로덕션에서 도는 사고를 막는다** (ADR-008 귀결). 기본은 실제 연동이고,
mock 은 **명시적으로 골라야** 쓰인다. 골랐을 때는 기동 로그에 경고를 남긴다 —
설정 실수로 mock 이 돌면 "질문이 없다"와 구분되지 않기 때문이다.
"""

from __future__ import annotations

import warnings

from agentic_service_desk.adapters.parent_system import ParentSystem
from agentic_service_desk.config import Settings


def build_parent_system(settings: Settings) -> ParentSystem:
    """설정에 따라 어댑터를 만든다."""
    if settings.parent_adapter == "mock":
        from agentic_service_desk.adapters.mock import MockParentSystem

        warnings.warn(
            "모 시스템 어댑터가 **mock** 이다. 실제 데이터가 아니며 게재도 나가지 않는다. "
            "프로덕션이라면 ASD_PARENT_ADAPTER 를 확인하라.",
            RuntimeWarning,
            stacklevel=2,
        )
        return MockParentSystem()

    from agentic_service_desk.adapters.http import HttpParentSystem

    return HttpParentSystem(
        settings.parent_api_base_url, publish_account=settings.publish_account
    )
