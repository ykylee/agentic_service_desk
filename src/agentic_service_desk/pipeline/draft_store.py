"""검수를 기다리는 답변 초안 (Q2, §8.2).

파이프라인이 초안을 만들고 에이전트가 1차 검수를 마치면 그 결과가 여기 남아 사람의
판정을 기다린다. **1국면에는 사람 검수가 기본**이므로(§5.5.3) 이 대기열이 곧 답변이
나가는 속도의 상한이다.

> **Q2 는 판정 대기열이지 작업 대기열이 아니다** (§6.4.4). 조사·수정이 필요한 일이
> 아니라 **보고 누르면 끝나는** 일이므로 상태 기계도, 진행 중 표시도 없다. 화면이
> 달라야 하는 이유가 그것이다 (FR-45).

## 무엇을 담는가

진술을 통째로 담는다. 본문만 남기면 근거 강도 표시(§5.6.5)가 사라지고, 그러면 사람이
**어디를 먼저 볼지** 알 수 없어 화면을 눈으로 훑어 넘기게 된다 — 그 습관화를 깨는
것이 그 표시의 목적이었다.

질문 원문도 담는다. **사람 검수자는 그것을 본다.** FR-20 의 분리는 *에이전트* 검수가
자기 채점이 되는 것을 막는 규칙이고(§5.5.2), 사람은 무엇을 물었는지 모르면 근거에
충실하되 엉뚱한 답을 통과시킨다. 에이전트가 못 본 것을 사람은 본다는 이 비대칭은
의도된 것이다.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_service_desk.pipeline.answer import Confidence, Draft, Statement
from agentic_service_desk.pipeline.review import (
    PASSED,
    Reject,
    ReviewInput,
    Verdict,
    record,
)

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


@dataclass(frozen=True)
class PendingDraft:
    """검수를 기다리는 초안 하나."""

    id: str
    qna_item_id: str | None
    question: str
    statements: tuple[Statement, ...]
    grounding: tuple[str, ...]
    unanswered: tuple[str, ...]
    agent_outcome: str | None
    agent_reason: str | None
    agent_detail: str
    generated_by: str
    """생성 시점의 모델 (§6.6.1 필드 5). 게재 시점이 아니다 — 초안이 큐에 머무는
    동안 설정이 바뀌면 모델 교체 추적이 어긋난다."""

    state: str
    created_at: str

    @property
    def body(self) -> str:
        return "\n\n".join(s.text for s in self.statements)

    @property
    def weak_points(self) -> tuple[Statement, ...]:
        """사람이 볼 곳 (§5.6.5). **매번 다른 자리가 표시된다.**"""
        return tuple(s for s in self.statements if s.confidence.needs_review)

    @property
    def agent_rejected(self) -> bool:
        return self.agent_outcome is not None and self.agent_outcome != PASSED

    @property
    def look_here_first(self) -> str:
        """**무엇부터 보라고 말해 준다.**

        1인 겸업이 대기열을 훑을 때 판단을 줄여 주는 것이 화면의 일이다 —
        여덟을 나란히 두면 매번 전부 훑게 된다는 §8.6.3 과 같은 이유다.
        """
        if self.agent_rejected:
            return f"에이전트가 {self.agent_reason} 로 반려했다 — 그 지점을 먼저 본다."
        if self.weak_points:
            return f"근거가 약한 진술 {len(self.weak_points)}개를 먼저 본다."
        return "에이전트 검수를 통과했고 약한 지점도 없다 — 근거 대조만 확인한다."


def save(
    conn: sqlite3.Connection,
    *,
    question: str,
    draft: Draft,
    verdict: Verdict | None = None,
    qna_item_id: str | None = None,
    generated_by: str = "",
) -> str:
    """초안을 검수 대기열에 올린다.

    **에이전트가 반려한 것도 올린다.** 버리면 사람이 뒤집을 기회가 사라지고, 무엇보다
    자동 판정이 틀렸을 때 그것을 알 길이 없어진다 — 1국면 사람 판정 기록이 2국면 자동
    검수의 기준이 된다는 §5.5.3 이 성립하려면 양쪽이 다 남아야 한다.
    """
    draft_id = f"ad-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO answer_draft "
        "(id, qna_item_id, question, statements, grounding, unanswered, "
        " agent_outcome, agent_reason, agent_detail, generated_by, state, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            draft_id,
            qna_item_id,
            question,
            json.dumps(
                [
                    {
                        "text": s.text,
                        "confidence": str(s.confidence),
                        "grounding": list(s.grounding),
                    }
                    for s in draft.statements
                ],
                ensure_ascii=False,
            ),
            json.dumps(list(draft.grounding), ensure_ascii=False),
            json.dumps(list(draft.unanswered), ensure_ascii=False),
            verdict.outcome if verdict else None,
            str(verdict.reason) if verdict and verdict.reason else None,
            verdict.detail if verdict else "",
            generated_by,
            PENDING,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    return draft_id


def pending(conn: sqlite3.Connection) -> list[PendingDraft]:
    """Q2 대기열. **오래된 것이 먼저다** — 사람이 기다리고 있다 (§8.2)."""
    rows = conn.execute(
        "SELECT * FROM answer_draft WHERE state = ? ORDER BY created_at", (PENDING,)
    ).fetchall()
    return [_from_row(r) for r in rows]


def get(conn: sqlite3.Connection, draft_id: str) -> PendingDraft | None:
    row = conn.execute(
        "SELECT * FROM answer_draft WHERE id = ?", (draft_id,)
    ).fetchone()
    return _from_row(row) if row else None


def decide(
    conn: sqlite3.Connection,
    draft_id: str,
    *,
    approved: bool,
    reason: Reject | None = None,
    detail: str = "",
    source_text: dict[str, str] | None = None,
) -> None:
    """사람이 판정한다. **판정 사건을 검수 기록에 남긴다** (FR-22).

    반려 사유가 없으면 분포를 읽을 수 없으므로(§5.5.6), 반려에는 사유를 요구한다 —
    그 요구가 호출부에 있다.
    """
    draft = get(conn, draft_id)
    if draft is None or draft.state != PENDING:
        return

    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE answer_draft SET state = ?, decided_at = ? WHERE id = ?",
        (APPROVED if approved else REJECTED, now, draft_id),
    )
    conn.commit()
    record(
        conn,
        review=ReviewInput(
            draft_body=draft.body,
            grounding=draft.grounding,
            source_text=source_text or {},
        ),
        verdict=Verdict(
            passed=approved, reason=reason, detail=detail, checked_by="human"
        ),
        qna_item_id=draft.qna_item_id,
    )


def _from_row(row: sqlite3.Row) -> PendingDraft:
    return PendingDraft(
        id=row["id"],
        qna_item_id=row["qna_item_id"],
        question=row["question"],
        statements=tuple(
            Statement(
                text=s["text"],
                confidence=Confidence(s["confidence"]),
                grounding=tuple(s.get("grounding") or ()),
            )
            for s in json.loads(row["statements"])
        ),
        grounding=tuple(json.loads(row["grounding"])),
        unanswered=tuple(json.loads(row["unanswered"])),
        agent_outcome=row["agent_outcome"],
        agent_reason=row["agent_reason"],
        agent_detail=row["agent_detail"] or "",
        generated_by=row["generated_by"] or "",
        state=row["state"],
        created_at=row["created_at"],
    )
