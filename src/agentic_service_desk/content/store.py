"""콘텐츠 초안과 제작 이력의 보관 (WBS-4.6.2).

**초안과 진행 표시를 나눠 둔다.** 초안은 판정받는 *물건*이고 제작 이력은 *사건*이다 —
`answer_draft` 와 `review` 를 나눈 것과 같은 이유이며, 여기서는 그 구분이 특히
중요하다: **돌았는데 아무것도 만들지 않는 주기가 정상**이기 때문이다.

> **진행 표시를 "내용이 바뀌었는가"에 걸지 않는다.** 걸면 바뀔 것이 없는 타입이 매
> 주기 LLM 에 다시 실린다 — ingest 에서 이미 밟은 실패이고, 라이브에서 실제로 봤다.
> 표시를 멈추는 경우는 **실패했을 때뿐**이다.
"""

from __future__ import annotations

import enum
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


class Outcome(enum.StrEnum):
    """한 주기가 무엇을 했는가. **아무것도 안 만든 것도 결과다.**"""

    PRODUCED = "produced"
    """초안을 만들어 Q3 에 올렸다."""

    UNCHANGED = "unchanged"
    """돌렸는데 직전 판본과 같았다. **초안을 만들지 않는다** — 바뀐 것이 없는데
    검수를 요청하면 대기열이 빈 판정으로 채워진다."""

    HELD = "held"
    """근거가 아직 낡아 기다린다. **지식이 먼저 따라잡아야 한다** (§6.6.3)."""

    NO_GROUNDING = "no_grounding"
    """쓸 근거가 없다. 지어내지 않는다 (D3, FR-18)."""

    PENDING_REVIEW = "pending_review"
    """앞 초안이 아직 Q3 에 있다. **다시 만들지 않는다** — 한 타입이 대기열을 채우면
    다른 타입이 밀린다 (§6.2 가 답변에서 정한 것과 같다)."""

    NOT_DUE = "not_due"
    """트리거가 아직 오지 않았다."""

    GENERATION_FAILED = "generation_failed"


@dataclass(frozen=True)
class ContentDraft:
    """검수를 기다리는 콘텐츠 하나."""

    id: str
    type_id: str
    title: str
    body: str
    grounding: tuple[str, ...]
    based_on: str | None = None
    """직전 판본. **있어야 diff 검수가 성립한다** (§5.5.5)."""

    ticket_id: str | None = None
    """Q3 대기열의 자리 (§6.4.3). **Q3 는 작업 대기열이다** (FR-45) — 초안 하나가
    처리 하나이고, 티켓이 그 기록 단위다."""

    state: str = PENDING
    generated_by: str = ""
    created_at: str = ""


def _from_row(row: sqlite3.Row) -> ContentDraft:
    return ContentDraft(
        id=row["id"],
        type_id=row["type_id"],
        title=row["title"],
        body=row["body"],
        grounding=tuple(json.loads(row["grounding"] or "[]")),
        based_on=row["based_on"],
        ticket_id=row["ticket_id"],
        state=row["state"],
        generated_by=row["generated_by"] or "",
        created_at=row["created_at"],
    )


def save(
    conn: sqlite3.Connection,
    *,
    type_id: str,
    title: str,
    body: str,
    grounding: tuple[str, ...],
    based_on: str | None = None,
    generated_by: str = "",
    ticket_id: str | None = None,
) -> str:
    draft_id = f"cd-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO content_draft "
        "(id, type_id, title, body, grounding, based_on, ticket_id, state, "
        " generated_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            draft_id,
            type_id,
            title,
            body,
            json.dumps(list(grounding), ensure_ascii=False),
            based_on,
            ticket_id,
            PENDING,
            generated_by,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    return draft_id


def get(conn: sqlite3.Connection, draft_id: str) -> ContentDraft | None:
    row = conn.execute(
        "SELECT * FROM content_draft WHERE id = ?", (draft_id,)
    ).fetchone()
    return _from_row(row) if row else None


def by_ticket(conn: sqlite3.Connection, ticket_id: str) -> ContentDraft | None:
    """티켓에서 초안으로. **Q3 화면이 티켓 id 로 열리기 때문이다** — 순위(`next_up`)가
    가리키는 것이 티켓이므로, 그 자리에서 바로 열려야 한다."""
    row = conn.execute(
        "SELECT * FROM content_draft WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()
    return _from_row(row) if row else None


def pending(conn: sqlite3.Connection, type_id: str | None = None) -> list[ContentDraft]:
    """Q3 대기열. **오래된 것이 먼저다.**"""
    sql = "SELECT * FROM content_draft WHERE state = ?"
    params: tuple = (PENDING,)
    if type_id is not None:
        sql += " AND type_id = ?"
        params += (type_id,)
    return [_from_row(r) for r in conn.execute(sql + " ORDER BY created_at", params)]


def approved(conn: sqlite3.Connection) -> list[ContentDraft]:
    """승인된 초안 전부. **오래된 것이 먼저다** — 게재도 순서가 있다."""
    return [
        _from_row(r)
        for r in conn.execute(
            "SELECT * FROM content_draft WHERE state = ? ORDER BY created_at",
            (APPROVED,),
        )
    ]


def current(conn: sqlite3.Connection, type_id: str) -> ContentDraft | None:
    """지금 유효한 판본 — **승인된 것 중 가장 최근**.

    살아있는 문서에서 이것이 다음 갱신의 입력이 된다 (§7.3). 승인되지 않은 초안을
    입력으로 삼으면 **사람이 보지 않은 글 위에 다음 글이 쌓인다.**
    """
    row = conn.execute(
        "SELECT * FROM content_draft WHERE type_id = ? AND state = ? "
        "ORDER BY decided_at DESC, created_at DESC LIMIT 1",
        (type_id, APPROVED),
    ).fetchone()
    return _from_row(row) if row else None


def decide(conn: sqlite3.Connection, draft_id: str, *, approved: bool) -> None:
    conn.execute(
        "UPDATE content_draft SET state = ?, decided_at = ? WHERE id = ? AND state = ?",
        (
            APPROVED if approved else REJECTED,
            datetime.now(UTC).isoformat(),
            draft_id,
            PENDING,
        ),
    )
    conn.commit()


# --- 제작 이력 ---------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """한 타입의 마지막 주기."""

    type_id: str
    last_run_at: str
    last_generated_at: str | None
    """모델을 **실제로 돌린** 마지막 시각. 주기 트리거가 보는 것은 이쪽이다."""

    last_commit: str | None
    outcome: Outcome
    detail: str


def last_run(conn: sqlite3.Connection, type_id: str) -> Run | None:
    row = conn.execute(
        "SELECT * FROM content_run WHERE type_id = ?", (type_id,)
    ).fetchone()
    if row is None:
        return None
    return Run(
        type_id=row["type_id"],
        last_run_at=row["last_run_at"],
        last_generated_at=row["last_generated_at"],
        last_commit=row["last_commit"],
        outcome=Outcome(row["outcome"]),
        detail=row["detail"] or "",
    )


def record_run(
    conn: sqlite3.Connection,
    *,
    type_id: str,
    outcome: Outcome,
    generated: bool,
    commit: str | None = None,
    detail: str = "",
) -> None:
    """**돈 것은 바뀌지 않았어도 돈 것이다.**

    다만 **본 것과 만든 것은 다르다.** 주기 시계(`last_generated_at`)와 소스 커서는
    모델을 실제로 돌렸을 때만 앞으로 간다 — 기다리기로 한 주기가 시계를 밀면 근거가
    낡아 한 번 미룬 타입이 **꼬박 한 주기를 더 기다리고**, 커서를 먼저 옮기면 그
    코드 변경이 **아무것도 만들지 않은 채 소비된다.**

    `NOT_DUE` 는 남기지 않는다 — 돌지 않은 것이고, 남기면 화면이 매 tick "방금 봤다"고
    말해 실제로 무슨 일이 있었는지가 지워진다.
    """
    if outcome is Outcome.NOT_DUE:
        return
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO content_run "
        "(type_id, last_run_at, last_generated_at, last_commit, outcome, detail) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(type_id) DO UPDATE SET "
        "  last_run_at = excluded.last_run_at, "
        "  last_generated_at = COALESCE(excluded.last_generated_at, content_run.last_generated_at), "
        "  last_commit = COALESCE(excluded.last_commit, content_run.last_commit), "
        "  outcome = excluded.outcome, detail = excluded.detail",
        (
            type_id,
            now,
            now if generated else None,
            commit if generated else None,
            str(outcome),
            detail,
        ),
    )
    conn.commit()
