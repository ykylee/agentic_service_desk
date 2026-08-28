"""게재 판정 — 무엇을 자동으로 내보내고 무엇을 사람에게 올리는가 (FR-25·57, D31).

검수는 필수이지만 **누가 어느 강도로 보는지는 국면에 따라 다르다**(§5.1, §5.5.3).
여기가 그 갈림길이다.

## 왜 1국면에도 자동 게재가 있는가

D22 는 1국면에 전수 사람 검수를 요구했다. 그런데 §8.6.3 이 그것과 정면으로 부딪힌다 —
**1인 겸업이 이틀에 한 번 들어오면 답변도 이틀 뒤에 나간다.** 사내에서는 담당자에게
직접 물으면 되므로(W4) 그 지연은 곧 채택 실패다. 그래서 D31 이 D22 를 완화했다:
근거가 충분하고 위험 신호가 없는 건은 1국면에도 자동으로 내보낸다.

**1국면에 지식베이스가 얇다는 점이 완충이 된다** — 근거가 충분한 건 자체가 드물어
자동 게재 대상이 적다. 완화가 오답 노출을 크게 늘리지 않는 이유다.

## 1국면과 2국면의 규칙이 같은 것은 실수가 아니다

§5.5.3 은 1국면을 "사람 검수 기본 + 고신뢰 건 자동", 2국면을 "에이전트 1차 + 위험
신호만 사람"이라고 다르게 적었지만, **집행 형태로 옮기면 같은 문장이 된다.**
실제 차이는 규칙이 아니라 **신호가 국면에 따라 저절로 꺼지는 데서 온다** —
특히 `NOVEL`(새로운 유형)은 1국면에 거의 모든 질문이 해당하므로 자동 게재가 드물고,
지식이 쌓이면 저절로 는다.

여기에 국면별 임계를 하나 더 얹으면 **그 자연스러운 변화 위에 인위적 계단**을
포개는 것이 된다. 실데이터 없이 정할 수 없는 숫자를 늘리기만 한다(O8).

**3국면만 규칙이 다르다.** 기준이 안정된 뒤 사람은 감사자로 물러나므로, 신호 선별이
아니라 **표본**으로 본다.

## 게재가 없는 단계에는 판정도 없다

D31 은 **S3 부터** 적용된다(§1.5.3). S0~S2 는 아무것도 내보내지 않으므로 자동 게재도
없다 — 두 결정이 부딪히는 것이 아니라 적용 시점이 다르다.
"""

from __future__ import annotations

import enum
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.search import Hit
from agentic_service_desk.pipeline.answer import Analysis, Draft
from agentic_service_desk.pipeline.review import Verdict, check_ungrounded_numbers
from agentic_service_desk.pipeline.review import ReviewInput

PUBLISHING_STAGES = frozenset({"S3", "S4", "S5"})
"""게재가 켜진 단계 (§1.5.3). S0~S2 는 **아무것도 내보내지 않는다.**"""

EXPIRY_WARNING_RATIO = 0.8
"""주기형 무효화 조건의 만료 임박 기준 — 주기의 80% 가 지났으면 임박으로 본다.

실데이터 없이 정할 수 없는 숫자라(O8) 넉넉한 쪽으로 잡았다. 임박을 좁게 잡으면
**검증되지 않은 지식이 신호 없이 나간다**(§6.5.3).
"""


class Signal(enum.StrEnum):
    """사람에게 올리는 위험 신호 (§5.5.4). **일곱이 서로 다른 것을 잡는다.**"""

    THIN_GROUNDING = "근거가 1개 이하"
    """교차 확인이 불가능하다."""

    STALE_GROUNDING = "근거에 stale 표시가 있다"
    """P4 직결. 검수가 이미 반려했어야 하는 건이다."""

    EXPIRING = "주기형 근거의 만료가 임박했다"
    """검증되지 않은 지식일 수 있다 (§6.5.3)."""

    UNGROUNDED_FACTS = "근거에 없는 수치·기간·고유명사가 있다"
    """P1 직결."""

    NOVEL = "새로운 유형의 질문이다"
    """1단계 분석이 이미 유사도를 판정하므로 **추가 비용이 없다.**

    1국면에는 거의 모든 질문이 여기 걸린다 — 그것이 §8.6.3 이 말한 "1국면에 자동
    게재 대상이 적다"의 실체이며, 지식이 쌓이면 저절로 꺼진다.
    """

    DIVERGENT = "유사 질문의 과거 답변과 근거가 다르다"
    """모순 신호. **답변 이력(§6.6)이 여기서 다시 값을 한다** — 근거 버전 고정이
    없으면 "그때 무엇에 기대어 답했는지"를 알 수 없어 비교 자체가 불가능하다."""

    UNREVIEWED = "에이전트 검수를 통과하지 못했다"
    """반려됐거나 아예 판정을 받지 못했다.

    §5.5.4 의 7번은 "판정이 **경계선**"인데, 지금 `Verdict` 에는 확신도가 없어 그것을
    표현할 수 없다. **판정이 없는 것을 경계선보다 안전한 쪽으로 다룬다** — 검수가
    없는 것과 통과한 것은 다르고, 후자로 취급하면 "검증했다"는 라벨이 남는다(§5.6.1).
    """


class Route(enum.StrEnum):
    """이 초안이 갈 곳."""

    AUTO = "자동 게재"
    HUMAN = "사람 검수"
    NOT_PUBLISHING = "게재하지 않는 단계"


@dataclass(frozen=True)
class Assessment:
    """판정 하나. **왜 그렇게 갈렸는지를 함께 든다.**

    이유를 남기지 않으면 자동 게재율이 왜 오르내리는지 나중에 설명할 수 없고,
    그러면 국면을 올려도 되는지 판단할 근거가 사라진다 (§1.3.3).
    """

    route: Route
    signals: tuple[Signal, ...] = ()
    sampled: bool = False
    """3국면에서 표본으로 뽑혀 사람에게 갔는가. **신호 때문이 아니라는 뜻이다.**"""

    @property
    def auto(self) -> bool:
        return self.route is Route.AUTO

    @property
    def reason(self) -> str:
        if self.route is Route.NOT_PUBLISHING:
            return "게재가 켜지지 않은 단계다 (S3 부터 나간다)."
        if self.sampled:
            return "표본으로 뽑혔다 — 신호가 있어서가 아니다 (3국면 감사)."
        if self.signals:
            return "위험 신호: " + " · ".join(str(s) for s in self.signals)
        return "위험 신호가 없다."


def assess(
    conn: sqlite3.Connection,
    *,
    draft: Draft,
    hits: list[Hit],
    analysis: Analysis | None,
    verdict: Verdict | None,
    repo: KnowledgeRepository,
    stage: str,
    phase: int,
    draft_key: str,
    sample_rate: float = 0.2,
    now: datetime | None = None,
) -> Assessment:
    """이 초안을 자동으로 내보내도 되는가.

    **판정에 모델을 부르지 않는다.** 일곱 신호가 전부 셀 수 있는 것이기 때문이고,
    확정 가능한 것을 확률적 판정에 맡기지 않는 것은 검수(§5.5.1)에서 이미 택한
    방식이다. 여기서 모델을 부르면 자동 게재 여부가 **매번 조금씩 달라진다.**
    """
    if stage not in PUBLISHING_STAGES:
        return Assessment(route=Route.NOT_PUBLISHING)

    found = detect(
        conn,
        draft=draft,
        hits=hits,
        analysis=analysis,
        verdict=verdict,
        repo=repo,
        now=now,
    )

    if phase >= 3:
        # 기준이 안정됐다 — 사람은 감사자로 물러난다. **반려는 여전히 사람에게 간다.**
        if Signal.UNREVIEWED in found:
            return Assessment(route=Route.HUMAN, signals=found)
        if _sampled(draft_key, sample_rate):
            return Assessment(route=Route.HUMAN, signals=found, sampled=True)
        return Assessment(route=Route.AUTO, signals=found)

    return Assessment(
        route=Route.HUMAN if found else Route.AUTO, signals=found
    )


def detect(
    conn: sqlite3.Connection,
    *,
    draft: Draft,
    hits: list[Hit],
    analysis: Analysis | None,
    verdict: Verdict | None,
    repo: KnowledgeRepository,
    now: datetime | None = None,
) -> tuple[Signal, ...]:
    """일곱을 센다 (§5.5.4). 국면과 무관하다 — **무엇이 위험한가는 국면이 바꾸지 않는다.**"""
    found: list[Signal] = []
    by_id = {h.item.id: h for h in hits}

    if len(draft.grounding) <= 1:
        found.append(Signal.THIN_GROUNDING)

    if any(by_id[g].is_stale for g in draft.grounding if g in by_id):
        found.append(Signal.STALE_GROUNDING)

    if any(_expiring(repo, by_id[g], now) for g in draft.grounding if g in by_id):
        found.append(Signal.EXPIRING)

    review = ReviewInput.of(draft, hits)
    if check_ungrounded_numbers(review) is not None:
        found.append(Signal.UNGROUNDED_FACTS)

    if analysis is not None and not analysis.similar_questions:
        found.append(Signal.NOVEL)

    if _divergent(conn, draft, analysis):
        found.append(Signal.DIVERGENT)

    if verdict is None or not verdict.passed:
        found.append(Signal.UNREVIEWED)

    return tuple(found)


def _expiring(repo: KnowledgeRepository, hit: Hit, now: datetime | None) -> bool:
    """주기형 무효화 조건의 만료가 임박했는가 (§6.5.3).

    **주기형인 것 자체가 신호는 아니다.** 코드에 묶을 대상이 없어 주기로 대신하는
    항목이고, 그것이 아직 유효한 동안에는 다른 항목과 다를 바 없다. 이미 지났으면
    Lint 가 stale 로 표시하므로 `STALE_GROUNDING` 이 잡는다 — 여기는 **그 전**을 본다.

    마지막 확인 시각은 Lint 와 같은 것을 쓴다(`last_commit_date`). 지식이 git 위에
    살기 때문에 저장소가 그 답을 이미 알고 있고, 항목에 필드를 따로 두면 두 곳이
    어긋난다.
    """
    period = hit.item.invalidation.period_days
    if period is None:
        return False
    checked = repo.last_commit_date(hit.path)
    if not checked:
        # 커밋된 적이 없다 — **언제 확인됐는지 모른다.** 모르는 것은 안전한 쪽으로 다룬다.
        return True
    try:
        since = ((now or datetime.now(UTC)) - datetime.fromisoformat(checked)).days
    except ValueError:
        return True
    return since >= period * EXPIRY_WARNING_RATIO


def _divergent(
    conn: sqlite3.Connection, draft: Draft, analysis: Analysis | None
) -> bool:
    """유사 질문에 이미 나간 답변과 **근거 구성이 다른가**.

    본문을 의미로 비교하지 않는다 — 그러면 모델을 불러야 하고 판정이 흔들린다.
    **같은 물음에 다른 근거로 답했다면 그 자체가 확인할 거리**이며, 그것은 답변
    이력의 근거 기록(§6.6)으로 셀 수 있다.
    """
    if analysis is None or not analysis.similar_questions:
        return False
    # `similar_questions` 는 **질문 원문**이지 id 가 아니다 (1단계가 그렇게 준다).
    placeholders = ",".join("?" * len(analysis.similar_questions))
    rows = conn.execute(
        "SELECT g.answer_record_id, g.knowledge_item_id FROM answer_grounding g "
        "JOIN answer_record r ON r.id = g.answer_record_id AND r.state = 'published' "
        "JOIN qna_item i ON i.id = r.qna_item_id "
        "JOIN raw_question q ON q.id = i.parent_question_id "
        f"WHERE q.body IN ({placeholders})",  # noqa: S608 — 자리표시자 개수만 만든다
        tuple(analysis.similar_questions),
    ).fetchall()
    if not rows:
        return False
    past: dict[str, set[str]] = {}
    for row in rows:
        past.setdefault(row["answer_record_id"], set()).add(row["knowledge_item_id"])
    now_set = set(draft.grounding)
    return all(items != now_set for items in past.values())


def _sampled(draft_key: str, rate: float) -> bool:
    """표본에 뽑혔는가. **초안 id 로 결정적으로 정한다.**

    난수를 쓰면 같은 초안을 다시 판정할 때 결과가 달라져, 재실행이 답변의 운명을
    바꾼다 — 그러면 "왜 이건 사람에게 갔는가"에 답할 수 없다.
    """
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(draft_key.encode("utf-8")).digest()
    return (int.from_bytes(digest[:4], "big") % 10_000) < rate * 10_000
