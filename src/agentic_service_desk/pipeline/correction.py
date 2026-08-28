"""stale 전파와 정정 (FR-34·35, PO-1, §5.2 W3, §6.6.3).

§5.2 의 **W3(게재 후 진실 변화)** 은 지금까지 원칙으로만 있었다. 소스코드가 바뀌면
이미 게재된 답변이 틀린 것이 되는데, 그 사실이 아무에게도 닿지 않았다.

**답변 이력의 근거 기록이 그 원칙을 동작시키는 배선이다** (§6.6.3).

    코드 커밋 변경
         │
         ▼
    그 커밋을 출처로 갖는 지식 항목 → stale 표시        (WBS-4.2.6 Lint 가 한다)
         │
         ▼
    그 지식 항목을 근거로 쓴 답변 이력 조회             ◀── §6.6 이 없으면 끊긴다
         │
         ▼
    해당 게재 답변 → Q5 정정 후보                       (여기)

세 단계가 모두 이어져야 코드 변경이 게재물까지 도달한다.

## 지식 항목의 stale 과 게재물의 stale 은 다른 것이다

Lint 는 지식 항목에 stale 을 **표시만** 하고 대기열로 보내지 않는다 — 그것은 현황
지표다. 여기서 대기열이 되는 것은 **그 항목을 근거로 이미 나간 답변**이며, 그것은
"틀린 내용이 지금 이 순간 노출되고 있다"는 뜻이라 방치 비용이 높다 (§8.2).

## 정정은 조용히 하지 않는다

원 답변을 고치고 **정정했다는 사실을 본문에 명시한다** (PO-1). 수정만 하고 넘어가면
이미 읽은 사람이 잘못된 내용을 그대로 갖고 가고, 후속 답글로만 정정하면 원 답변이
틀린 채 남아 나중에 읽는 사람이 정정을 놓친다.

무엇이 왜 바뀌었는지는 **기계가 채운다** — 근거 버전 고정(D20)이 어느 근거가 낡았는지
알려주기 때문이다.

## 정정도 게재다

그러므로 **검수를 건너뛰지 않는다** (§5.1). 정정 초안은 원 답변을 가리킨 채
(`answer_draft.corrects`) 게재 판정과 Q2 를 그대로 지나고, 나갈 때 새 글을 올리는
대신 그 답변을 고친다.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentic_service_desk.knowledge import lint
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.pipeline import draft_store, publication
from agentic_service_desk.pipeline.answer import AnswerPipeline
from agentic_service_desk.pipeline.review import Reviewer

KIND = "stale_answer"
"""정정 소견의 종류. **Lint 의 소견과 같은 표를 쓴다** — 열쇠로 중복을 막고 Q5
티켓을 다는 장치가 이미 거기 있으며, 같은 것을 두 벌 만들면 대기열이 갈린다."""


@dataclass(frozen=True)
class Candidate:
    """정정 후보 하나 — 근거가 낡은 채로 나가 있는 답변."""

    key: str
    ticket_id: str | None
    record_id: str
    qna_item_id: str
    question: str
    body: str
    stale_items: tuple[str, ...]
    first_seen: str

    @property
    def reason(self) -> str:
        return "낡은 근거: " + " · ".join(self.stale_items)


@dataclass
class PropagationReport:
    opened: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.opened or self.closed)


def propagate(
    conn: sqlite3.Connection, repo: KnowledgeRepository
) -> PropagationReport:
    """stale 표시된 지식 항목을 **게재물까지 밀어낸다** (FR-34).

    **낡은 항목에서 출발한다.** 답변에서 출발해 근거를 하나씩 확인하면 게재물 수만큼
    파일을 읽게 되는데, 낡은 항목은 대개 몇 개뿐이다.

    **항목이 갱신됐다고 소견이 닫히지는 않는다.** 낡은 것은 지식이 아니라 *그
    지식으로 만든 답변*이고, 지식이 새로워져도 이미 나간 글은 그대로다. 소견을
    닫는 것은 **정정하거나 무시하는 행위**뿐이다 (§8.2 의 "정정 / 갱신 / 무시").

    항목 갱신이 바꾸는 것은 하나다 — **그때부터 다시 만들 수 있다**(`ready`).
    """
    report = PropagationReport()
    if not repo.root.exists():
        return report

    stale_now = {s.item.id for s in repo.scan()[0] if s.item.stale}

    wanted: dict[str, set[str]] = {}
    for item_id in sorted(stale_now):
        for row in publication.answered_with(conn, item_id):
            wanted.setdefault(row["id"], set()).add(item_id)

    for record_id, items in wanted.items():
        if _open(conn, record_id, sorted(items)):
            report.opened.append(record_id)
    return report


def ready(
    conn: sqlite3.Connection, repo: KnowledgeRepository
) -> list[Candidate]:
    """지금 다시 만들 수 있는 정정 후보.

    **근거가 아직 낡은 동안에는 만들지 않는다.** 낡은 항목으로 다시 돌리면 같은
    답이 나오고, 그것을 정정이라 부르면 **고쳤다는 기록만 남고 내용은 그대로**가
    된다 — 이미 읽은 사람에게 거짓 신호를 보내는 셈이다.

    낡은 표시는 ingest 가 바뀐 커밋을 읽어 항목을 갱신할 때 풀린다. 즉 정정의
    선행 조건은 **지식이 먼저 따라잡는 것**이고, 그 순서가 §6.6.3 배선의 방향이다.
    """
    if not repo.root.exists():
        return []
    stale_now = {s.item.id for s in repo.scan()[0] if s.item.stale}
    return [c for c in pending(conn) if not set(c.stale_items) & stale_now]


def _open(conn: sqlite3.Connection, record_id: str, items: list[str]) -> bool:
    """정정 후보를 Q5 에 올린다. **이미 열려 있으면 다시 열지 않는다.**

    주기 실행이라 고쳐지기 전까지 매번 나온다. 매번 티켓을 찍으면 대기열이 같은
    항목으로 메워져 우선순위를 매길 수 없다 (§8.6).

    다만 **낡은 근거 목록은 갱신한다** — 처음에 하나였다가 둘이 될 수 있고, 그때
    화면이 옛 목록을 보여주면 사람이 고칠 범위를 잘못 잡는다.
    """
    detail = json.dumps(items, ensure_ascii=False)
    key = f"{KIND}:{record_id}"
    existing = conn.execute(
        "SELECT 1 FROM lint_finding WHERE key = ? AND state = ?", (key, lint.OPEN)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE lint_finding SET detail = ? WHERE key = ?", (detail, key)
        )
        conn.commit()
        return False

    ticket_id = ticket_domain.issue(conn, source=lint.CORRECTION).id
    conn.execute(
        "INSERT INTO lint_finding "
        "(key, kind, subject, detail, ticket_id, first_seen, state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET state = excluded.state, "
        "detail = excluded.detail, ticket_id = excluded.ticket_id, "
        "first_seen = excluded.first_seen",
        (
            key,
            KIND,
            record_id,
            detail,
            ticket_id,
            datetime.now(UTC).isoformat(),
            lint.OPEN,
        ),
    )
    conn.commit()
    return True


def _open_findings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM lint_finding WHERE kind = ? AND state = ? ORDER BY first_seen",
            (KIND, lint.OPEN),
        )
    )


def pending(conn: sqlite3.Connection) -> list[Candidate]:
    """Q5 — 근거가 낡은 채로 나가 있는 답변 (§8.2).

    **방치 비용이 높다** — Q6·Q7·Q8 과 달리 틀린 내용이 지금 이 순간 노출되고 있다.
    """
    rows = conn.execute(
        "SELECT f.key, f.subject, f.detail, f.ticket_id, f.first_seen, "
        "       r.body, r.qna_item_id, q.body AS question "
        "FROM lint_finding f "
        "JOIN answer_record r ON r.id = f.subject "
        "LEFT JOIN qna_item i ON i.id = r.qna_item_id "
        "LEFT JOIN raw_question q ON q.id = i.parent_question_id "
        "WHERE f.kind = ? AND f.state = ? ORDER BY f.first_seen",
        (KIND, lint.OPEN),
    ).fetchall()
    return [
        Candidate(
            key=r["key"],
            ticket_id=r["ticket_id"],
            record_id=r["subject"],
            qna_item_id=r["qna_item_id"],
            question=r["question"] or "",
            body=r["body"],
            stale_items=tuple(json.loads(r["detail"] or "[]")),
            first_seen=r["first_seen"],
        )
        for r in rows
    ]


def draft_correction(
    conn: sqlite3.Connection,
    candidate: Candidate,
    *,
    pipeline: AnswerPipeline,
    reviewer: Reviewer | None = None,
) -> str | None:
    """지금 지식으로 다시 답을 만들어 **원 답변을 가리킨 초안**으로 올린다.

    **정정도 게재이므로 검수를 건너뛰지 않는다** (§5.1). 여기서 만드는 것은 초안일
    뿐이고, 나갈지 사람이 볼지는 게재 판정(WBS-4.5.5)이 정한다.

    질문 원문이 없으면 만들지 않는다 — 무엇에 답하는지 모르는 채로 답을 다시 쓰면
    그것은 정정이 아니라 새로 지어내는 것이다.
    """
    if not candidate.question.strip():
        return None
    outcome = pipeline.run(candidate.question)
    if outcome.draft is None:
        # 지금 지식으로도 답을 만들지 못한다. **그것도 결과다** — 사람이 봐야 하고,
        # 소견은 열린 채로 남아 Q5 에 그대로 있다.
        return None

    verdict = None
    if reviewer is not None:
        from agentic_service_desk.pipeline.review import ReviewInput

        verdict = reviewer.review(ReviewInput.of(outcome.draft, outcome.hits))
    return draft_store.save(
        conn,
        question=candidate.question,
        draft=outcome.draft,
        verdict=verdict,
        qna_item_id=candidate.qna_item_id,
        generated_by=outcome.generated_by,
        corrects=candidate.record_id,
    )


def ignore(conn: sqlite3.Connection, record_id: str) -> None:
    """근거는 낡았지만 **답변은 여전히 맞다**고 사람이 판정했다 (§8.2 의 "무시").

    항목이 낡았다는 것과 그것으로 만든 답이 틀렸다는 것은 다르다 — 경로가 바뀌었을
    뿐 내용이 그대로일 수 있다. 다만 **그 항목이 다시 낡으면 다시 뜬다**: 소견의
    열쇠가 답변 하나를 가리키므로, 닫힌 뒤 새로 낡은 근거가 생기면 새 소견이 열린다.

    **무시가 잦으면 stale 판정이 과하다는 신호다.** 그것을 읽으려면 정정과 무시가
    기록에서 갈려 있어야 한다.
    """
    lint.resolve(conn, f"{KIND}:{record_id}", note="무시함 — 근거는 낡았으나 답변은 맞다")
