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
    phase: int = Field(
        default=1,
        description=(
            "운영 국면 — 1 콜드 스타트 / 2 축적 / 3 성숙 (§1.3.3). **단계(stage)와 다른 "
            "축이다**: 단계는 *우리가 무엇을 켰는가*이고 국면은 *지식베이스가 무엇을 할 수 "
            "있는가*다. 대응은 느슨하며 고정 매핑이 아니다. 이 값이 **검수 강도**를 정한다 "
            "(FR-57): 1·2국면은 위험 신호로 선별하고 3국면은 표본으로 본다. "
            "**완화는 운영자 승인, 강화(역행)는 자동**이며 그 판정은 WBS-4.8.1 이 붙인다."
        ),
    )
    review_sample_rate: float = Field(
        default=0.2,
        description=(
            "3국면에서 사람이 볼 표본 비율 (FR-57). 기준이 안정된 뒤 **사람은 감사자로 "
            "물러나므로** 신호 선별이 아니라 표본으로 본다. 실데이터로 다시 정해야 하는 "
            "임계값이다 (O8). 초안 id 로 결정적으로 뽑는다 — 다시 판정해도 같은 건이 "
            "걸려야 재실행이 결과를 바꾸지 않는다."
        ),
    )
    relax_promotion: bool = Field(
        default=False,
        description=(
            "3국면에서 자동 승격 조건을 완화한다 (§6.8.4-b, FR-33). **완화할 수 있는 것은 "
            "조건 2(무반려 통과) 하나뿐이다** — 조건 1(코드 연결)은 §6.8.4-c 가 '판정을 "
            "대신한다'고 못 박았으므로 풀면 우회책이 표준이 될 우려로 되돌아가고, "
            "조건 3(명시적 해결)은 §5.3 의 배제를 푸는 조건이라 풀 수 없다. "
            "**완화는 운영자 승인 사항**이므로 기본은 꺼짐이고, 3국면이 아니면 무시된다."
        ),
    )
    quiet_hours: int = Field(
        default=336,
        description=(
            "이 시간만큼 아무 일도 없으면 QnA 추적을 닫는다 (O18, §6.1). 마지막으로 말한 "
            "쪽이 등급을 정한다 — 답이 나간 뒤의 침묵은 **암묵적 해결**, 물음 뒤의 침묵은 "
            "**미해결 종료**(지식 공백)다. 닫지 않으면 항목이 영원히 열려 있어 모든 비율의 "
            "분모가 계속 자란다. **기본값 14일은 넉넉한 쪽**이다 — 짧게 잡으면 아직 읽지도 "
            "않은 답변이 암묵적 해결로 닫힌다. 실데이터로 다시 정해야 하는 임계값이다."
        ),
    )
    bot_accounts: str = Field(
        default="",
        description=(
            "**읽을 때 걸러낼** 계정 목록. 쉼표로 여럿. **되먹임 차단의 유일한 기준**이다 "
            "(D7). 비어 있으면 산출물 필터가 **동작을 거부한다** — 목록이 없으면 봇 답변이 "
            "사람 답변으로 보여 §5.3 이 조용히 무력화되기 때문이다. mock 으로 개발할 때는 "
            "`svc-agentic-desk` 를 넣는다. 계정을 갈아 끼웠다면 **옛 계정도 남긴다** — "
            "빼는 순간 옛 산출물이 지식 원천이 된다."
        ),
    )
    publish_account: str = Field(
        default="",
        description=(
            "**쓸 때 쓰는** 계정 하나 (XR-5). `bot_accounts` 와 다른 설정인 것이 요점이다 — "
            "거를 계정은 여럿이지만 게재하는 계정은 하나다. 게재 관문이 이 값을 "
            "`bot_accounts` 와 대조하고, 목록에 없으면 **게재하지 않는다**: 목록 밖 계정으로 "
            "나간 답변은 다음 주기에 사람 답변으로 읽혀 자기 산출물을 다시 배운다. "
            "`mock` 어댑터는 이 값을 쓰지 않고 자기 계정(`svc-agentic-desk`)을 밝힌다."
        ),
    )

    # --- LLM (ADR-005) ----------------------------------------------------
    llm_base_url: str = Field(
        default="",
        description="로컬 LLM 엔드포인트. **외부 API 를 넣지 않는다** — NFR-1 위반이다.",
    )
    llm_model: str = Field(default="", description="생성·검수에 쓸 모델 식별자.")
    llm_embedding_model: str = Field(
        default="", description="임베딩 모델. 실제 데이터를 다룰 때는 로컬이어야 한다 (NFR-1)."
    )
    embedding_provider: str = Field(
        default="openai_compatible",
        description=(
            "임베딩 제공자 형식 — `openai_compatible` | `minimax`. **채팅과 다를 수 있다.** "
            "채팅은 OpenAI 호환이 사실상 표준이 됐지만 임베딩은 그렇지 않다 "
            "(MiniMax 는 `texts`+`type` 을 받고 `vectors` 를 준다)."
        ),
    )
    embedding_base_url: str = Field(
        default="",
        description="임베딩 엔드포인트. 비우면 `llm_base_url` 을 쓴다.",
    )
    llm_api_key: str = Field(
        default="", description="원격 제공자를 쓸 때의 키. 로컬 런타임에는 대개 불필요하다."
    )
    llm_allow_remote: bool = Field(
        default=False,
        description=(
            "원격 LLM 을 허용할 것인가. **개발 환경 전용이다.** 이것만으로는 열리지 않고 "
            "실제 데이터가 없어야 한다 — 어댑터가 mock 이고 소스 저장소가 비어 있어야 "
            "한다 (ADR-005 §개발 환경). 셋 중 하나라도 어긋나면 거부한다."
        ),
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
