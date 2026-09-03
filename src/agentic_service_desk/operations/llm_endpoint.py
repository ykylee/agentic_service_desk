"""모델 연결의 단일 출처 (FR-62, ADR-009 개정 2026-09-03).

**`.env` 는 씨앗이고 SSOT 는 운영 DB 다.** 국면(`phase_state`)이 이미 같은 형태다 —
설정은 처음 한 번을 정하고, 그 뒤로 값을 바꾸는 일은 시스템 안에서 일어난다.

옮긴 이유는 하나다. 연결을 바꾸는 길이 파일 편집과 두 프로세스 재기동뿐이면 그것은
**대시보드에서 할 수 없는 일**이 되고, §8.1 에 따르면 곧 아무도 할 수 없는 일이 된다.

**검문소는 옮기지 않았다.** 저장이 `assert_endpoint_allowed` 를 지나며, 통과하지
못하면 DB 도 `models.json` 도 바뀌지 않는다 — 화면이 생겼다고 NFR-1 을 우회하는 문이
열려서는 안 된다. 채팅과 임베딩 **두 주소를 모두** 본다 (`build_gateway` 와 같은 판정).

**키는 여기서 다루지 않는다.** 키는 환경변수에 남고 `models.json` 에는 `$VAR` 참조만
들어간다 (ADR-009) — 자격증명을 `var/` 의 파일 하나가 들게 하지 않는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from agentic_service_desk.config import Settings
from agentic_service_desk.llm import harness as pi_config
from agentic_service_desk.llm.policy import DataExposure, assert_endpoint_allowed

SEED = "씨앗(.env)"
"""DB 에 행이 없을 때. **`.env` 를 그대로 읽은 상태**다."""

DASHBOARD = "화면"
"""운영자가 화면에서 정한 값. 이후로 `.env` 를 고쳐도 따라가지 않는다."""


@dataclass(frozen=True)
class Endpoint:
    """지금 쓰는 연결."""

    base_url: str = ""
    model: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    max_output_tokens: int = 32_768
    allow_remote: bool = False
    source: str = SEED
    updated_at: str = ""
    note: str = ""

    @property
    def configured(self) -> bool:
        """생성을 부를 수 있는가.

        **모델이 안 붙은 것과 지식이 없는 것은 다른 상태다.** 둘을 가르지 못하면
        화면에서 "답을 못 만들었다"가 같은 모양으로 보이고, 대응은 정반대다.
        """
        return bool(self.base_url and self.model)

    @property
    def effective_embedding_base_url(self) -> str:
        """임베딩 주소. 비우면 채팅 주소를 쓴다 (`ASD_EMBEDDING_BASE_URL` 과 같은 규칙)."""
        return self.embedding_base_url or self.base_url


def current(conn: sqlite3.Connection, settings: Settings) -> Endpoint:
    """지금 값. **행이 없으면 `.env` 를 씨앗으로 읽는다.**

    이행이 행을 만들지 않는 이유가 이것이다 — 계단 하나로 도는 구성이 달라지지 않는다.
    """
    row = conn.execute("SELECT * FROM llm_endpoint WHERE id = 1").fetchone()
    if row is None:
        return _from_settings(settings)
    return Endpoint(
        base_url=row["base_url"],
        model=row["model"],
        embedding_model=row["embedding_model"],
        embedding_base_url=row["embedding_base_url"],
        max_output_tokens=row["max_output_tokens"],
        allow_remote=bool(row["allow_remote"]),
        source=DASHBOARD,
        updated_at=row["updated_at"],
        note=row["note"],
    )


def _from_settings(settings: Settings) -> Endpoint:
    return Endpoint(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        embedding_model=settings.llm_embedding_model,
        embedding_base_url=settings.embedding_base_url,
        max_output_tokens=settings.llm_max_output_tokens,
        allow_remote=settings.llm_allow_remote,
        source=SEED,
    )


def apply(settings: Settings, endpoint: Endpoint) -> Settings:
    """설정에 연결을 입힌다.

    **호출부를 고치지 않기 위해서다.** 연결을 쓰는 자리가 웹·워커에 흩어져 있고
    전부 `Settings` 를 읽는다 — 진입 지점에서 한 번 갈아 끼우면 나머지는 그대로다.
    """
    return settings.model_copy(
        update={
            "llm_base_url": endpoint.base_url,
            "llm_model": endpoint.model,
            "llm_embedding_model": endpoint.embedding_model,
            "embedding_base_url": endpoint.embedding_base_url,
            "llm_max_output_tokens": endpoint.max_output_tokens,
            "llm_allow_remote": endpoint.allow_remote,
        }
    )


def effective(conn: sqlite3.Connection, settings: Settings) -> Settings:
    """DB 의 연결을 입힌 설정. 웹은 요청마다, 워커는 틱마다 부른다."""
    return apply(settings, current(conn, settings))


def exposure_of(settings: Settings) -> DataExposure:
    """이 실행이 실제 데이터를 다루는가 (`build_gateway` 와 같은 판정)."""
    return DataExposure(
        adapter=settings.parent_adapter,
        source_repo_url=settings.parent_repo_url,
        source_is_simulated=settings.simulated_source,
    )


def check(settings: Settings, endpoint: Endpoint) -> None:
    """이 연결을 써도 되는가. 안 되면 `RemoteEndpointRejected` 를 올린다.

    **두 주소를 다 본다.** 임베딩이 다른 주소를 가리킬 수 있는데 나가는 것은 지식
    본문 그 자체다 — 채팅만 검문하면 그 문은 열린 채로 남는다.
    """
    exposure = exposure_of(settings)
    for url in (endpoint.base_url, endpoint.effective_embedding_base_url):
        if url:
            assert_endpoint_allowed(
                url, allow_remote=endpoint.allow_remote, exposure=exposure
            )


@dataclass(frozen=True)
class SaveResult:
    """저장 결과. **pi 설정까지 갔는지를 함께 말한다.**

    DB 만 바뀌고 `models.json` 이 옛것이면 답변은 새 모델로 나가는데 지식 구축은
    옛 모델로 도는, 화면에서 보이지 않는 어긋남이 생긴다 (ADR-009 가 애초에 막으려던
    바로 그것이다). 그래서 실패를 삼키지 않고 돌려준다.
    """

    endpoint: Endpoint
    models_json: Path | None = None
    harness_error: str = ""


def save(
    conn: sqlite3.Connection,
    settings: Settings,
    endpoint: Endpoint,
    *,
    models_json_path: Path | None = None,
) -> SaveResult:
    """화면이 정한 값을 SSOT 에 쓰고 pi 설정을 다시 만든다.

    **정책이 먼저다.** 거부되면 DB 도 파일도 건드리지 않는다 — 반출은 되돌릴 수 없다.
    """
    check(settings, endpoint)

    now = datetime.now(UTC).isoformat(timespec="seconds")
    saved = replace(endpoint, source=DASHBOARD, updated_at=now)
    conn.execute(
        """
        INSERT INTO llm_endpoint (
            id, base_url, model, embedding_model, embedding_base_url,
            max_output_tokens, allow_remote, updated_at, note
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            base_url = excluded.base_url,
            model = excluded.model,
            embedding_model = excluded.embedding_model,
            embedding_base_url = excluded.embedding_base_url,
            max_output_tokens = excluded.max_output_tokens,
            allow_remote = excluded.allow_remote,
            updated_at = excluded.updated_at,
            note = excluded.note
        """,
        (
            saved.base_url,
            saved.model,
            saved.embedding_model,
            saved.embedding_base_url,
            saved.max_output_tokens,
            int(saved.allow_remote),
            saved.updated_at,
            saved.note,
        ),
    )
    conn.commit()

    if not saved.configured:
        # 비운 것도 뜻이 있는 저장이다 — "모델을 떼겠다". pi 설정은 손대지 않는다.
        return SaveResult(endpoint=saved)
    try:
        path = pi_config.write_models_json(apply(settings, saved), models_json_path)
    except (OSError, ValueError) as exc:
        return SaveResult(endpoint=saved, harness_error=str(exc))
    return SaveResult(endpoint=saved, models_json=path)


@dataclass(frozen=True)
class ProbeResult:
    """연결 시험 한 번."""

    ok: bool
    detail: str
    elapsed_ms: int = 0
    models: tuple[str, ...] = ()

    @property
    def model_listed(self) -> bool:
        return bool(self.models)


def probe(endpoint: Endpoint, *, api_key: str = "", timeout: float = 5.0) -> ProbeResult:
    """엔드포인트에 닿는가 — **모델 목록을 물어본다.**

    OpenAI 호환 표면의 `GET /models` 다. 생성까지 되는지는 이것으로 알 수 없고
    (그것은 `pi` 가 답한다), 여기서 답하는 것은 **주소와 키가 맞는가**까지다.
    둘을 한 버튼에 묶지 않는 이유는 소요가 두 자릿수 배 다르기 때문이다.
    """
    if not endpoint.base_url:
        return ProbeResult(ok=False, detail="주소가 비어 있다 — 연결이 설정되지 않았다")

    url = endpoint.base_url.rstrip("/") + "/models"
    # **키는 환경변수에서 온다** — 화면은 키를 받지도 보여주지도 않는다 (ADR-009).
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    started = datetime.now(UTC)
    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        return ProbeResult(ok=False, detail=f"닿지 않는다: {exc}")
    elapsed = int((datetime.now(UTC) - started).total_seconds() * 1000)

    if response.status_code >= 400:
        return ProbeResult(
            ok=False,
            detail=f"{response.status_code} — {response.text.strip()[:200]}",
            elapsed_ms=elapsed,
        )
    try:
        payload = response.json()
        listed = tuple(str(m.get("id", "")) for m in payload.get("data", []))
    except (ValueError, AttributeError):
        # 200 이 왔으면 주소는 산다. 형식이 우리 기대와 달라도 그것은 별개의 사실이다.
        return ProbeResult(
            ok=True, detail="닿는다 — 다만 모델 목록 형식이 OpenAI 호환이 아니다",
            elapsed_ms=elapsed,
        )
    return ProbeResult(ok=True, detail="닿는다", elapsed_ms=elapsed, models=listed)
