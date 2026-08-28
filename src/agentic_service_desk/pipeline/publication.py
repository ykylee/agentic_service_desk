"""게재 — 파이프라인 5단계 (XR-5, FR-24, PO-2, NFR-3).

검수를 통과한 초안이 **모 시스템 QnA 에 봇 계정으로 올라가는** 자리다. 이 시스템이
모 시스템에 가하는 쓰기는 §13 이 셋으로 한정했고, 그중 답변 게재가 이것이다.

## 출구는 하나다 (NFR-3)

`publish_answer` 를 부르는 모듈은 **이 파일 하나뿐**이고, 그것을 시험이 지킨다
(`test_publication.py::test_단일_출구`). 문서로 적어 둔 규칙이 아니라 세어서 확인하는
사실이다 — 이 저장소가 반복해 쓰는 해법이다.

출구를 하나로 두는 이유는 §9.6 이 셋으로 적었는데, 게재에서는 그중 둘이 특히 걸린다.

- **감사 기록** — 무엇이 언제 어디로 나갔는지가 한 곳에 모인다
- **정정 추적** — 모 시스템에서 받은 게재 id 를 붙들지 못하면 XR-7 로 고칠 수 없다.
  나가는 문이 여럿이면 그중 하나가 id 를 흘려도 아무도 모른다

## 기록을 먼저 남기고 내보낸다

게재는 **되돌리기 어려운 대외 행위**다(§5.2). 그래서 순서가 뒤집혀 있다.

    1. `answer_record` 를 `in_flight` 로 남기고 커밋한다
    2. 어댑터를 부른다
    3. 받은 게재 id 로 `published` 로 올린다

2 와 3 사이에서 죽으면 **나갔는지 모르는 기록**이 남는다. 우리는 그것을 추측하지
않는다 — 통신이 끊긴 것과 응답만 못 받은 것을 코드가 구별할 방법이 없기 때문이다.
`in_flight` 는 사람이 모 시스템을 눈으로 확인하고 `settle()` 로 닫는다.

반대 순서(내보내고 기록)를 택하면 **게재됐는데 기록이 없는 상태**가 생기고, 그것은
조용하다 — 아무도 그 답변을 정정할 수 없게 되는데 화면에는 아무 표시도 나지 않는다.

## 근거는 **게재 시점으로 고정해** 함께 남긴다 (FR-28, D20)

게재된 답변의 텍스트는 모 시스템에 남지만 **무엇을 근거로 만들어졌는지는 우리만
안다**(§6.6). 그것을 링크로만 남기면, 항목이 갱신됐을 때 링크를 따라가 봐야 *지금의*
지식이 나올 뿐이다.

    필드 2  근거 목록      — 인용한 지식 항목의 불변 id
    필드 3  근거의 출처    — 그 항목이 **당시** 무엇에서 유래했는가
    필드 4  근거 버전      — 게재 시점의 **지식베이스 커밋**. 원천 저장소 커밋이 아니다

"그때는 맞았고 지금은 틀리다"와 "그때부터 틀렸다"는 **다른 사고**이고 대응도 다르다 —
전자는 정상적인 stale 이고 후자는 품질 결함이다. 버전 고정이 둘을 가르며, 게재 시점의
stale 여부를 함께 적는 것이 후자를 셀 수 있게 한다.

**고정할 수 없으면 게재하지 않는다.** 커밋 없는 저장소에서 없는 해시를 지어내 박으면,
거짓은 나중에 재현을 시도할 때에야 드러나고 그때는 이미 답변이 나가 있다.

## 누가 올리는가를 나가기 전에 대조한다

게재 계정이 `ASD_BOT_ACCOUNTS` 에 없으면 **게재하지 않는다.** 목록 밖 계정으로 나간
답변은 다음 수집 주기에 *사람 답변*으로 읽히고, 그러면 산출물 필터가 통과시켜
**시스템이 자기 답변을 지식으로 다시 배운다**(§5.3, W2). 이 확인이 가능한 것은
출구가 하나이기 때문이다.
"""

from __future__ import annotations

import enum
import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_service_desk.adapters.parent_system import ParentSystem
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.pipeline import draft_store

AUTHOR_BOT = "bot"
"""`answer_record.author_kind`. 되먹임 차단의 판정 근거다 (D7)."""

STATE_PUBLISHED_QNA = "게재됨"
"""`qna_item.state` — 답변이 올라간 뒤의 대외 상태 (§6.2)."""

IN_FLIGHT = "in_flight"
PUBLISHED = "published"
ABANDONED = "abandoned"

ATTRIBUTION = "이 답변은 AI 가 작성했습니다."
"""PO-2. **숨기지 않는다** — 근거를 함께 싣는 이상 어차피 드러나고, 숨겼다가 오답이
문제가 되면 신뢰 타격이 훨씬 크다 (§3.2)."""

ASK_RESOLUTION = (
    "해결되었다면 **해결 표시**를 눌러 주세요. 아직 해결되지 않았다면 댓글로 알려 주시면 "
    "다시 확인합니다."
)
"""해결 표시를 유도하는 한 줄 (§5.3.1.1).

화면은 모 시스템 것이지만 **답변 내용은 우리 것**이다. 해결 표시율은 지식 성장 속도의
*선행 지표*이고, 표시가 눌리지 않으면 명시적 해결이 없어 ingest 자격이 열리지 않는다 —
지식이 자라야 할 때 자라지 않는데 그 원인은 몇 주 뒤에야 드러난다. 후속 댓글을 함께
청하는 것은 그것이 파이프라인 재실행의 트리거이기 때문이다 (D9).
"""


class Refusal(enum.StrEnum):
    """게재하지 않은 이유. **전부 정상 결과다** — 예외가 아니라 판정이다."""

    UNKNOWN_DRAFT = "없는 초안"
    NOT_APPROVED = "승인되지 않았다"
    """검수를 통과하지 않았다. **검수 없는 게재는 없다** (§5.1)."""

    NO_DESTINATION = "게재할 자리가 없다"
    """모 시스템을 거치지 않은 질문이다. 담당자가 메신저로 직접 받아 등록한 건은
    (§1.4.3) 올릴 글이 애초에 없다 — 실패가 아니라 **그 경로의 정상 종점**이다."""

    UNIDENTIFIED_AUTHOR = "게재 계정이 봇 목록에 없다"
    """§5.3 되먹임 차단이 무너지는 조건이다. 나가기 전에 막는다."""

    ALREADY_OUT = "이미 나갔다"
    """한 초안은 한 번만 나간다. 스키마의 부분 유니크 인덱스가 함께 지킨다."""

    UNPINNABLE = "근거 버전을 고정할 수 없다"
    """지식베이스에 커밋이 없어 게재 시점의 항목 내용을 재현할 수 없다 (FR-28).

    **고정 없이 내보내지 않는다.** 그러면 stale 전파의 배선이 없는 답변이 나가고,
    나중에 "그때는 맞았는가"를 물을 수 없다 — 물을 수 없으면 정정도 못 한다.
    """

    IN_FLIGHT = "지난 게재가 끝을 보지 못했다"
    """나갔는지 모르는 기록이 남아 있다. **사람이 확인해야 한다** — 여기서 추측해
    다시 보내면 이용자의 질문에 같은 답변이 둘 달린다."""


@dataclass(frozen=True)
class Published:
    """나간 것."""

    record_id: str
    parent_answer_id: str
    body: str
    """실제로 올라간 본문. 조립이 끝난 그대로 보관한다 — 나중에 "무엇이 게재됐는가"를
    물을 때 다시 조립하면 그 사이 바뀐 형식으로 답하게 된다."""


@dataclass(frozen=True)
class Refused:
    """나가지 않은 것과 그 이유."""

    reason: Refusal
    detail: str = ""


@dataclass(frozen=True)
class Unsettled:
    """끝을 보지 못한 게재 하나 (`in_flight`). 사람이 닫아야 한다."""

    record_id: str
    draft_id: str | None
    qna_item_id: str
    parent_question_id: str | None
    attempted_at: str


@dataclass(frozen=True)
class Pinned:
    """게재 시점으로 고정된 근거 하나 (§6.6.1 필드 2~4)."""

    item_id: str
    title: str
    pinned_commit: str
    """게재 시점의 **지식베이스** 커밋. 원천 저장소 커밋이 아니다 (ADR-002 결정 3)."""

    source: tuple[str, ...] = ()
    """그 항목이 **당시** 무엇에서 유래했는가. 항목이 갱신되면 지금 provenance 는
    달라지므로 여기 박아 둔다 — 그래야 "무엇이 바뀌어 틀리게 됐는가"에 답할 수 있다."""

    stale: bool = False
    """게재 시점에 이미 낡아 있었는가. **P4 검수가 막는 것이므로 여기 참이 쌓이면
    검수가 새고 있다는 뜻이다** (§6.6.2)."""


def pin(repo: KnowledgeRepository, item_ids: Sequence[str]) -> list[Pinned] | None:
    """근거를 게재 시점으로 고정한다. **고정할 수 없으면 `None`.**

    커밋이 없는 저장소에서는 값을 지어내지 않는다 — 없는 해시를 박아 두면 나중에
    재현을 시도할 때에야 거짓이 드러나고, 그때는 이미 답변이 나가 있다.

    항목을 못 찾은 근거는 **자리를 비우지 않고 제목 없이 남긴다**: 근거 목록에서
    빠지면 그 답변이 실제보다 얇은 근거로 쓰인 것처럼 읽힌다 (ADR-002 결정 4 의
    Lint 검사가 그 깨진 링크를 나중에 Q5 로 올린다).
    """
    head = repo.head()
    if head is None:
        return None
    pinned = []
    for item_id in item_ids:
        stored = repo.find(item_id)
        if stored is None:
            pinned.append(Pinned(item_id=item_id, title="", pinned_commit=head))
            continue
        item = stored.item
        pinned.append(
            Pinned(
                item_id=item_id,
                title=item.title,
                pinned_commit=head,
                source=tuple(
                    ref
                    for prov in item.provenance
                    for ref in (prov.commit, prov.path, prov.qna)
                    if ref
                ),
                stale=item.stale,
            )
        )
    return pinned


# --- 본문 조립 (FR-24, PO-2) ------------------------------------------------


def compose(
    *,
    body: str,
    grounding: Sequence[str],
    titles: Mapping[str, str] | None = None,
    unanswered: Sequence[str] = (),
) -> str:
    """게재될 본문을 만든다.

    **근거 강도 표시는 싣지 않는다.** 그것은 검수자가 어디를 볼지 정하는 장치이지
    (§5.6.5) 이용자에게 줄 정보가 아니다 — 이용자에게 "이 문장은 근거가 얇습니다"라고
    말하는 답변은 답변이 아니다.

    **모른다고 밝힌 경계는 싣는다.** 기본값이 부분 답변 + 경계 명시이므로(§5.4.2)
    경계가 이용자에게 닿지 않으면 그 절반이 사라진다.

    제목을 못 찾은 근거는 **id 를 그대로 적는다.** 빼면 근거가 실제보다 적어 보이고,
    그 답변은 실제보다 얇은 근거로 쓰인 것처럼 읽힌다.
    """
    titles = titles or {}
    parts = [body.strip()]

    if unanswered:
        lines = "\n".join(f"- {u}" for u in unanswered)
        parts.append(f"**답변하지 못한 부분**\n{lines}")

    parts.append("---")

    grounding_lines = "\n".join(f"- {titles.get(g) or g}" for g in grounding)
    attribution = ATTRIBUTION
    if grounding_lines:
        attribution += f"\n\n**근거**\n{grounding_lines}"
    parts.append(attribution)

    parts.append(ASK_RESOLUTION)
    return "\n\n".join(parts)


# --- 관문 -------------------------------------------------------------------


def publish(
    conn: sqlite3.Connection,
    parent: ParentSystem,
    draft_id: str,
    *,
    bot_accounts: frozenset[str],
    repo: KnowledgeRepository,
) -> Published | Refused:
    """승인된 초안 하나를 게재한다. **이 시스템에서 답변이 나가는 유일한 문이다.**

    `bot_accounts` 를 인자로 받는 이유는 이 모듈이 설정을 읽지 않게 하기 위해서다 —
    호출부가 하나뿐이라 감출 것이 없고, 인자로 두면 시험이 목록을 바꿔 가며 대조
    실패를 실제로 확인할 수 있다.
    """
    draft = draft_store.get(conn, draft_id)
    if draft is None:
        return Refused(Refusal.UNKNOWN_DRAFT, draft_id)
    if draft.state != draft_store.APPROVED:
        return Refused(Refusal.NOT_APPROVED, f"상태: {draft.state}")

    if not draft.qna_item_id:
        return Refused(Refusal.NO_DESTINATION, "초안이 QnA 항목에 붙어 있지 않다")
    qna = conn.execute(
        "SELECT id, parent_question_id, origin FROM qna_item WHERE id = ?",
        (draft.qna_item_id,),
    ).fetchone()
    if qna is None:
        return Refused(Refusal.NO_DESTINATION, f"없는 QnA 항목: {draft.qna_item_id}")
    question_id = qna["parent_question_id"]
    if not question_id:
        return Refused(
            Refusal.NO_DESTINATION,
            f"모 시스템을 거치지 않은 질문이다 (origin={qna['origin']})",
        )

    account = parent.bot_account
    if account not in bot_accounts:
        return Refused(
            Refusal.UNIDENTIFIED_AUTHOR,
            f"게재 계정 {account!r} 이 ASD_BOT_ACCOUNTS 에 없다 — "
            "이대로 올리면 다음 주기에 사람 답변으로 읽혀 자기 산출물을 다시 배운다",
        )

    existing = conn.execute(
        "SELECT id, state FROM answer_record WHERE draft_id = ? AND state <> ?",
        (draft_id, ABANDONED),
    ).fetchone()
    if existing is not None:
        if existing["state"] == PUBLISHED:
            return Refused(Refusal.ALREADY_OUT, existing["id"])
        return Refused(Refusal.IN_FLIGHT, existing["id"])

    # **고정을 먼저 한다.** 나간 뒤에 고정하려 하면, 실패했을 때 이미 되돌릴 수 없다.
    pinned = pin(repo, draft.grounding)
    if pinned is None:
        return Refused(
            Refusal.UNPINNABLE,
            "지식베이스에 커밋이 없다 — 게재 시점의 근거를 재현할 수 없다",
        )

    body = compose(
        body=draft.body,
        grounding=draft.grounding,
        titles={p.item_id: p.title for p in pinned if p.title},
        unanswered=draft.unanswered,
    )

    record_id = f"ar-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO answer_record "
        "(id, qna_item_id, draft_id, body, author_kind, author_account, "
        " generated_by, review_outcome, state, attempted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record_id,
            draft.qna_item_id,
            draft_id,
            body,
            AUTHOR_BOT,
            account,
            draft.generated_by or None,
            "passed",
            IN_FLIGHT,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.executemany(
        "INSERT INTO answer_grounding "
        "(answer_record_id, knowledge_item_id, pinned_commit, source, stale_at_publish) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (
                record_id,
                p.item_id,
                p.pinned_commit,
                json.dumps(list(p.source), ensure_ascii=False),
                int(p.stale),
            )
            for p in pinned
        ],
    )
    conn.commit()

    parent_answer_id = parent.publish_answer(question_id, body, list(draft.grounding))

    conn.execute(
        "UPDATE answer_record SET parent_answer_id = ?, state = ?, published_at = ? "
        "WHERE id = ?",
        (parent_answer_id, PUBLISHED, datetime.now(UTC).isoformat(), record_id),
    )
    conn.execute(
        "UPDATE qna_item SET state = ? WHERE id = ?",
        (STATE_PUBLISHED_QNA, draft.qna_item_id),
    )
    conn.commit()
    return Published(record_id=record_id, parent_answer_id=parent_answer_id, body=body)


# --- 끝을 보지 못한 게재 ----------------------------------------------------


def unsettled(conn: sqlite3.Connection) -> list[Unsettled]:
    """나갔는지 모르는 기록. **비어 있는 것이 정상이다.**

    비어 있지 않으면 사람이 모 시스템을 열어 확인해야 한다 — 자동으로 풀 수 있는
    상태가 아니다.
    """
    rows = conn.execute(
        "SELECT r.id, r.draft_id, r.qna_item_id, r.attempted_at, q.parent_question_id "
        "FROM answer_record r LEFT JOIN qna_item q ON q.id = r.qna_item_id "
        "WHERE r.state = ? ORDER BY r.attempted_at",
        (IN_FLIGHT,),
    ).fetchall()
    return [
        Unsettled(
            record_id=r["id"],
            draft_id=r["draft_id"],
            qna_item_id=r["qna_item_id"],
            parent_question_id=r["parent_question_id"],
            attempted_at=r["attempted_at"] or "",
        )
        for r in rows
    ]


def settle(
    conn: sqlite3.Connection,
    record_id: str,
    *,
    parent_answer_id: str | None,
) -> bool:
    """사람이 확인한 결과를 적는다. **둘 중 하나다.**

    - `parent_answer_id` 가 있다 → 나갔다. 그 id 로 `published` 를 확정한다.
      정정 경로(XR-7)가 이 id 하나에 걸려 있으므로 여기서 받아 둔다
    - `None` → 나가지 않았다. `abandoned` 로 닫고, 그 초안은 **다시 게재할 수 있다**

    되돌리지 못할 쪽으로 자동 판정하지 않는 것이 요점이다. 추측해서 다시 보내면
    이용자의 질문에 같은 답변이 둘 달리고, 그것은 우리가 지울 수 없다.
    """
    row = conn.execute(
        "SELECT qna_item_id, state FROM answer_record WHERE id = ?", (record_id,)
    ).fetchone()
    if row is None or row["state"] != IN_FLIGHT:
        return False

    if parent_answer_id:
        conn.execute(
            "UPDATE answer_record SET parent_answer_id = ?, state = ?, published_at = ? "
            "WHERE id = ?",
            (parent_answer_id, PUBLISHED, datetime.now(UTC).isoformat(), record_id),
        )
        conn.execute(
            "UPDATE qna_item SET state = ? WHERE id = ?",
            (STATE_PUBLISHED_QNA, row["qna_item_id"]),
        )
    else:
        conn.execute(
            "UPDATE answer_record SET state = ? WHERE id = ?", (ABANDONED, record_id)
        )
    conn.commit()
    return True


def record(conn: sqlite3.Connection, record_id: str) -> sqlite3.Row | None:
    """게재 기록 하나. 정정(XR-7)과 stale 전파(4.5.7)가 여기서 출발한다."""
    return conn.execute(
        "SELECT * FROM answer_record WHERE id = ?", (record_id,)
    ).fetchone()


def grounding_of(conn: sqlite3.Connection, record_id: str) -> list[Pinned]:
    """그 답변이 **무엇에 기대어 나갔는가**, 게재 시점 그대로 (§6.6.1 필드 2~4).

    지금의 지식이 아니다 — 그것을 보려면 항목을 열면 된다. 여기 있는 것은
    **그때의 것**이고, 둘의 차이가 곧 "무엇이 바뀌었는가"다.
    """
    rows = conn.execute(
        "SELECT * FROM answer_grounding WHERE answer_record_id = ? "
        "ORDER BY knowledge_item_id",
        (record_id,),
    ).fetchall()
    return [
        Pinned(
            item_id=r["knowledge_item_id"],
            title="",
            pinned_commit=r["pinned_commit"],
            source=tuple(json.loads(r["source"])),
            stale=bool(r["stale_at_publish"]),
        )
        for r in rows
    ]


def answered_with(conn: sqlite3.Connection, item_id: str) -> list[sqlite3.Row]:
    """이 지식 항목을 근거로 쓴 **게재된** 답변들. stale 전파의 입구다 (§6.6.3).

    항목이 낡으면 그것으로 답한 게재물이 정정 후보(Q5)가 된다 — 그 목록이 여기서
    나온다. 전파는 WBS-4.5.7 이 잇는다.
    """
    return conn.execute(
        "SELECT r.* FROM answer_record r "
        "JOIN answer_grounding g ON g.answer_record_id = r.id "
        "WHERE g.knowledge_item_id = ? AND r.state = ? ORDER BY r.published_at",
        (item_id, PUBLISHED),
    ).fetchall()
