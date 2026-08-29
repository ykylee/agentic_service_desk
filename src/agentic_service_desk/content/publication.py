"""콘텐츠 게재 — 5단계 (XR-6, WBS-4.6.3, §7.7).

승인된 콘텐츠가 **모 시스템의 문서 면 또는 발행 면으로 나가는** 자리다. 이 시스템이
모 시스템에 가하는 쓰기는 §13 이 한정했고, 그중 콘텐츠 게재가 이것이다.

## 출구는 하나다

`upsert_document` 와 `create_publication` 을 부르는 모듈은 **이 파일 하나뿐**이고,
그것을 시험이 센다 — 답변 게재(`pipeline.publication`)에서 이미 쓴 방식이다. 문서로
적어 둔 규칙이 아니라 **세어서 확인하는 사실**이어야 감사 기록이 한 곳에 모인다.

## 기록을 먼저 남기고 내보낸다

    1. `content_publication` 을 `in_flight` 로 남기고 커밋한다
    2. 어댑터를 부른다
    3. 받은 참조로 `published` 로 올린다

순서를 뒤집으면 **나갔는데 기록이 없는 상태**가 생기고, 그것은 조용하다.

## `in_flight` 를 닫는 방법이 자리마다 다르다 (D46)

여기가 답변 게재와 갈리는 지점이다.

| | **문서 면** (upsert) | **발행 면** (create) |
|---|---|---|
| 멱등한가 | **그렇다** — 같은 경로에 같은 본문을 다시 쓴다 | 아니다 |
| 결과를 모를 때 | **그냥 다시 보낸다** | **사람이 확인한다** |
| 잘못 재시도하면 | 아무 일도 없다 | **회차가 둘 생기고 우리는 그것을 지울 수 없다** |

답변 게재가 `in_flight` 를 추측해서 닫지 않기로 한 것은 게재가 멱등하지 않아서였다.
**멱등한 연산에까지 그 조심성을 그대로 옮기면 고칠 수 있는 것을 사람에게 미룬다** —
문서 면은 다시 보내면 되는데 대기열에 세워 두는 셈이다.

## 발행물은 최종 확인 없이 나가지 않는다

살아있는 문서는 승인이 곧 게재다 — 틀리면 다음 갱신이 고친다 (§7.3). 발행물은
되돌릴 수 없으므로 **승인 위에 발행 직전 최종 확인이 하나 더 있고**(§5.5.5), 그
확인은 이 함수의 인자로 들어온다. 기본값을 참으로 두지 않는 것이 요점이다 —
기본값이 있으면 언젠가 누군가 넘기지 않고 부른다.

## 귀속을 본문에 싣는다

**모 시스템의 렌더링에 맡기지 않는다** (PO-2 와 같은 이유). 화면은 저쪽 것이라
그쪽에 걸면 우리가 지킬 수 없는 요구가 된다. 콘텐츠는 불특정 다수에게 가므로
답변보다 약할 이유가 없고, 칼럼은 특히 **조직의 입장으로 오독될 여지가 크다**
(§7.6.5).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_service_desk.adapters.parent_system import ParentSystem
from agentic_service_desk.content import store
from agentic_service_desk.content.registry import (
    ContentType,
    Input,
    InvalidDeclaration,
    Place,
    Registry,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository

IN_FLIGHT = "in_flight"
PUBLISHED = "published"
ABANDONED = "abandoned"

ATTRIBUTION = "이 문서는 AI 가 지식베이스를 근거로 작성했습니다."
"""PO-2 와 같은 뜻. **숨기지 않는다** — 근거를 함께 싣는 이상 어차피 드러나고,
숨겼다가 틀린 내용이 문제가 되면 신뢰 타격이 훨씬 크다 (§3.2)."""


class NotApproved(RuntimeError):
    """승인되지 않은 초안이다. **콘텐츠는 전수 사람 승인이다** (FR-39)."""


class FinalCheckMissing(RuntimeError):
    """발행물인데 발행 직전 최종 확인을 거치지 않았다 (§5.5.5, §7.3)."""


class AlreadyPublished(RuntimeError):
    """이 초안은 이미 나갔다. **한 초안은 한 번만 나간다.**"""


@dataclass(frozen=True)
class Publication:
    """게재 기록 하나."""

    id: str
    draft_id: str
    type_id: str
    place: str
    path: str | None
    parent_ref: str | None
    state: str
    attempted_at: str
    published_at: str | None

    @property
    def settled(self) -> bool:
        return self.state != IN_FLIGHT


def compose(ctype: ContentType, draft: store.ContentDraft) -> str:
    """게재될 본문을 조립한다 — **귀속과 근거가 붙은 그대로.**

    이 문자열이 그대로 나가고 그대로 기록된다. 두 벌로 만들면 **무엇이 나갔는지**와
    **무엇을 남겼는지**가 갈린다.
    """
    parts = [draft.body.strip(), "", "---", ATTRIBUTION]
    if draft.grounding:
        parts.append("근거: " + ", ".join(draft.grounding))
    # **관찰도 근거다** (§7.6.2). 본문이 권고와 함께 밝히지만 근거 목록에도 남긴다 —
    # 발행물은 회수할 수 없으므로 **그때 무엇을 세었는지**가 나간 글에 함께 있어야
    # 한다. 지금 다시 세면 숫자가 다르다.
    #
    # **본문이 쓴 것만 싣는다.** 라이브에서 잡았다: 본 관찰을 전부 실었더니 글이
    # 다루지도 않은 관찰이 근거로 붙었다. 읽는 사람은 그 조언이 그것에 기댄 줄로
    # 읽고, 검수자도 밝혀진 줄로 본다 — 근거 목록을 부풀리는 것은 근거가 없는 것과
    # 다른 종류의 거짓말이다.
    label = "집계" if ctype.input is Input.PERIOD_SUMMARY else "관찰"
    for fact in store.facts_of(draft):
        if fact.cited:
            parts.append(f"{label}: {fact.text}")
    return "\n".join(parts)


def publish(
    conn: sqlite3.Connection,
    parent: ParentSystem,
    ctype: ContentType,
    draft: store.ContentDraft,
    *,
    repo: KnowledgeRepository | None = None,
    final_check_by: str | None = None,
) -> Publication:
    """승인된 콘텐츠를 내보낸다. **여기가 유일한 출구다.**

    `final_check_by` 는 발행물에만 필요하다 — 누가 발행 직전에 확인했는가다.
    살아있는 문서에는 그 단계가 없다 (§5.5.5).
    """
    if draft.state != store.APPROVED:
        raise NotApproved(
            f"초안 {draft.id} 는 {draft.state} 다 — **콘텐츠는 전수 사람 승인**이며 "
            "자동으로 나가는 경로가 없다 (FR-39)"
        )
    if ctype.review.final_check and not final_check_by:
        raise FinalCheckMissing(
            f"{ctype.title} 은 발행물이라 **발행 직전 최종 확인**이 남아 있다 "
            "(§5.5.5) — 되돌릴 수 없기 때문이다"
        )

    existing = of_draft(conn, draft.id)
    if existing is not None and existing.state == PUBLISHED:
        raise AlreadyPublished(f"초안 {draft.id} 는 이미 나갔다: {existing.parent_ref}")

    place = ctype.destination.place
    body = compose(ctype, draft)
    record = existing if existing is not None else _open(
        conn, ctype, draft, body=body, repo=repo
    )

    if place is Place.DOCUMENT:
        parent_ref = parent.upsert_document(
            ctype.destination.path, draft.title, body
        )
    else:
        parent_ref = parent.create_publication(draft.title, body)

    conn.execute(
        "UPDATE content_publication SET state = ?, parent_ref = ?, published_at = ? "
        "WHERE id = ?",
        (PUBLISHED, parent_ref, datetime.now(UTC).isoformat(), record.id),
    )
    conn.commit()
    return of_draft(conn, draft.id)


def _open(
    conn: sqlite3.Connection,
    ctype: ContentType,
    draft: store.ContentDraft,
    *,
    body: str,
    repo: KnowledgeRepository | None,
) -> Publication:
    """내보내기 **전에** 기록을 남긴다.

    근거와 지식베이스 커밋을 함께 박는다 — 답변의 근거 버전 고정(D20)과 같은 뜻이다.
    살아있는 문서는 갱신으로 stale 을 흡수하므로(§7.3) 정정 경로는 붙지 않지만,
    **"이 판본이 무엇에 기대어 쓰였는가"** 는 그것과 별개로 남아야 한다.
    """
    publication_id = f"cp-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO content_publication "
        "(id, draft_id, type_id, place, path, body, grounding, pinned_commit, "
        " state, attempted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            publication_id,
            draft.id,
            ctype.id,
            str(ctype.destination.place),
            ctype.destination.path or None,
            body,
            json.dumps(list(draft.grounding), ensure_ascii=False),
            repo.head() if repo is not None else None,
            IN_FLIGHT,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    return of_draft(conn, draft.id)


def _from_row(row: sqlite3.Row) -> Publication:
    return Publication(
        id=row["id"],
        draft_id=row["draft_id"],
        type_id=row["type_id"],
        place=row["place"],
        path=row["path"],
        parent_ref=row["parent_ref"],
        state=row["state"],
        attempted_at=row["attempted_at"],
        published_at=row["published_at"],
    )


def of_draft(conn: sqlite3.Connection, draft_id: str) -> Publication | None:
    row = conn.execute(
        "SELECT * FROM content_publication WHERE draft_id = ? AND state <> ?",
        (draft_id, ABANDONED),
    ).fetchone()
    return _from_row(row) if row else None


def current(conn: sqlite3.Connection, type_id: str) -> Publication | None:
    """지금 나가 있는 판본."""
    row = conn.execute(
        "SELECT * FROM content_publication WHERE type_id = ? AND state = ? "
        "ORDER BY published_at DESC LIMIT 1",
        (type_id, PUBLISHED),
    ).fetchone()
    return _from_row(row) if row else None


def unsettled(conn: sqlite3.Connection, types: Registry) -> list[Publication]:
    """끝을 보지 못한 게재 중 **사람이 봐야 하는 것**.

    문서 면은 여기 오지 않는다 — 멱등하므로 다음 주기가 그냥 다시 보낸다. 발행 면만
    남는다: 다시 보내면 **회차가 둘 생기고 우리는 그것을 지울 수 없다.**
    """
    rows = conn.execute(
        "SELECT * FROM content_publication WHERE state = ? ORDER BY attempted_at",
        (IN_FLIGHT,),
    ).fetchall()
    return [
        p
        for p in (_from_row(r) for r in rows)
        if p.place == str(Place.PUBLICATION)
    ]


def retriable(conn: sqlite3.Connection, types: Registry) -> list[store.ContentDraft]:
    """승인됐는데 아직 나가지 않은 것.

    **문서 면의 `in_flight` 도 여기 포함된다** — 멱등하므로 다시 보내면 되고, 그것이
    사람을 부르지 않고 스스로 낫는 유일한 경우다. 발행 면의 `in_flight` 는 빠진다:
    결과를 모르는 채 다시 보내면 회차가 둘 생긴다.
    """
    out = []
    for draft in store.approved(conn):
        try:
            ctype = types.get(draft.type_id)
        except InvalidDeclaration:
            # 선언이 사라진 타입이다. **어디로 보낼지 모르므로 보내지 않는다** —
            # 경로를 짐작하면 엉뚱한 문서를 덮어쓴다.
            continue
        existing = of_draft(conn, draft.id)
        if existing is not None and existing.state == PUBLISHED:
            continue
        if existing is not None and ctype.destination.place is not Place.DOCUMENT:
            continue  # 발행 면의 미확정은 사람이 본다
        if ctype.review.final_check:
            continue  # 발행물은 최종 확인이 남았다 — 배치가 대신 누르지 않는다
        out.append(draft)
    return out


def settle(conn: sqlite3.Connection, publication_id: str, *, published: bool) -> None:
    """사람이 모 시스템을 눈으로 확인하고 닫는다 (발행 면 전용).

    **추측하지 않는다.** 통신이 끊긴 것과 응답만 못 받은 것을 코드가 구별할 수 없고,
    틀리게 다시 보내면 같은 회차가 둘 올라간다.
    """
    conn.execute(
        "UPDATE content_publication SET state = ?, published_at = ? WHERE id = ? AND state = ?",
        (
            PUBLISHED if published else ABANDONED,
            datetime.now(UTC).isoformat() if published else None,
            publication_id,
            IN_FLIGHT,
        ),
    )
    conn.commit()
