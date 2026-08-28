"""모순 기록 — 에이전트가 진 쪽의 주장을 남긴다 (FR-6, D38).

사람이 고친 항목과 에이전트의 새 판단이 어긋나면 **덮어쓰지 않는다.** 그런데
덮어쓰지 않는 것과 **없던 일로 하는 것은 다르다.** 에이전트의 판단을 버리면 사람은
자기 글이 무엇과 어긋났는지 볼 수 없고, 그러면 판정(Q4) 자체가 성립하지 않는다.

llm-wiki 의 additive merge 를 따른다 — **양쪽을 남기고 표시한다.** 사람 쪽은 지식
파일에 그대로 있고, 에이전트 쪽이 여기 남는다.

> **왜 지식 파일이 아니라 운영 DB 인가.** 대립 주장을 파일 본문에 넣으면 **다음
> ingest 가 그것을 본문으로 읽는다.** 원천이 아닌 것이 원천처럼 되돌아오는 경로를
> 만들지 않는다 — 산출물 필터(NFR-4)를 세운 것과 같은 이유다.

모순은 **판정이 아니라 작업**이다 (§6.4.4) — 상세와 상태 전이가 필요하므로 티켓을
발행한다. `ticket.source = "contradiction"` 이 그 자리이며, 티켓 출처가 QnA 만이
아니라는 것(§6.4.1)이 여기서 실제로 쓰인다.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_service_desk.knowledge.item import Provenance

SOURCE = "contradiction"
"""`ticket.source` 값. Q4 대기열이 이것으로 걸린다."""

OPEN = "open"
RESOLVED = "resolved"


@dataclass(frozen=True)
class Contradiction:
    """열려 있는 모순 하나."""

    id: str
    knowledge_item_id: str
    ticket_id: str | None
    proposed_title: str
    proposed_body: str
    provenance: list[Provenance]
    detected_at: str
    state: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def record(
    conn: sqlite3.Connection,
    *,
    knowledge_item_id: str,
    proposed_title: str,
    proposed_body: str,
    provenance: list[Provenance],
) -> Contradiction:
    """모순을 남기고 **티켓을 함께 발행한다.**

    같은 항목에 이미 열린 모순이 있으면 **새로 만들지 않고 그것을 돌려준다.**
    ingest 는 주기마다 도는데 사람이 판정하기 전까지 같은 충돌이 계속 나온다 —
    주기마다 티켓을 찍으면 대기열이 같은 항목으로 메워져 **1인 겸업이 소화할 수
    없게 된다**(§8.6). 우선순위를 매길 수 없는 대기열은 없는 것과 같다.
    """
    existing = open_for(conn, knowledge_item_id)
    if existing:
        return existing

    now = _now()
    contradiction_id = f"c-{uuid.uuid4().hex[:12]}"
    ticket_id = f"t-{uuid.uuid4().hex[:12]}"

    conn.execute(
        "INSERT INTO ticket (id, source, qna_item_id, state, opened_at) VALUES (?, ?, ?, ?, ?)",
        # `qna_item_id` 가 비는 것이 정상이다 — 이 티켓은 QnA 에서 오지 않았다 (§6.4.1).
        (ticket_id, SOURCE, None, "open", now),
    )
    conn.execute(
        "INSERT INTO contradiction "
        "(id, knowledge_item_id, ticket_id, proposed_title, proposed_body, provenance, "
        " detected_at, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            contradiction_id,
            knowledge_item_id,
            ticket_id,
            proposed_title,
            proposed_body,
            json.dumps([_to_dict(p) for p in provenance], ensure_ascii=False),
            now,
            OPEN,
        ),
    )
    conn.commit()
    return Contradiction(
        id=contradiction_id,
        knowledge_item_id=knowledge_item_id,
        ticket_id=ticket_id,
        proposed_title=proposed_title,
        proposed_body=proposed_body,
        provenance=provenance,
        detected_at=now,
        state=OPEN,
    )


def open_for(conn: sqlite3.Connection, knowledge_item_id: str) -> Contradiction | None:
    row = conn.execute(
        "SELECT * FROM contradiction WHERE knowledge_item_id = ? AND state = ? "
        "ORDER BY detected_at LIMIT 1",
        (knowledge_item_id, OPEN),
    ).fetchone()
    return _from_row(row) if row else None


def list_open(conn: sqlite3.Connection) -> list[Contradiction]:
    """Q4 대기열. **방치 비용이 높다** — 모순된 지식이 계속 답변에 쓰인다 (§8.2)."""
    rows = conn.execute(
        "SELECT * FROM contradiction WHERE state = ? ORDER BY detected_at", (OPEN,)
    ).fetchall()
    return [_from_row(r) for r in rows]


def resolve(conn: sqlite3.Connection, contradiction_id: str, *, resolution: str) -> None:
    """사람이 판정했다. 티켓도 함께 닫는다.

    `resolution` 은 `kept_human` · `took_agent` · `merged` 중 하나다. 어느 쪽을 골랐는지
    남기는 이유는, **에이전트가 자주 이기면 사람의 편집이 틀리고 있다는 뜻**이고 그
    반대면 ingest 프롬프트가 틀리고 있다는 뜻이기 때문이다. 분포가 곧 지표다.
    """
    now = _now()
    cur = conn.execute(
        "UPDATE contradiction SET state = ?, resolution = ?, resolved_at = ? "
        "WHERE id = ? AND state = ?",
        (RESOLVED, resolution, now, contradiction_id, OPEN),
    )
    if cur.rowcount:
        conn.execute(
            "UPDATE ticket SET state = 'closed', closed_at = ? "
            "WHERE id = (SELECT ticket_id FROM contradiction WHERE id = ?)",
            (now, contradiction_id),
        )
    conn.commit()


def _to_dict(p: Provenance) -> dict:
    return {k: v for k, v in (("commit", p.commit), ("path", p.path), ("qna", p.qna)) if v}


def _from_row(row: sqlite3.Row) -> Contradiction:
    return Contradiction(
        id=row["id"],
        knowledge_item_id=row["knowledge_item_id"],
        ticket_id=row["ticket_id"],
        proposed_title=row["proposed_title"],
        proposed_body=row["proposed_body"],
        provenance=[Provenance(**d) for d in json.loads(row["provenance"])],
        detected_at=row["detected_at"],
        state=row["state"],
    )
