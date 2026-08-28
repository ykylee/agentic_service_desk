"""QnA 추적 — 게재는 끝이 아니다 (FR-29~32, D8, D9, §6.1~6.3).

**QnA 항목은 답변 1회로 종료되지 않는다.** 해결로 처리될 때까지 추적되는 상태를
가진 엔터티이며, 이것이 이 시스템을 "질문 하나에 답 하나"를 반환하는 봇과 구분한다.

여기서 하는 일은 셋이다.

    ① 후속이 달리면 파이프라인을 **다시 돌린다** (FR-29)
    ② 조용해진 건을 **등급과 함께 닫는다** (FR-30)
    ③ 암묵적 해결을 사람이 확인하면 **명시적으로 올린다** (FR-32)

## 재실행은 이전 답변까지 입력에 넣는다

§6.2 가 그렇게 정했다. 후속만 넘기면 "그 메뉴가 안 보이는데요?" 같은 말이 **무엇에
대한 이의인지 모르는 채로** 검색에 걸리고, 그러면 엉뚱한 근거를 찾아 온다.

## 마지막 사건이 무엇이냐가 암묵과 미해결을 가른다

타임아웃이 왔을 때 그 건을 해결로 볼지 미해결로 볼지는 **누가 마지막으로 말했는가**로
정한다.

    마지막이 답변이다        → 해결(암묵) — 답이 나갔고 그 뒤로 조용하다
    마지막이 질문·후속이다   → 미해결 종료 — 물었는데 답이 못 갔다

암묵을 해결로 세되 **등급을 낮게 두는 것**이 §5.3.1 의 요지다. 만족해서 조용한 것과
포기하고 떠난 것은 데이터상 같은 모양이라, 그 신호에 지식의 자격을 줄 수는 없다.
미해결 종료는 버려지는 것이 아니라 **지식 공백의 신호**로 Q8 에 남는다 (§6.2).

## 타임아웃 기간은 아직 정해지지 않았다 (O18)

실데이터 없이 정할 수 없는 임계값이라 설정으로 뺐다. **기본값은 넉넉한 쪽**이다 —
짧게 잡으면 아직 읽지도 않은 답변이 암묵적 해결로 닫히고, 그 등급은 상향은 되어도
사건 자체는 되돌아오지 않는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agentic_service_desk.operations import intake, qna_state
from agentic_service_desk.pipeline import draft_store
from agentic_service_desk.pipeline.answer import AnswerPipeline
from agentic_service_desk.pipeline.review import Reviewer


@dataclass(frozen=True)
class Rerun:
    """후속 때문에 다시 돈 처리 하나."""

    qna_item_id: str
    parent_question_id: str
    ticket_id: str
    draft_id: str | None


@dataclass
class RerunReport:
    reruns: list[Rerun] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.reruns or self.failures)


@dataclass(frozen=True)
class Settled:
    """타임아웃으로 닫힌 건 하나."""

    qna_item_id: str
    parent_question_id: str | None
    state: str
    grade: str | None

    @property
    def is_gap(self) -> bool:
        """지식 공백인가 (§6.2). Q8 이 이것을 본다."""
        return self.state == qna_state.UNRESOLVED_CLOSED


@dataclass
class SettleReport:
    settled: list[Settled] = field(default_factory=list)

    @property
    def implicit(self) -> int:
        return sum(1 for s in self.settled if not s.is_gap)

    @property
    def gaps(self) -> int:
        return sum(1 for s in self.settled if s.is_gap)

    @property
    def changed(self) -> bool:
        return bool(self.settled)


# --- ① 후속 재실행 (FR-29, D9) ----------------------------------------------


def awaiting_rerun(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """후속이 달렸는데 아직 답하지 못한 QnA.

    판정은 **시각 비교 하나**다: 가장 늦은 후속이 가장 늦은 답변보다 뒤면 아직
    답하지 않은 것이다. 답변이 아예 없으면 그 자체로 대상이다.

    **이미 초안이 대기 중이면 뺀다.** 빼지 않으면 사람이 Q2 를 처리할 때까지 매
    주기 새 초안이 쌓이고, 그러면 검수 대기열이 같은 질문으로 채워져 **다른 질문이
    영원히 밀린다.** 초안을 만드는 데 드는 LLM 호출도 그만큼 버려진다.

    종점(해결·미해결종료)에 닿은 건도 뺀다 — 같은 질문이 또 오면 그것은 새 질문이다.
    """
    return conn.execute(
        """
        SELECT i.id AS qna_item_id, i.parent_question_id, q.body AS question,
               (SELECT max(f.created_at) FROM raw_followup f
                 WHERE f.question_id = i.parent_question_id) AS last_followup,
               (SELECT max(a.created_at) FROM raw_answer a
                 WHERE a.question_id = i.parent_question_id) AS last_answer
        FROM qna_item i
        JOIN raw_question q ON q.id = i.parent_question_id
        WHERE i.state NOT IN (?, ?)
          AND last_followup IS NOT NULL
          AND (last_answer IS NULL OR last_followup > last_answer)
          AND NOT EXISTS (
                SELECT 1 FROM answer_draft d
                 WHERE d.qna_item_id = i.id AND d.state = ?
          )
        ORDER BY last_followup
        """,
        (qna_state.RESOLVED, qna_state.UNRESOLVED_CLOSED, draft_store.PENDING),
    ).fetchall()


def build_rerun_input(conn: sqlite3.Connection, parent_question_id: str) -> str:
    """재실행의 1단계 입력 — **원 질문 + 이전 답변 + 후속** (§6.2).

    후속만 넘기면 "그 메뉴가 안 보이는데요?" 가 무엇에 대한 이의인지 모르는 채로
    검색에 걸린다. 이전 답변을 함께 넣는 것은 §6.2 가 정한 것이고, 그래야 **이미
    말한 것을 또 말하지 않는다.**
    """
    question = conn.execute(
        "SELECT body FROM raw_question WHERE id = ?", (parent_question_id,)
    ).fetchone()
    answers = conn.execute(
        "SELECT body FROM raw_answer WHERE question_id = ? ORDER BY created_at, id",
        (parent_question_id,),
    ).fetchall()
    followups = conn.execute(
        "SELECT body FROM raw_followup WHERE question_id = ? ORDER BY created_at, id",
        (parent_question_id,),
    ).fetchall()

    parts = [question["body"] if question else ""]
    if answers:
        parts.append("[이전 답변]\n" + "\n\n".join(a["body"] for a in answers))
    if followups:
        parts.append("[후속]\n" + "\n\n".join(f["body"] for f in followups))
    return "\n\n".join(p for p in parts if p.strip())


def rerun(
    conn: sqlite3.Connection,
    *,
    pipeline: AnswerPipeline | None = None,
    reviewer: Reviewer | None = None,
    gate: intake.Gate | None = None,
) -> RerunReport:
    """후속이 달린 건을 다시 돌린다.

    **처리 하나마다 티켓이 하나 더 발행된다** — 재실행은 새로운 처리이지 지난
    처리의 연장이 아니다 (FR-56). 그래서 `intake.process()` 를 그대로 쓴다:
    갈라 두면 한쪽에만 규칙이 붙어 통계가 경로마다 달라진다.
    """
    report = RerunReport()
    for row in awaiting_rerun(conn):
        try:
            conn.execute(
                "UPDATE qna_item SET state = ? WHERE id = ?",
                (qna_state.FOLLOWUP, row["qna_item_id"]),
            )
            conn.commit()
            processed = intake.process(
                conn,
                qna_item_id=row["qna_item_id"],
                question=build_rerun_input(conn, row["parent_question_id"]),
                pipeline=pipeline,
                reviewer=reviewer,
                gate=gate,
            )
        except Exception as exc:  # noqa: BLE001 — 한 건이 주기를 세우지 않는다
            report.failures.append(f"{row['parent_question_id']}: {exc}")
            continue
        report.reruns.append(
            Rerun(
                qna_item_id=row["qna_item_id"],
                parent_question_id=row["parent_question_id"],
                ticket_id=processed.ticket_id,
                draft_id=processed.draft_id,
            )
        )
    return report


# --- ② 조용해진 건을 닫는다 (FR-30, O18) --------------------------------------


def settle_quiet(
    conn: sqlite3.Connection,
    *,
    quiet_hours: int,
    now: datetime | None = None,
) -> SettleReport:
    """일정 기간 아무 일도 없던 건을 **등급과 함께** 닫는다.

    닫지 않으면 QnA 항목이 영원히 열려 있어 **모든 비율의 분모가 계속 자란다** —
    해결률이 실제 품질과 무관하게 떨어져 보인다 (O18).

    **명시적으로 해결된 건은 손대지 않는다.** 그것은 이미 종점이고, 여기서 다시
    판정하면 우리 타임아웃이 사람의 확인을 덮어쓴다 — 강등은 없다 (§5.3.2).

    **검수를 기다리는 초안이 있으면 닫지 않는다.** 답은 이미 만들어졌고 나가기
    직전이다 — 그것을 미해결로 닫으면 사람이 Q2 에서 승인하려는 순간 그 QnA 는
    이미 닫혀 있다.

    **열린 티켓은 이유가 되지 않는다.** 티켓과 QnA 항목은 축이 다르기 때문이다
    (D15): 티켓은 *우리에게 무슨 일이 남았는가*이고 QnA 항목은 *이용자에게 이
    질문이 어떻게 되었는가*다. 파이프라인이 답을 못 만들어 Q1 에 걸린 건은 **정말로
    지식 공백**이며(§6.2), 우리 대기열이 밀렸다는 이유로 그 사실을 지우면 Q8 이
    가려야 할 것을 못 가린다. 밀린 작업은 대기열의 경과 시간이 드러낸다.
    """
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(hours=quiet_hours)).isoformat()
    report = SettleReport()

    rows = conn.execute(
        """
        SELECT i.id AS qna_item_id, i.parent_question_id,
               (SELECT max(a.created_at) FROM raw_answer a
                 WHERE a.question_id = i.parent_question_id) AS last_answer,
               (SELECT max(f.created_at) FROM raw_followup f
                 WHERE f.question_id = i.parent_question_id) AS last_followup,
               q.created_at AS asked_at
        FROM qna_item i
        JOIN raw_question q ON q.id = i.parent_question_id
        LEFT JOIN raw_resolution r ON r.question_id = i.parent_question_id
        WHERE i.state NOT IN (?, ?)
          AND (r.grade IS NULL OR r.grade != ?)
          AND NOT EXISTS (
                SELECT 1 FROM answer_draft d
                 WHERE d.qna_item_id = i.id AND d.state = ?
          )
        ORDER BY q.created_at
        """,
        (
            qna_state.RESOLVED,
            qna_state.UNRESOLVED_CLOSED,
            qna_state.EXPLICIT,
            draft_store.PENDING,
        ),
    ).fetchall()

    for row in rows:
        last_answer = row["last_answer"]
        last_word = max(
            v for v in (row["asked_at"], last_answer, row["last_followup"]) if v
        )
        if last_word > cutoff:
            continue

        # **마지막으로 말한 쪽이 등급을 정한다.** 답이 나간 뒤의 침묵과 물음 뒤의
        # 침묵은 같은 침묵이 아니다.
        answered_last = last_answer is not None and last_answer == last_word
        state = qna_state.RESOLVED if answered_last else qna_state.UNRESOLVED_CLOSED
        grade = qna_state.IMPLICIT if answered_last else None
        conn.execute(
            "UPDATE qna_item SET state = ?, resolution_grade = ?, closed_at = ? "
            "WHERE id = ?",
            (state, grade, now.isoformat(), row["qna_item_id"]),
        )
        report.settled.append(
            Settled(
                qna_item_id=row["qna_item_id"],
                parent_question_id=row["parent_question_id"],
                state=state,
                grade=grade,
            )
        )
    conn.commit()
    return report


def adopt_explicit(conn: sqlite3.Connection) -> list[str]:
    """모 시스템이 알려준 **명시적 해결**을 추적 상태에 반영한다 (D35).

    수집은 `raw_resolution` 에 사실만 담는다 — 그것은 *모 시스템이 알려준 것*이고,
    `qna_item` 은 *우리가 추적하는 상태*라 표가 다르다. 여기가 둘을 잇는 자리다.

    **이미 닫힌 건도 올린다.** 암묵으로 닫아 둔 뒤에 이용자가 해결 표시를 누를 수
    있고, 그때 등급이 올라가야 **ingest 자격이 열린다** (§5.3.2, FR-32).
    """
    rows = conn.execute(
        "SELECT i.id, i.parent_question_id FROM qna_item i "
        "JOIN raw_resolution r ON r.question_id = i.parent_question_id "
        "WHERE r.grade = ? AND (i.resolution_grade IS NULL OR i.resolution_grade != ?)",
        (qna_state.EXPLICIT, qna_state.EXPLICIT),
    ).fetchall()
    now = datetime.now(UTC).isoformat()
    for row in rows:
        conn.execute(
            "UPDATE qna_item SET state = ?, resolution_grade = ?, "
            "closed_at = COALESCE(closed_at, ?) WHERE id = ?",
            (qna_state.RESOLVED, qna_state.EXPLICIT, now, row["id"]),
        )
    conn.commit()
    return [r["id"] for r in rows]


# --- ③ 암묵 → 명시 상향 (FR-32, Q6) ------------------------------------------


def awaiting_confirmation(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Q6 — 사람 확인 없이 닫힌 QnA (§5.3.2).

    **방치해도 사고가 나지 않아서 영원히 밀리는 대기열이다**(§8.2). 그래서 화면이
    무엇을 얻는지 말해 줘야 한다: 여기서 하나를 확인하면 그 봇 답변에 **ingest
    자격이 생기고 지식베이스가 자란다.**
    """
    return conn.execute(
        "SELECT i.id, i.parent_question_id, i.closed_at, q.body AS question, "
        "       (SELECT a.body FROM raw_answer a "
        "         WHERE a.question_id = i.parent_question_id "
        "         ORDER BY a.created_at DESC, a.id DESC LIMIT 1) AS answer "
        "FROM qna_item i "
        "JOIN raw_question q ON q.id = i.parent_question_id "
        "WHERE i.resolution_grade = ? ORDER BY i.closed_at",
        (qna_state.IMPLICIT,),
    ).fetchall()


def upgrade(conn: sqlite3.Connection, qna_item_id: str) -> bool:
    """운영자가 확인해 **명시적으로 올린다** (FR-32, §5.3.1-1).

    "운영자의 확인 종결"은 §5.3.1-1 이 꼽은 명시적 해결 신호 둘 중 하나이며, 모
    시스템이 아니라 **이 시스템에서 나온다.** 그래서 `raw_resolution` 이 아니라
    `qna_item` 에 적는다 — 저쪽에 쓰면 우리 판정이 모 시스템의 원문을 덮어쓴다.
    산출물 필터가 두 곳을 함께 보는 것이 그 대가다.

    **강등은 없다.** 이미 명시적인 건에 다시 부르면 아무 일도 일어나지 않는다 —
    한 번 사람이 확인한 사실은 시간이 지나도 확인된 사실이다 (§5.3.2).
    """
    row = conn.execute(
        "SELECT resolution_grade FROM qna_item WHERE id = ?", (qna_item_id,)
    ).fetchone()
    if row is None or row["resolution_grade"] != qna_state.IMPLICIT:
        return False
    conn.execute(
        "UPDATE qna_item SET state = ?, resolution_grade = ?, "
        "closed_at = COALESCE(closed_at, ?) WHERE id = ?",
        (
            qna_state.RESOLVED,
            qna_state.EXPLICIT,
            datetime.now(UTC).isoformat(),
            qna_item_id,
        ),
    )
    conn.commit()
    return True


# --- 등급별 분리 집계 (FR-30 확인, §6.3) --------------------------------------


@dataclass(frozen=True)
class Grades:
    """등급별 집계. **하나의 숫자로 뭉치면 실제 품질이 가려진다** (§6.3)."""

    total: int
    explicit: int
    implicit: int
    unresolved: int
    open_items: int

    @property
    def explicit_rate(self) -> float:
        """**진짜 품질 지표.** 이것이 올라가야 시스템이 나아진 것이다."""
        return self.explicit / self.total if self.total else 0.0

    @property
    def implicit_rate(self) -> float:
        """**경고 지표.** 단독으로는 좋고 나쁨을 말할 수 없다 — "잘 해결됐는데 표시를
        안 한 것"과 "포기하고 떠난 것"을 함께 담기 때문이다. 명시적 해결률과 함께
        읽어야 하며, **둘이 같이 오르면 유입이 는 것이고 암묵만 오르면 품질이
        의심되는 상황**이다 (§6.3)."""
        return self.implicit / self.total if self.total else 0.0

    @property
    def unresolved_rate(self) -> float:
        """지식 공백의 크기."""
        return self.unresolved / self.total if self.total else 0.0


def grades(conn: sqlite3.Connection) -> Grades:
    """등급별로 나눠 센다 (FR-30 확인 조건)."""
    rows = dict(
        conn.execute(
            "SELECT COALESCE(resolution_grade, state), count(*) FROM qna_item "
            "GROUP BY COALESCE(resolution_grade, state)"
        ).fetchall()
    )
    explicit = rows.get(qna_state.EXPLICIT, 0)
    implicit = rows.get(qna_state.IMPLICIT, 0)
    unresolved = rows.get(qna_state.UNRESOLVED_CLOSED, 0)
    total = conn.execute("SELECT count(*) c FROM qna_item").fetchone()["c"]
    return Grades(
        total=total,
        explicit=explicit,
        implicit=implicit,
        unresolved=unresolved,
        open_items=total - explicit - implicit - unresolved,
    )
