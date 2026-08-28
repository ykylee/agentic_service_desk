"""WBS-4.4.4 — 검수 (FR-20·21·22, §5.5).

**검수는 재생성이 아니라 대조 작업이다.** 여기서 지키는 것은 넷.

    1. **생성과 분리된다** — 입력은 초안과 근거 원문뿐이고 질문 의도도 생성 추론도
       주지 않는다 (FR-20). 의도를 알면 "그럴 만했다"로 기운다
    2. P1~P5 가 반려 사유가 된다 (FR-21)
    3. 반려 사유별 **분포를 집계할 수 있다** (FR-22) — 반려된 초안도 남긴다
    4. **기계적으로 잡히는 것은 모델에 묻지 않는다** (§5.5.1)
"""

from __future__ import annotations

from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.search import Hit
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline.answer import Confidence, Draft, Statement
from agentic_service_desk.pipeline.review import (
    PASSED,
    REJECTED,
    Reject,
    ReviewInput,
    Reviewer,
    build_prompt,
    check_stale,
    check_ungrounded_numbers,
    distribution,
    parse_verdict,
    record,
    unmatched_terms,
)

from conftest import FakeHarness

SOURCE = "결재 한도는 부서 등급으로 정해진다. 등급이 바뀌면 한도도 바뀐다."


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _input(body: str, *, stale: bool = False, source: str = SOURCE) -> ReviewInput:
    return ReviewInput(
        draft_body=body,
        grounding=("k-1",),
        source_text={"k-1": source},
        stale_ids=frozenset({"k-1"}) if stale else frozenset(),
    )


def _hit(stale: bool = False) -> Hit:
    item = KnowledgeItem(
        title="결재 한도 결정 규칙",
        body=SOURCE,
        provenance=[Provenance(commit="a" * 40)],
        invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90),
        stale=stale,
    )
    from pathlib import Path

    return Hit(item=item, path=Path("x.md"), score=1.0)


class TestSeparation:
    """FR-20 — 검수 입력에 생성 컨텍스트가 없다."""

    def test_입력이_초안과_근거_원문뿐이다(self) -> None:
        draft = Draft(
            statements=(
                Statement(text="결재 한도는 등급으로 정해진다.", confidence=Confidence.CONFIRMED,
                          grounding=("k-1",)),
            ),
            grounding=("k-1",),
            unanswered=("현재 값은 모른다",),
        )
        hit = _hit()
        review = ReviewInput.of(draft, [hit])

        assert review.draft_body == "결재 한도는 등급으로 정해진다."
        assert set(review.source_text) == {hit.item.id}
        assert not hasattr(review, "question")

    def test_프롬프트에_질문이_없다(self) -> None:
        # 의도를 알면 "그럴 만했다"로 기울고, 모르면 글에 적힌 것만 보게 된다.
        prompt = build_prompt(_input("결재 한도는 등급으로 정해진다."))
        assert "질문" not in prompt.replace("어떤 질문에 대한 것인지", "")
        assert "모른다" in prompt  # 어떤 질문인지 모른다고 명시한다

    def test_불확실성_표시도_넘기지_않는다(self) -> None:
        # 생성 측의 자기 평가를 보면 그것에 기대게 된다 — 대조가 아니라 확인이 된다.
        draft = Draft(
            statements=(
                Statement(text="가", confidence=Confidence.THIN, grounding=("k-1",)),
            ),
            grounding=("k-1",),
        )
        prompt = build_prompt(ReviewInput.of(draft, [_hit()]))
        assert "근거 얇음" not in prompt


class TestMechanical:
    """§5.5.1 — 확정 가능한 것을 확률적 판정에 맡기지 않는다."""

    def test_낡은_근거는_P4_다(self) -> None:
        verdict = check_stale(_input("결재 한도는 등급으로 정해진다.", stale=True))
        assert verdict.reason is Reject.P4

    def test_낡지_않으면_통과시킨다(self) -> None:
        assert check_stale(_input("결재 한도는 등급으로 정해진다.")) is None

    def test_근거에_없는_수치는_P1_이다(self) -> None:
        verdict = check_ungrounded_numbers(_input("한도는 보통 300만원입니다."))
        assert verdict.reason is Reject.P1
        assert "300" in verdict.detail

    def test_근거에_있는_수치는_통과한다(self) -> None:
        assert check_ungrounded_numbers(
            _input("한도는 300만원이다.", source="한도는 300만원으로 정해진다.")
        ) is None

    def test_기계가_잡으면_모델을_부르지_않는다(self) -> None:
        # 반려는 하나면 충분하고, 확정된 것을 다시 물을 이유가 없다.
        harness = FakeHarness('{"passed": true}')
        verdict = Reviewer(harness).review(_input("한도는 등급으로 정해진다.", stale=True))

        assert verdict.reason is Reject.P4
        assert harness.prompts == []

    def test_수치가_먼저_걸려도_모델을_부르지_않는다(self) -> None:
        harness = FakeHarness('{"passed": true}')
        Reviewer(harness).review(_input("보통 300만원입니다."))
        assert harness.prompts == []


class TestSemantic:
    """P2·P3·P5 는 의미 판정이다."""

    def test_통과시킨다(self) -> None:
        verdict = Reviewer(FakeHarness('{"passed": true}')).review(
            _input("결재 한도는 부서 등급으로 정해진다.")
        )
        assert verdict.passed

    def test_사유를_받아_분류한다(self) -> None:
        verdict = Reviewer(
            FakeHarness('{"passed": false, "reason": "P2", "detail": "항상 이라고 단정"}')
        ).review(_input("한도는 항상 등급으로만 정해진다."))
        assert verdict.reason is Reject.P2
        assert verdict.detail

    def test_사유_없는_반려는_가장_넓은_것으로_분류한다(self) -> None:
        # 사유 없는 반려는 기록으로 쓸 수 없다 (§5.5.6).
        assert parse_verdict('{"passed": false}').reason is Reject.P5

    def test_모르는_사유도_분류된다(self) -> None:
        assert parse_verdict('{"passed": false, "reason": "P99"}').reason is Reject.P5


class TestNoReviewIsNotPass:
    """§5.6.1 — 형식적 승인은 무검증보다 나쁘다."""

    def test_판정할_수_없으면_통과시키지_않는다(self) -> None:
        # 검수가 없는 것과 통과한 것은 다르며, 후자는 "검증했다"는 라벨을 남긴다.
        verdict = Reviewer(None).review(_input("결재 한도는 등급으로 정해진다."))
        assert not verdict.passed
        assert verdict.checked_by == "none"

    def test_검수가_터져도_통과시키지_않는다(self) -> None:
        verdict = Reviewer(FakeHarness("JSON 이 아니다")).review(
            _input("결재 한도는 등급으로 정해진다.")
        )
        assert not verdict.passed


class TestRecord:
    """FR-22 — 반려 사유별 분포를 집계할 수 있다."""

    def test_반려된_초안도_남는다(self, tmp_path) -> None:
        # 버리면 왜 반려됐는지의 분포를 잃는다 (§5.5.6).
        conn = _conn(tmp_path)
        review = _input("보통 300만원입니다.")
        verdict = Reviewer(FakeHarness()).review(review)
        record(conn, review=review, verdict=verdict)

        row = conn.execute("SELECT * FROM review").fetchone()
        assert row["outcome"] == REJECTED
        assert row["reason"] == "P1"
        assert "300만원" in row["draft_body"]

    def test_통과도_남는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        review = _input("결재 한도는 등급으로 정해진다.")
        record(conn, review=review, verdict=Reviewer(FakeHarness('{"passed": true}')).review(review))
        assert conn.execute("SELECT outcome FROM review").fetchone()["outcome"] == PASSED

    def test_사유별_분포가_나온다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        for body, harness in [
            ("보통 300만원입니다.", FakeHarness()),
            ("보통 500만원입니다.", FakeHarness()),
            ("한도는 등급으로 정해진다.", FakeHarness('{"passed": true}')),
        ]:
            review = _input(body)
            record(conn, review=review, verdict=Reviewer(harness).review(review))

        dist = distribution(conn)
        assert (dist.passed, dist.rejected) == (1, 2)
        assert dist.by_reason["P1"] == 2
        assert dist.rejection_rate > 0.6

    def test_근거_부족_신호를_읽을_수_있다(self, tmp_path) -> None:
        # P1·P5 가 몰리면 근거가 부족하다는 뜻이다 → Q8 로 연결된다 (§5.5.6).
        conn = _conn(tmp_path)
        review = _input("보통 300만원입니다.")
        record(conn, review=review, verdict=Reviewer(FakeHarness()).review(review))
        assert distribution(conn).grounding_gap_share == 1.0

    def test_아무것도_없으면_0_이다(self, tmp_path) -> None:
        dist = distribution(_conn(tmp_path))
        assert dist.total == 0
        assert dist.rejection_rate == 0.0


class TestUnmatchedTerms:
    def test_근거에_없는_낱말을_알려준다(self) -> None:
        # 판정에는 쓰지 않는다 — 바꿔 쓴 문장은 낱말이 다른 것이 정상이다.
        # 사람이 어디를 먼저 볼지 알려주는 재료로만 쓴다.
        terms = unmatched_terms(_input("결재 한도는 인사이동과 무관하다."))
        assert "인사이동과" in terms
        assert "결재" not in terms
