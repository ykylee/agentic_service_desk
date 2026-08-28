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

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agentic_service_desk.knowledge.item import KnowledgeItem, Provenance
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


def promote(
    conn: sqlite3.Connection, repo: KnowledgeRepository, ticket_id: str
) -> Promotion:
    """종결된 티켓의 기록을 지식 항목으로 올린다.

    자격은 셋이다 — **닫혔고, QnA 유래이고, 무효화 조건이 채워졌다.** 셋을 여기서
    다시 확인하는 이유는 이 함수가 화면 밖에서도 불릴 수 있기 때문이다.
    """
    t = ticket_domain.get(conn, ticket_id)
    if t.source not in PROMOTABLE_SOURCES:
        raise NotPromotable(
            f"출처가 {t.source} 다. 승격 경로 A 는 QnA 유래 티켓만 탄다 (§6.4.3) — "
            "모순 판정과 Lint 처리는 새 지식을 만드는 일이 아니다"
        )
    if t.state is not ticket_domain.State.CLOSED:
        raise NotPromotable(f"티켓이 아직 {t.state} 다. 종결된 것만 승격한다")

    record = resolution_domain.get(conn, ticket_id)
    if record is None or not record.promotable:
        raise NotPromotable(
            "무효화 조건이 비어 있다 — 검증할 수 없는 지식을 들이는 것보다 "
            "공백으로 두는 편이 낫다 (FR-14)"
        )
    if record.promoted_item_id:
        raise NotPromotable(f"이미 승격됐다: {record.promoted_item_id}")

    repo.ensure_initialized()
    item = to_knowledge_item(record, qna_item_id=t.qna_item_id)
    path = repo.save(item)
    repo.append_log(f"승격 — {item.title} (티켓 {ticket_id})")
    commit = repo.commit(f"promote: {item.title}")

    conn.execute(
        "UPDATE ticket_resolution SET promoted_item_id = ? WHERE ticket_id = ?",
        (item.id, ticket_id),
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
