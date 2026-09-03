"""대상별 정제 (FR-61, WBS-5.7.2).

같은 답을 두 사람에게 — 질문한 사람에게는 내부 경로가 소음이고, 원인을 찾는
사람에게는 그것이 정확히 필요한 것이다.

**정제는 다시 쓰는 것이지 새로 답하는 것이 아니다.** 사실을 보태면 검수를 지나지
않은 글에 근거 없는 말이 섞인다.
"""

from __future__ import annotations

from pathlib import Path

from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.search import Hit
from agentic_service_desk.pipeline import audience
from agentic_service_desk.pipeline.answer import Confidence, Draft, Statement
from agentic_service_desk.pipeline.review import ReviewInput


def _hit(item_id: str, title: str, body: str) -> Hit:
    return Hit(
        item=KnowledgeItem(
            title=title,
            body=body,
            provenance=[Provenance(commit="a" * 40)],
            invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90),
            id=item_id,
        ),
        path=Path("x.md"),
        score=1.0,
    )


def _draft(*, unanswered: tuple[str, ...] = ()) -> Draft:
    return Draft(
        statements=(
            Statement(text="확인은 inquire_fills 로 한다.", confidence=Confidence.CONFIRMED),
            Statement(text="지연이 있을 수 있다.", confidence=Confidence.INFERRED),
        ),
        grounding=("k-1",),
        unanswered=unanswered,
    )


HITS = [_hit("k-1", "체결 확인", "inquire_fills 로 확인한다. 한도는 3000 이다.")]


class TestPrompt:
    def test_초안과_근거_원문만_준다(self) -> None:
        # 검수와 같은 입력 규율이다 — 질문 밖의 무엇도 정제에 닿지 않는다.
        p = audience.build_prompt("어떻게 확인하나요", _draft(), HITS)
        assert "inquire_fills 로 확인한다" in p
        assert "어떻게 확인하나요" in p

    def test_사실을_더하지_말라는_지시가_있다(self) -> None:
        p = audience.build_prompt("q", _draft(), HITS)
        assert "새 사실을 더하지 않는다" in p

    def test_두_대상을_구분해_지시한다(self) -> None:
        p = audience.build_prompt("q", _draft(), HITS)
        assert "customer" in p and "developer" in p
        assert "내부 파일 경로" in p

    def test_불확실한_진술을_단정하지_말라고_한다(self) -> None:
        # `추론` 이 정제에서 사실로 굳으면 강도 표시가 무의미해진다 (ADR-007).
        p = audience.build_prompt("q", _draft(), HITS)
        assert "단정하지 않는다" in p

    def test_밝힌_경계도_함께_준다(self) -> None:
        p = audience.build_prompt("q", _draft(unanswered=("재시도 정책은 모른다",)), HITS)
        assert "재시도 정책은 모른다" in p


class TestParse:
    def _review(self) -> ReviewInput:
        return ReviewInput.of(_draft(), HITS)

    def test_둘을_읽는다(self) -> None:
        out = audience.parse({"customer": "이렇게 하세요", "developer": "여기를 보세요"},
                             self._review())
        assert [r.audience for r in out] == [audience.CUSTOMER, audience.DEVELOPER]

    def test_한쪽이_비면_그쪽은_만들지_않는다(self) -> None:
        # 빈 칸을 보여 주는 것보다 없다고 말하는 편이 정직하다.
        out = audience.parse({"customer": "이렇게 하세요", "developer": "  "}, self._review())
        assert [r.audience for r in out] == [audience.CUSTOMER]

    def test_근거에_있는_수치는_잡지_않는다(self) -> None:
        out = audience.parse({"customer": "한도는 3000 입니다."}, self._review())
        assert out[0].ungrounded == ()

    def test_근거에_없는_수치를_잡는다(self) -> None:
        # **정제가 사실을 보탰을 수 있다** — 검수 P1 과 같은 대조를 한 번 더 건다.
        out = audience.parse({"customer": "한도는 9999 입니다."}, self._review())
        assert "9999" in out[0].ungrounded

    def test_아무것도_없으면_빈_목록이다(self) -> None:
        assert audience.parse({}, self._review()) == []
