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
    parent_repo_url: str = Field(
        default="",
        description=(
            "모 시스템 소스 저장소 (읽기 전용). **쉼표로 여럿** — 모 시스템이 저장소 "
            "하나라는 보장이 없다. 저장소마다 자기 미러 칸과 자기 커서를 갖는다: 커서는 "
            "커밋 해시라 저장소 안에서만 뜻이 있고, 합치면 A 의 해시로 B 의 변경분을 "
            "묻게 된다. **지식베이스는 하나다** — 나누는 것은 원천을 읽는 진행 지점뿐이고, "
            "개념은 한 자리에 모여야 저장소를 넘는 모순이 드러난다."
        ),
    )
    retired_repo_url: str = Field(
        default="",
        description=(
            "**한때 원천이었으나 더는 읽지 않는 저장소** (쉼표로 여럿). 수집은 하지 "
            "않고 **출처 확인에만 쓴다.**\n\n"
            "저장소를 `parent_repo_url` 에서 빼면 거기서 만든 지식이 통째로 근거를 "
            "잃는다 — Lint 가 '출처 커밋이 저장소에 없다'로 그 항목 전부를 Q5 로 "
            "올리고, 그것은 오탐이 아니라 **사실**이다(ADR-002 결정 4). 그런데 "
            "**지식을 지울 이유는 되지 않는다**: 그 커밋은 여전히 실재하고 미러도 "
            "디스크에 남아 있으며, 달라진 것은 '앞으로 더 읽을 것인가'뿐이다.\n\n"
            "그래서 *읽을 목록*과 *되짚을 목록*을 나눈다. 읽기를 멈추는 것과 "
            "근거를 버리는 것은 다른 결정이고, 하나로 묶으면 **원천을 줄이는 순간 "
            "과거의 지식이 조용히 근거를 잃는다.**"
        ),
    )
    source_exclude: str = Field(
        default="",
        description=(
            "원천에서 뺄 경로 패턴 (**쉼표로 여럿**, glob). 비어 있으면 아무것도 빼지 "
            "않는다 — **선언하지 않은 경로는 읽는다**가 기본이다. FR-9 의 설정 파일 "
            "배제와 별개다: 그쪽은 *무엇이 지식이 될 수 없는가*(상태값)를 코드가 알고, "
            "이쪽은 *이 저장소에서 무엇이 모 시스템의 것이 아닌가*를 **사람만 안다.** "
            "메타 계층(세션 인계·백로그)이나 벤더링된 남의 소스가 그렇다 — 읽으면 "
            "'작업을 어떻게 했는가'가 모 시스템 지식인 척 들어온다. "
            "뺀 경로는 조용히 사라지지 않고 워커가 건수를 보고한다."
        ),
    )
    simulated_source: bool = Field(
        default=False,
        description=(
            "`parent_repo_url` 이 가리키는 것이 **모 시스템이 아니라는 선언** (검증 실행). "
            "파이프라인을 실제 저장소로 검증하려면 코드가 로컬에 있어야 하는데, 그 저장소가 "
            "우리 것이면 NFR-1 이 지키려던 반출 위험 자체가 없다. **`llm_allow_remote` 와 "
            "합치지 않은 것이 요점이다** — 하나로 두면 개발 편의로 켠 플래그가 모 시스템이 "
            "붙는 날 소스 조건까지 함께 푼다. **어댑터 조건은 이것으로 풀리지 않는다**: "
            "저장소가 우리 것이어도 QnA 는 질문자의 말이다. 기본이 거짓인 이유는 "
            "**선언하지 않은 저장소를 모 시스템으로 보기** 위해서다."
        ),
    )
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
            "운영 국면의 **씨앗** — 1 콜드 스타트 / 2 축적 / 3 성숙 (§1.3.3). **단계"
            "(stage)와 다른 축이다**: 단계는 *우리가 무엇을 켰는가*이고 국면은 *지식"
            "베이스가 무엇을 할 수 있는가*다. 국면이 **검수 강도**(FR-57)와 자동 승격 "
            "범위(§6.8.4-b)를 함께 정한다. "
            "**이 값은 국면이 한 번도 정해지지 않은 DB 에만 쓰인다** — 그 뒤로 SSOT 는 "
            "`phase_state` 이고 여기를 고쳐도 따라가지 않는다 (WBS-4.8.1). 그렇게 옮긴 "
            "이유는 하나다: **후퇴는 시스템이 자동으로 하는데**(§1.3.3-c) 환경변수는 "
            "시스템이 내릴 수 없다. 화면이 설정과 DB 가 어긋난 것을 말해 준다."
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

    phase_window_days: int = Field(
        default=30,
        description=(
            "국면 세 축을 관측하는 창 (§1.3.3-a, WBS-4.8.1). **누적이 아니라 창인 이유**가 "
            "있다 — 국면은 *지금* 무엇을 할 수 있는가인데, 누적으로 재면 반년 전의 콜드 "
            "스타트가 오늘의 커버리지를 계속 끌어내려 실제로 나아진 시스템이 영영 2국면에 "
            "닿지 못한다. 짧게 잡으면 반대로 표본이 얇아져 비율이 튄다 (§1.3.1)."
        ),
    )
    phase_min_sample: int = Field(
        default=10,
        description=(
            "축 하나를 판정하려면 필요한 최소 분모 (§1.3.1). 이 규모의 문의는 **일 단위로 "
            "소수**라 창 안에 세 건이면 비율이 33%p 단위로 튄다. 못 미치는 축은 값이 아니라 "
            "**없음**이고, 없는 축이 있으면 전진 제안도 역행 판정도 하지 않는다 — "
            "국면이 잡음을 따라 움직이지 않게."
        ),
    )
    phase_thresholds_path: Path | None = Field(
        default=None,
        description=(
            "국면 전환 임계 선언 (TOML). 비우면 패키지의 `phase_thresholds.toml` 을 쓴다. "
            "**콘텐츠 타입 선언과 달리 덮는다** — 임계는 목록이 아니라 값이라 더할 것이 "
            "없다. 기본 선언의 **전진 임계는 비어 있고**(O8) 비어 있는 동안 전진은 "
            "제안되지 않는다. 역행 임계에만 기본값이 있다: 비워 두면 후퇴가 영영 일어나지 "
            "않는데, 그것은 §0 이 꼽은 실패 방식 그대로다."
        ),
    )

    recheck_period_days: int = Field(
        default=7,
        description=(
            "표본 재검증 주기 (WBS-4.8.4, §5.6.7). 배치 주기(분 단위)로 뽑으면 표본이 "
            "순식간에 쌓여 **재검증이 이중 작업**이 되고, 그러면 사람이 이 화면을 아예 "
            "닫는다. 실데이터로 다시 정할 값이지만(O50) **0 으로 두지 않았다** — 0 은 "
            "장치가 있는데 한 번도 돌지 않는 상태이고, 화면에서 없는 것과 구분되지 않는다."
        ),
    )
    recheck_sample_size: int = Field(
        default=3,
        description=(
            "한 주기에 다시 볼 건수 (O50). **비율이 아니라 건수인 이유**는 1인 겸업이기 "
            "때문이다 — 운영자가 답할 물음은 '승인분의 몇 %'가 아니라 '이번 주에 몇 건을 "
            "다시 볼 것인가'다. 너무 적으면 무의미하고 많으면 이중 작업이 된다 (§5.6.7)."
        ),
    )

    # --- 콘텐츠 (FR-42 · §7.5) --------------------------------------------
    content_types_path: Path | None = Field(
        default=None,
        description=(
            "**더할** 콘텐츠 타입 선언 파일 (TOML). 기본 넷(FAQ · 가이드 · 칼럼 · "
            "뉴스레터)은 패키지와 함께 오고 이 파일은 거기 더해진다 — **덮지 않는다.** "
            "덮게 두면 기본 타입의 검수 강도가 설정 한 줄로 낮아지는데(발행물이 변경분 "
            "검수가 되는 식) 그 사실이 어디에도 남지 않는다. 새 타입을 더하는 데 "
            "코드 변경이 필요 없다는 FR-42 가 이 설정으로 성립한다."
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
        description=(
            "위험 대기열·국면 역행 알림 (ADR-007 결정 2). 비어 있으면 대시보드 배너로만 "
            "알린다 — **배너는 웹훅이 있어도 함께 뜬다**: 알림이 도착하지 않은 것과 경고가 "
            "없는 것을 화면에서 구분할 수 없으면 침묵이 안전으로 읽힌다. **채널은 하나뿐**"
            "이다: 메일·SMS 를 더하면 표면이 번지고, 어느 채널이 살아 있는지 아무도 모르게 "
            "된다. 보내는 것은 **세는 것뿐**이며 질문 원문도 지식 본문도 싣지 않는다."
        ),
    )
    alert_neglect_hours: int = Field(
        default=72,
        description=(
            "Q4·Q5 가 이만큼 밀리면 알린다 (§8.2, O53). **건수가 아니라 시간으로 재는 "
            "이유**는 위험이 '몇 건인가'가 아니라 '얼마나 오래 노출됐는가'이기 때문이다 — "
            "건수로 잡으면 한 건짜리 모순이 한 달 방치돼도 조용하고, 하루 만에 다섯 건이 "
            "잡히면 볼 시간도 없었는데 울린다. 기본 3일은 **1인 겸업이 며칠 열지 않을 수 "
            "있다는 전제**(§8.6.3)에서 나왔고, 실데이터로 다시 정할 값이다."
        ),
    )

    @property
    def parent_repo_urls(self) -> tuple[str, ...]:
        """붙은 소스 저장소들. 쉼표로 나뉘고 빈 칸은 버린다.

        `parent_repo_url` 은 **정책이 보는 값**으로 그대로 남는다 — 비었는가만
        묻기 때문이다(NFR-1). 목록이 필요한 것은 수집 쪽이다.
        """
        return tuple(u.strip() for u in self.parent_repo_url.split(",") if u.strip())

    @property
    def retired_repo_urls(self) -> tuple[str, ...]:
        """더는 읽지 않지만 출처를 되짚을 수 있어야 하는 저장소들."""
        return tuple(u.strip() for u in self.retired_repo_url.split(",") if u.strip())

    @property
    def verifiable_repo_urls(self) -> tuple[str, ...]:
        """출처를 확인할 수 있어야 하는 저장소 전체 — 현행 원천 + 물러난 것.

        **순서를 지킨다.** 현행 원천이 앞이라 같은 커밋이 양쪽에 있으면 현행
        쪽으로 풀린다. 중복은 뒤엣것을 버린다.
        """
        seen: dict[str, None] = {}
        for url in (*self.parent_repo_urls, *self.retired_repo_urls):
            seen.setdefault(url, None)
        return tuple(seen)

    @property
    def source_exclude_patterns(self) -> tuple[str, ...]:
        """원천에서 뺄 glob 패턴들. 쉼표로 나뉘고 빈 칸은 버린다."""
        return tuple(x.strip() for x in self.source_exclude.split(",") if x.strip())


def load_settings() -> Settings:
    """설정을 읽는다. 호출부는 이 함수만 쓴다 — 전역 싱글턴을 두지 않는다."""
    return Settings()
