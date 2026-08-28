"""WBS-4.3.2 — 티켓 종결 기록 (FR-13·14, §6.5·5.6.4).

종결 기록은 작업 로그가 아니라 **지식 항목의 초안**이다. 그래야 승격이 번역이 아니라
**승인**이 되고, 번역이 번거로워 승격이 밀리는 일이 없다 (§6.5.1).

여기서 지키는 것은 셋.

    1. 필수 넷 중 하나라도 비면 **종결되지 않는다** (FR-13)
    2. **무효화 조건은 비워 둔 채로 온다** — 에이전트가 기본값을 채우면 강제 입력
       지점이 무력해진다 (§5.6.4). `draft()` 가 그 인자를 아예 받지 않는다
    3. 무효화 조건 없이는 **승격 대기에 남는다** (FR-14)
"""

from __future__ import annotations

import inspect

import pytest

from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind
from agentic_service_desk.operations import resolution, ticket
from agentic_service_desk.operations.resolution import (
    Ground,
    GroundKind,
    IncompleteResolution,
    Resolution,
)
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.operations.ticket import ResolutionRequired, Source, State


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _ticket(conn):  # noqa: ANN001, ANN202
    return ticket.issue(conn, source=Source.QNA)


def _draft(conn, ticket_id: str, **over):  # noqa: ANN001, ANN003, ANN202
    base = dict(
        generalized_question="승인 한도는 무엇으로 결정되는가",
        answer="부서 등급으로 결정된다.",
        grounding=[Ground(kind=GroundKind.CODE, ref="src/approval/limit.py")],
    )
    base.update(over)
    return resolution.draft(conn, ticket_id=ticket_id, **base)  # type: ignore[arg-type]


class TestRequiredFields:
    """§6.5.2 — 일곱 중 넷이 필수다."""

    def test_일반화된_질문이_비면_거부한다(self, tmp_path) -> None:
        # 원 질문에서 개인·상황 요소를 걷어낸 형태여야 한다 — PO-3 의 집행 지점이다.
        conn = _conn(tmp_path)
        with pytest.raises(IncompleteResolution, match="일반화된 질문"):
            _draft(conn, _ticket(conn).id, generalized_question="  ")

    def test_답이_비면_거부한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        with pytest.raises(IncompleteResolution, match="답"):
            _draft(conn, _ticket(conn).id, answer="")

    def test_근거가_비면_거부한다(self, tmp_path) -> None:
        # 출처는 1급 시민이다 (D3).
        conn = _conn(tmp_path)
        with pytest.raises(IncompleteResolution, match="근거"):
            _draft(conn, _ticket(conn).id, grounding=[])

    def test_가리키는_것_없는_근거는_거부한다(self) -> None:
        with pytest.raises(ValueError):
            Ground(kind=GroundKind.CODE, ref="   ")

    def test_선택_필드는_없어도_된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = _draft(conn, _ticket(conn).id)
        assert (r.cause, r.scope, r.recurrence) == (None, None, None)


class TestForcedInput:
    """§5.6.4 — 가장 사람이 필요한 칸을 비워 둔다."""

    def test_draft_는_무효화_조건_인자를_아예_받지_않는다(self) -> None:
        # **인자에 없는 것이 요점이다.** 있으면 언젠가 누군가 기본값을 넘기고,
        # 그 순간 강제 입력 지점이 사라진다.
        assert "invalidation" not in inspect.signature(resolution.draft).parameters

    def test_초안은_무효화_조건이_비어_있다(self, tmp_path) -> None:
        # 비어 있는 것이 초안의 **정상 상태**다.
        conn = _conn(tmp_path)
        r = _draft(conn, _ticket(conn).id)
        assert r.invalidation is None
        assert not r.confirmed

    def test_후보를_제시해도_값이_되지는_않는다(self, tmp_path) -> None:
        # 에이전트는 후보로 제시할 수 있다. **선택 자체는 사람이 한다** —
        # 기본값을 미리 채워 두면 강제 입력 지점의 효과가 사라진다.
        conn = _conn(tmp_path)
        r = _draft(
            conn,
            _ticket(conn).id,
            invalidation_candidates=[
                Invalidation(kind=InvalidationKind.LINKED, refs=("src/approval/limit.py",)),
                Invalidation(kind=InvalidationKind.PERIODIC, period_days=180),
            ],
        )
        assert len(r.invalidation_candidates) == 2
        assert r.invalidation is None
        assert not r.confirmed

    def test_사람이_채우면_승인된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = _ticket(conn)
        _draft(conn, t.id)
        r = resolution.confirm(
            conn, t.id, invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=180)
        )
        assert r.confirmed
        assert r.confirmed_at

    def test_비어_있는_초안이_대기_목록에_뜬다(self, tmp_path) -> None:
        # 이 목록이 밀리면 티켓이 닫히지 않고, 닫히지 않으면 승격 재료가 되지 않는다.
        conn = _conn(tmp_path)
        first, second = _ticket(conn), _ticket(conn)
        _draft(conn, first.id)
        _draft(conn, second.id)
        resolution.confirm(
            conn, second.id, invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90)
        )

        waiting = resolution.awaiting_invalidation(conn)
        assert [r.ticket_id for r in waiting] == [first.id]


class TestClosingTheTicket:
    """FR-13 — 넷 중 하나라도 비면 종결되지 않는다."""

    def test_초안만으로는_닫히지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = _ticket(conn)
        _draft(conn, t.id)

        with pytest.raises(ResolutionRequired, match="무효화 조건"):
            ticket.transition(conn, t.id, State.CLOSED)
        assert ticket.get(conn, t.id).state is State.OPEN

    def test_기록이_아예_없어도_닫히지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = _ticket(conn)
        with pytest.raises(ResolutionRequired):
            ticket.transition(conn, t.id, State.CLOSED)

    def test_사람이_채운_뒤에_닫힌다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = _ticket(conn)
        _draft(conn, t.id)
        resolution.confirm(
            conn,
            t.id,
            invalidation=Invalidation(
                kind=InvalidationKind.LINKED, refs=("src/approval/limit.py",)
            ),
        )
        assert ticket.transition(conn, t.id, State.CLOSED).state is State.CLOSED


class TestPromotability:
    """FR-14 — 검증할 수 없는 지식을 들이는 것보다 공백으로 두는 편이 낫다."""

    def test_무효화_조건이_없으면_승격_대기에_남는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        assert not _draft(conn, _ticket(conn).id).promotable

    def test_연결형이면_승격할_수_있다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = _ticket(conn)
        _draft(conn, t.id)
        r = resolution.confirm(
            conn, t.id,
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("src/a.py",)),
        )
        assert r.promotable

    def test_주기형도_승격할_수_있다(self, tmp_path) -> None:
        # 연결형이 원칙이고 주기형은 **묶을 대상이 없을 때의 대비책**이다.
        conn = _conn(tmp_path)
        t = _ticket(conn)
        _draft(conn, t.id)
        r = resolution.confirm(
            conn, t.id, invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=180)
        )
        assert r.promotable


class TestGroundKind:
    """§6.5.3 — 담당자 확인이 근거인 지식은 코드에 묶이지 않는다."""

    def test_코드에_닿는_근거를_구분한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        code = _draft(conn, _ticket(conn).id)
        assert code.code_traceable

    def test_담당자_확인만_있으면_코드에_닿지_않는다(self, tmp_path) -> None:
        # 그러면 무효화 조건이 **유일한 stale 장치**다 — §2.2.3 이 운영 문서를 배제한
        # 이유가 승격이라는 뒷문으로 되돌아오지 않게 막는 자리다.
        conn = _conn(tmp_path)
        r = _draft(
            conn,
            _ticket(conn).id,
            grounding=[Ground(kind=GroundKind.PERSON, ref="인사팀 김OO 확인")],
        )
        assert not r.code_traceable


class TestRoundTrip:
    def test_저장하고_읽어도_같다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        t = _ticket(conn)
        _draft(
            conn,
            t.id,
            grounding=[
                Ground(kind=GroundKind.COMMIT, ref="a1b2c3d"),
                Ground(kind=GroundKind.PERSON, ref="담당자 확인"),
            ],
            invalidation_candidates=[Invalidation(kind=InvalidationKind.PERIODIC, period_days=90)],
            cause="인사이동 때마다 손이 갔다",
            recurrence="반복",
        )
        resolution.confirm(
            conn, t.id, invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("src/a.py",))
        )

        back = resolution.get(conn, t.id)
        assert isinstance(back, Resolution)
        assert [g.kind for g in back.grounding] == [GroundKind.COMMIT, GroundKind.PERSON]
        assert back.invalidation == Invalidation(kind=InvalidationKind.LINKED, refs=("src/a.py",))
        assert back.invalidation_candidates[0].period_days == 90
        assert back.cause == "인사이동 때마다 손이 갔다"
        assert back.recurrence == "반복"

    def test_없는_티켓은_None_이다(self, tmp_path) -> None:
        assert resolution.get(_conn(tmp_path), "t-없음") is None
