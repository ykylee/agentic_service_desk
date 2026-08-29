"""운영 국면의 판정과 전환 (WBS-4.8.1, FR-49 · NFR-8, §1.3.3).

국면에 매달린 결정이 셋이다 — 지표 임계(**O8**), 검수 강도(**FR-57**), 자동 승격
범위(§6.8.4-b). 그런데 지금까지 그 값은 **설정 파일의 숫자 하나**였다. 사람이 손으로
적은 값이 검수 강도와 자동화 범위를 함께 넓히는데, **그 숫자가 맞는지 아무도 세지
않았다.** 여기가 그것을 세는 자리다.

## 국면의 SSOT 를 DB 로 옮긴다

`ASD_PHASE` 는 이제 **씨앗**이다 — 국면이 한 번도 정해지지 않은 DB 에만 쓰인다.
그 뒤로는 `phase_state` 가 진실이고, 설정과 달라져도 설정을 따르지 않는다.

이유가 하나뿐이다. **후퇴는 시스템이 자동으로 한다**(§1.3.3-c). 환경변수는 시스템이
내릴 수 없으므로, 국면이 설정에 있는 한 "강화는 자동"은 성립하지 않는다. 화면이
설정과 DB 가 어긋난 것을 말해 주는 것으로 그 대가를 갚는다.

## 완화는 승인, 강화는 자동 — 그 비대칭이 코드에도 있다

| 방향 | 무엇이 일어나는가 | 누가 | 임계가 없으면 |
|---|---|---|---|
| **전진** (1→2→3) | 검수가 느슨해지고 자동화율이 오른다 | 지표가 **제안**하고 운영자가 승인한다 | **제안하지 않는다** |
| **후퇴** (3→2→1) | 검수가 빡빡해지고 사람이 는다 | **시스템이 내린다** | 기본값으로 **내린다** |

임계가 없을 때의 동작이 방향마다 다른 것이 요점이다. 전진 임계는 **실데이터 없이
정할 수 없어**(O8) 비어 있고, 비어 있으면 제안하지 않는다 — 지어낸 숫자로 검수를
느슨하게 하는 것이 이 저장소가 가장 하지 않으려는 일이다. 반대로 역행 임계에는
기본값이 있다: 비워 두면 **후퇴가 영영 일어나지 않는데**, 그것은 안전한 쪽의 침묵이
아니라 §0 이 꼽은 실패 방식("성숙했다고 착각한 채 자동화한다") 그대로다.

역행 임계에 기본값을 둘 수 있는 이유가 하나 더 있다. **그것은 절대 수준이 아니라
변화폭**이다 — "근거 확보율이 얼마여야 2국면인가"는 국면마다 다르지만, "지난달보다
20%p 떨어졌다"는 국면과 무관하게 이상 신호다.

## 표본이 적으면 판정하지 않는다

§1.3.1 이 밝혔듯 이 규모의 문의는 **일 단위로 소수**다. 창 안에 질문이 세 건이면
비율은 33%p 단위로 튀는데, 그 요동으로 국면을 올리고 내리면 **국면이 잡음을 따라
움직인다.** 그래서 분모가 `min_sample` 에 못 미치는 축은 값이 아니라 **없음**이고,
없는 축이 있으면 전진 제안도 역행 판정도 하지 않는다.

지표 화면이 "분모가 0 이면 0% 가 아니라 없음"이라고 한 것과 같은 규칙이되, 여기서는
그 판단이 **화면이 아니라 동작**으로 이어진다.

## 역행은 한 계단씩이고, 증거를 소비한다

역행 신호가 잡히면 한 계단 내린다. 두 계단을 한 번에 내리지 않는 것은 그 판단의
근거가 "얼마나 나빠졌는가"이지 "얼마나 깊이 되돌아가야 하는가"가 아니기 때문이다.

내린 뒤에는 **기준선이 그 시점으로 다시 잡힌다.** 그러지 않으면 한 번의 급락이
다음 주기에도 같은 기준선과 견줘져 계속 잡히고, 사건 하나로 3국면이 이틀 만에
1국면이 된다 — **같은 증거로 두 번 내리는 것**은 판정이 아니라 되풀이다.
"""

from __future__ import annotations

import sqlite3
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentic_service_desk.content import qna_stats
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.search import tokenize

COLD_START = 1
ACCUMULATION = 2
MATURE = 3

NAMES = {
    COLD_START: "1국면 — 콜드 스타트",
    ACCUMULATION: "2국면 — 축적",
    MATURE: "3국면 — 성숙",
}

BUILTIN_THRESHOLDS = Path(__file__).parent / "phase_thresholds.toml"

SEED = "seed"
"""국면이 처음 정해진 경로 — 설정값을 씨앗으로 썼다."""

OPERATOR = "operator"
SYSTEM = "system"


# --- 관측 -------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """축 하나의 관측값. **값이 없을 수 있고, 없는 이유가 값보다 중요하다.**"""

    metric: str
    value: float | None
    denominator: int = 0
    unavailable: str = ""

    @property
    def available(self) -> bool:
        return self.value is not None and not self.unavailable

    @property
    def percent(self) -> str:
        return "—" if self.value is None else f"{self.value * 100:.0f}%"


#: 관측하는 것. **세 축**(§1.3.3-a)과 그 축을 이루는 낱개, 그리고 역행 신호가 보는 것.
COVERAGE = "coverage"
"""커버리지 — 2단계(조회)에서 근거를 찾아 **초안까지 간 비율**. 세 축의 첫째다."""

EXPLICIT = "explicit_resolution"
"""명시적 해결률 — 정확도 축의 한쪽 (§6.3)."""

REJECTION = "rejection"
"""검수 반려율 — 정확도 축의 다른 쪽. **낮을수록 좋다**: 임계도 상한이다."""

REPETITION = "repetition"
"""반복성 — 반복 묶음에 속한 질문의 비율. **FAQ 재료가 생겼는가**를 묻는다."""

AGREEMENT = "agreement"
"""자동·사람 판정 일치율. **2→3 에만 붙는 넷째 축**이다 (§1.3.3-b)."""

NOVELTY = "novelty"
"""신규 유형 질문 비율 — 역행 신호. 이전 기간의 어느 묶음과도 닮지 않은 질문."""

STALE = "stale"
"""stale 비율 — 역행 신호. 대규모 코드 변경의 흔적이다."""

REPEAT_MIN = 2
"""반복으로 셀 최소 횟수. **두 번이면 되풀이고 한 번은 일화다.**

FAQ 승격 임계(O37)와 같은 숫자가 아니다 — 저쪽은 "무엇을 FAQ 한 문항으로 만들 것인가"의
하한이고, 여기는 "질문이 되풀이되기 시작했는가"를 보는 축이다. 같은 값으로 묶어 두면
FAQ 임계를 올리는 순간 반복성 축이 함께 내려앉는다.
"""

AXES = (COVERAGE, EXPLICIT, REJECTION, REPETITION, AGREEMENT)
METRICS = (*AXES, NOVELTY, STALE)


@dataclass(frozen=True)
class Observation:
    """한 시점의 관측. **추이를 보려면 남아 있어야 한다.**"""

    observed_on: str
    window_days: int
    readings: dict[str, Reading] = field(default_factory=dict)

    def get(self, metric: str) -> Reading:
        return self.readings.get(metric, Reading(metric, None, unavailable="관측되지 않았다"))


def observe(
    conn: sqlite3.Connection,
    *,
    repo: KnowledgeRepository | None = None,
    window_days: int = 30,
    min_sample: int = 10,
    now: datetime | None = None,
) -> Observation:
    """창 하나를 관측한다. **거르지 않고 세되, 적으면 없다고 말한다.**

    창을 두는 이유는 국면이 *지금* 무엇을 할 수 있는가이기 때문이다. 누적으로 재면
    반년 전의 콜드 스타트가 오늘의 커버리지를 계속 끌어내려, 실제로 나아진 시스템이
    영영 2국면에 닿지 못한다.
    """
    moment = now or datetime.now(UTC)
    since = (moment - timedelta(days=window_days)).isoformat()
    prior = (moment - timedelta(days=window_days * 2)).isoformat()

    readings = {
        COVERAGE: _coverage(conn, since, min_sample),
        EXPLICIT: _explicit(conn, since, min_sample),
        REJECTION: _rejection(conn, since, min_sample),
        AGREEMENT: _agreement(conn, since, min_sample),
        **_question_readings(conn, since=since, prior=prior, min_sample=min_sample),
        STALE: _stale(repo),
    }
    return Observation(
        observed_on=moment.date().isoformat(),
        window_days=window_days,
        readings=readings,
    )


def _ratio(numerator: int, denominator: int, *, min_sample: int, metric: str) -> Reading:
    """분모가 얇으면 **비율이 아니라 잡음**이다 (§1.3.1)."""
    if denominator < max(min_sample, 1):
        return Reading(
            metric,
            None,
            denominator=denominator,
            unavailable=f"표본이 얇다 ({denominator}건 < {min_sample}건) — "
            f"이 규모에서는 몇 건 차이로 비율이 통째로 튄다",
        )
    return Reading(metric, numerator / denominator, denominator=denominator)


def _coverage(conn: sqlite3.Connection, since: str, min_sample: int) -> Reading:
    """근거를 찾아 초안까지 간 비율.

    **초안의 존재로 잰다.** 근거가 0건이면 생성 단계에 가지 않으므로(FR-18) 초안이
    있다는 것이 곧 "지식이 닿았다"는 뜻이다 — 조회 단계의 적중 건수를 따로 세지
    않아도 되고, 세면 오히려 근거를 찾고도 답을 못 만든 건이 커버리지에 잡힌다.
    """
    total = _count(conn, "SELECT count(*) c FROM qna_item WHERE opened_at >= ?", (since,))
    answered = _count(
        conn,
        "SELECT count(DISTINCT i.id) c FROM qna_item i "
        "JOIN answer_draft d ON d.qna_item_id = i.id WHERE i.opened_at >= ?",
        (since,),
    )
    return _ratio(answered, total, min_sample=min_sample, metric=COVERAGE)


def _explicit(conn: sqlite3.Connection, since: str, min_sample: int) -> Reading:
    """닫힌 것 중 **명시적으로** 해결된 비율.

    분모가 "창 안에 열린 건"이 아니라 **"창 안에 닫힌 건"**이다. 해결 표시는 답변보다
    늦게 오므로 열린 기준으로 재면 최근 창이 언제나 낮게 나오고, 그러면 전진은 영영
    제안되지 않는다.
    """
    closed = _count(
        conn,
        "SELECT count(*) c FROM qna_item WHERE closed_at IS NOT NULL AND closed_at >= ?",
        (since,),
    )
    explicit = _count(
        conn,
        "SELECT count(*) c FROM qna_item WHERE closed_at IS NOT NULL AND closed_at >= ? "
        "AND resolution_grade = 'explicit'",
        (since,),
    )
    return _ratio(explicit, closed, min_sample=min_sample, metric=EXPLICIT)


def _rejection(conn: sqlite3.Connection, since: str, min_sample: int) -> Reading:
    """답변 검수의 반려율. **답변만 센다** — 콘텐츠 반려가 섞이면 뜻이 달라진다."""
    reviewed = _count(
        conn,
        "SELECT count(*) c FROM review WHERE kind = 'answer' AND reviewed_at >= ?",
        (since,),
    )
    rejected = _count(
        conn,
        "SELECT count(*) c FROM review WHERE kind = 'answer' AND outcome = 'rejected' "
        "AND reviewed_at >= ?",
        (since,),
    )
    return _ratio(rejected, reviewed, min_sample=min_sample, metric=REJECTION)


def _agreement(conn: sqlite3.Connection, since: str, min_sample: int) -> Reading:
    """에이전트와 사람이 같은 건을 같게 판정한 비율 (§1.3.3-b).

    **양쪽 판정이 다 있는 건만 분모다.** 사람이 아직 안 본 건을 넣으면 대기열이
    밀릴수록 일치율이 떨어지는데, 그것은 판정 품질과 무관하다.
    """
    rows = conn.execute(
        "SELECT qna_item_id, "
        "  max(CASE WHEN reviewed_by = 'agent' THEN outcome END) AS a, "
        "  max(CASE WHEN reviewed_by = 'human' THEN outcome END) AS h "
        "FROM review WHERE kind = 'answer' AND qna_item_id IS NOT NULL "
        "AND reviewed_at >= ? GROUP BY qna_item_id",
        (since,),
    ).fetchall()
    both = [r for r in rows if r["a"] and r["h"]]
    agreed = sum(1 for r in both if r["a"] == r["h"])
    return _ratio(agreed, len(both), min_sample=min_sample, metric=AGREEMENT)


def _question_readings(
    conn: sqlite3.Connection, *, since: str, prior: str, min_sample: int
) -> dict[str, Reading]:
    """반복성과 신규 유형 — **같은 묶기로 두 축을 낸다** (§7.2 의 셈을 그대로 쓴다).

    반복이 없다는 것과 전부 새롭다는 것은 같은 사실의 양면이라, 다른 방법으로 세면
    두 숫자가 서로 모순되는 말을 한다.
    """
    recent = qna_stats.load_questions(conn, since=since)
    earlier = [
        q
        for q in qna_stats.load_questions(conn, since=prior)
        if q.created_at < since
    ]

    repeated = sum(g.count for g in qna_stats.cluster(recent) if g.count >= REPEAT_MIN)
    repetition = _ratio(repeated, len(recent), min_sample=min_sample, metric=REPETITION)

    if not earlier:
        novelty = Reading(
            NOVELTY,
            None,
            denominator=len(recent),
            unavailable="견줄 이전 기간이 없다 — 처음 도는 창이다",
        )
    else:
        known = qna_stats.cluster(earlier)
        novel = sum(1 for q in recent if _is_novel(q, known))
        novelty = _ratio(novel, len(recent), min_sample=min_sample, metric=NOVELTY)
    return {REPETITION: repetition, NOVELTY: novelty}


def _is_novel(question: qna_stats.Question, known: list[qna_stats.RepeatQuestion]) -> bool:
    """이전 기간의 어느 묶음에도 닮지 않았는가.

    **묶는 조건과 같은 조건을 쓴다** (`qna_stats.belongs`). 다른 기준을 쓰면 같은
    질문이 반복성에서는 반복이고 신규 유형에서는 새것이 된다.
    """
    tokens = set(tokenize(question.text))
    if not tokens:
        return False
    return not any(qna_stats.belongs(tokens, g) for g in known)


def _stale(repo: KnowledgeRepository | None) -> Reading:
    """stale 비율. **표본 하한을 걸지 않는다** — 지식 항목은 질문과 달리 창이 없다."""
    if repo is None or not repo.root.exists():
        return Reading(STALE, None, unavailable="지식 저장소가 없다")
    stored, _ = repo.scan()
    if not stored:
        return Reading(STALE, None, unavailable="지식 항목이 없다")
    stale = sum(1 for s in stored if s.item.stale)
    return Reading(STALE, stale / len(stored), denominator=len(stored))


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()["c"]


# --- 임계 선언 (O8) ----------------------------------------------------------


class InvalidThresholds(ValueError):
    """임계 선언이 성립하지 않는다. **읽는 시점에 터진다** — 판정하다가 아니라."""


@dataclass(frozen=True)
class Advance:
    """전진 임계 하나. **비어 있는 축은 판정하지 않는다** (O8)."""

    coverage: float | None = None
    explicit_resolution: float | None = None
    rejection: float | None = None
    """**상한**이다 — 이 값보다 높으면 전진하지 않는다."""

    repetition: float | None = None
    agreement: float | None = None
    """2→3 에만 붙는 넷째 축 (§1.3.3-b). 1→2 선언에 적으면 거부한다."""

    @property
    def declared(self) -> bool:
        """전진을 판정할 수 있는가. **하나라도 비면 판정하지 않는다.**

        일부만 채우고 판정하면 채운 축만 보고 올라가는데, 세 축을 함께 보기로 한
        것(§1.3.3-a)이 바로 그 오판을 막으려는 결정이었다.
        """
        return all(
            v is not None
            for v in (self.coverage, self.explicit_resolution, self.rejection, self.repetition)
        )


@dataclass(frozen=True)
class Regression:
    """역행 임계 — **변화폭**이다. 절대 수준이 아니라서 국면에 종속되지 않는다."""

    coverage_drop: float = 0.2
    stale_rise: float = 0.2
    novelty_rise: float = 0.2
    rejection_rise: float = 0.2
    lookback_days: int = 14
    """기준선이 이만큼은 묵어야 견준다. **어제와 오늘을 견주면 잡음이 신호가 된다.**"""


@dataclass(frozen=True)
class Thresholds:
    advance: dict[int, Advance] = field(default_factory=dict)
    regression: Regression = Regression()

    def for_phase(self, target: int) -> Advance:
        return self.advance.get(target, Advance())


_ADVANCE_KEYS = {"coverage", "explicit_resolution", "rejection", "repetition", "agreement"}
_REGRESSION_KEYS = {
    "coverage_drop",
    "stale_rise",
    "novelty_rise",
    "rejection_rise",
    "lookback_days",
}


def load_thresholds(path: Path | None = None) -> Thresholds:
    """선언을 읽는다. **모르는 열쇠를 무시하지 않는다.**

    `coverge = 0.6` 은 오타지만, 무시하면 커버리지 임계가 **비어 있는 채로** 판정에
    들어간다 — 그러면 `declared` 가 거짓이라 제안이 조용히 사라지고, 운영자는 값을
    적었는데 아무 일도 일어나지 않는 것을 본다.
    """
    source = path or BUILTIN_THRESHOLDS
    if not source.exists():
        raise InvalidThresholds(f"임계 선언을 찾을 수 없다: {source}")
    raw = tomllib.loads(source.read_text(encoding="utf-8"))

    unknown = set(raw) - {"advance", "regression"}
    if unknown:
        raise InvalidThresholds(f"모르는 절: {', '.join(sorted(unknown))}")

    advance: dict[int, Advance] = {}
    for key, values in (raw.get("advance") or {}).items():
        if key not in {"2", "3"}:
            raise InvalidThresholds(f"전진 대상 국면은 2 또는 3 이다: {key}")
        extra = set(values) - _ADVANCE_KEYS
        if extra:
            raise InvalidThresholds(f"advance.{key}: 모르는 열쇠 {', '.join(sorted(extra))}")
        if key == "2" and "agreement" in values:
            raise InvalidThresholds(
                "advance.2: 일치율은 2→3 에만 붙는다 (§1.3.3-b) — "
                "1국면에는 자동 검수가 없어 견줄 판정이 없다"
            )
        advance[int(key)] = Advance(**values)

    regression_values = raw.get("regression") or {}
    extra = set(regression_values) - _REGRESSION_KEYS
    if extra:
        raise InvalidThresholds(f"regression: 모르는 열쇠 {', '.join(sorted(extra))}")
    return Thresholds(advance=advance, regression=Regression(**regression_values))


# --- 판정 -------------------------------------------------------------------


@dataclass(frozen=True)
class Judgment:
    """지금 무엇을 해야 하는가. **제안과 하강은 함께 나오지 않는다.**"""

    current: int
    proposal: int | None = None
    """전진 제안. **운영자 승인 전까지는 아무 일도 일어나지 않는다** (§1.3.3-c)."""

    regression: int | None = None
    """자동으로 내릴 국면. 나오면 그대로 내린다."""

    met: tuple[str, ...] = ()
    unmet: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()
    """잡힌 역행 신호. **하강하지 않아도 보인다** — 신호 하나는 경고이지 판정이 아니다."""

    undecidable: str = ""
    """판정할 수 없는 이유. **"조건 미달"과 "잴 수 없다"는 다르다.**"""


def judge(
    observation: Observation,
    baseline: Observation | None,
    *,
    current: int,
    thresholds: Thresholds,
) -> Judgment:
    """관측을 판정으로 바꾼다. **순수 함수다** — DB 도 시계도 보지 않는다.

    **역행을 먼저 본다.** 전진 조건과 역행 신호가 같은 창에서 함께 나올 수 있는데
    (커버리지는 올랐지만 stale 이 급등한 경우), 그때 올리는 것은 §1.3.3 이 정한
    비대칭을 뒤집는 일이다 — 강화는 지체하지 않는다.
    """
    signals = _regression_signals(observation, baseline, thresholds.regression)
    if signals:
        return Judgment(
            current=current,
            regression=max(COLD_START, current - 1) if current > COLD_START else None,
            signals=signals,
            undecidable=""
            if current > COLD_START
            else "이미 1국면이다 — 더 내려갈 곳이 없다",
        )

    if current >= MATURE:
        return Judgment(current=current, undecidable="3국면이 끝이다 — 올라갈 곳이 없다")

    target = current + 1
    rule = thresholds.for_phase(target)
    if not rule.declared:
        return Judgment(
            current=current,
            undecidable=f"{target}국면 전진 임계가 아직 정해지지 않았다 (O8) — "
            "실데이터 없이 정할 수 없고, 지어내 붙이면 검수가 근거 없이 느슨해진다",
        )

    met: list[str] = []
    unmet: list[str] = []
    missing: list[str] = []
    for metric, want, upper in (
        (COVERAGE, rule.coverage, False),
        (EXPLICIT, rule.explicit_resolution, False),
        (REJECTION, rule.rejection, True),
        (REPETITION, rule.repetition, False),
        *(((AGREEMENT, rule.agreement, False),) if target == MATURE else ()),
    ):
        if want is None:
            missing.append(f"{metric} 임계 미정")
            continue
        reading = observation.get(metric)
        if not reading.available:
            missing.append(f"{metric}: {reading.unavailable}")
            continue
        ok = reading.value <= want if upper else reading.value >= want
        (met if ok else unmet).append(
            f"{metric} {reading.percent} {'≤' if upper else '≥'} {want:.0%}"
        )

    if missing:
        return Judgment(
            current=current,
            met=tuple(met),
            unmet=tuple(unmet),
            undecidable="판정할 수 없는 축이 있다 — " + " · ".join(missing),
        )
    return Judgment(
        current=current,
        proposal=target if not unmet else None,
        met=tuple(met),
        unmet=tuple(unmet),
    )


def _regression_signals(
    observation: Observation, baseline: Observation | None, rule: Regression
) -> tuple[str, ...]:
    """§1.3.3-d 의 넷을 **변화폭으로** 센다.

    기준선이 없으면 신호도 없다 — 견줄 것이 없는데 잡아내면 그것은 감지가 아니라
    첫 관측에 대한 오판이다.
    """
    if baseline is None:
        return ()
    found = []
    for metric, delta, label in (
        (COVERAGE, -rule.coverage_drop, "근거 확보율 급락 — 모 시스템에 새 영역이 생겼는가"),
        (STALE, rule.stale_rise, "stale 비율 급등 — 대규모 코드 변경이 있었는가"),
        (NOVELTY, rule.novelty_rise, "신규 유형 질문 급등 — 이용자 구성이 바뀌었는가"),
        (REJECTION, rule.rejection_rise, "검수 반려율 급등 — 지식이 낡았거나 질문이 달라졌는가"),
    ):
        now_reading, was_reading = observation.get(metric), baseline.get(metric)
        if not (now_reading.available and was_reading.available):
            continue
        change = now_reading.value - was_reading.value
        if (change <= delta) if delta < 0 else (change >= delta):
            found.append(
                f"{label} ({was_reading.percent} → {now_reading.percent})"
            )
    return tuple(found)


# --- 상태와 이력 -------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """국면이 바뀐 사건 하나. **왜 바뀌었는지가 함께 남는다.**"""

    id: int
    from_phase: int | None
    to_phase: int
    decided_by: str
    reason: str
    decided_at: str

    @property
    def direction(self) -> str:
        if self.from_phase is None:
            return "씨앗"
        return "전진" if self.to_phase > self.from_phase else "후퇴"


class NotProposed(RuntimeError):
    """제안되지 않은 전진이다. **운영자 승인은 제안에 대한 승인**이다 (§1.3.3-c)."""


def current(conn: sqlite3.Connection, *, seed: int = COLD_START) -> int:
    """지금 국면. **없으면 설정을 씨앗으로 심고 그 사실을 남긴다.**

    조용히 기본값을 돌려주지 않는 이유는, 그러면 첫 전진의 `from_phase` 가 무엇이었는지
    이력에 없기 때문이다 — 국면이 바뀐 경위는 검수 강도가 바뀐 경위와 같은 것이라
    되짚을 수 있어야 한다.
    """
    row = conn.execute("SELECT phase FROM phase_state WHERE id = 1").fetchone()
    if row is not None:
        return int(row["phase"])
    target = seed if seed in NAMES else COLD_START
    _write(
        conn,
        from_phase=None,
        to_phase=target,
        decided_by=SEED,
        reason=f"설정 ASD_PHASE={seed} 을 씨앗으로 심었다. "
        "이 뒤로 국면의 SSOT 는 DB 다 — 설정을 고쳐도 따라가지 않는다",
    )
    return target


def advance(
    conn: sqlite3.Connection, *, to: int, judgment: Judgment, by: str = OPERATOR
) -> Decision:
    """전진 — **운영자가 승인한다** (§1.3.3-c).

    제안이 없는데 올리는 길은 두지 않는다. 화면의 버튼이 국면 다이얼이 되면
    "지표가 제안하고 운영자가 승인한다"가 "운영자가 정한다"가 되고, 그러면 검수를
    느슨하게 하는 결정에 근거가 남지 않는다.
    """
    if judgment.proposal is None or judgment.proposal != to:
        raise NotProposed(
            f"{to}국면 전진은 지금 제안되지 않았다 — "
            + (judgment.undecidable or "세 축이 아직 함께 오르지 않았다")
        )
    return _write(
        conn,
        from_phase=judgment.current,
        to_phase=to,
        decided_by=by,
        reason="세 축이 함께 올랐다 — " + " · ".join(judgment.met),
    )


def regress(conn: sqlite3.Connection, *, to: int, signals: tuple[str, ...]) -> Decision:
    """후퇴 — **시스템이 내린다.** 승인을 기다리지 않는다 (§1.3.3-c)."""
    return _write(
        conn,
        from_phase=current(conn),
        to_phase=to,
        decided_by=SYSTEM,
        reason="역행 신호: " + " · ".join(signals),
    )


def history(conn: sqlite3.Connection, *, limit: int = 10) -> list[Decision]:
    rows = conn.execute(
        "SELECT * FROM phase_decision ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        Decision(
            id=r["id"],
            from_phase=r["from_phase"],
            to_phase=r["to_phase"],
            decided_by=r["decided_by"],
            reason=r["reason"],
            decided_at=r["decided_at"],
        )
        for r in rows
    ]


def _write(
    conn: sqlite3.Connection,
    *,
    from_phase: int | None,
    to_phase: int,
    decided_by: str,
    reason: str,
) -> Decision:
    at = datetime.now(UTC).isoformat()
    cursor = conn.execute(
        "INSERT INTO phase_decision (from_phase, to_phase, decided_by, reason, decided_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (from_phase, to_phase, decided_by, reason, at),
    )
    conn.execute(
        "INSERT INTO phase_state (id, phase, since, decided_by) VALUES (1, ?, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET phase = excluded.phase, since = excluded.since, "
        "decided_by = excluded.decided_by",
        (to_phase, at, decided_by),
    )
    conn.commit()
    return Decision(
        id=int(cursor.lastrowid),
        from_phase=from_phase,
        to_phase=to_phase,
        decided_by=decided_by,
        reason=reason,
        decided_at=at,
    )


# --- 관측의 보관과 추이 -------------------------------------------------------


def save(conn: sqlite3.Connection, observation: Observation) -> None:
    """관측을 남긴다. **하루에 한 벌**이고 다시 돌면 덮는다.

    덮는 쪽을 고른 이유는 배치가 하루에도 여러 번 돌기 때문이다. 매번 쌓으면 추이의
    간격이 배치 주기에 따라 달라져 "지난달 대비"가 무엇을 견주는지 흐려진다.
    """
    for metric in METRICS:
        reading = observation.get(metric)
        conn.execute(
            "INSERT INTO phase_observation "
            "(observed_on, metric, value, denominator, unavailable, window_days, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (observed_on, metric) DO UPDATE SET "
            "value = excluded.value, denominator = excluded.denominator, "
            "unavailable = excluded.unavailable, window_days = excluded.window_days, "
            "observed_at = excluded.observed_at",
            (
                observation.observed_on,
                metric,
                reading.value,
                reading.denominator,
                reading.unavailable,
                observation.window_days,
                datetime.now(UTC).isoformat(),
            ),
        )
    conn.commit()


def trend(conn: sqlite3.Connection, *, limit: int = 6) -> list[Observation]:
    """최근 관측들. **최신이 앞이다.**"""
    dates = [
        r["observed_on"]
        for r in conn.execute(
            "SELECT DISTINCT observed_on FROM phase_observation "
            "ORDER BY observed_on DESC LIMIT ?",
            (limit,),
        ).fetchall()
    ]
    return [_load(conn, day) for day in dates]


def baseline(
    conn: sqlite3.Connection, *, before: str, lookback_days: int
) -> Observation | None:
    """견줄 기준선. **국면이 마지막으로 바뀐 뒤**의 관측 중 충분히 묵은 것 하나.

    결정 이후로 자르는 것이 요점이다 (§ 모듈 머리말). 그러지 않으면 한 번의 급락이
    다음 주기에도 같은 기준선과 견줘져 **같은 증거로 두 번 내린다.**
    """
    cutoff = (
        datetime.fromisoformat(before).date() - timedelta(days=lookback_days)
    ).isoformat()
    # **바닥은 국면이 실제로 바뀐 시점이다.** 씨앗은 바닥이 되지 않는다 — 아무것도
    # 바뀌지 않았으므로 소비할 증거도 없고, 그것을 바닥으로 삼으면 처음 심은 날부터
    # `lookback_days` 가 지나야 역행 감지가 켜진다.
    changed = conn.execute(
        "SELECT max(decided_at) AS at FROM phase_decision "
        "WHERE from_phase IS NOT NULL AND from_phase <> to_phase"
    ).fetchone()
    floor = (changed["at"] or "")[:10] if changed else ""
    row = conn.execute(
        "SELECT observed_on FROM phase_observation "
        "WHERE observed_on <= ? AND observed_on >= ? "
        "ORDER BY observed_on DESC LIMIT 1",
        (cutoff, floor),
    ).fetchone()
    return _load(conn, row["observed_on"]) if row else None


def latest(conn: sqlite3.Connection) -> Observation | None:
    row = conn.execute(
        "SELECT observed_on FROM phase_observation ORDER BY observed_on DESC LIMIT 1"
    ).fetchone()
    return _load(conn, row["observed_on"]) if row else None


def _load(conn: sqlite3.Connection, observed_on: str) -> Observation:
    rows = conn.execute(
        "SELECT * FROM phase_observation WHERE observed_on = ?", (observed_on,)
    ).fetchall()
    readings = {
        r["metric"]: Reading(
            metric=r["metric"],
            value=r["value"],
            denominator=r["denominator"] or 0,
            unavailable=r["unavailable"] or "",
        )
        for r in rows
    }
    window = next((r["window_days"] for r in rows), 0)
    return Observation(observed_on=observed_on, window_days=window, readings=readings)
