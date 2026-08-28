"""승격 경로 A — 티켓 종결 기록이 지식 항목이 된다 (FR-15, D40, §6.8.1).

**1국면에서는 티켓 해결의 승격이 지식 성장의 주 경로다** (§1.3). 종결 기록을 처음부터
지식 항목의 초안 형식으로 받아 뒀으므로(§6.5.1), 여기서 하는 일은 번역이 아니라
**옮겨 담는 것**이다.

## Q7 을 거치지 않는다

승격 경로는 셋인데 주체가 다르다 (§6.8.1).

| 경로 | 사람 손을 이미 거쳤는가 | Q7 |
|---|---|---|
| **A. 티켓 해결** | **그렇다** — 종결 시 사람이 무효화 조건을 채웠다 | **안 거친다** |
| B. 명시적 해결된 봇 답변 | 아니다 — 검수는 거쳤으나 지식으로서는 판정된 적이 없다 | 거친다 |
| C. 반복 질문 → FAQ | 그렇다 — 콘텐츠는 전수 사람 승인이다 | 안 거친다 |

> **A 에서는 사람이 무효화 조건을 채운 것이 곧 승격 승인이다.**

여기에 Q7 승인을 또 붙이면 **이중 승인**이 되고, 1인 겸업에게 그 중복이 곧 대기열
정체다 (§8.6). Q7 을 거치는 것은 B 하나뿐이다.

## 승격된 항목은 사람이 고친 것으로 표시한다

`edited_by_human` 을 켠다. 본문을 쓴 것은 에이전트지만 **가장 중요한 칸(무효화 조건)을
사람이 골랐고**, 그것이 §5.6.4 의 강제 입력 지점이 만들어 낸 판단이다.

표시하지 않으면 다음 ingest 가 이 항목을 자기 것으로 알고 **사람이 고른 무효화 조건을
자기 기본값으로 갈아 치운다** — 강제 입력으로 얻은 것이 조용히 사라진다. 표시해 두면
에이전트는 덮지 않고 모순으로 올리고(D38), 판정은 다시 사람이 한다.
"""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.operations import qna_state
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations import resolution as resolution_domain
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations.resolution import GroundKind, Resolution

#: 승격 대상 티켓 출처. **QnA 유래만이다** (§6.4.3).
#:
#: 모순 판정과 Lint 소견 처리도 종결 기록을 남기지만 그것은 **이미 있는 지식 중
#: 하나를 고르거나 정합성을 되돌리는 일**이지 새 지식을 만드는 일이 아니다. 걸러내지
#: 않으면 "판정: kept_human" 같은 것이 지식 항목이 된다.
PROMOTABLE_SOURCES = frozenset({ticket_domain.Source.QNA})


class NotPromotable(RuntimeError):
    """승격 자격이 없다."""


@dataclass(frozen=True)
class Promotion:
    """승격 결과."""

    ticket_id: str
    item: KnowledgeItem
    path: Path
    commit: str | None


TERMINAL_STATES = frozenset(
    {ticket_domain.State.CLOSED, ticket_domain.State.AUTO_CLOSED}
)
"""승격할 수 있는 티켓 상태.

**둘 다 받는다.** 경로 A 는 사람이 닫은 `closed` 에서 오고, 경로 B 는 파이프라인이
끝까지 처리한 `auto_closed` 에서 온다 (§6.4.3-1) — 무엇이 자격을 주는가는 경로마다
다르고, 그 판정은 여기가 아니라 각 경로가 한다.
"""


def promote(
    conn: sqlite3.Connection,
    repo: KnowledgeRepository,
    ticket_id: str,
    *,
    by: str = "human",
) -> Promotion:
    """종결된 티켓의 기록을 지식 항목으로 올린다.

    자격은 셋이다 — **닫혔고, QnA 유래이고, 무효화 조건이 채워졌다.** 셋을 여기서
    다시 확인하는 이유는 이 함수가 화면 밖에서도 불릴 수 있기 때문이다.

    `by` 는 **누가 올렸는가**다 (§6.8.4). 자동 승격분은 사람이 본 적이 없어 표본
    재검증의 우선순위가 높으므로, 그 안전망이 성립하려면 남아 있어야 한다.
    """
    t = ticket_domain.get(conn, ticket_id)
    if t.source not in PROMOTABLE_SOURCES:
        raise NotPromotable(
            f"출처가 {t.source} 다. 승격은 QnA 유래 티켓만 탄다 (§6.4.3) — "
            "모순 판정과 Lint 처리는 새 지식을 만드는 일이 아니다"
        )
    if t.state not in TERMINAL_STATES:
        raise NotPromotable(
            f"티켓이 아직 {t.state} 다. **종결된 것만** 승격한다 (자동 종결 포함)"
        )

    record = resolution_domain.get(conn, ticket_id)
    if record is None or not record.promotable:
        raise NotPromotable(
            "무효화 조건이 비어 있다 — 검증할 수 없는 지식을 들이는 것보다 "
            "공백으로 두는 편이 낫다 (FR-14)"
        )
    if record.promoted_item_id:
        raise NotPromotable(f"이미 승격됐다: {record.promoted_item_id}")
    if record.promotion_declined_at:
        raise NotPromotable("사람이 올리지 않기로 판정한 건이다 (Q7)")

    repo.ensure_initialized()
    item = to_knowledge_item(record, qna_item_id=t.qna_item_id)
    path = repo.save(item)
    repo.append_log(f"승격 — {item.title} (티켓 {ticket_id})")
    commit = repo.commit(f"promote: {item.title}")

    conn.execute(
        "UPDATE ticket_resolution SET promoted_item_id = ?, promoted_by = ? "
        "WHERE ticket_id = ?",
        (item.id, by, ticket_id),
    )
    conn.commit()
    return Promotion(ticket_id=ticket_id, item=item, path=path, commit=commit)


def to_knowledge_item(record: Resolution, *, qna_item_id: str | None) -> KnowledgeItem:
    """종결 기록을 지식 항목으로 옮겨 담는다.

    **다시 쓰지 않는다.** 일반화된 질문이 곧 개념의 이름이고 답이 곧 본문이다 —
    종결 기록을 초안 형식으로 받아 둔 것이 여기서 값을 한다(§6.5.1). 여기서 또
    문장을 만들면 사람이 승인한 것과 지식이 된 것이 달라진다.
    """
    return KnowledgeItem(
        title=record.generalized_question,
        body=_body(record),
        provenance=to_provenance(record, qna_item_id=qna_item_id),
        invalidation=record.invalidation,  # 사람이 고른 것이 그대로 간다
        edited_by_human=True,
    )


def _body(record: Resolution) -> str:
    """답이 본문이고, 선택 필드는 있으면 덧붙인다.

    원인·적용 범위·재발 가능성은 §6.5.2 의 선택 필드다 — 없는 것이 정상이므로
    빈 절을 만들지 않는다.
    """
    parts = [record.answer]
    for label, value in (
        ("원인", record.cause),
        ("적용 범위", record.scope),
        ("재발 가능성", record.recurrence),
    ):
        if value:
            parts.append(f"## {label}\n\n{value}")
    return "\n\n".join(parts)


def to_provenance(record: Resolution, *, qna_item_id: str | None) -> list[Provenance]:
    """근거를 출처로 옮긴다 (D3).

    **QnA 출처가 언제나 하나 붙는다.** 티켓에서 온 지식은 코드 근거가 있더라도
    커밋을 모르기 때문이다 — 담당자가 "이 파일이 그렇게 동작한다"고 말한 것이지
    우리가 그 커밋을 읽은 것이 아니다. 경로는 참고로 함께 남기되 **버전 고정은 하지
    않는다**: 모르는 것을 아는 척하면 Lint 의 참조 부재 검사가 헛돌게 된다.

    `person` 근거만 있으면 경로도 없다. 그런 지식은 코드에 묶이지 않으므로 stale
    자동 판정이 되지 않고, **무효화 조건이 유일한 장치**가 된다 (§6.5.3).
    """
    anchor = qna_item_id or "manual"
    paths = [
        g.ref
        for g in record.grounding
        if g.kind in (GroundKind.CODE, GroundKind.CONFIG) and g.ref
    ]
    commits = [g.ref for g in record.grounding if g.kind is GroundKind.COMMIT and g.ref]

    provenance = [Provenance(commit=c) for c in commits]
    provenance += [Provenance(qna=anchor, path=p) for p in paths]
    if not provenance:
        provenance = [Provenance(qna=anchor)]
    return provenance


def promote_if_eligible(
    conn: sqlite3.Connection, repo: KnowledgeRepository, ticket_id: str
) -> Promotion | None:
    """자격이 되면 올리고, 아니면 조용히 넘어간다.

    닫히는 티켓이 모두 승격 대상은 아니므로(모순·Lint) 호출부가 매번 출처를 따지지
    않아도 되게 한다. **자격 없음은 오류가 아니다.**
    """
    try:
        return promote(conn, repo, ticket_id)
    except NotPromotable:
        return None


# ─── 승격 경로 B — 명시적 해결된 봇 답변 (FR-33, D41·D42, §6.8.4) ──────────────
#
# **명시적 해결은 필요조건이지 충분조건이 아니다** (§6.8.2). 이용자의 해결 표시는
# *"나에게 유효했다"는 증언*이지 *"이것이 일반적으로 옳다"는 판정*이 아니다 —
# 우회책으로 문제가 풀렸다면 그것을 지식으로 굳히는 순간 **잘못된 처방이 표준이
# 되고**, 이후 같은 증상의 모든 질문이 그 우회책으로 답해진다.
#
# 그러나 전부 사람이 보면 성장이 멈춘다 (§6.8.3). Q7 은 방치해도 사고가 나지 않아
# **영원히 밀리는** 대기열이고, 승격이 밀리면 오답이 나가지는 않지만 지식이 자라지
# 않는다 — **사고 없이 정체하는 실패**다.
#
# 그래서 세 조건을 모두 만족하면 자동으로 넘긴다.


class Condition(enum.StrEnum):
    """자동 승격의 세 조건 (§6.8.4). **하나라도 어긋나면 Q7 로 간다.**"""

    CODE_LINKED = "근거가 소스코드에 직접 연결된다"
    """연결형 무효화 조건을 **자동으로 도출할 수 있다** — 사람이 채울 칸이 비지 않는다.

    이것이 §6.8.2 를 뒤집지 않는 이유이기도 하다: 코드에 묶인다는 것은 그 답이
    *이용자에게 유효했다*를 넘어 **코드가 실제로 그렇게 동작한다**는 뜻이고,
    §6.8.2 가 경계한 우회책은 코드에 묶이지 않는다. **자동으로 넘어가는 것은 판정이
    필요 없어서가 아니라 판정의 근거를 기계가 확인할 수 있어서다.**
    """

    CLEAN_REVIEW = "검수를 반려 없이 통과했다"
    """첫 시도에 통과했다는 것은 근거가 명확했다는 뜻이다."""

    EXPLICIT_RESOLUTION = "명시적 해결이다"
    """§5.3 의 배제가 풀린 상태다."""


AUTO_PROMOTION_PHASE = 2
"""자동 승격이 켜지는 국면 (§6.8.4-b).

**1국면에는 하지 않는다.** 지식베이스가 얇아 판단 기준 자체가 없고, 이 시기 승격의
주 경로는 어차피 A(티켓 해결)다. 검수 강도와 같은 패턴이다 — **자동화는 시간이
아니라 국면을 기준으로 올린다.**
"""


@dataclass(frozen=True)
class Candidate:
    """승격 후보 하나와 그 판정."""

    ticket_id: str
    qna_item_id: str | None
    record: Resolution
    missing: tuple[Condition, ...]
    derived: Invalidation | None
    """조건 1 이 충족될 때 코드에서 도출한 연결형 무효화 조건."""

    @property
    def eligible(self) -> bool:
        return not self.missing

    @property
    def reason(self) -> str:
        if self.eligible:
            return "세 조건을 모두 만족한다."
        return "못 채운 조건: " + " · ".join(str(c) for c in self.missing)


def derive_invalidation(record: Resolution) -> Invalidation | None:
    """근거의 코드 경로에서 연결형 무효화 조건을 **도출한다** (§6.8.4 조건 1).

    에이전트가 그럴듯한 기본값을 미리 채우는 것과 다르다 — 여기서 쓰는 것은 종결
    기록에 이미 적힌 **코드 경로 그 자체**이고, 그것이 바뀌면 이 지식이 틀려진다는
    것은 판단이 아니라 사실이다. 사람이 고를 것이 남아 있지 않으므로 §5.6.4 의
    강제 입력 지점이 지키려던 것이 이미 충족된 상태다.

    경로가 없으면 도출하지 않는다 — 커밋만 있는 근거는 *그때 그 커밋*을 가리킬 뿐
    **무엇이 바뀌면 틀려지는가**를 말해 주지 않는다.
    """
    paths = tuple(
        g.ref
        for g in record.grounding
        if g.kind in (GroundKind.CODE, GroundKind.CONFIG) and g.ref
    )
    if not paths:
        return None
    return Invalidation(kind=InvalidationKind.LINKED, refs=paths)


def assess(conn: sqlite3.Connection, ticket_id: str) -> Candidate | None:
    """이 종결 기록이 자동 승격 조건을 채우는가 (§6.8.4)."""
    t = ticket_domain.get(conn, ticket_id)
    record = resolution_domain.get(conn, ticket_id)
    if record is None:
        return None

    derived = derive_invalidation(record)
    missing: list[Condition] = []
    if derived is None:
        missing.append(Condition.CODE_LINKED)
    if not _clean_review(conn, t.qna_item_id):
        missing.append(Condition.CLEAN_REVIEW)
    if not _explicitly_resolved(conn, t.qna_item_id):
        missing.append(Condition.EXPLICIT_RESOLUTION)

    return Candidate(
        ticket_id=ticket_id,
        qna_item_id=t.qna_item_id,
        record=record,
        missing=tuple(missing),
        derived=derived,
    )


def _clean_review(conn: sqlite3.Connection, qna_item_id: str | None) -> bool:
    """반려 없이 통과했는가.

    **반려 이력이 하나라도 있으면 아니다** — 나중에 사람이 뒤집어 통과시켰더라도
    첫 시도에 통과하지 못했다는 사실은 남는다. 뒤집힌 건까지 자동으로 넘기려면
    3국면의 완화를 켜야 한다.
    """
    if not qna_item_id:
        return False
    row = conn.execute(
        "SELECT "
        "  sum(outcome = 'passed') AS passed, "
        "  sum(outcome = 'rejected') AS rejected "
        "FROM review WHERE qna_item_id = ?",
        (qna_item_id,),
    ).fetchone()
    return bool(row["passed"]) and not row["rejected"]


def _explicitly_resolved(conn: sqlite3.Connection, qna_item_id: str | None) -> bool:
    """명시적 해결인가. **출처가 둘이다** (§5.3.1-1, WBS-4.5.4)."""
    if not qna_item_id:
        return False
    row = conn.execute(
        "SELECT i.resolution_grade, r.grade FROM qna_item i "
        "LEFT JOIN raw_resolution r ON r.question_id = i.parent_question_id "
        "WHERE i.id = ?",
        (qna_item_id,),
    ).fetchone()
    if row is None:
        return False
    return qna_state.EXPLICIT in (row["resolution_grade"], row["grade"])


def awaiting_decision(conn: sqlite3.Connection) -> list[Candidate]:
    """Q7 — 아직 승격 판정을 받지 못한 자동 처리 건 (§8.2).

    **경로 A 는 여기 오지 않는다.** 사람이 무효화 조건을 채운 것이 곧 승격 승인이고,
    거기 Q7 을 또 붙이면 이중 승인이라 1인 겸업에게 대기열 정체가 된다 (§6.8.1).
    그래서 대상은 **자동 종결 티켓**뿐이다.
    """
    rows = conn.execute(
        "SELECT t.id FROM ticket t "
        "JOIN ticket_resolution r ON r.ticket_id = t.id "
        "WHERE t.state = ? AND t.source = ? "
        "  AND r.promoted_item_id IS NULL AND r.promotion_declined_at IS NULL "
        "ORDER BY t.opened_at",
        (str(ticket_domain.State.AUTO_CLOSED), str(ticket_domain.Source.QNA)),
    ).fetchall()
    return [c for c in (assess(conn, r["id"]) for r in rows) if c is not None]


@dataclass
class PromotionReport:
    promoted: list[Promotion] = field(default_factory=list)
    to_queue: list[Candidate] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.promoted or self.failures)


def run_auto(
    conn: sqlite3.Connection,
    repo: KnowledgeRepository,
    *,
    phase: int,
    relax_clean_review: bool = False,
) -> PromotionReport:
    """조건을 채운 건을 자동으로 올리고, 나머지는 Q7 에 남긴다 (FR-33).

    **1국면에는 아무것도 올리지 않는다** (§6.8.4-b) — 판단 기준 자체가 없고, 이
    시기 승격의 주 경로는 A 다. 그래도 후보 목록은 만든다: Q7 에 쌓이는 것이
    "무엇이 승격을 기다리고 있는가"이고, 그것을 보는 것이 국면을 올릴 판단 재료다.

    `relax_clean_review` 는 3국면에서만 뜻이 있는 완화다 (§6.8.4-b). **완화할 수
    있는 것은 조건 2 하나뿐이다**: 조건 1 은 §6.8.4-c 가 "판정을 대신한다"고 못
    박았으므로 풀면 §6.8.2 의 우려로 되돌아가고, 조건 3 은 §5.3 의 배제를 푸는
    조건이라 풀 수 없다.
    """
    report = PromotionReport()
    for candidate in awaiting_decision(conn):
        missing = set(candidate.missing)
        if relax_clean_review and phase >= 3:
            missing.discard(Condition.CLEAN_REVIEW)
        if phase < AUTO_PROMOTION_PHASE or missing:
            report.to_queue.append(candidate)
            continue
        try:
            resolution_domain.confirm(
                conn, candidate.ticket_id, invalidation=candidate.derived
            )
            report.promoted.append(
                promote(conn, repo, candidate.ticket_id, by="gate")
            )
        except (NotPromotable, RuntimeError) as exc:
            report.failures.append(f"{candidate.ticket_id}: {exc}")
    return report
