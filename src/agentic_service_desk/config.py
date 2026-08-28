"""설정.

값은 환경변수 또는 `.env` 에서 읽는다. **기본값은 안전한 쪽으로** 둔다 —
설정을 잊었을 때 조용히 위험한 동작을 하지 않게 하기 위해서다.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """실행에 필요한 값. 접두사 `ASD_` 로 환경변수를 받는다."""

    model_config = SettingsConfigDict(
        env_prefix="ASD_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- 저장소 (ADR-002) -------------------------------------------------
    knowledge_dir: Path = Field(
        default=Path("var/knowledge"),
        description="지식베이스. 파일 + git 이다 (D12). 운영 DB 와 분리된다.",
    )
    operations_db: Path = Field(
        default=Path("var/operations.sqlite3"),
        description="운영 데이터 — QnA · 티켓 · 답변 이력 · 통계 (SQLite, WAL).",
    )
    source_mirror_dir: Path = Field(
        default=Path("var/source-mirror"),
        description="모 시스템 소스의 읽기 전용 전체 클론 (ADR-006). shallow 는 쓰지 않는다.",
    )

    # --- 모 시스템 연동 (ADR-006 · XR-1~7) --------------------------------
    parent_api_base_url: str = Field(
        default="",
        description="모 시스템 내부 API. 비어 있으면 어댑터가 동작을 거부한다.",
    )
    parent_repo_url: str = Field(default="", description="모 시스템 소스 저장소 (읽기 전용).")
    parent_adapter: str = Field(
        default="http",
        description=(
            "어느 어댑터를 쓸 것인가 — `http` | `mock`. **기본은 실제 연동**이다. "
            "mock 은 명시적으로 골라야 쓰인다 (ADR-008) — 설정 실수로 mock 이 돌면 "
            "'질문이 없다'와 구분되지 않는다."
        ),
    )
    poll_interval_seconds: int = Field(
        default=60,
        description="QnA 폴링 주기 (NFR-7). 주기가 답변 지연에 그대로 더해진다.",
    )

    # --- LLM (ADR-005) ----------------------------------------------------
    llm_base_url: str = Field(
        default="",
        description="로컬 LLM 엔드포인트. **외부 API 를 넣지 않는다** — NFR-1 위반이다.",
    )
    llm_model: str = Field(default="", description="생성·검수에 쓸 모델 식별자.")
    llm_embedding_model: str = Field(
        default="", description="임베딩 모델. 이것도 로컬이어야 한다 (NFR-1)."
    )

    # --- 도입 단계 (D49 · FR-59) ------------------------------------------
    stage: str = Field(
        default="S0",
        description=(
            "현재 켜진 도입 단계. S0~S5. 켜지지 않은 기능의 대기열은 표시하지 않는다. "
            "기본이 S0 인 이유는 그 단계가 **대외 노출이 없는** 구간이기 때문이다."
        ),
    )

    # --- 보존 (PO-4 · FR-51) ----------------------------------------------
    retention_days: int | None = Field(
        default=None,
        description=(
            "QnA 원문·티켓 보존 기간. **기본은 무제한** — 사내에 정해진 정책이 없어서 "
            "두지 않기로 *결정* 한 것이지 정책 부재가 아니다. 값을 넣으면 만료가 켜진다."
        ),
    )

    # --- 알림 (ADR-007) ---------------------------------------------------
    alert_webhook_url: str = Field(
        default="",
        description="위험 대기열·국면 역행 알림. 비어 있으면 대시보드 배너로만 알린다.",
    )


def load_settings() -> Settings:
    """설정을 읽는다. 호출부는 이 함수만 쓴다 — 전역 싱글턴을 두지 않는다."""
    return Settings()
