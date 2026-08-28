"""검수 — 파이프라인 4단계 (FR-20·21·22, §5.5).

**검수는 재생성이 아니라 대조 작업이다.**

## 생성과 분리한다

검수하는 에이전트가 생성한 에이전트와 같은 맥락을 공유하면 **자기 채점**이 된다 —
자기가 만든 추정을 "근거 있었다"고 볼 가능성이 높고, 그것은 §5.3 이 막으려 한 것과
같은 종류의 순환이다.

> **규칙**: 검수의 입력은 **답변 초안과 근거 원문뿐**이며, 질문의 의도나 생성 시의
> 추론은 주지 않는다 (FR-20).

의도를 알면 "그럴 만했다"로 기울고, 모르면 **글에 적힌 것만** 보게 된다. 같은 모델을
쓰더라도 이 분리만으로 판정의 성격이 달라진다.

> 이 분리에는 대가가 있다. **질문을 모르므로 이용자가 말한 수치를 되받은 문장과
> 지어낸 수치를 구분하지 못한다** — 둘 다 근거에 없기 때문이다. 그래서 P1 이
> 넉넉하게 걸린다. 반려는 버리는 것이 아니라 사람에게 보내는 것이므로(§5.5.3
> 1국면은 사람 검수가 기본) 이쪽으로 틀리는 편이 낫다.

## 다섯이 같은 종류의 판정이 아니다

§5.5.1 이 분해해 두었다. **기계적으로 잡히는 것을 모델에 묻지 않는다** — 물으면
느려지고, 무엇보다 확정 가능한 것을 확률적 판정에 맡기게 된다.

| # | 반려 사유 | 잡는 방법 |
|---|---|---|
| **P4** | stale 항목을 현재형으로 | **기계적** — 근거에 stale 이 있는가 |
| **P1** | 현재 값을 예시로 채움 | **준기계적** — 답변의 수치가 근거 원문에 있는가 |
| **P5** | 근거 범위를 넘겨 일반화 | 의미 판정 (적용 범위 필드가 없어 아직 기계로 못 잡는다) |
| **P2** | 예외 모르고 단정 | 의미 판정 |
| **P3** | 업계 통념을 사실처럼 | 의미 판정 |

**P1~P5 는 모두 문장이 자연스럽고 근거 목록도 붙어 있다.** 그래서 형식 검사로는
걸리지 않으며, 검수는 "근거가 있는가"가 아니라 **"이 문장이 그 근거로 뒷받침되는가"**
를 본다.
"""

from __future__ import annotations

import enum
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentic_service_desk.ingest.agent import AgentOutputError, Harness, extract_json
from agentic_service_desk.knowledge.search import _matches, tokenize
from agentic_service_desk.pipeline.answer import Draft, Hit

PASSED = "passed"
REJECTED = "rejected"


class Reject(enum.StrEnum):
    """반려 사유 (§5.4.3). **분류해 기록한다** (FR-22)."""

    P1 = "P1"
    """현재 값을 모르면서 예시로 채운다 — "보통 300만 원입니다"."""

    P2 = "P2"
    """규칙은 알지만 예외를 모르면서 단정한다 — "항상 그렇습니다"."""

    P3 = "P3"
    """다른 시스템의 일반 관행을 이 시스템의 사실처럼 말한다."""

    P4 = "P4"
    """낡은 지식 항목을 현재형으로 말한다."""

    P5 = "P5"
    """근거의 범위를 넘겨 일반화한다 — 한 모듈의 동작을 시스템 전체의 규칙처럼."""

    # --- 칼럼 전용 (§7.6.4) ---
    # **한 enum 에 둔다.** 반려 사유 분포가 한 표(`review`)에 쌓이므로 분류도 한
    # 곳이어야 한다 — 나누면 "P 로 시작하는 사유"가 두 벌이 되어 분포를 합쳐 읽을
    # 수 없다. 답변 경로는 이 셋을 쓰지 않는다: P1~P5 가 사실 진술을 전제하듯
    # P6~P8 은 **논평**을 전제한다.

    P6 = "P6"
    """근거로 환원되지 않는 **가치 판단** — "이 방식이 더 낫습니다"."""

    P7 = "P7"
    """관찰을 **일반 법칙으로 확대** — "N 건 있었다" → "다들 그렇게 씁니다"."""

    P8 = "P8"
    """조직의 **정책·방침처럼 읽히는 서술** — "앞으로는 ~하십시오".

    **문장 형태만으로 판정된다** (§7.6.4) — 명령형·당위 표현이 신호다. P6·P7 은
    의미 판정이 필요하다.
    """


DESCRIPTIONS: dict[Reject, str] = {
    Reject.P1: "현재 값을 모르면서 예시로 채웠다",
    Reject.P2: "예외를 모르면서 단정했다",
    Reject.P3: "업계 통념을 이 시스템의 사실처럼 말했다",
    Reject.P4: "낡은 지식을 현재형으로 말했다",
    Reject.P5: "근거의 범위를 넘겨 일반화했다",
    Reject.P6: "근거로 환원되지 않는 가치 판단이다",
    Reject.P7: "관찰을 일반 법칙으로 확대했다",
    Reject.P8: "조직의 정책·방침처럼 읽힌다",
}

ANSWER_REASONS: tuple[Reject, ...] = (Reject.P1, Reject.P2, Reject.P3, Reject.P4, Reject.P5)
"""답변에 쓰는 사유. **P6~P8 을 섞지 않는다** — 그 셋은 논평을 전제하므로
답변 검수자에게 내밀면 고를 수 없는 선택지가 화면에 는다."""

COLUMN_REASONS: tuple[Reject, ...] = (Reject.P6, Reject.P7, Reject.P8)
"""칼럼 전용 (§7.6.4). 콘텐츠 타입이 **선언으로** 고른다 (FR-42)."""


#: 수치가 들어간 토큰. **P1 의 준기계적 판정에 쓴다.**
#: 숫자를 고른 이유는 문자열 대조로 확정 가능한 부분이 그것이기 때문이다 —
#: 고유명사는 언어별 판정이 필요해 의미 판정 쪽으로 넘긴다 (§5.5.1).
_NUMERIC = re.compile(r"[0-9][0-9,._]*")


@dataclass(frozen=True)
class Verdict:
    """검수 결과."""

    passed: bool
    reason: Reject | None = None
    detail: str = ""
    checked_by: str = "agent"

    @property
    def outcome(self) -> str:
        return PASSED if self.passed else REJECTED


@dataclass(frozen=True)
class ReviewInput:
    """검수가 보는 것의 **전부** (FR-20).

    질문도, 분석 결과도, 생성 프롬프트도 여기 없다. 있는 것은 **글에 적힌 것과
    근거 원문**뿐이다.
    """

    draft_body: str
    grounding: tuple[str, ...]
    source_text: dict[str, str]
    stale_ids: frozenset[str] = frozenset()

    @classmethod
    def of(cls, draft: Draft, hits: list[Hit]) -> ReviewInput:
        """초안과 근거에서 만든다. **여기가 분리의 집행 지점이다** — 이 생성자가
        받는 것 밖의 무엇도 검수에 닿지 않는다."""
        return cls(
            draft_body=draft.body,
            grounding=draft.grounding,
            source_text={h.item.id: f"{h.item.title} {h.item.body}" for h in hits},
            stale_ids=frozenset(h.item.id for h in hits if h.is_stale),
        )


# --- 기계적 검사 -------------------------------------------------------------


def check_stale(review: ReviewInput) -> Verdict | None:
    """P4 — 근거에 낡은 항목이 있는가.

    **기계적이다.** 근거 버전 고정(D20)만 있으면 되며, 판단이 필요 없으므로 모델에
    묻지 않는다. 반려는 버리는 것이 아니라 사람에게 보내는 것이다 — §5.5.4 도 stale
    근거를 사람에게 올리는 위험 신호로 꼽는다.
    """
    stale = [g for g in review.grounding if g in review.stale_ids]
    if not stale:
        return None
    return Verdict(
        passed=False,
        reason=Reject.P4,
        detail=f"낡은 근거에 기댔다: {', '.join(stale)}",
    )


def check_ungrounded_numbers(review: ReviewInput) -> Verdict | None:
    """P1 — 답변의 수치가 근거 원문에 있는가.

    **준기계적이다.** 문자열 대조로 상당 부분 걸린다 (§5.5.1).

    질문을 모르므로 **이용자가 말한 수치를 되받은 것도 여기 걸린다.** 그것이 분리의
    대가이며, 근거에 없는 수치는 검수 입장에서 확인할 수 없는 수치라는 점에서
    판정 자체는 옳다.
    """
    source = " ".join(review.source_text.get(g, "") for g in review.grounding)
    source_numbers = set(_NUMERIC.findall(source))
    ungrounded = [
        n for n in _NUMERIC.findall(review.draft_body) if n not in source_numbers
    ]
    if not ungrounded:
        return None
    return Verdict(
        passed=False,
        reason=Reject.P1,
        detail=f"근거 원문에 없는 수치다: {', '.join(sorted(set(ungrounded)))}",
    )


MECHANICAL = (check_stale, check_ungrounded_numbers)


# --- 의미 판정 ---------------------------------------------------------------

_RULES = """아래 **초안**이 아래 **근거 원문**으로 뒷받침되는지 대조한다.

당신은 이 초안이 어떤 질문에 대한 것인지, 왜 이렇게 쓰였는지 **모른다.** 알 필요도
없다 — **글에 적힌 것만** 본다. "그럴 만했다"고 봐주지 않는다.

묻는 것은 "근거가 붙어 있는가"가 아니라 **"이 문장이 그 근거로 뒷받침되는가"** 다.
아래 셋 중 하나라도 해당하면 반려한다.

- **P2** — 규칙은 근거에 있지만 **예외를 모르면서 단정**했다. "항상", "무조건",
  "모든 경우에" 같은 단정이 근거에 그 범위까지 적혀 있지 않은 채로 붙은 경우다.
- **P3** — **다른 시스템의 일반 관행**을 이 시스템의 사실처럼 말했다. 근거에 없는데
  "보통 이렇게 동작합니다" 식으로 업계 통념을 서술한 경우다.
- **P5** — **근거의 범위를 넘겨 일반화**했다. 한 모듈·한 경우의 동작을 시스템 전체의
  규칙처럼 말한 경우다.

셋 다 **문장이 자연스럽고 근거 목록도 붙어 있다.** 자연스러움은 판단 근거가 아니다.
해당하지 않으면 통과시킨다 — 트집을 잡는 것이 목적이 아니다.

출력은 **JSON 하나만** 낸다.

{"passed": true}
{"passed": false, "reason": "P2", "detail": "무엇이 문제인지 한 문장"}"""


def build_prompt(review: ReviewInput) -> str:
    """검수 프롬프트. **질문이 여기 없다** (FR-20)."""
    sources = "\n\n".join(
        f"### {g}\n{review.source_text.get(g, '(원문 없음)')}" for g in review.grounding
    )
    return "\n".join(
        [_RULES, "", "초안:", review.draft_body, "", "근거 원문:", sources]
    )


def parse_verdict(text: str) -> Verdict:
    payload = extract_json(text)
    if payload.get("passed", False):
        return Verdict(passed=True)
    try:
        reason = Reject(str(payload.get("reason") or "").strip())
    except ValueError:
        # 반려인데 사유를 못 댔다. **사유 없는 반려는 기록으로 쓸 수 없으므로**
        # (§5.5.6) 가장 넓은 것으로 분류하고 상세를 남긴다.
        reason = Reject.P5
    return Verdict(
        passed=False, reason=reason, detail=str(payload.get("detail") or "").strip()
    )


class Reviewer:
    """초안을 근거와 대조한다."""

    def __init__(self, harness: Harness | None = None) -> None:
        self._harness = harness

    def review(self, review: ReviewInput) -> Verdict:
        """기계적인 것을 먼저 본다.

        **확정 가능한 것을 확률적 판정에 맡기지 않는다.** 그리고 기계가 이미 잡았으면
        모델을 부를 이유도 없다 — 반려는 하나면 충분하다.
        """
        for check in MECHANICAL:
            verdict = check(review)
            if verdict is not None:
                return verdict

        if self._harness is None:
            # 의미 판정을 할 수 없다. **통과시키지 않는다** — 검수가 없는 것과
            # 통과한 것은 다르며, 후자는 "검증했다"는 라벨을 남긴다 (§5.6.1).
            return Verdict(
                passed=False,
                reason=None,
                detail="의미 판정을 할 수 없다 — 사람이 본다",
                checked_by="none",
            )
        try:
            return parse_verdict(self._harness.run(build_prompt(review)).text)
        except (AgentOutputError, RuntimeError) as exc:
            return Verdict(
                passed=False, reason=None, detail=f"검수 실패: {exc}", checked_by="none"
            )


# --- 기록 -------------------------------------------------------------------


ANSWER = "answer"
CONTENT = "content"


def record(
    conn: sqlite3.Connection,
    *,
    review: ReviewInput,
    verdict: Verdict,
    qna_item_id: str | None = None,
    kind: str = ANSWER,
) -> str:
    """검수 결과를 남긴다 (FR-22, §5.5.6).

    **반려된 초안도 남긴다.** 버리면 왜 반려됐는지의 분포를 잃는다.

    `kind` 로 답변과 콘텐츠를 가른다. 섞으면 §5.5.6 의 반려율이 무엇을 뜻하는지가
    달라진다 — 그 숫자는 "사람이 에이전트의 *답변*을 얼마나 믿는가"이고, 콘텐츠는
    애초에 자동 게재 관문이 없어 **비교 대상이 아니다.**
    """
    review_id = f"rv-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO review "
        "(id, qna_item_id, kind, outcome, reason, detail, draft_body, grounding, "
        " reviewed_by, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            review_id,
            qna_item_id,
            kind,
            verdict.outcome,
            str(verdict.reason) if verdict.reason else None,
            verdict.detail,
            review.draft_body,
            json.dumps(list(review.grounding), ensure_ascii=False),
            verdict.checked_by,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    return review_id


@dataclass
class Distribution:
    """반려 사유별 분포 (FR-22 검증).

    **읽는 법이 정해져 있다** (§5.5.6, §8.3).
        - P1·P5 가 몰리면 **근거가 부족하다** → 지식 공백(Q8)으로 이어진다
        - 반려율이 0 에 수렴하는데 오답이 나오면 **검수가 형식적으로 흐르고 있다**
    """

    passed: int = 0
    rejected: int = 0
    by_reason: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.passed + self.rejected

    @property
    def rejection_rate(self) -> float:
        return (self.rejected / self.total) if self.total else 0.0

    @property
    def grounding_gap_share(self) -> float:
        """P1·P5 가 반려 중 차지하는 비율. **높으면 지식이 얇다는 신호다.**"""
        if not self.rejected:
            return 0.0
        gap = self.by_reason.get(str(Reject.P1), 0) + self.by_reason.get(str(Reject.P5), 0)
        return gap / self.rejected


def distribution(
    conn: sqlite3.Connection, *, reviewed_by: str | None = None, kind: str = ANSWER
) -> Distribution:
    """검수 분포. `reviewed_by` 로 **판정 주체를 가른다.**

    섞으면 읽을 수 없다. §8.3 이 "검수 반려율은 신뢰 지표"라고 한 것은 **사람이
    에이전트의 산출물을 얼마나 믿는가**를 뜻하므로 사람 판정만 세야 하고, 반대로
    2국면 자동 검수의 학습 자료가 되는 것은(§5.5.3) **사람 판정 기록**이다.
    에이전트 판정은 기계·의미 층이 무엇을 잡는지를 따로 보여 준다.
    """
    # **답변만 센다.** 콘텐츠 반려가 섞이면 이 비율이 두 가지를 한 숫자에 누른다 —
    # 콘텐츠는 애초에 자동 게재 관문이 없어 "믿는가"를 물을 대상이 아니다.
    query = "SELECT outcome, reason FROM review WHERE kind = ?"
    params: tuple = (kind,)
    if reviewed_by:
        query += " AND reviewed_by = ?"
        params += (reviewed_by,)

    out = Distribution()
    for row in conn.execute(query, params):
        if row["outcome"] == PASSED:
            out.passed += 1
            continue
        out.rejected += 1
        reason = row["reason"] or "미분류"
        out.by_reason[reason] = out.by_reason.get(reason, 0) + 1
    return out


def unmatched_terms(review: ReviewInput) -> list[str]:
    """초안의 낱말 중 근거 원문에 없는 것.

    검수의 판정에는 쓰지 않는다 — 바꿔 쓴 문장은 낱말이 다른 것이 정상이기 때문이다.
    **사람이 볼 때 어디를 먼저 볼지 알려주는 재료**로만 쓴다 (§5.6.5 와 같은 목적).
    """
    source: set[str] = set()
    for g in review.grounding:
        source |= set(tokenize(review.source_text.get(g, "")))
    return [t for t in dict.fromkeys(tokenize(review.draft_body)) if not _matches(t, source)]
