"""표본 재검증 — 승인이 실질이었는지 사후에 묻는다 (WBS-4.8.4, FR-50, §5.6.7).

§5.6 이 경계한 고장은 **검증 라벨만 붙는 것**이다. 대기열이 밀리면 초안을 읽지 않고
승인하게 되고, 그러면 검증되지 않은 것이 "사람이 검증함"으로 기록되어 지식이 된다.
대책 셋(강제 입력 지점 · 불확실성 표시 · 부하 관측)은 **일어나기 전에** 막는 장치이고,
여기 있는 것은 **일어난 뒤에 재는** 유일한 장치다.

§1.3.3-b 의 일치율과 대칭이며 방향만 반대다 — 그쪽은 **자동을 믿어도 되는가**를 묻고,
이쪽은 **사람 승인이 실질이었는가**를 묻는다.

## 대기열을 아홉 개로 만들지 않는다

재검증은 **처리해야 할 일이 아니라 감사**다. Q1~Q8 옆에 세우면 밀린 대기열을 비우는
손이 여기까지 와서, **이 장치가 재려던 바로 그 형식적 승인**이 재검증에서 일어난다.
그러면 일치율은 100% 로 수렴하고 아무것도 재지 못한다.

그래서 화면을 따로 두고(`/recheck`) 현황에서 연다. 국면 전진 승인을 대기열이 아니라
현황 화면의 알림으로 둔 것과 같은 자리다 — 빈도가 낮고, 밀려도 즉시 사고가 나지 않으며,
**대기열의 우선순위 싸움에 넣으면 성격이 바뀌는** 일이다.

## 사람이 본 적 없는 것도 대상이다

자동 승격분(`promoted_by='gate'`)은 사람이 한 번도 보지 않았으므로 재검증에서
**우선순위가 가장 높다** (§6.8.4-a). 여기서 "원래 판정"은 사람의 클릭이 아니라
관문의 통과다 — 물음이 "그 클릭이 실질이었는가"에서 "그 통과가 옳았는가"로 바뀔 뿐
재는 것은 같다.

## 표본은 결정적으로 뽑고, 한 번 뽑은 것은 다시 뽑지 않는다

배치가 하루에도 여러 번 도는데 매번 새로 뽑으면 목록이 볼 때마다 달라져 **사람이
집어 든 건이 사라진다.** 뽑은 것은 표에 남고(`recheck`), 같은 건은 두 번 뽑히지 않는다 —
같은 건을 다시 보면 일치율의 분모만 부풀고 새로 아는 것은 없다.

크기와 주기는 **O50** 이다. 실데이터로 다시 정할 값이지만 **비워 두지 않았다**:
0 으로 두면 장치가 있는데 한 번도 돌지 않고, 그것은 없는 것과 화면에서 구분되지 않는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agentic_service_desk.operations import qna_state

# --- 지점과 위험도 (§5.6.2) ---------------------------------------------------

AUTO_PROMOTION = "auto_promotion"
"""Q7 자동 승격 — **사람이 본 적이 없다** (§6.8.4-a). 가장 먼저 뽑는다."""

PROMOTION = "promotion"
"""Q7 승격 승인 — 클릭 하나가 봇 답변을 지식으로 만든다."""

UPGRADE = "upgrade"
"""Q6 암묵→명시 상향 — 클릭 하나가 **ingest 자격**을 만든다 (§5.3.1)."""

RESOLUTION = "resolution"
"""종결 기록 승인 — 초안이 승격 재료로 확정된다 (§6.5.4)."""

ANSWER_REVIEW = "answer_review"
"""Q2 답변 검수 승인 — 게재된다."""

CONTENT_REVIEW = "content_review"
"""Q3 콘텐츠 검수 승인 — **발행물은 회수할 수 없다** (§7.3)."""

RISK: dict[str, int] = {
    AUTO_PROMOTION: 0,
    PROMOTION: 1,
    UPGRADE: 1,
    RESOLUTION: 2,
    ANSWER_REVIEW: 3,
    CONTENT_REVIEW: 3,
}
"""**낮을수록 먼저 뽑는다** (§5.6.2 의 위험도 표 그대로).

Q6·Q7 이 가장 위험한 이유는 그 클릭이 *중간 산출물*이 아니라 **지식베이스에 직접
들어가는 자격**을 만들기 때문이다. 자동 승격이 그보다도 앞인 것은 §6.8.4-a 다 —
같은 자격이 **사람 없이** 생겼다.
"""

LABELS = {
    AUTO_PROMOTION: "Q7 자동 승격 — 사람이 본 적이 없다",
    PROMOTION: "Q7 승격 승인",
    UPGRADE: "Q6 암묵적 해결 상향",
    RESOLUTION: "종결 기록 승인",
    ANSWER_REVIEW: "Q2 답변 검수 승인",
    CONTENT_REVIEW: "Q3 콘텐츠 검수 승인",
}

PENDING = "pending"
AGREED = "agreed"
DISAGREED = "disagreed"


@dataclass(frozen=True)
class Sample:
    """다시 볼 한 건."""

    id: str
    point: str
    subject_id: str
    original_by: str
    original_at: str
    selected_at: str
    state: str = PENDING
    note: str = ""
    decided_at: str | None = None

    @property
    def label(self) -> str:
        return LABELS.get(self.point, self.point)

    @property
    def risk(self) -> int:
        return RISK.get(self.point, 9)

    @property
    def unseen(self) -> bool:
        """사람이 본 적 없이 나간 건인가 (§6.8.4-a)."""
        return self.point == AUTO_PROMOTION


@dataclass(frozen=True)
class Candidate:
    point: str
    subject_id: str
    original_by: str
    original_at: str


class NotPending(RuntimeError):
    """이미 판정한 표본이다. **다시 누르면 일치율이 조용히 달라진다.**"""


# --- 뽑기 --------------------------------------------------------------------


def due(conn: sqlite3.Connection, *, period_days: int, now: datetime | None = None) -> bool:
    """이번 주기에 뽑을 때가 되었는가.

    **마지막으로 뽑은 시각으로 잰다.** 배치 주기(분 단위)로 뽑으면 표본이 순식간에
    쌓여 재검증이 이중 작업이 되고, 그러면 사람이 이 화면을 아예 닫는다.
    """
    if period_days <= 0:
        return False
    row = conn.execute("SELECT max(selected_at) AS at FROM recheck").fetchone()
    if row is None or not row["at"]:
        return True
    moment = now or datetime.now(UTC)
    try:
        last = datetime.fromisoformat(row["at"])
    except (TypeError, ValueError):
        return True
    return moment - last >= timedelta(days=period_days)


def candidates(conn: sqlite3.Connection) -> list[Candidate]:
    """아직 뽑히지 않은 승인 건을 **위험도 순으로** 모은다.

    각 지점의 SSOT 를 그대로 읽는다 — 승인 이력을 따로 한 표에 복사해 두면 두 벌이
    어긋나고, 어긋난 쪽을 재검증하는 순간 이 장치는 거짓을 재게 된다.
    """
    found: list[Candidate] = []
    found += _promotions(conn)
    found += _upgrades(conn)
    found += _resolutions(conn)
    found += _reviews(conn)

    taken = {
        (r["point"], r["subject_id"])
        for r in conn.execute("SELECT point, subject_id FROM recheck").fetchall()
    }
    fresh = [c for c in found if (c.point, c.subject_id) not in taken]
    # 위험도가 먼저, 같으면 **최근 것부터**. 오래된 것부터 보면 지금의 승인 습관이
    # 아니라 몇 달 전의 습관을 재게 된다. 파이썬 정렬이 안정적이라 두 번 부른다.
    fresh.sort(key=lambda c: c.original_at, reverse=True)
    fresh.sort(key=lambda c: RISK.get(c.point, 9))
    return fresh


def select(
    conn: sqlite3.Connection, *, size: int, now: datetime | None = None
) -> list[Sample]:
    """표본을 뽑아 남긴다. **뽑은 것은 표에 남는다** — 목록이 볼 때마다 달라지지 않게."""
    if size <= 0:
        return []
    at = (now or datetime.now(UTC)).isoformat()
    taken: list[Sample] = []
    for candidate in candidates(conn)[:size]:
        sample_id = f"rc-{candidate.point}-{candidate.subject_id}"
        conn.execute(
            "INSERT OR IGNORE INTO recheck "
            "(id, point, subject_id, original_by, original_at, selected_at, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                sample_id,
                candidate.point,
                candidate.subject_id,
                candidate.original_by,
                candidate.original_at,
                at,
                PENDING,
            ),
        )
        taken.append(
            Sample(
                id=sample_id,
                point=candidate.point,
                subject_id=candidate.subject_id,
                original_by=candidate.original_by,
                original_at=candidate.original_at,
                selected_at=at,
            )
        )
    conn.commit()
    return taken


def _promotions(conn: sqlite3.Connection) -> list[Candidate]:
    rows = conn.execute(
        "SELECT ticket_id, promoted_by, COALESCE(confirmed_at, '') AS at "
        "FROM ticket_resolution WHERE promoted_item_id IS NOT NULL"
    ).fetchall()
    return [
        Candidate(
            point=AUTO_PROMOTION if row["promoted_by"] == "gate" else PROMOTION,
            subject_id=row["ticket_id"],
            original_by=row["promoted_by"] or "unknown",
            original_at=row["at"],
        )
        for row in rows
    ]


def _upgrades(conn: sqlite3.Connection) -> list[Candidate]:
    """Q6 상향. **`upgraded_at` 이 없으면 뽑을 수 없다** — 클릭이 남지 않은 건이다.

    모 시스템의 해결 표시로 명시적이 된 건은 여기 오지 않는다: 그것은 우리의 판정이
    아니라 이용자의 표시라 재검증할 클릭이 없다.
    """
    rows = conn.execute(
        "SELECT id, upgraded_at FROM qna_item "
        "WHERE upgraded_at IS NOT NULL AND resolution_grade = ?",
        (qna_state.EXPLICIT,),
    ).fetchall()
    return [
        Candidate(
            point=UPGRADE,
            subject_id=row["id"],
            original_by="human",
            original_at=row["upgraded_at"],
        )
        for row in rows
    ]


def _resolutions(conn: sqlite3.Connection) -> list[Candidate]:
    """종결 기록 승인 — **무효화 조건을 채운 것이 곧 승인**이다 (§5.6.4).

    승격까지 간 건은 빼 둔다. 같은 티켓이 두 지점에서 뽑히면 한 번의 재검토가
    분모를 둘 늘리는데, 그러면 일치율이 **본 만큼이 아니라 센 만큼** 좋아진다.
    """
    rows = conn.execute(
        "SELECT ticket_id, confirmed_at FROM ticket_resolution "
        "WHERE confirmed_at IS NOT NULL AND promoted_item_id IS NULL"
    ).fetchall()
    return [
        Candidate(
            point=RESOLUTION,
            subject_id=row["ticket_id"],
            original_by="human",
            original_at=row["confirmed_at"],
        )
        for row in rows
    ]


def _reviews(conn: sqlite3.Connection) -> list[Candidate]:
    """Q2·Q3 의 **사람 승인**만. 반려는 뽑지 않는다.

    §5.6 이 재려는 것은 "읽지 않고 통과시켰는가"다 — 반려에는 사유가 붙고(§5.5.6)
    사유를 쓰려면 읽어야 하므로, 형식적으로 일어나기 어려운 판정이다.
    """
    rows = conn.execute(
        "SELECT id, kind, reviewed_at FROM review "
        "WHERE reviewed_by = 'human' AND outcome = 'passed'"
    ).fetchall()
    return [
        Candidate(
            point=ANSWER_REVIEW if row["kind"] == "answer" else CONTENT_REVIEW,
            subject_id=row["id"],
            original_by="human",
            original_at=row["reviewed_at"],
        )
        for row in rows
    ]


# --- 판정과 셈 ---------------------------------------------------------------


def pending(conn: sqlite3.Connection) -> list[Sample]:
    """아직 다시 보지 않은 표본. **위험한 것부터.**"""
    rows = conn.execute(
        "SELECT * FROM recheck WHERE state = ? ORDER BY selected_at DESC", (PENDING,)
    ).fetchall()
    samples = [_of(row) for row in rows]
    samples.sort(key=lambda s: (s.risk, s.selected_at))
    return samples


def decided(conn: sqlite3.Connection, *, limit: int = 20) -> list[Sample]:
    rows = conn.execute(
        "SELECT * FROM recheck WHERE state <> ? ORDER BY decided_at DESC LIMIT ?",
        (PENDING, limit),
    ).fetchall()
    return [_of(row) for row in rows]


def decide(conn: sqlite3.Connection, sample_id: str, *, agreed: bool, note: str = "") -> Sample:
    """다시 본 결과를 남긴다.

    **다르다면 사유를 받는다.** 사유 없는 불일치는 기록으로 쓸 수 없다 — Q2 의 반려가
    사유를 요구하는 것과 같은 이유이고, 여기서는 그 사유가 **무엇을 고쳐야 하는지**를
    가리키는 유일한 재료다.

    **한 번 판정한 표본은 다시 누를 수 없다.** 뒤집을 수 있게 두면 일치율이 조용히
    달라지는데, 이 숫자는 "그때 그 승인이 실질이었는가"의 기록이지 현재 의견이 아니다.
    """
    row = conn.execute("SELECT * FROM recheck WHERE id = ?", (sample_id,)).fetchone()
    if row is None:
        raise NotPending(f"없는 표본이다: {sample_id}")
    if row["state"] != PENDING:
        raise NotPending(f"이미 판정한 표본이다: {sample_id} ({row['state']})")
    if not agreed and not note.strip():
        raise NotPending("다르다면 사유가 필요하다 — 사유 없는 불일치는 기록으로 쓸 수 없다")

    at = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE recheck SET state = ?, note = ?, decided_at = ? WHERE id = ?",
        (AGREED if agreed else DISAGREED, note.strip(), at, sample_id),
    )
    conn.commit()
    return _of(conn.execute("SELECT * FROM recheck WHERE id = ?", (sample_id,)).fetchone())


def context(conn: sqlite3.Connection, sample: Sample) -> list[tuple[str, str]]:
    """다시 보려면 무엇을 읽어야 하는가.

    **원래 판정만 보여 주면 재검증이 아니라 확인이 된다.** "사람이 승인했다"는 사실을
    다시 읽고 누르는 것은 아무것도 재지 않는다 — 그때 무엇을 보고 눌렀는지, 즉 질문과
    답변과 근거가 함께 있어야 판정을 **다시 할 수** 있다.
    """
    if sample.point in (AUTO_PROMOTION, PROMOTION, RESOLUTION):
        row = conn.execute(
            "SELECT generalized_question, answer, invalidation, grounding, "
            "       promoted_item_id FROM ticket_resolution WHERE ticket_id = ?",
            (sample.subject_id,),
        ).fetchone()
        if row is None:
            return [("없다", "종결 기록이 지워졌다")]
        return [
            ("일반화된 질문", row["generalized_question"]),
            ("답", row["answer"]),
            ("근거", row["grounding"]),
            ("무효화 조건", row["invalidation"] or "**비어 있다**"),
            ("승격된 지식 항목", row["promoted_item_id"] or "—"),
        ]

    if sample.point == UPGRADE:
        row = conn.execute(
            "SELECT i.parent_question_id, q.body AS question, "
            "       (SELECT a.body FROM raw_answer a "
            "         WHERE a.question_id = i.parent_question_id "
            "         ORDER BY a.created_at DESC, a.id DESC LIMIT 1) AS answer "
            "FROM qna_item i LEFT JOIN raw_question q ON q.id = i.parent_question_id "
            "WHERE i.id = ?",
            (sample.subject_id,),
        ).fetchone()
        if row is None:
            return [("없다", "QnA 항목이 지워졌다")]
        return [
            ("질문", row["question"] or "—"),
            ("답변", row["answer"] or "—"),
            (
                "물음",
                "이 침묵을 **해결로 읽은 것이 옳았는가**. 상향은 그 봇 답변에 "
                "ingest 자격을 준다 (§5.3.1)",
            ),
        ]

    row = conn.execute(
        "SELECT draft_body, grounding, reason FROM review WHERE id = ?",
        (sample.subject_id,),
    ).fetchone()
    if row is None:
        return [("없다", "검수 기록이 지워졌다")]
    return [
        ("통과시킨 본문", row["draft_body"]),
        ("근거", row["grounding"]),
    ]


@dataclass(frozen=True)
class Agreement:
    """일치율과 그 분모. **분모가 0 이면 0% 가 아니라 없음이다.**"""

    agreed: int = 0
    disagreed: int = 0
    waiting: int = 0

    @property
    def decided(self) -> int:
        return self.agreed + self.disagreed

    @property
    def rate(self) -> float | None:
        return self.agreed / self.decided if self.decided else None

    @property
    def percent(self) -> str:
        return "—" if self.rate is None else f"{self.rate * 100:.0f}%"


def agreement(conn: sqlite3.Connection) -> Agreement:
    """FR-50 의 "표본 재검증 일치율".

    **뽑기만 하고 보지 않은 것은 분모가 아니다.** 대기 중인 표본을 분모에 넣으면
    재검증이 밀릴수록 일치율이 떨어지는데, 그것은 승인 품질이 아니라 부하의 신호다 —
    §1.3.3-b 가 "양쪽 판정이 다 있는 건만 분모"라고 정한 것과 같은 자리다.
    """
    counts = {
        row["state"]: row["c"]
        for row in conn.execute(
            "SELECT state, count(*) AS c FROM recheck GROUP BY state"
        ).fetchall()
    }
    return Agreement(
        agreed=counts.get(AGREED, 0),
        disagreed=counts.get(DISAGREED, 0),
        waiting=counts.get(PENDING, 0),
    )


def _of(row: sqlite3.Row) -> Sample:
    return Sample(
        id=row["id"],
        point=row["point"],
        subject_id=row["subject_id"],
        original_by=row["original_by"],
        original_at=row["original_at"],
        selected_at=row["selected_at"],
        state=row["state"],
        note=row["note"] or "",
        decided_at=row["decided_at"],
    )
