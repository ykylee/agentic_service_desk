"""보존 만료 — 지식은 남기고 원본은 지운다 (WBS-4.8.3, FR-51, PO-4).

전건 티켓 발행(D19)이라 기록이 무한정 쌓인다. 그런데 **사내에 정해진 보존 정책이
없다**(2026-08-28 확인). 그래서 기본은 무제한이되, 그것을 *정책 부재*가 아니라
**결정**으로 기록한다 — 정책이 없어서 그냥 쌓이는 것과 두지 않기로 정한 것은 나중에
달라진다. 전자는 개인정보 이슈가 생겼을 때 소급이 어렵다.

장치를 지금 만드는 이유도 같다. 나중에 정책이 생기면 **값만 넣으면 되게** 해 두는
것과, 그때 가서 만드는 것은 다르다 — 후자에서는 그 사이 쌓인 것이 그대로 남는다.

## 만료는 원문을 지우는 것이지 셈을 지우는 것이 아니다

FR-51 은 두 가지를 함께 요구한다: 기간이 지난 **QnA 원문**은 만료시키고, **통계
집계값은 만료시키지 않는다.** 이 시스템은 집계를 저장하지 않고 `qna_item` 을 세어
그때그때 낸다(§6.3) — 그러니 `qna_item` 행을 지우면 **명시적 해결률·커버리지의 분모가
통째로 사라진다.** 작년 통계가 사라지는 것은 만료가 아니라 소실이다.

그래서 지우는 것과 남기는 것을 이렇게 가른다.

| 지운다 | 남긴다 |
|---|---|
| Raw Layer 원문 (질문·답변·후속·해결 표시) | `qna_item` 행 — **상태·등급·시각만 남은 셈의 뼈대** |
| 수동 등록 원문 (붙여넣은 메신저 문의) | `ticket` · `ticket_resolution` — PO-3 가 이미 일반화한 형태다 |
| `qna_item.asker_id` — 유일하게 남은 식별자 | 지식 항목 — 이미 식별 요소가 없다 (PO-3) |
| | 답변 이력 — **무엇을 근거로 답했는가**(D20). 지우면 감사와 정정 추적이 끊긴다 |

원문이 사라져도 "그 질문이 언제 들어와 어떻게 닫혔는가"는 남는다. **개인정보는
원문에 있지 셈에 있지 않다.**

## 사람이 아직 봐야 할 것은 만료하지 않는다

기간이 지났다는 것이 처리되었다는 뜻은 아니다. Q6 에서 확인을 기다리는 건, Q7 의
승격 후보, Q2 의 검수 대기 초안, 재검증 표본 — 이것들은 **원문이 있어야 판정할 수
있다.** 손안에 있는 일이 기간이 지났다는 이유로 사라지면, 사람은 아무것도 하지 않았는데
대기열이 줄어드는 것을 본다.

그래서 만료는 **끝난 것에만 닿는다.**

## 지우기 전에 세고, 지운 뒤에 남긴다

삭제는 되돌릴 수 없는 행위다(게재와 같은 종류다). 무엇이 몇 건 사라졌는지가 남지
않으면 **"원래 없었다"와 "지웠다"를 구분할 수 없다.** `retention_run` 에 건수를
남기되 **무엇을 지웠는지는 남기지 않는다** — 지웠다면서 흔적을 남기는 것은 모순이다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agentic_service_desk.operations import qna_state
from agentic_service_desk.operations import recheck as recheck_domain
from agentic_service_desk.pipeline import draft_store


class InvalidRetention(ValueError):
    """보존 기간이 성립하지 않는다. **0 이나 음수는 '즉시 지운다'가 아니다.**"""


@dataclass(frozen=True)
class Report:
    """한 번의 만료. **건수만 남는다.**"""

    cutoff: str = ""
    questions: int = 0
    answers: int = 0
    followups: int = 0
    resolutions: int = 0
    manual_entries: int = 0
    anonymized: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.questions
            or self.answers
            or self.followups
            or self.resolutions
            or self.manual_entries
            or self.anonymized
        )

    @property
    def summary(self) -> str:
        return (
            f"질문 {self.questions} · 답변 {self.answers} · 후속 {self.followups} · "
            f"해결 표시 {self.resolutions} · 수동 등록 {self.manual_entries} · "
            f"식별자 삭제 {self.anonymized}"
        )


def expirable(
    conn: sqlite3.Connection, *, retention_days: int, now: datetime | None = None
) -> list[str]:
    """만료해도 되는 `qna_item.id`.

    **닫힌 시각으로 잰다.** 들어온 시각으로 재면 오래 끌던 건이 해결되자마자 만료되고,
    그러면 방금 끝난 일의 원문이 사라진다 — 후속(§6.1)이 달릴 여지가 아직 있는데도.
    """
    if retention_days <= 0:
        raise InvalidRetention(
            f"보존 기간은 1일 이상이어야 한다: {retention_days} — "
            "'즉시 지운다'를 설정으로 표현할 수 있게 두면 오타 하나가 원문을 통째로 지운다"
        )
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=retention_days)).isoformat()
    rows = conn.execute(
        "SELECT i.id FROM qna_item i "
        "WHERE i.state IN (?, ?) AND i.closed_at IS NOT NULL AND i.closed_at < ? "
        # Q6 — 확인을 기다린다. 원문이 없으면 확인할 수 없다 (§5.3.2)
        "  AND COALESCE(i.resolution_grade, '') <> ? "
        # Q2 — 검수 대기 초안이 남아 있다
        "  AND NOT EXISTS (SELECT 1 FROM answer_draft d "
        "                   WHERE d.qna_item_id = i.id AND d.state = ?) "
        # Q7 — 승격 판정을 기다린다 (§6.8)
        "  AND NOT EXISTS (SELECT 1 FROM ticket t "
        "                   JOIN ticket_resolution r ON r.ticket_id = t.id "
        "                   WHERE t.qna_item_id = i.id "
        "                     AND r.promoted_item_id IS NULL "
        "                     AND r.promotion_declined_at IS NULL) "
        # 재검증 표본 — 다시 볼 것이 남아 있다 (§5.6.7)
        "  AND NOT EXISTS (SELECT 1 FROM recheck c "
        "                   WHERE c.state = ? AND c.subject_id = i.id) "
        # **아직 지울 것이 남아 있는가.** 이 조건이 없으면 이미 만료된 건이 영원히
        # 대상으로 남아, 배치가 돌 때마다 아무것도 지우지 않은 만료가 기록된다 —
        # 라이브에서 실제로 그랬다: 이력이 0건짜리 행으로 메워져 **진짜 만료가 그
        # 사이에 묻혔다.**
        "  AND (EXISTS (SELECT 1 FROM raw_question q WHERE q.id = i.parent_question_id) "
        "       OR EXISTS (SELECT 1 FROM manual_entry m WHERE m.qna_item_id = i.id) "
        "       OR i.asker_id IS NOT NULL) "
        "ORDER BY i.closed_at",
        (
            qna_state.RESOLVED,
            qna_state.UNRESOLVED_CLOSED,
            cutoff,
            qna_state.IMPLICIT,
            draft_store.PENDING,
            recheck_domain.PENDING,
        ),
    ).fetchall()
    return [row["id"] for row in rows]


def expire(
    conn: sqlite3.Connection, *, retention_days: int, now: datetime | None = None
) -> Report:
    """기간이 지난 원문을 지운다. **되돌릴 수 없다.**

    한 트랜잭션으로 끝낸다 — 원문만 지워지고 식별자가 남거나 그 반대가 되면, 만료가
    절반만 일어난 상태를 나중에 아무도 알아보지 못한다.
    """
    moment = now or datetime.now(UTC)
    cutoff = (moment - timedelta(days=retention_days)).isoformat()
    targets = expirable(conn, retention_days=retention_days, now=moment)
    if not targets:
        return Report(cutoff=cutoff)

    marks = ",".join("?" * len(targets))
    parents = [
        row["parent_question_id"]
        for row in conn.execute(
            f"SELECT parent_question_id FROM qna_item WHERE id IN ({marks}) "  # noqa: S608
            "AND parent_question_id IS NOT NULL",
            targets,
        ).fetchall()
    ]

    counts = {"answers": 0, "followups": 0, "resolutions": 0, "questions": 0}
    if parents:
        pmarks = ",".join("?" * len(parents))
        for key, table in (
            ("answers", "raw_answer"),
            ("followups", "raw_followup"),
            ("resolutions", "raw_resolution"),
        ):
            counts[key] = conn.execute(
                f"DELETE FROM {table} WHERE question_id IN ({pmarks})",  # noqa: S608
                parents,
            ).rowcount
        counts["questions"] = conn.execute(
            f"DELETE FROM raw_question WHERE id IN ({pmarks})",  # noqa: S608
            parents,
        ).rowcount

    manual = conn.execute(
        f"DELETE FROM manual_entry WHERE qna_item_id IN ({marks})",  # noqa: S608
        targets,
    ).rowcount
    # **행은 남기고 식별자만 지운다.** 이 행이 통계의 분모다 (§6.3).
    anonymized = conn.execute(
        f"UPDATE qna_item SET asker_id = NULL WHERE id IN ({marks}) "  # noqa: S608
        "AND asker_id IS NOT NULL",
        targets,
    ).rowcount

    report = Report(
        cutoff=cutoff,
        questions=counts["questions"],
        answers=counts["answers"],
        followups=counts["followups"],
        resolutions=counts["resolutions"],
        manual_entries=manual,
        anonymized=anonymized,
    )
    if not report.changed:
        # **지운 것이 없으면 기록하지 않는다.** 이력은 "무엇이 사라졌는가"의 기록이지
        # "배치가 돌았는가"의 기록이 아니다 — 후자를 섞으면 진짜 만료를 찾을 수 없다.
        conn.commit()
        return report
    conn.execute(
        "INSERT INTO retention_run (ran_at, cutoff, questions, answers, followups, "
        "resolutions, manual_entries, anonymized) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            moment.isoformat(),
            cutoff,
            report.questions,
            report.answers,
            report.followups,
            report.resolutions,
            report.manual_entries,
            report.anonymized,
        ),
    )
    conn.commit()
    return report


def last_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """마지막 만료. **화면이 이것을 말한다** — 돌지 않은 것과 지울 것이 없던 것은 다르다."""
    return conn.execute(
        "SELECT * FROM retention_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
