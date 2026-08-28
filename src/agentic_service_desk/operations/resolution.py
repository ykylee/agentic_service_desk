"""티켓 종결 기록 — **지식 항목의 초안** (FR-13·14, §6.5).

종결 기록을 *작업 로그*로 보면 승격 단계에서 누군가 그것을 지식 항목으로 **번역**해야
한다. 그 번역이 번거로우면 승격은 밀리고(Q7 은 방치 비용이 낮은 대기열이다), 밀리면
1국면의 지식 성장이 멈춘다. 그래서 **처음부터 지식 항목의 초안 형식으로 받는다** —
그러면 승격이 번역이 아니라 **승인**이 된다 (§6.5.1).

1국면에서는 티켓 비율이 높고 **티켓 해결의 승격이 지식 성장의 주 경로**다. 티켓을
"완료" 체크로 닫는 순간 승격할 재료가 사라지므로, 닫는 행위 자체에 이 기록을 묶었다.

## 무효화 조건은 비워 둔 채로 온다

일곱 필드 중 넷이 필수인데(§6.5.2), 그 넷째가 이 모듈의 핵심이다.

> **강제 입력 지점** (§5.6.4): 초안에서 **에이전트가 잘 못 채우는 필드를 비워 두고**,
> 사람이 채우지 않으면 승인이 성립하지 않게 한다.

"무엇이 바뀌면 이 답이 틀려지는가"는 시스템 전체에 대한 이해를 요구하는 판단이라
에이전트가 가장 약한 지점이고, 동시에 **빠지면 지식이 영영 낡지 않는**(§6.5.3) 가장
중요한 필드다. 이 칸이 비어 있으면 클릭만으로는 넘어갈 수 없고, 사람이 그 답을
쓰려면 초안을 읽을 수밖에 없으므로 **검증이 부산물로 실질이 된다.**

그래서 `draft()` 는 무효화 조건을 받지 않는다. 에이전트는 **후보로 제시**할 수 있고
(`invalidation_candidates`) 그것은 `confirm()` 에서 사람이 고를 수 있는 목록이 될
뿐이다 — **기본값을 미리 채워 두면 강제 입력 지점의 효과가 사라진다.**
"""

from __future__ import annotations

import enum
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind


class GroundKind(enum.StrEnum):
    """근거가 어디서 왔는가 (§6.5.2 필드 3).

    **`PERSON` 이 있는 것이 중요하다.** 담당자 확인이 근거인 지식은 코드에 묶이지
    않으므로 stale 을 자동 판정할 수 없다 — §6.5.3 이 무효화 조건을 필수로 만든
    바로 그 경우다. 근거의 종류를 남겨 두면 그 위험을 나중에 셀 수 있다.
    """

    CODE = "code"
    COMMIT = "commit"
    CONFIG = "config"
    PERSON = "person"


@dataclass(frozen=True)
class Ground:
    """근거 하나."""

    kind: GroundKind
    ref: str

    def __post_init__(self) -> None:
        if not self.ref.strip():
            raise ValueError("근거에 가리키는 것이 없다 (D3)")

    @property
    def traceable_to_code(self) -> bool:
        """코드에 묶이는가. **아니면 stale 자동 판정이 되지 않는다** (§6.5.3)."""
        return self.kind in (GroundKind.CODE, GroundKind.COMMIT, GroundKind.CONFIG)


class IncompleteResolution(ValueError):
    """종결 기록이 필수 필드를 갖추지 못했다 (FR-13)."""


class NotConfirmed(RuntimeError):
    """무효화 조건이 아직 비어 있다 — **강제 입력 지점을 지나지 않았다** (§5.6.4)."""


@dataclass
class Resolution:
    """종결 기록 하나. 필수 넷과 선택 셋 (§6.5.2)."""

    ticket_id: str
    generalized_question: str
    """원 질문에서 **개인·상황 특정 요소를 걷어낸** 형태.

    PO-3 를 집행하는 자리다 — 질문자 식별자가 지식으로 넘어가지 않는 것이 여기서
    끊긴다. 종결 기록의 첫 필드가 *일반화된 질문*인 것이 그 집행 지점이다.
    """

    answer: str
    """**재사용 가능한 진술.** 이 티켓 하나가 아니라 같은 유형 전체에 적용되도록."""

    grounding: tuple[Ground, ...]
    invalidation: Invalidation | None = None
    """**비어 있는 것이 초안의 정상 상태다** (§5.6.4)."""

    invalidation_candidates: tuple[Invalidation, ...] = ()
    """에이전트의 제안. **선택 자체는 사람이 한다.**"""

    cause: str | None = None
    scope: str | None = None
    recurrence: str | None = None
    """일회성인가 반복될 유형인가. 승격 우선순위에 쓰인다."""

    drafted_by: str = "agent"
    confirmed_at: str | None = None
    promoted_item_id: str | None = None
    """승격된 지식 항목의 불변 id (경로 A). **두 번 올리지 않기 위한 표시**이자
    "이 종결 기록이 무엇이 되었는가"의 답이다."""

    def __post_init__(self) -> None:
        # 셋은 초안 단계에서도 있어야 한다. 넷째(무효화 조건)만 비워 둔다.
        if not self.generalized_question.strip():
            raise IncompleteResolution("일반화된 질문이 비었다 (§6.5.2 필드 1)")
        if not self.answer.strip():
            raise IncompleteResolution("답이 비었다 (§6.5.2 필드 2)")
        if not self.grounding:
            raise IncompleteResolution("근거가 비었다 — 출처는 1급 시민이다 (D3)")

    @property
    def confirmed(self) -> bool:
        """사람이 무효화 조건을 채웠는가. **닫으려면 참이어야 한다** (FR-13)."""
        return self.invalidation is not None

    @property
    def promotable(self) -> bool:
        """지식 항목으로 승격할 수 있는가 (FR-14).

        연결형·주기형 둘 다 없으면 **승격 대기에 남는다** — 검증할 수 없는 지식을
        들이는 것보다 공백으로 두는 편이 낫다.
        """
        return self.confirmed

    @property
    def code_traceable(self) -> bool:
        """근거가 코드에 닿는가. 아니면 무효화 조건이 유일한 stale 장치다 (§6.5.3)."""
        return any(g.traceable_to_code for g in self.grounding)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def draft(
    conn: sqlite3.Connection,
    *,
    ticket_id: str,
    generalized_question: str,
    answer: str,
    grounding: list[Ground],
    invalidation_candidates: list[Invalidation] | None = None,
    cause: str | None = None,
    scope: str | None = None,
    recurrence: str | None = None,
    drafted_by: str = "agent",
) -> Resolution:
    """초안을 남긴다. **무효화 조건은 받지 않는다.**

    인자에 아예 없는 것이 이 함수의 요점이다 — 있으면 언젠가 누군가 기본값을
    넘기고, 그 순간 강제 입력 지점이 사라진다 (§5.6.4). 에이전트가 아는 것은
    후보까지이며 **선택은 사람의 행위**다.
    """
    resolution = Resolution(
        ticket_id=ticket_id,
        generalized_question=generalized_question,
        answer=answer,
        grounding=tuple(grounding),
        invalidation_candidates=tuple(invalidation_candidates or ()),
        cause=cause,
        scope=scope,
        recurrence=recurrence,
        drafted_by=drafted_by,
    )
    _write(conn, resolution)
    return resolution


def confirm(
    conn: sqlite3.Connection, ticket_id: str, *, invalidation: Invalidation
) -> Resolution:
    """사람이 무효화 조건을 채운다. **이 행위가 곧 승인이다** (§6.5.4).

    그 답을 쓰려면 초안을 읽을 수밖에 없으므로 검증이 부산물로 실질이 된다 —
    감시 없이 목적을 달성한다.
    """
    resolution = get(conn, ticket_id)
    if resolution is None:
        raise IncompleteResolution(f"티켓 {ticket_id} 에 초안이 없다")
    resolution.invalidation = invalidation
    resolution.confirmed_at = _now()
    _write(conn, resolution)
    return resolution


def get(conn: sqlite3.Connection, ticket_id: str) -> Resolution | None:
    row = conn.execute(
        "SELECT * FROM ticket_resolution WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()
    return _from_row(row) if row else None


def is_confirmed(conn: sqlite3.Connection, ticket_id: str) -> bool:
    """티켓을 닫아도 되는가. `ticket.transition` 이 이것을 묻는다."""
    resolution = get(conn, ticket_id)
    return bool(resolution and resolution.confirmed)


def awaiting_invalidation(conn: sqlite3.Connection) -> list[Resolution]:
    """무효화 조건이 비어 있는 초안들.

    **이 목록이 밀리면 지식이 자라지 않는다** — 티켓이 닫히지 않고, 닫히지 않으면
    승격 재료가 되지 않는다 (§6.4.5).
    """
    rows = conn.execute(
        "SELECT * FROM ticket_resolution WHERE invalidation IS NULL"
    ).fetchall()
    return [_from_row(r) for r in rows]


# --- 직렬화 ---------------------------------------------------------------


def _write(conn: sqlite3.Connection, r: Resolution) -> None:
    conn.execute(
        "INSERT INTO ticket_resolution "
        "(ticket_id, generalized_question, answer, grounding, invalidation, "
        " invalidation_candidates, cause, scope, recurrence, drafted_by, confirmed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(ticket_id) DO UPDATE SET "
        "generalized_question = excluded.generalized_question, answer = excluded.answer, "
        "grounding = excluded.grounding, invalidation = excluded.invalidation, "
        "invalidation_candidates = excluded.invalidation_candidates, cause = excluded.cause, "
        "scope = excluded.scope, recurrence = excluded.recurrence, "
        # `promoted_item_id` 는 여기서 건드리지 않는다 — 초안을 다시 써도 승격
        # 사실이 지워지면 같은 기록이 두 번 지식이 된다.
        "confirmed_at = excluded.confirmed_at",
        (
            r.ticket_id,
            r.generalized_question,
            r.answer,
            json.dumps([{"kind": str(g.kind), "ref": g.ref} for g in r.grounding],
                       ensure_ascii=False),
            _dump_invalidation(r.invalidation),
            json.dumps(
                [json.loads(_dump_invalidation(i)) for i in r.invalidation_candidates],
                ensure_ascii=False,
            ),
            r.cause,
            r.scope,
            r.recurrence,
            r.drafted_by,
            r.confirmed_at,
        ),
    )
    conn.commit()


def _dump_invalidation(inv: Invalidation | None) -> str | None:
    if inv is None:
        return None
    out: dict = {"kind": str(inv.kind)}
    if inv.refs:
        out["refs"] = list(inv.refs)
    if inv.period_days:
        out["period_days"] = inv.period_days
    return json.dumps(out, ensure_ascii=False)


def _load_invalidation(raw: str | None) -> Invalidation | None:
    if not raw:
        return None
    data = json.loads(raw)
    return Invalidation(
        kind=InvalidationKind(data["kind"]),
        refs=tuple(data.get("refs") or ()),
        period_days=data.get("period_days"),
    )


def _from_row(row: sqlite3.Row) -> Resolution:
    candidates = json.loads(row["invalidation_candidates"] or "[]")
    return Resolution(
        ticket_id=row["ticket_id"],
        generalized_question=row["generalized_question"],
        answer=row["answer"],
        grounding=tuple(
            Ground(kind=GroundKind(g["kind"]), ref=g["ref"])
            for g in json.loads(row["grounding"])
        ),
        invalidation=_load_invalidation(row["invalidation"]),
        invalidation_candidates=tuple(
            _load_invalidation(json.dumps(c, ensure_ascii=False)) for c in candidates
        ),
        cause=row["cause"],
        scope=row["scope"],
        recurrence=row["recurrence"],
        drafted_by=row["drafted_by"],
        confirmed_at=row["confirmed_at"],
        promoted_item_id=row["promoted_item_id"],
    )
