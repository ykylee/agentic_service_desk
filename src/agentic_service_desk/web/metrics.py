"""핵심 지표와 현황 (FR-47·50·58, NFR-9, §6.3, §8.3, §5.6.6).

**현황 숫자는 주인공이 아니다** (§8.1). 대기열이 주인공이고, 여기 있는 숫자는
**대기열이 왜 쌓였는지를 설명하는 보조 정보**다. 그래서 화면도 대기열과 나눠 둔다.

## 셀 수 없는 것을 세는 척하지 않는다

지표 중에는 지금 구조로 **잴 수 없는 것**이 있다 — 무수정 승인 비율은 초안을 고치는
기능이 없어 언제나 100% 로 나오고, 표본 재검증 일치율은 그 장치(WBS-4.8.4)가 아직
없다. 그런 값을 0 이나 100 으로 내보내면 **없는 신호가 있는 것처럼 읽힌다.**

> 빈 값과 "아직 만들지 않았다"는 다르다. 화면은 그 구분을 말해야 한다.

## 이 숫자들은 사람이 아니라 부하를 가리킨다 (NFR-9)

형식적 승인이 늘어나는 것은 대개 태만이 아니라 **대기열 과부하의 증상**이다. 개인
평가로 쓰면 운영자는 지표를 만족시키는 쪽으로 움직인다 — 일부러 시간을 끌거나
불필요하게 반려한다. **그러면 지표는 좋아지고 품질은 그대로다.**
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from agentic_service_desk.content import registry
from agentic_service_desk.content import store as content_store
from agentic_service_desk.operations import qna_state
from agentic_service_desk.pipeline import draft_store


@dataclass(frozen=True)
class Metric:
    """지표 하나. **값이 없을 수 있고, 없는 이유가 값보다 중요하다.**"""

    label: str
    value: float | None
    reading: str
    """이 숫자를 어떻게 읽는가. 숫자만 두면 **좋고 나쁨을 각자 짐작한다.**"""

    unavailable: str = ""
    """잴 수 없다면 그 이유. 비어 있으면 잴 수 있다는 뜻이다."""

    denominator: int = 0

    @property
    def available(self) -> bool:
        return not self.unavailable

    @property
    def percent(self) -> str:
        if self.value is None:
            return "—"
        return f"{self.value * 100:.0f}%"


def _ratio(numerator: int, denominator: int) -> float | None:
    """분모가 0 이면 **0 이 아니라 없음**이다.

    0% 로 내면 "한 건도 해결되지 않았다"로 읽히는데, 실제로는 아직 아무 일도 없었던
    것이다 — 1국면의 낮은 숫자를 고장으로 오판하는 것이 §0 이 꼽은 실패 방식이다.
    """
    return numerator / denominator if denominator else None


# --- FR-58 핵심 지표 여섯 -----------------------------------------------------


def core(conn: sqlite3.Connection) -> list[Metric]:
    """§6.3 · D36 이 꼽은 여섯. **하나로 뭉치면 실제 품질이 가려진다.**

    국면별 임계와 함께 봐야 하지만 **그 수치는 아직 정해지지 않았다**(O8) — 실데이터
    없이 정할 수 없다. 임계를 지어내 붙이면 1국면의 정상 상태가 빨간불이 된다.
    """
    total = _count(conn, "SELECT count(*) c FROM qna_item")
    grades = _grade_counts(conn)
    published = _count(
        conn,
        "SELECT count(DISTINCT qna_item_id) c FROM answer_record WHERE state = 'published'",
    )
    marked = _count(
        conn,
        "SELECT count(DISTINCT r.qna_item_id) c FROM answer_record r "
        "JOIN qna_item i ON i.id = r.qna_item_id "
        "WHERE r.state = 'published' AND i.resolution_grade = ?",
        (qna_state.EXPLICIT,),
    )
    answered = _count(
        conn, "SELECT count(DISTINCT qna_item_id) c FROM answer_draft"
    )
    # **답변 검수만 센다** (`kind`). 콘텐츠 반려가 섞이면 이 비율이 "사람이
    # 에이전트의 답변을 얼마나 믿는가"를 더는 뜻하지 않는다 — 콘텐츠는 애초에
    # 자동 게재 관문이 없어 비교 대상이 아니다.
    rejected = _count(
        conn,
        "SELECT count(*) c FROM review WHERE kind = 'answer' AND outcome = 'rejected'",
    )
    reviewed = _count(conn, "SELECT count(*) c FROM review WHERE kind = 'answer'")

    return [
        Metric(
            "명시적 해결률",
            _ratio(grades.get(qna_state.EXPLICIT, 0), total),
            "**진짜 품질 지표.** 이것이 올라가야 시스템이 나아진 것이다",
            denominator=total,
        ),
        Metric(
            "암묵적 해결 비율",
            _ratio(grades.get(qna_state.IMPLICIT, 0), total),
            "**경고 지표.** 단독으로는 좋고 나쁨을 말할 수 없다 — 명시적 해결률과 "
            "함께 읽는다. 둘이 같이 오르면 유입이 는 것이고, **암묵만 오르면 품질이 "
            "의심된다**",
            denominator=total,
        ),
        Metric(
            "미해결 종료율",
            _ratio(grades.get(qna_state.UNRESOLVED_CLOSED, 0), total),
            "지식 공백의 크기. Q8 이 그 목록이다",
            denominator=total,
        ),
        Metric(
            "해결 표시율",
            _ratio(marked, published),
            "**선행 지표다** — 해결률·커버리지보다 먼저 움직인다. 낮으면 명시적 해결이 "
            "적어 ingest 자격을 얻는 답변이 적고, **몇 주 뒤 지식 성장이 느려진다**",
            denominator=published,
        ),
        Metric(
            "커버리지",
            _ratio(answered, total),
            "지식베이스가 **답을 만들 수 있었던** 비율. 근거가 0건이면 초안이 만들어지지 "
            "않으므로(FR-18) 이 숫자가 곧 지식이 닿는 범위다",
            denominator=total,
        ),
        Metric(
            "검수 반려율",
            _ratio(rejected, reviewed),
            "품질 지표인 동시에 **신뢰 지표**다. 높으면 에이전트 산출물을 믿지 못하고 "
            "있다는 뜻이고, **0 에 수렴하는데 오답이 나오면 검수가 형식적으로 흐르고 "
            "있다는 뜻**이다 (§8.3)",
            denominator=reviewed,
        ),
    ]


# --- FR-47 현황 다섯 종 -------------------------------------------------------


@dataclass
class Status:
    """현황 화면 하나."""

    title: str
    question: str
    """이 숫자들이 답하는 물음. **없으면 숫자가 그냥 숫자로 남는다.**"""

    rows: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""


def qna_status(conn: sqlite3.Connection) -> Status:
    """QnA 처리 현황 (§8.3)."""
    total = _count(conn, "SELECT count(*) c FROM qna_item")
    manual = _count(
        conn, "SELECT count(*) c FROM qna_item WHERE origin = 'manual'"
    )
    published = _count(
        conn, "SELECT count(*) c FROM answer_record WHERE state = 'published'"
    )
    auto = _count(
        conn,
        "SELECT count(*) c FROM review WHERE kind = 'answer' AND reviewed_by = 'gate'",
    )
    followups = _count(conn, "SELECT count(*) c FROM raw_followup")
    corrected = _count(
        conn, "SELECT count(*) c FROM answer_record WHERE state = 'corrected'"
    )

    return Status(
        title="QnA 처리 현황",
        question="답변이 실제로 문제를 풀고 있는가, **몇 주 뒤 지식이 자랄 것인가**",
        rows=[
            ("추적 중인 QnA", f"{total}건"),
            (
                "수동 등록",
                f"{manual}건 — **W4(질문이 기록되지 않는다)의 유일한 간접 지표**다 "
                "(§1.4.6). 늘면 메신저 문의를 흡수하고 있다는 뜻이다",
            ),
            ("게재된 답변", f"{published}건"),
            (
                "자동 게재",
                f"{auto}건 — 사람을 거치지 않고 나갔다 (§8.6.3 의 D22 완화)",
            ),
            (
                "평균 후속 횟수",
                _fmt(_ratio(followups, total), "회") + " — 높으면 한 번에 풀지 못하고 있다",
            ),
            ("정정된 답변", f"{corrected}건 — 게재 후 진실이 바뀐 건이다 (W3)"),
        ],
    )


def agent_status(conn: sqlite3.Connection) -> Status:
    """에이전트 운영 현황 (§8.3, §5.6.6).

    **개인 평가가 아니라 부하 지표다** (NFR-9). 형식적 승인이 늘어나는 것은 대개
    태만이 아니라 대기열 과부하의 증상이며, 대응도 사람이 아니라 부하를 향한다 —
    국면 강도 조정 · 대기열 우선순위 · 자동화 범위 재조정.
    """
    by = {
        who: (
            _count(
                conn,
                "SELECT count(*) c FROM review "
                "WHERE kind = 'answer' AND reviewed_by = ? AND outcome = 'passed'",
                (who,),
            ),
            _count(
                conn,
                "SELECT count(*) c FROM review "
                "WHERE kind = 'answer' AND reviewed_by = ? AND outcome = 'rejected'",
                (who,),
            ),
        )
        for who in ("agent", "human", "gate")
    }
    seconds = _median_decision_seconds(conn)
    agreement = _agreement(conn)

    return Status(
        title="에이전트 운영 현황",
        question="파이프라인이 건강한가, 자동 검수를 믿어도 되는가, "
        "**사람 승인이 실질인가**",
        rows=[
            ("에이전트 검수", f"통과 {by['agent'][0]} · 반려 {by['agent'][1]}"),
            ("사람 검수", f"통과 {by['human'][0]} · 반려 {by['human'][1]}"),
            ("게재 판정 통과", f"{by['gate'][0]}건 — 사람을 거치지 않은 자동 게재다"),
            (
                "자동·사람 판정 일치율",
                _fmt(agreement, "")
                + " — 2국면에서 **자동을 믿어도 되는지**를 묻는다 (§1.3.3)",
            ),
            (
                "승인 소요 시간(중앙값)",
                (f"{seconds:.0f}초" if seconds is not None else "—")
                + " — **초안 길이에 비해 극단적으로 짧으면 읽지 않은 것**이다 (§5.6.6)",
            ),
            (
                "무수정 승인 비율",
                "**잴 수 없다** — 초안을 고치는 기능이 없어 언제나 100% 로 나온다. "
                "0 이나 100 을 내보내면 없는 신호가 있는 것처럼 읽힌다",
            ),
            (
                "표본 재검증 일치율",
                "**아직 없다** — 표본 재검증 장치가 WBS-4.8.4 다. "
                "빈 값과 아직 만들지 않은 것은 다르다",
            ),
        ],
        note="이 숫자들은 **개인 평가가 아니라 시스템 건강도**로 읽는다 (NFR-9). "
        "개인 평가로 쓰면 운영자는 지표를 만족시키는 쪽으로 움직이고 — 일부러 "
        "시간을 끌거나 불필요하게 반려한다 — **지표는 좋아지고 품질은 그대로다.**",
    )


def content_status(conn: sqlite3.Connection, reg: registry.Registry) -> Status:
    """콘텐츠 현황 (§8.3).

    **등록된 것과 만들어진 것은 다르다.** 타입 레지스트리가 섰다고 콘텐츠가
    생긴 것은 아니므로, 화면은 지금 무엇이 선언돼 있는지까지만 말하고 제작·게재
    수는 그것이 생긴 뒤에 센다 — 0 을 내면 "만들었는데 하나도 없다"로 읽힌다.
    """
    rows = [
        (
            t.title,
            f"{'살아있는 문서' if t.living else '발행물'} · "
            f"{'문서 면' if t.destination.place is registry.Place.DOCUMENT else '발행 면'}"
            f"({t.destination.place.operation}) · "
            f"검수 {'변경분' if t.review.scope is registry.Scope.DIFF else '전문'}"
            + (" + 발행 직전 최종 확인" if t.review.final_check else "")
            + (
                f" · 추가 반려 {'·'.join(t.review.extra_rejections)}"
                if t.review.extra_rejections
                else ""
            ),
        )
        for t in reg.all()
    ]
    rows.append(("검수 대기 초안", f"{len(content_store.pending(conn))}건 — Q3"))
    rows.append(
        (
            "마지막 제작",
            " · ".join(_last_runs(conn, reg)) or "**아직 한 번도 돌지 않았다**",
        )
    )
    rows.append(
        (
            "게재",
            "**아직 없다** — 문서 면 게재는 WBS-4.6.3 이다 (XR-6). **만든 것과 "
            "나간 것은 다르므로** 0 을 내지 않는다",
        )
    )
    return Status(
        title="콘텐츠 현황",
        question="어떤 콘텐츠가 돌고 있고 최신인가",
        rows=rows,
        note="검수자는 타입이 고르지 않는다 — 콘텐츠는 **국면과 무관하게 전수 사람 "
        "승인**이다 (FR-39). 타입이 고르는 것은 범위와 추가 반려 사유뿐이다.",
    )


def _last_runs(conn: sqlite3.Connection, reg: registry.Registry) -> list[str]:
    """타입별 마지막 주기. **아무것도 안 만든 주기도 말한다.**

    조용하면 돌지 않은 것과 구분되지 않는다 — "만들 것이 없었다"와 "배치가 죽었다"가
    화면에서 같아 보이면 후자를 아무도 알아채지 못한다.
    """
    out = []
    for t in reg.all():
        run = content_store.last_run(conn, t.id)
        if run is None:
            continue
        out.append(f"{t.title} {run.last_run_at[:10]} {run.outcome}")
    return out


def phase_status(conn: sqlite3.Connection, *, stage: str, phase: int) -> Status:
    """국면 상태 (§8.3, §1.3.3).

    **국면과 단계는 다른 축이다** (§1.5.3) — 단계는 *우리가 무엇을 켰는가*이고
    국면은 *지식베이스가 무엇을 할 수 있는가*다. 대응은 느슨하며 고정 매핑이 아니다.
    """
    names = {1: "1국면 — 콜드 스타트", 2: "2국면 — 축적", 3: "3국면 — 성숙"}
    return Status(
        title="국면 상태",
        question="지금 이 시스템이 어느 단계에 있는가",
        rows=[
            ("국면", f"{names.get(phase, phase)} — 검수 강도와 자동 승격 범위를 정한다"),
            ("단계", f"{stage} — 무엇을 켰는가. **국면과 다른 축이다**"),
            (
                "세 축 추이",
                "**아직 없다** — 커버리지·정확도·반복성의 추이와 전진 제안/역행 "
                "경고는 WBS-4.8.1 이다 (FR-49)",
            ),
            (
                "국면별 임계",
                "**아직 정해지지 않았다** (O8) — 실데이터 없이 정할 수 없다. "
                "지어내 붙이면 **1국면의 정상 상태가 빨간불이 된다**",
            ),
        ],
    )


# --- 셈 --------------------------------------------------------------------


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()["c"]


def _grade_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT COALESCE(resolution_grade, state) AS k, count(*) AS c "
        "FROM qna_item GROUP BY k"
    ).fetchall()
    return {r["k"]: r["c"] for r in rows}


def _median_decision_seconds(conn: sqlite3.Connection) -> float | None:
    """사람이 판정하는 데 걸린 시간의 중앙값.

    **평균이 아니라 중앙값이다.** 한 건을 며칠 뒤에 처리하면 평균이 통째로 끌려가
    "오래 본다"로 읽히는데, 그것은 부하의 신호이지 꼼꼼함의 신호가 아니다.
    """
    rows = conn.execute(
        "SELECT created_at, decided_at FROM answer_draft "
        "WHERE decided_at IS NOT NULL AND state IN (?, ?)",
        (draft_store.APPROVED, draft_store.REJECTED),
    ).fetchall()
    spans = []
    for row in rows:
        try:
            from datetime import datetime

            start = datetime.fromisoformat(row["created_at"])
            end = datetime.fromisoformat(row["decided_at"])
        except (TypeError, ValueError):
            continue
        spans.append((end - start).total_seconds())
    if not spans:
        return None
    spans.sort()
    mid = len(spans) // 2
    return spans[mid] if len(spans) % 2 else (spans[mid - 1] + spans[mid]) / 2


def _agreement(conn: sqlite3.Connection) -> float | None:
    """에이전트와 사람이 같은 건을 같게 판정한 비율 (§1.3.3).

    **양쪽 판정이 다 있는 건만 분모다.** 사람이 아직 안 본 건을 분모에 넣으면
    대기열이 밀릴수록 일치율이 떨어지는데, 그것은 판정 품질과 무관하다.
    """
    rows = conn.execute(
        "SELECT qna_item_id, "
        "  max(CASE WHEN reviewed_by = 'agent' THEN outcome END) AS a, "
        "  max(CASE WHEN reviewed_by = 'human' THEN outcome END) AS h "
        "FROM review WHERE kind = 'answer' AND qna_item_id IS NOT NULL "
        "GROUP BY qna_item_id"
    ).fetchall()
    both = [r for r in rows if r["a"] and r["h"]]
    if not both:
        return None
    return sum(1 for r in both if r["a"] == r["h"]) / len(both)


def _fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    return f"{value:.0%}" if not unit else f"{value:.1f}{unit}"
