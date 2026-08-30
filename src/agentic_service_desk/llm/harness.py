"""pi 하네스 제공자 설정 생성 (ADR-009).

**pi 는 우리 코드가 아니다.** 우리 게이트웨이는 NFR-1 정책을 지나지만 pi 는 지나지
않는다 — 그런데 지식 구축이야말로 **소스코드를 직접 읽는** 경로다(D5).

그래서 pi 의 `models.json` 을 **우리가 생성하고, 생성 시점에 같은 정책을 건다.**
설정을 만드는 그 순간이 유일한 검문소다.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentic_service_desk.config import Settings
from agentic_service_desk.llm.policy import DataExposure, assert_endpoint_allowed

PROVIDER_NAME = "asd"
"""pi 쪽 제공자 이름. **어느 제공자를 쓰든 이 이름은 고정이다** —
로컬 GPU 든 원격이든 pi 입장에서는 같은 제공자를 가리킨다."""

DEFAULT_MODELS_JSON = Path.home() / ".pi" / "agent" / "models.json"


def render_models_json(settings: Settings) -> dict:
    """pi `models.json` 내용을 만든다. **애플리케이션과 같은 정책을 통과시킨다.**

    실제 데이터에 닿는 실행에서 원격을 가리키면 여기서 거부된다 — pi 가 우리 정책을
    지나지 않으므로 이 시점이 마지막 기회다.
    """
    assert_endpoint_allowed(
        settings.llm_base_url,
        allow_remote=settings.llm_allow_remote,
        exposure=DataExposure(
            adapter=settings.parent_adapter,
            source_repo_url=settings.parent_repo_url,
            source_is_simulated=settings.simulated_source,
        ),
    )
    if not settings.llm_base_url or not settings.llm_model:
        raise ValueError("ASD_LLM_BASE_URL 과 ASD_LLM_MODEL 이 필요하다")

    return {
        "providers": {
            PROVIDER_NAME: {
                "baseUrl": settings.llm_base_url,
                "api": "openai-completions",
                # **키를 파일에 박지 않는다.** pi 가 `$VAR` 참조를 지원한다 (ADR-009).
                "apiKey": "$ASD_LLM_API_KEY",
                "models": [{"id": settings.llm_model}],
            }
        }
    }


def write_models_json(settings: Settings, path: Path | None = None) -> Path:
    """생성해 파일로 쓴다.

    **이 파일은 생성물이다** — 손으로 고치면 다음 생성에서 덮인다. 그렇게 두는 이유는
    `.env` 를 단일 출처로 유지하기 위해서다(ADR-009).
    """
    target = path or DEFAULT_MODELS_JSON
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = render_models_json(settings)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target
