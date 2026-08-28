"""WBS-4.2.3 — 산출물 필터 (NFR-4, FR-31).

**Raw Layer 실적재분으로 시험한다.** 손으로 만든 dict 가 아니라 mock 어댑터에서
실제로 수집된 다섯 분기를 그대로 통과시켜 본다 — 그것이 4.2.2 를 먼저 만든 값이다.

여기서 지키는 것은 넷이다.

    1. 사람 답변은 **해결 여부와 무관하게** 통과한다 — 우리 산출물이 아니다
    2. 봇 답변은 **명시적 해결분만** 통과한다 (FR-31)
    3. 원천을 꺼내는 문이 **하나뿐**이고, 그 문의 질의가 곧 규칙이다 (NFR-4)
    4. 계정 목록이 없으면 **동작을 거부**한다 — 조용히 다 통과시키지 않는다
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic_service_desk.adapters.contract import Resolution, ResolutionMethod
from agentic_service_desk.adapters.mock import BOT_ACCOUNT, MockParentSystem
from agentic_service_desk.config import Settings
from agentic_service_desk.ingest.output_filter import (
    BotAccountsNotConfigured,
    ExclusionReason,
    OutputFilter,
    build_output_filter,
)
from agentic_service_desk.ingest.qna import QnaCollector, QnaStore, ResolutionGrade
from agentic_service_desk.operations.schema import connect, initialize


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _filter() -> OutputFilter:
    return OutputFilter(frozenset({BOT_ACCOUNT}))


def _collected(tmp_path, parent: MockParentSystem | None = None):  # noqa: ANN001, ANN202
    """mock 을 실제로 수집한 Raw Layer 를 돌려준다."""
    conn = connect(tmp_path / "ops.sqlite3")
    initialize(conn)
    QnaCollector(parent or MockParentSystem(), conn).collect()
    return conn


class TestConfiguration:
    def test_계정_목록이_비면_거부한다(self) -> None:
        # 전부 통과시키면 봇이 쓴 것을 봇이 다시 배운다. 그 고장은 조용하다 (D7).
        with pytest.raises(BotAccountsNotConfigured):
            OutputFilter(frozenset())

    def test_설정_기본값은_비어_있다(self) -> None:
        # 채우라고 말하는 쪽을 택했다 — 실제 계정 이름을 우리가 알 수 없다.
        assert Settings(_env_file=None).bot_accounts == ""

    def test_쉼표로_여럿을_받는다(self) -> None:
        f = build_output_filter("svc-agentic-desk, svc-old-bot")
        assert f.is_bot("svc-agentic-desk")
        assert f.is_bot("svc-old-bot")
        assert not f.is_bot("emp-999")

    def test_빈_문자열은_거부로_이어진다(self) -> None:
        with pytest.raises(BotAccountsNotConfigured):
            build_output_filter("  ,  ")


class TestJudge:
    """§5.3 의 표를 그대로 옮긴 것인가."""

    def test_사람_답변은_언제나_통과한다(self) -> None:
        # 해결 여부를 묻지 않는다 — 애초에 우리 산출물이 아니라 되먹임이 성립하지 않는다.
        f = _filter()
        assert f.judge(author_account="emp-999", grade=None)
        assert f.judge(author_account="emp-999", grade=ResolutionGrade.IMPLICIT)

    def test_봇_답변은_명시적_해결이면_통과한다(self) -> None:
        # 해결 확정이 곧 외부 검증 신호다 (D8).
        d = _filter().judge(author_account=BOT_ACCOUNT, grade=ResolutionGrade.EXPLICIT)
        assert d.ingestible
        assert d.reason is None

    def test_봇_답변은_미해결이면_막힌다(self) -> None:
        d = _filter().judge(author_account=BOT_ACCOUNT, grade=None)
        assert not d
        assert d.reason == ExclusionReason.BOT_UNRESOLVED

    def test_봇_답변은_암묵적_해결이어도_막힌다(self) -> None:
        # **여기가 필터의 핵심 시험이다** (FR-31). 만족해서 조용한 것과 포기하고
        # 떠난 것이 데이터상 같은 모양이라, 구분 못 하는 신호에 자격을 줄 수 없다.
        d = _filter().judge(author_account=BOT_ACCOUNT, grade=ResolutionGrade.IMPLICIT)
        assert not d
        assert d.reason == ExclusionReason.BOT_IMPLICIT


class TestGate:
    """ingest 가 원천을 얻는 유일한 문 (NFR-4)."""

    def test_mock_다섯_분기가_실제로_갈린다(self, tmp_path) -> None:
        conn = _collected(tmp_path)
        allowed = {a.question_id for a in _filter().ingestible_answers(conn)}

        assert allowed == {"Q-2", "Q-4"}
        # Q-2 봇 + 명시적 해결 → 통과 / Q-4 사람 → 통과
        # Q-1 답변 없음 / Q-3 봇 + 미해결 / Q-5 봇 + 미해결(후속만 달림) → 없거나 차단

    def test_차단된_원문은_Raw_Layer_에_그대로_남는다(self, tmp_path) -> None:
        # **지식으로 삼지 않는 것과 기록하지 않는 것은 다르다** (§5.3).
        # 여기서 지우면 통계와 FAQ 후보까지 함께 사라진다.
        conn = _collected(tmp_path)
        assert len(QnaStore(conn).answers_for("Q-3")) == 1
        assert "Q-3" not in {a.question_id for a in _filter().ingestible_answers(conn)}

    def test_질문_본문이_함께_나온다(self, tmp_path) -> None:
        # 답변만으로는 무엇에 대한 답인지 알 수 없어 개념을 뽑을 수 없다 (WBS-4.2.4).
        conn = _collected(tmp_path)
        answers = {a.question_id: a for a in _filter().ingestible_answers(conn)}
        assert answers["Q-2"].question_body == "승인 한도는 어떻게 정해지나요?"

    def test_나중에_눌린_해결_표시가_문을_연다(self, tmp_path) -> None:
        # §5.3.2 — 상향되는 그 순간에 ingest 자격이 발생한다. 이것이 열리지 않으면
        # 운영자가 Q6 을 아무리 처리해도 지식이 자라지 않는다.
        parent = MockParentSystem()
        conn = _collected(tmp_path, parent)
        assert "Q-3" not in {a.question_id for a in _filter().ingestible_answers(conn)}

        parent._resolutions["Q-3"] = Resolution(
            question_id="Q-3",
            resolved=True,
            method=ResolutionMethod.USER_MARKED,
            resolved_by="emp-102",
            resolved_at=_now(),
        )
        QnaCollector(parent, conn).collect()

        assert "Q-3" in {a.question_id for a in _filter().ingestible_answers(conn)}

    def test_봇_계정이_바뀌어도_옛_계정이_막힌다(self, tmp_path) -> None:
        # 계정을 갈아 끼운 뒤 옛 계정을 목록에서 빼면, 그때부터 옛 산출물이
        # 지식 원천이 된다. 목록이 여럿을 받는 이유다.
        conn = _collected(tmp_path)
        narrow = OutputFilter(frozenset({"svc-new-bot"}))
        assert "Q-3" in {a.question_id for a in narrow.ingestible_answers(conn)}

        wide = OutputFilter(frozenset({"svc-new-bot", BOT_ACCOUNT}))
        assert "Q-3" not in {a.question_id for a in wide.ingestible_answers(conn)}


class TestRuleIsStatedOnce:
    """규칙이 `judge()` 와 질의 두 벌로 쓰여 있다. **어긋나면 여기서 잡는다.**"""

    def test_문과_판정이_모든_적재분에서_일치한다(self, tmp_path) -> None:
        parent = MockParentSystem()
        conn = _collected(tmp_path, parent)
        # 암묵적 해결분을 하나 만들어 둔다 — 시드에는 없는 분기다.
        QnaStore(conn).record_resolution(
            Resolution(question_id="Q-5", resolved=True), collected_at=_now()
        )
        conn.commit()

        f = _filter()
        by_gate = {a.id for a in f.ingestible_answers(conn)}
        by_judge = {
            row["id"]
            for row in conn.execute(
                "SELECT a.id, a.author_account, r.grade FROM raw_answer a "
                "LEFT JOIN raw_resolution r ON r.question_id = a.question_id"
            )
            if f.judge(author_account=row["author_account"], grade=row["grade"])
        }
        assert by_gate == by_judge

    def test_암묵적_해결이_문에서도_막힌다(self, tmp_path) -> None:
        conn = _collected(tmp_path)
        QnaStore(conn).record_resolution(
            Resolution(question_id="Q-5", resolved=True), collected_at=_now()
        )
        conn.commit()

        assert QnaStore(conn).resolution_of("Q-5")["grade"] == ResolutionGrade.IMPLICIT
        assert "Q-5" not in {a.question_id for a in _filter().ingestible_answers(conn)}
