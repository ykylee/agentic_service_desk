"""운영자 대시보드의 자료 (§8.2·8.3, FR-44·59).

**운영자는 감시자가 아니라 루프의 일부다** (§8.4). 여기서 처리되지 않으면 시스템은
사고 없이 조용히 성장을 멈춘다 — 대시보드가 그것을 드러내지 못하면 아무도 알아채지
못한다.

두 가지 원칙이 화면 모양을 정한다.

**켜지지 않은 대기열은 보여주지 않는다** (FR-59). 여덟을 다 늘어놓으면 1인 운영자가
매번 빈 화면을 훑게 되고, 그러면 **실제로 밀린 것이 그 사이에 묻힌다.**

**화면은 지목만 하고 편집은 평소 도구로 한다** (§8.5.5). 지식베이스가 파일이고
운영자가 개발자라면 대시보드 안에 에디터를 만드는 것은 중복 투자다. 그래서 Q4 는
**어느 파일의 무엇을 봐야 하는지**까지만 알려준다.
"""

from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agentic_service_desk.knowledge import contradiction, lint
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations import manual_entry
from agentic_service_desk.operations import resolution as resolution_domain
from agentic_service_desk.operations import ticket as ticket_domain


#: 방치 비용의 크기 (§8.2). **정렬의 첫 축이다.**
HIGH, MEDIUM, LOW = 3, 2, 1


@dataclass(frozen=True)
class Queue:
    """대기열 하나의 정의 (§8.2)."""

    id: str
    title: str
    what: str
    neglect_cost: str
    """**방치 비용이 우선순위다** — 여덟을 나란히 늘어놓으면 무엇부터 볼지 모른다."""

    kind: str
    """`작업` | `판정` — 화면이 다르다 (§6.4.4, FR-45).

    작업은 조사·수정이 필요해 상세와 상태 전이가 있고, 판정은 보고 누르면 끝난다.
    """

    weight: int = MEDIUM
    """방치 비용의 수치. **경과 시간과 곱해 순위를 만든다** (D32, FR-46)."""

    source: str | None = None
    """이 대기열을 채우는 티켓 출처 (§6.4.3). 판정 대기열에는 없다."""


QUEUES: dict[str, Queue] = {
    "Q1": Queue("Q1", "티켓", "사람 손이 필요한 작업", "높음 — 사람이 기다린다",
                "작업", HIGH, "qna"),
    "Q2": Queue("Q2", "검수 대기 (답변)", "게재 전 답변 초안",
                "높음 — 답변이 나가지 못한다", "판정", HIGH),
    "Q3": Queue("Q3", "검수 대기 (콘텐츠)", "발행 전 콘텐츠",
                "중간 — 발행이 밀린다", "작업", MEDIUM, "content"),
    "Q4": Queue("Q4", "모순", "사람이 고친 지식과 에이전트의 판단이 어긋난 것",
                "높음 — 모순된 지식이 계속 답변에 쓰인다", "작업", HIGH, "contradiction"),
    "Q5": Queue("Q5", "정정 후보", "근거가 낡은 게재 답변·살아있는 문서",
                "높음 — 틀린 내용이 계속 노출된다", "작업", HIGH, "correction"),
    "Q6": Queue("Q6", "암묵적 해결 확인", "사람 확인 없이 닫힌 QnA",
                "낮음 — 다만 지식이 자라지 않는다", "판정", LOW),
    "Q7": Queue("Q7", "승격 후보", "자동 승격 조건을 못 채운 명시적 해결분",
                "낮음 — 다만 지식이 자라지 않는다", "판정", LOW),
    "Q8": Queue("Q8", "지식 공백", "미해결로 종료된 QnA 가 가리키는 영역",
                "낮음 — 같은 질문이 계속 실패한다", "판정", LOW),
}

QUEUE_BY_SOURCE: dict[str, str] = {q.source: q.id for q in QUEUES.values() if q.source}
"""티켓 출처에서 대기열로. **작업 대기열 넷이 모두 티켓을 낳는다** (§6.4.4)."""

#: 단계별로 뜨는 대기열 (FR-59, D49).
STAGE_QUEUES: dict[str, list[str]] = {
    "S0": ["Q4", "Q8"],
    "S1": ["Q1", "Q4", "Q8"],
    "S2": ["Q1", "Q2", "Q4", "Q8"],
    "S3": ["Q1", "Q2", "Q4", "Q5", "Q6", "Q7", "Q8"],
    "S4": ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"],
    "S5": ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"],
}


def queues_for_stage(stage: str) -> list[Queue]:
    return [QUEUES[q] for q in STAGE_QUEUES.get(stage, STAGE_QUEUES["S0"])]


@dataclass(frozen=True)
class ContradictionRow:
    """Q4 한 줄. **양쪽을 나란히 놓는다** — 판정하려면 둘 다 보여야 한다."""

    id: str
    item_id: str
    item_title: str
    item_path: str
    """**어느 파일을 열면 되는가.** 대시보드가 하는 일은 여기까지다 (§8.5.5)."""

    human_body: str
    agent_title: str
    agent_body: str
    agent_grounds: str
    detected_at: str
    missing_item: bool = False
    """지식 항목이 사라졌는가. 그러면 판정할 대상이 없다."""


@dataclass(frozen=True)
class WorkItem:
    """대기열에 뜬 작업 하나."""

    ticket: ticket_domain.Ticket
    queue: Queue
    age_hours: float

    @property
    def score(self) -> float:
        """**방치 비용 × 경과 시간** (D32).

        곱셈인 것이 요점이다 — 덧셈이면 낮은 비용의 대기열은 아무리 오래돼도
        높은 비용을 넘지 못해 §8.2 가 걱정한 "영원히 밀리는" 문제가 남는다.
        """
        return self.queue.weight * self.age_hours

    @property
    def age_label(self) -> str:
        if self.age_hours < 1:
            return "방금"
        if self.age_hours < 48:
            return f"{int(self.age_hours)}시간"
        return f"{int(self.age_hours / 24)}일"


@dataclass(frozen=True)
class TicketDetail:
    """작업 화면 하나에 필요한 전부."""

    item: WorkItem
    entry: manual_entry.Entry | None
    resolution: resolution_domain.Resolution | None
    question: str | None = None
    """유입 원문. **파이프라인이 멈춘 건에는 이것뿐이다** — 초안도 담당자 답변도
    아직 없으므로, 이것마저 없으면 화면이 무엇을 물었는지조차 말하지 못한다."""

    @property
    def needs_answer(self) -> bool:
        """담당자가 직접 답해야 하는가 (WBS-4.5.2).

        파이프라인이 초안을 만들지 못해 열린 티켓이다. **여기서 답을 적지 않으면
        종결 기록의 재료가 없어 닫을 길이 없다** (§6.4.5).
        """
        return (
            self.entry is None
            and self.resolution is None
            and self.item.ticket.state in ticket_domain.QUEUE_VISIBLE
            and self.item.ticket.source is ticket_domain.Source.QNA
        )

    @property
    def next_step(self) -> str:
        """**지금 무엇을 해야 하는가.** 화면이 답을 대신 말해 준다."""
        state = self.item.ticket.state
        if state in (ticket_domain.State.CLOSED, ticket_domain.State.AUTO_CLOSED):
            if self.resolution and self.resolution.promoted_item_id:
                return (
                    f"끝났다. 지식 항목이 되었다 — {self.resolution.promoted_item_id} "
                    f"(승격 경로 A, §6.8.1)."
                )
            return "끝났다. 이 티켓은 승격 대상이 아니다 — 새 지식을 만드는 일이 아니었다."
        if state is ticket_domain.State.HELD:
            return "질문자의 응답을 기다린다 — 후속이 오면 사람 없이 다시 열린다."
        if self.resolution is None:
            if self.needs_answer:
                # **기다리라고 하지 않는다.** 담당자 답변이 없으면 초안의 재료가
                # 없어 어떤 배치도 이것을 채우지 않는다 — 오지 않을 것을 기다리게
                # 하면 대기열이 조용히 막힌다.
                return (
                    "파이프라인이 답을 만들지 못했다. **직접 답하고 그 답을 아래에 "
                    "적는다** — 초안은 다음 배치가 그것으로 만든다."
                )
            return "종결 기록 초안을 기다리는 중이다 — 다음 배치 주기에 만들어진다."
        if not self.resolution.confirmed:
            return "무효화 조건을 채운다. 그것이 곧 승인이며, 채워야 티켓이 닫힌다."
        return "닫을 수 있다."


@dataclass
class KnowledgeStatus:
    """지식베이스 현황 (§8.3) — **지식이 자라고 있는가, 썩고 있는가.**"""

    total: int = 0
    from_source: int = 0
    from_qna: int = 0
    stale: int = 0
    open_contradictions: int = 0
    lint_findings: dict[str, int] = field(default_factory=dict)
    """대기열로 가지 않는 것까지 **어딘가에는 보이게** 한다.

    S0 에서 Q5 는 화면에 뜨지 않는데(FR-59) 참조 부재 같은 소견은 실제로 생길 수 있다.
    현황에도 없으면 그것이 완전히 보이지 않게 된다.
    """

    broken_files: list[str] = field(default_factory=list)
    recent_ingests: list[tuple[str, str]] = field(default_factory=list)
    """`(날짜, 요약)`. **최근 ingest 이력** — 지식이 언제 마지막으로 자랐는가."""

    @property
    def stale_ratio(self) -> float:
        return (self.stale / self.total) if self.total else 0.0

    @property
    def source_ratio(self) -> float:
        """출처 구성 — 소스코드에서 온 비율.

        원천이 둘인데(D2) 한쪽으로 쏠리면 다른 쪽 수집이 막혔다는 신호다.
        """
        known = self.from_source + self.from_qna
        return (self.from_source / known) if known else 0.0


class Dashboard:
    """화면이 필요한 것을 모은다."""

    def __init__(self, *, repo: KnowledgeRepository, conn: sqlite3.Connection) -> None:
        self._repo = repo
        self._conn = conn

    # --- 현황 ------------------------------------------------------------

    def knowledge_status(self) -> KnowledgeStatus:
        status = KnowledgeStatus()
        if not self._repo.root.exists():
            return status

        stored, status.broken_files = self._repo.scan()
        status.total = len(stored)
        for s in stored:
            if s.item.stale:
                status.stale += 1
            # 한 항목이 두 원천에 걸칠 수 있다 (ADR-003) — 양쪽에 센다.
            if any(p.commit for p in s.item.provenance):
                status.from_source += 1
            if any(p.qna for p in s.item.provenance):
                status.from_qna += 1

        status.open_contradictions = len(contradiction.list_open(self._conn))
        for row in lint.list_open(self._conn):
            status.lint_findings[row["kind"]] = status.lint_findings.get(row["kind"], 0) + 1
        status.recent_ingests = self._recent_ingests()
        return status

    def _recent_ingests(self, limit: int = 5) -> list[tuple[str, str]]:
        """지식 저장소의 최근 커밋. **1 ingest = 1 커밋이라 이것이 곧 이력이다.**"""
        if not (self._repo.root / ".git").exists():
            return []
        result = subprocess.run(  # noqa: S603
            ["git", "log", f"-{limit}", "--format=%aI\x1f%s"],
            cwd=self._repo.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        out = []
        for line in result.stdout.splitlines():
            if "\x1f" not in line:
                continue
            when, subject = line.split("\x1f", 1)
            out.append((when[:16].replace("T", " "), subject))
        return out

    # --- Q4 --------------------------------------------------------------

    def contradictions(self) -> list[ContradictionRow]:
        by_id = {s.item.id: s for s in self._repo.scan()[0]}
        rows = []
        for c in contradiction.list_open(self._conn):
            stored = by_id.get(c.knowledge_item_id)
            grounds = ", ".join(
                p.commit[:8] if p.commit else f"QnA {p.qna}" for p in c.provenance
            )
            rows.append(
                ContradictionRow(
                    id=c.id,
                    item_id=c.knowledge_item_id,
                    item_title=stored.item.title if stored else "(항목이 사라졌다)",
                    item_path=(
                        str(stored.path.relative_to(self._repo.root)) if stored else ""
                    ),
                    human_body=stored.item.body if stored else "",
                    agent_title=c.proposed_title,
                    agent_body=c.proposed_body,
                    agent_grounds=grounds or "(없음)",
                    detected_at=c.detected_at[:16].replace("T", " "),
                    missing_item=stored is None,
                )
            )
        return rows

    def resolve_contradiction(self, contradiction_id: str, resolution: str) -> None:
        contradiction.resolve(self._conn, contradiction_id, resolution=resolution)

    # --- Q1 --------------------------------------------------------------

    def tickets(self) -> list[WorkItem]:
        """Q1 대기열. **오래된 것이 위로 온다** — 기한이 없으니 경과 시간이 축이다."""
        return [
            self._work_item(t)
            for t in ticket_domain.queue(self._conn)
            if QUEUE_BY_SOURCE.get(str(t.source)) == "Q1"
        ]

    def next_up(self, stage: str, limit: int = 5) -> list[WorkItem]:
        """**시스템이 다음에 볼 것을 제시한다** (FR-46, D32, §8.6.3).

        여덟을 나란히 두면 1인 겸업은 매번 전부 훑어야 한다. 순위는 **방치 비용 ×
        경과 시간** 두 축이다 — 위험한 대기열이 우선하되 **낮은 비용의 항목도
        오래되면 올라온다.** Q6·Q7·Q8 이 "영원히 밀리는" 문제에 대한 실질적 대응이
        곱셈이라는 형태 자체에 있다.

        켜지지 않은 단계의 대기열은 여기서도 빠진다 (FR-59).
        """
        visible = {q.id for q in queues_for_stage(stage)}
        items = [
            self._work_item(t)
            for t in ticket_domain.queue(self._conn)
            if QUEUE_BY_SOURCE.get(str(t.source)) in visible
        ]
        items.sort(key=lambda i: i.score, reverse=True)
        return items[:limit]

    def ticket_detail(self, ticket_id: str) -> TicketDetail | None:
        """작업 화면이 필요한 것 — 원문·초안·상태 (FR-45).

        판정 화면과 달리 **상세가 있어야 한다.** 조사와 수정이 필요한 일이라
        목록과 버튼만으로는 처리되지 않는다 (§6.4.4).
        """
        try:
            t = ticket_domain.get(self._conn, ticket_id)
        except ticket_domain.TicketNotFound:
            return None
        entry = (
            manual_entry.get_entry(self._conn, t.qna_item_id) if t.qna_item_id else None
        )
        return TicketDetail(
            item=self._work_item(t),
            entry=entry,
            resolution=resolution_domain.get(self._conn, ticket_id),
            question=self._origin_question(t.qna_item_id),
        )

    def _origin_question(self, qna_item_id: str | None) -> str | None:
        """유입 원문. 모 시스템을 거쳐 온 건에만 있다."""
        if not qna_item_id:
            return None
        row = self._conn.execute(
            "SELECT r.body FROM qna_item q "
            "JOIN raw_question r ON r.id = q.parent_question_id WHERE q.id = ?",
            (qna_item_id,),
        ).fetchone()
        return row["body"] if row else None

    def _work_item(self, t: ticket_domain.Ticket) -> WorkItem:
        queue_id = QUEUE_BY_SOURCE.get(str(t.source), "Q1")
        return WorkItem(ticket=t, queue=QUEUES[queue_id], age_hours=t.age())

    # --- Q8 --------------------------------------------------------------

    def knowledge_gaps(self) -> list[dict]:
        """미해결로 종료된 QnA 가 가리키는 영역 (§6.2).

        **아직 비어 있다.** 미해결 종료 판정은 추적 상태를 다루는 WBS-4.5.4 의 몫이고,
        그 판정 없이는 이 대기열에 넣을 것이 생기지 않는다. 화면에는 그 사실을
        적어 둔다 — 빈 목록과 "아직 만들지 않았다"는 다르다.
        """
        rows = self._conn.execute(
            "SELECT id, parent_question_id FROM qna_item WHERE state = '미해결종료'"
        ).fetchall()
        return [dict(r) for r in rows]


def build(knowledge_dir: Path, conn: sqlite3.Connection) -> Dashboard:
    return Dashboard(repo=KnowledgeRepository(knowledge_dir), conn=conn)
