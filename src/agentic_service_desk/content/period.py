"""기간 요약 — 뉴스레터의 주 입력 (WBS-4.7.3, FR-38, §7.2).

뉴스레터는 칼럼과 같은 발행 면에 나가지만 **다른 것을 읽는다.** §7.2 는 그 주 입력을
"기간 내 변경·발행 요약"이라고 적었다 — 칼럼이 한 주제를 깊이 다룬다면 뉴스레터는
**그 기간에 무슨 일이 있었는가**를 적는다.

| | 무엇을 읽는가 | 무엇을 쓰는가 |
|---|---|---|
| 칼럼 | 지식베이스 + 관찰 | 한 주제의 해설·권고 |
| **뉴스레터** | **기간 내 변경·발행 요약** | 그 회차의 일 |

## 요약은 세는 것이지 판단하는 것이 아니다

여기서 나오는 것은 전부 **셀 수 있는 사실**이다 — 지식 항목 몇 개가 생기고 몇 개가
갱신됐는가, 콘텐츠가 몇 건 나갔는가, 답변이 몇 건 나갔는가, 무엇이 자주 묻혔는가.
모델에게 "이번 기간이 어땠는지" 묻지 않는다: 그 답은 검증할 수 없고, 검증할 수 없는
서술이 발행물에 실리면 회수할 방법이 없다 (§7.3).

그래서 요약은 **초안에 박힌다** (`store.Fact`). 발행 뒤에 다시 세면 숫자가 달라져
검수가 본문과 대조할 수 없다 — 칼럼의 관찰과 같은 이유이고, 실은 같은 자리다.

## 새로 생긴 것과 고쳐진 것을 나눈다

한 말로 적으면 읽는 사람은 없던 것이 생긴 줄로 읽는다. 지식베이스가 자라는 것과
틀린 것을 고치는 것은 **다른 소식**이다.

## 쓸 것이 없으면 내지 않는다

근거는 **기간 안에 바뀐 지식 항목**이다. 아무것도 바뀌지 않았으면 근거가 없고,
근거가 없으면 초안을 만들지 않는다 (D3) — 할 말이 없는 회차를 억지로 내면 발행 면에
빈 회차가 쌓인다. 발행물은 지울 수 없으므로 그것도 회수할 수 없는 종류다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from agentic_service_desk.content import qna_stats
from agentic_service_desk.content.store import Fact
from agentic_service_desk.knowledge.repository import KnowledgeRepository


@dataclass
class Summary:
    """한 기간의 요약."""

    items: list = field(default_factory=list)
    """기간 안에 새로 생기거나 갱신된 지식 항목. **이것이 근거다.**"""

    facts: tuple[Fact, ...] = ()
    """박을 사실. 번호는 `sum-*` 이고 관찰(`obs-*`)과 섞이지 않는다."""

    added: int = 0
    updated: int = 0


def _statement(index: int, text: str) -> Fact:
    return Fact(id=f"sum-{index}", text=text)


def knowledge_facts(
    repo: KnowledgeRepository, since: str, *, window_days: int
) -> tuple[list, int, int, str]:
    """기간 안에 바뀐 지식 항목과 그 요약 문장.

    **낡은 항목도 센다.** stale 표시는 "다시 봐야 한다"는 뜻이지 그 기간에 손대지
    않았다는 뜻이 아니다 — 요약에서 빼면 셈이 저장소와 어긋난다. 다만 근거로는
    쓰지 않는다: 그 판단은 부르는 쪽이 한다.
    """
    changed, added_paths = repo.changed_since(since)
    stored, _broken = repo.scan()
    by_path = {
        s.path.resolve().relative_to(repo.root).as_posix(): s for s in stored
    }
    items = [by_path[p].item for p in sorted(changed) if p in by_path]
    added = len([p for p in added_paths if p in by_path])
    updated = len(items) - added
    if not items:
        return [], 0, 0, ""
    parts = []
    if added:
        parts.append(f"{added}건이 새로 생겼")
    if updated > 0:
        parts.append(f"{updated}건이 갱신됐")
    return (
        items,
        added,
        max(updated, 0),
        f"지난 {window_days}일 동안 지식 항목 " + "고 ".join(parts) + "다.",
    )


def publication_fact(
    conn: sqlite3.Connection, since: str, *, window_days: int
) -> str:
    """기간 안에 나간 콘텐츠. **타입 이름까지 적는다** — 건수만으로는 소식이 아니다."""
    rows = conn.execute(
        "SELECT type_id, COUNT(*) AS n FROM content_publication "
        "WHERE state = 'published' AND published_at >= ? GROUP BY type_id "
        "ORDER BY type_id",
        (since,),
    ).fetchall()
    if not rows:
        return ""
    detail = ", ".join(f"{r['type_id']} {r['n']}건" for r in rows)
    total = sum(r["n"] for r in rows)
    return f"지난 {window_days}일 동안 콘텐츠 {total}건이 게재됐다 ({detail})."


def answer_fact(conn: sqlite3.Connection, since: str, *, window_days: int) -> str:
    """기간 안에 나간 답변과 그중 해결된 것.

    **해결은 명시적인 것만 센다** (§5.3.1). 암묵적 해결을 섞으면 "N건이 해결됐다"가
    "N건이 조용해졌다"와 같은 말이 되는데, 만족해서 조용한 것과 포기하고 떠난 것은
    데이터상 같은 모양이다.
    """
    answers = conn.execute(
        "SELECT COUNT(*) AS n FROM answer_record "
        "WHERE state = 'published' AND published_at >= ?",
        (since,),
    ).fetchone()["n"]
    if not answers:
        return ""
    resolved = conn.execute(
        "SELECT COUNT(*) AS n FROM qna_item "
        "WHERE resolution_grade = 'explicit' AND closed_at >= ?",
        (since,),
    ).fetchone()["n"]
    return (
        f"지난 {window_days}일 동안 답변 {answers}건이 게재됐고, "
        f"그중 명시적으로 해결된 것이 {resolved}건이다."
    )


def summarize(
    conn: sqlite3.Connection,
    repo: KnowledgeRepository,
    *,
    since: str,
    window_days: int,
    groups: list | None = None,
) -> Summary:
    """기간을 요약한다. **빈 항목은 싣지 않는다.**

    "콘텐츠 0건이 게재됐다"는 사실이지만 소식이 아니다 — 없는 것을 줄줄이 적으면
    있는 것이 그 사이에 묻힌다 (§8.6 이 대기열에서 정한 것과 같다).
    """
    items, added, updated, knowledge = knowledge_facts(
        repo, since, window_days=window_days
    )
    statements = [
        s
        for s in (
            knowledge,
            publication_fact(conn, since, window_days=window_days),
            answer_fact(conn, since, window_days=window_days),
        )
        if s
    ]
    facts = [_statement(i, s) for i, s in enumerate(statements, start=1)]
    # 관찰은 **`obs-*` 번호를 그대로 쓴다.** 뉴스레터에서도 "무엇이 자주 묻혔는가"는
    # 같은 종류의 사실이고, 번호를 다시 매기면 같은 관찰이 타입마다 다른 이름을 갖는다.
    facts += [
        Fact(id=o.id, text=o.text)
        for o in qna_stats.observations(groups or [], window_days=window_days)
    ]
    return Summary(items=items, facts=tuple(facts), added=added, updated=updated)
