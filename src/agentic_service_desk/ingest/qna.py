"""QnA 이력 수집 (FR-52, NFR-7).

원천이 둘인데(D2) 지금까지 수집되는 것은 소스 저장소뿐이었다. **여기가 나머지 절반이다.**

수집이 지키는 것은 셋이다.

**두 필드를 반드시 함께 보관한다.** 답변의 **작성자 계정**과 해결의 **등급**.
전자가 없으면 봇과 사람을 가릴 수 없고(D7), 후자가 없으면 봇 답변의 ingest 배제를
언제 풀지 알 수 없다(D8). 둘 중 하나만 빠져도 §5.3 되먹임 차단이 성립하지 않는다.

**걸러내지 않는다.** Raw Layer 는 수집된 그대로를 담는다. 봇이 쓴 미해결 답변까지
적재한다 — 지식으로 삼지 않는 것과 기록하지 않는 것은 다르며, 여기서 미리 버리면
**통계와 FAQ 후보까지 함께 사라진다**(§5.3). 거르는 자리는 ingest 입구 하나뿐이다(NFR-4).

**새 질문만 보지 않는다.** 어제 들어온 질문에 오늘 후속이 달리고 오늘 해결 표시가
눌린다. 커서 이후의 질문만 가져오면 그 변화를 영영 못 본다 — 그러면 **추적(§6.2)이
성립하지 않고 명시적 해결이 발생해도 ingest 자격이 열리지 않는다.** 그래서 아직
명시적으로 해결되지 않은 저장분을 매 주기 다시 훑는다.
"""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_service_desk.adapters.contract import Answer, Followup, Question, Resolution
from agentic_service_desk.adapters.parent_system import ParentSystem
from agentic_service_desk.operations.checkpoint import QNA, get_cursor, set_cursor


class ResolutionGrade(enum.StrEnum):
    """해결 등급 (D8, §5.3.1). **ingest 자격이 여기서 갈린다.**"""

    EXPLICIT = "explicit"
    """사람이 확인해 종결됐다 — 이용자의 해결 표시, 운영자의 확인 종결.

    검증 신호이므로 봇 답변의 ingest 배제가 **해제된다**.
    """

    IMPLICIT = "implicit"
    """사람의 확인 없이 해결로 간주됐다 — 타임아웃, 에이전트 자체 판단.

    기록·통계·FAQ 후보에는 반영하되 **지식 원천으로 쓰지 않는다**. 만족해서 조용한
    것과 포기하고 떠난 것이 데이터상 같은 모양이라, 구분하지 못하는 신호에 지식의
    자격을 줄 수 없기 때문이다.
    """


def grade_of(resolution: Resolution) -> ResolutionGrade | None:
    """모 시스템이 알려준 해결 표시를 등급으로 옮긴다.

    미해결이면 등급이 없다. 해결이되 **방법이 비어 있으면 암묵으로 본다** —
    누가 어떻게 확인했는지 모르는 해결을 명시적이라고 부를 수는 없다. 애매한 쪽을
    암묵으로 밀어 두는 것이 안전한 방향이다: 등급은 나중에 상향될 수 있지만(§5.3.2)
    잘못 부여된 명시적 해결은 **틀린 지식을 이미 만든 뒤에야** 드러난다.
    """
    if not resolution.resolved:
        return None
    return ResolutionGrade.EXPLICIT if resolution.is_explicit else ResolutionGrade.IMPLICIT


@dataclass(frozen=True)
class CollectionReport:
    """한 주기가 무엇을 했는가. 로그와 지표의 재료다."""

    new_questions: int = 0
    refreshed_questions: int = 0
    answers: int = 0
    followups: int = 0
    upgraded: int = 0
    """암묵 → 명시로 상향된 건수 (§5.3.2). **그 시점에 ingest 자격이 발생한다.**"""

    cursor: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.new_questions or self.answers or self.followups or self.upgraded)


class GradeDowngradeRejected(RuntimeError):
    """명시적 해결을 암묵으로 되돌리려 했다.

    **강등은 없다** (§5.3.2) — 한 번 사람이 확인한 사실은 시간이 지나도 확인된
    사실이다. 조용히 무시하지 않고 예외로 드러내는 이유는, 이 일이 일어났다면
    모 시스템 쪽 데이터가 흔들렸다는 뜻이라 사람이 알아야 하기 때문이다.
    """


class QnaStore:
    """Raw Layer 의 QnA 쪽. **적재만 한다 — 판정하지 않는다.**

    `INSERT ... ON CONFLICT DO UPDATE` 로 멱등하게 쓴다. 폴링은 같은 것을 몇 번이고
    다시 가져오므로(NFR-7), 다시 넣어도 늘지 않아야 한다.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- 적재 ------------------------------------------------------------

    def upsert_question(self, q: Question, *, collected_at: str) -> bool:
        """질문을 넣는다. **처음 본 것이면 True.**"""
        cur = self._conn.execute("SELECT 1 FROM raw_question WHERE id = ?", (q.id,))
        is_new = cur.fetchone() is None
        self._conn.execute(
            "INSERT INTO raw_question (id, title, body, asker_account, created_at, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET title = excluded.title, body = excluded.body",
            (q.id, q.title, q.body, q.asker_account, q.created_at, collected_at),
        )
        return is_new

    def upsert_answer(self, a: Answer, *, collected_at: str) -> bool:
        """답변을 넣는다. **`author_account` 가 비면 거부한다.**

        스키마의 NOT NULL 에 맡기지 않고 여기서 먼저 막는 이유는, 빈 문자열이
        NOT NULL 을 통과하기 때문이다. 계정을 모르는 답변이 들어오면 그 뒤로
        봇/사람 판정이 조용히 틀린다 — 적재 자체를 막는 편이 낫다.
        """
        if not a.author_account:
            raise ValueError(
                f"답변 {a.id} 에 작성자 계정이 없다. "
                "이 필드가 없으면 §5.3 되먹임 차단이 성립하지 않는다 (D7)"
            )
        cur = self._conn.execute("SELECT 1 FROM raw_answer WHERE id = ?", (a.id,))
        is_new = cur.fetchone() is None
        self._conn.execute(
            "INSERT INTO raw_answer "
            "(id, question_id, body, author_account, created_at, revised_at, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET body = excluded.body, revised_at = excluded.revised_at",
            (
                a.id,
                a.question_id,
                a.body,
                a.author_account,
                a.created_at,
                a.revised_at,
                collected_at,
            ),
        )
        return is_new

    def upsert_followup(self, f: Followup, *, collected_at: str) -> bool:
        """후속을 넣는다. **처음 본 것이면 True** — 그것이 파이프라인 재실행의 신호다(D9)."""
        cur = self._conn.execute("SELECT 1 FROM raw_followup WHERE id = ?", (f.id,))
        is_new = cur.fetchone() is None
        self._conn.execute(
            "INSERT INTO raw_followup "
            "(id, question_id, body, author_account, created_at, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET body = excluded.body",
            (f.id, f.question_id, f.body, f.author_account, f.created_at, collected_at),
        )
        return is_new

    def record_resolution(self, r: Resolution, *, collected_at: str) -> bool:
        """해결 표시와 등급을 남긴다. **상향된 경우에만 True.**

        상향만 허용한다 (§5.3.2). 되돌리는 시도는 삼키지 않고 예외로 올린다.
        """
        grade = grade_of(r)
        prev = self._conn.execute(
            "SELECT grade FROM raw_resolution WHERE question_id = ?", (r.question_id,)
        ).fetchone()
        prev_grade = prev["grade"] if prev else None

        if prev_grade == ResolutionGrade.EXPLICIT and grade != ResolutionGrade.EXPLICIT:
            raise GradeDowngradeRejected(
                f"질문 {r.question_id} 의 해결 등급을 명시 → {grade} 로 되돌리려 했다. "
                "강등은 없다 (§5.3.2)"
            )

        self._conn.execute(
            "INSERT INTO raw_resolution "
            "(question_id, resolved, grade, method, resolved_by, resolved_at, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(question_id) DO UPDATE SET "
            "resolved = excluded.resolved, grade = excluded.grade, method = excluded.method, "
            "resolved_by = excluded.resolved_by, resolved_at = excluded.resolved_at, "
            "collected_at = excluded.collected_at",
            (
                r.question_id,
                int(r.resolved),
                grade.value if grade else None,
                r.method.value if r.method else None,
                r.resolved_by,
                r.resolved_at,
                collected_at,
            ),
        )
        return prev_grade != ResolutionGrade.EXPLICIT and grade == ResolutionGrade.EXPLICIT

    # --- 조회 ------------------------------------------------------------

    def unsettled_question_ids(self) -> list[str]:
        """아직 명시적으로 해결되지 않은 질문들.

        **매 주기 다시 훑을 대상이다.** 명시적 해결이 붙은 건은 빠진다 — 등급은
        상향만 되고(§5.3.2) 명시가 종점이라 더 볼 이유가 없다.

        끝내 해결 표시를 받지 못한 질문은 여기 남아 계속 다시 조회된다. 지금은
        그래도 된다 — QnA 는 일 단위 수십 건이고(ADR-002) 조회는 질문당 세 번이다.
        이 범위를 닫는 것은 **미해결 종료 판정**이며, 그것은 타임아웃 기간(O18)이
        정해져야 가능하다. 추적 상태를 다루는 WBS-4.5.4 의 몫이다.
        """
        rows = self._conn.execute(
            "SELECT q.id FROM raw_question q "
            "LEFT JOIN raw_resolution r ON r.question_id = q.id "
            "WHERE r.grade IS NULL OR r.grade != ? "
            "ORDER BY q.created_at",
            (ResolutionGrade.EXPLICIT.value,),
        ).fetchall()
        return [row["id"] for row in rows]

    def answers_for(self, question_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM raw_answer WHERE question_id = ? ORDER BY created_at, id",
                (question_id,),
            )
        )

    def followups_for(self, question_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM raw_followup WHERE question_id = ? ORDER BY created_at, id",
                (question_id,),
            )
        )

    def resolution_of(self, question_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM raw_resolution WHERE question_id = ?", (question_id,)
        ).fetchone()


class QnaCollector:
    """모 시스템 QnA 를 Raw Layer 로 옮긴다 (FR-52).

    한 주기가 하는 일은 둘이다.
        1. 커서 이후의 **새 질문**을 가져온다
        2. 아직 명시적으로 해결되지 않은 **저장분을 다시 훑는다** — 답변·후속·해결 표시

    2 가 없으면 새 질문만 쌓이고 그 뒤의 변화는 보이지 않는다.
    """

    def __init__(self, parent: ParentSystem, conn: sqlite3.Connection) -> None:
        self._parent = parent
        self._conn = conn
        self._store = QnaStore(conn)

    @property
    def store(self) -> QnaStore:
        return self._store

    def collect(self) -> CollectionReport:
        """한 주기. **커서는 적재가 커밋된 뒤에만 옮긴다.**

        먼저 옮기면 중단됐을 때 그 구간의 질문을 영영 건너뛰고, 그러면 **지식에
        구멍이 생기는데 아무도 알아채지 못한다.** 반대 순서의 대가는 같은 것을 한 번
        더 가져오는 것뿐인데, upsert 라 결과가 달라지지 않는다.

        이 커서는 **수집 지점이지 ingest 지점이 아니다.** Raw Layer 에 남아 있으므로
        ingest(WBS-4.2.4)는 자기 진행 지점을 따로 들고 여기서 다시 읽으면 된다.
        """
        collected_at = datetime.now(UTC).isoformat()
        cursor = get_cursor(self._conn, QNA)

        new_questions = self._parent.list_questions(since=cursor)
        new_count = 0
        for q in new_questions:
            if self._store.upsert_question(q, collected_at=collected_at):
                new_count += 1

        # 새 질문 + 아직 안 닫힌 저장분. 새 질문이 이미 저장됐으므로 후자에 포함된다.
        targets = self._store.unsettled_question_ids()
        answers = followups = upgraded = 0
        for qid in targets:
            for a in self._parent.list_answers(qid):
                if self._store.upsert_answer(a, collected_at=collected_at):
                    answers += 1
            for f in self._parent.list_followups(qid):
                if self._store.upsert_followup(f, collected_at=collected_at):
                    followups += 1
            # 강등이 여기서 터지지는 않는다 — 다시 훑는 범위에 명시적 해결분이
            # 없기 때문이다. 그래도 터진다면 그건 그 전제가 깨졌다는 뜻이므로
            # 주기를 통째로 세우는 편이 낫다. 커밋 전이라 적재도 남지 않는다.
            if self._store.record_resolution(
                self._parent.get_resolution(qid), collected_at=collected_at
            ):
                upgraded += 1

        self._conn.commit()

        next_cursor = _advance(cursor, new_questions)
        if next_cursor and next_cursor != cursor:
            set_cursor(self._conn, QNA, next_cursor)

        return CollectionReport(
            new_questions=new_count,
            refreshed_questions=len(targets),
            answers=answers,
            followups=followups,
            upgraded=upgraded,
            cursor=next_cursor,
        )


def _advance(cursor: str | None, questions: list[Question]) -> str | None:
    """다음 커서. 이번에 본 질문 중 **가장 늦은 생성 시각**이다.

    `list_questions(since)` 가 `since` 를 배타적으로 해석하므로(그보다 늦은 것만
    돌려준다) 마지막으로 본 시각을 그대로 둔다. 새 질문이 없으면 커서는 그대로다.
    """
    if not questions:
        return cursor
    return max(q.created_at for q in questions)
