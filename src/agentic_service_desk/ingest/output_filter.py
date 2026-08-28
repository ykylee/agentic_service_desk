"""산출물 필터 — 되먹임 차단의 **단일 집행 지점** (NFR-4, §9.7).

이 시스템이 스스로 만들어 내보낸 것을 다시 배우지 않게 한다. 그것을 배우면
**오답이 지식베이스에 굳고**, 굳은 지식이 다음 오답을 낳는다 —
지식이 자라는 메커니즘과 오답이 자라는 메커니즘은 동일하다.

규칙을 한 곳에만 두는 이유가 있다. 여러 곳에 흩어 두면 언젠가 한 곳이 새고,
새는 순간 **사후에 발견하기 어렵다** — 지식베이스는 틀린 채로도 잘 동작하는 것처럼
보이기 때문이다. **ingest 로 가는 모든 입력이 여기를 지나며 우회 경로를 두지 않는다.**

그래서 이 모듈은 판정만 내놓지 않는다. **원천을 꺼내는 질의까지 들고 있다.**
`ingestible_answers()` 가 ingest 가 QnA 원천을 얻는 **유일한 문**이며, 배제 조건이
그 질의의 `WHERE` 안에 있다 — 문을 지나면서 걸러지므로 필터를 잊고 지나갈 수 없다.

**거르는 것은 여기 하나뿐이고, 거르는 용도도 하나뿐이다.** 봇 답변은 ingest 와
승격에서만 빠지고 기록·통계·FAQ 후보에는 그대로 남는다(§5.3) — Raw Layer 가 원문을
통째로 들고 있는 이유가 그것이다. 통계를 내는 쪽은 이 문을 쓰지 않는다.
"""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass

from agentic_service_desk.ingest.qna import ResolutionGrade


class BotAccountsNotConfigured(RuntimeError):
    """봇 계정 목록이 비어 있다.

    **동작을 거부한다.** 목록이 없으면 모든 답변이 사람 것으로 보이고, 그러면
    필터가 있으나 마나가 되어 **봇이 쓴 것을 봇이 다시 배운다.** 이 고장은 조용하다 —
    ingest 는 정상으로 보이고 지식베이스도 자라며, 틀렸다는 것은 한참 뒤에 드러난다.

    비어 있을 때 전부 통과시키는 것과 전부 막는 것 중에는 **막는 쪽이 안전하지만**,
    그것도 "질문이 없다"와 구분되지 않는다. 그래서 셋째를 택했다 — 설정하라고 말한다.
    """


class ExclusionReason(enum.StrEnum):
    """왜 배제됐는가. 로그와 지표에 남길 이름이다."""

    BOT_UNRESOLVED = "bot_unresolved"
    """봇이 썼고 아직 해결되지 않았다. 검증 신호가 없다."""

    BOT_IMPLICIT = "bot_implicit"
    """봇이 썼고 암묵적으로 해결됐다.

    만족해서 조용한 것과 포기하고 떠난 것은 데이터상 같은 모양이다. 구분하지 못하는
    신호에 지식의 자격을 줄 수 없다 (§5.3.1).
    """


@dataclass(frozen=True)
class Decision:
    """한 건에 대한 판정."""

    ingestible: bool
    reason: ExclusionReason | None = None
    """배제됐을 때만 채워진다. 통과에는 이유가 없다 — 그것이 기본값이기 때문이다."""

    def __bool__(self) -> bool:
        return self.ingestible


PASS = Decision(ingestible=True)


@dataclass(frozen=True)
class IngestibleAnswer:
    """필터를 통과한 답변. **ingest 가 보는 것은 이것뿐이다.**"""

    id: str
    question_id: str
    question_body: str
    body: str
    author_account: str
    created_at: str
    grade: str | None
    """통과 시점의 해결 등급. 사람 답변은 미해결이어도 통과하므로 `None` 일 수 있다."""


class OutputFilter:
    """봇이 만든 것을 걸러낸다. 단, 사람의 검증을 거친 것은 통과시킨다 (D7)."""

    def __init__(self, bot_accounts: frozenset[str]) -> None:
        """식별은 **계정 단위**다.

        내용 판별이나 신뢰도 점수 같은 흐릿한 기준을 쓰지 않는다 — "누가 올렸는가"는
        기계적으로 확정 가능한 사실이라 오작동하지 않는다.
        """
        if not bot_accounts:
            raise BotAccountsNotConfigured(
                "봇 계정 목록이 비어 있다. 설정 `ASD_BOT_ACCOUNTS` 를 채운다. "
                "목록이 없으면 봇 답변이 사람 답변으로 보여 §5.3 되먹임 차단이 "
                "조용히 무력화된다 (D7)."
            )
        self._bot_accounts = bot_accounts

    def is_bot(self, account: str) -> bool:
        return account in self._bot_accounts

    # --- 판정 ------------------------------------------------------------

    def judge(self, *, author_account: str, grade: str | None) -> Decision:
        """이 답변을 지식의 근거로 삼아도 되는가.

        - 사람이 쓴 답변 → 통과. **해결 여부를 묻지 않는다** — 애초에 우리 산출물이
          아니므로 되먹임이 성립하지 않는다
        - 봇이 썼고 **명시적 해결** → 통과. 해결 확정이 곧 외부 검증 신호다 (D8).
          답이 실제로 문제를 풀었다면 그것은 더 이상 검증되지 않은 자기 산출물이 아니다
        - 봇이 썼고 암묵적 해결이거나 미해결 → **차단** (FR-31)
        """
        if not self.is_bot(author_account):
            return PASS
        if grade == ResolutionGrade.EXPLICIT:
            return PASS
        reason = (
            ExclusionReason.BOT_IMPLICIT
            if grade == ResolutionGrade.IMPLICIT
            else ExclusionReason.BOT_UNRESOLVED
        )
        return Decision(ingestible=False, reason=reason)

    # --- 원천 조회 — ingest 가 지나는 유일한 문 --------------------------

    def ingestible_answers(self, conn: sqlite3.Connection) -> list[IngestibleAnswer]:
        """ingest 에 쓸 수 있는 답변을 Raw Layer 에서 꺼낸다 (NFR-4).

        **배제 조건이 질의 안에 있다.** 꺼낸 다음 거르는 것이 아니라 꺼내면서 걸러지므로,
        필터를 잊고 원천을 읽는 경로가 생기지 않는다. `judge()` 와 규칙이 같은지는
        시험이 지킨다 — 같은 규칙을 두 벌 쓰는 것이 이 구조의 대가다.

        질문 본문을 함께 준다. 답변만으로는 무엇에 대한 답인지 알 수 없어 개념을
        뽑을 수 없기 때문이다 (WBS-4.2.4).
        """
        placeholders = ",".join("?" * len(self._bot_accounts))
        rows = conn.execute(
            f"""
            SELECT a.id, a.question_id, q.body AS question_body, a.body,
                   a.author_account, a.created_at, r.grade
            FROM raw_answer a
            JOIN raw_question q ON q.id = a.question_id
            LEFT JOIN raw_resolution r ON r.question_id = a.question_id
            WHERE a.author_account NOT IN ({placeholders})
               OR r.grade = ?
            ORDER BY a.created_at, a.id
            """,  # noqa: S608 — 자리표시자 개수만 문자열로 만든다. 값은 바인딩된다
            (*sorted(self._bot_accounts), ResolutionGrade.EXPLICIT.value),
        ).fetchall()
        return [
            IngestibleAnswer(
                id=row["id"],
                question_id=row["question_id"],
                question_body=row["question_body"],
                body=row["body"],
                author_account=row["author_account"],
                created_at=row["created_at"],
                grade=row["grade"],
            )
            for row in rows
        ]


def build_output_filter(bot_accounts: str) -> OutputFilter:
    """설정 문자열에서 필터를 만든다. 쉼표로 나눈다."""
    accounts = frozenset(a.strip() for a in bot_accounts.split(",") if a.strip())
    return OutputFilter(accounts)
