"""배치 진행 지점 (ADR-005 · ADR-006).

배치는 **중단 가능해야 하고**(온라인에 양보한다), 중단돼도 다음 주기에 이어서 해야
한다. 어디까지 했는지를 여기 남긴다.

증분의 단위가 커밋이므로(ADR-006) 소스 커서는 **커밋 해시**다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

SOURCE = "source"
"""소스 저장소 커서 — 마지막으로 ingest 한 커밋 해시."""

QNA = "qna"
"""QnA 커서 — 마지막으로 수집한 시각."""


def get_cursor(conn: sqlite3.Connection, kind: str) -> str | None:
    """마지막 처리 지점. **없으면 None** — 최초 1회 전체 수집의 신호다 (FR-2)."""
    row = conn.execute(
        "SELECT cursor FROM ingest_checkpoint WHERE kind = ?", (kind,)
    ).fetchone()
    return row["cursor"] if row else None


def set_cursor(conn: sqlite3.Connection, kind: str, cursor: str) -> None:
    """처리 지점을 옮긴다.

    **ingest 가 실제로 끝난 뒤에만** 부른다. 먼저 옮기면 중단됐을 때 그 구간을
    건너뛰고, 그러면 지식에 구멍이 생기는데 **아무도 알아채지 못한다.**
    """
    conn.execute(
        "INSERT INTO ingest_checkpoint (kind, cursor, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(kind) DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at",
        (kind, cursor, datetime.now(UTC).isoformat()),
    )
    conn.commit()
