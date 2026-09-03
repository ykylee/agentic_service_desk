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


class TestCustomerPrompt:
    """게재 경로 안의 정제 — 진술 하나를 진술 하나로."""

    def test_초안과_근거_원문만_준다(self) -> None:
        # 검수와 같은 입력 규율이다 — 질문 밖의 무엇도 정제에 닿지 않는다.
        p = audience.build_customer_prompt("어떻게 확인하나요", _draft(), HITS)
        assert "inquire_fills 로 확인한다" in p
        assert "어떻게 확인하나요" in p

    def test_내부_식별자를_쓰지_말라고_한다(self) -> None:
        p = audience.build_customer_prompt("q", _draft(), HITS)
        assert "내부 말을 쓰지 않는다" in p
        assert "새 사실을 더하지 않는다" in p

    def test_한_편의_글로_쓰라고_한다(self) -> None:
        # 진술 1:1 을 강제했더니 **문단 나열이 되어 사람이 쓴 글로 읽히지 않았다**
        # (2026-09-03 라이브). 정제의 목적을 제약이 이겼다.
        p = audience.build_customer_prompt("q", _draft(), HITS)
        assert "질문에 답하는 글 한 편" in p
        assert "항목을 나열하지 말고" in p

    def test_묻는_것에만_답하라고_한다(self) -> None:
        # 길어질수록 답이 아니라 자료가 된다.
        assert "아는 것을 다 늘어놓지 않는다" in audience.build_customer_prompt("q", _draft(), HITS)

    def test_불확실한_진술을_단정하지_말라고_한다(self) -> None:
        # `추론` 이 정제에서 사실로 굳으면 강도 표시가 무의미해진다 (ADR-007).
        assert "단정하지 않는다" in audience.build_customer_prompt("q", _draft(), HITS)

    def test_질문의_언어를_못_박는다(self) -> None:
        # FR-17 — 1단계가 판정한 언어로 3단계가 썼는데 정제가 조용히 옮기면
        # **질문한 사람이 읽을 수 없는 답이 나간다.** 2026-09-03 라이브에서
        # 한국어 질문의 정제가 영어로 나오며 드러났다.
        assert "한국어 로 쓴다" in audience.build_customer_prompt("q", _draft(), HITS, "ko")
        assert "영어 로 쓴다" in audience.build_customer_prompt("q", _draft(), HITS, "en")


class TestParseCustomer:
    def test_글을_읽는다(self) -> None:
        assert audience.parse_customer({"answer": " 이렇게 확인하시면 됩니다. "}, _draft()) == (
            "이렇게 확인하시면 됩니다."
        )

    def test_비면_빈_문자열이다(self) -> None:
        # 비어 있으면 원본을 쓴다는 뜻이다.
        assert audience.parse_customer({"answer": "  "}, _draft()) == ""
        assert audience.parse_customer({}, _draft()) == ""


class TestDeveloperPrompt:
    """개발자용은 **나가지 않는다** — 운영자 화면 전용이다."""

    def test_경로와_식별자를_살리라고_한다(self) -> None:
        p = audience.build_developer_prompt("q", _draft(), HITS)
        assert "그대로 살리고" in p
        assert "새 사실을 더하지 않는다" in p

    def test_밝힌_경계도_함께_준다(self) -> None:
        p = audience.build_developer_prompt("q", _draft(unanswered=("재시도 정책은 모른다",)), HITS)
        assert "재시도 정책은 모른다" in p


class TestRenderOf:
    def _review(self) -> ReviewInput:
        return ReviewInput.of(_draft(), HITS)

    def test_비면_만들지_않는다(self) -> None:
        assert audience.render_of(audience.CUSTOMER, "  ", self._review()) is None

    def test_근거에_있는_수치는_잡지_않는다(self) -> None:
        r = audience.render_of(audience.CUSTOMER, "한도는 3000 입니다.", self._review())
        assert r.ungrounded == ()

    def test_근거에_없는_수치를_잡는다(self) -> None:
        # **정제가 사실을 보탰을 수 있다** — 검수 P1 과 같은 대조를 한 번 더 건다.
        r = audience.render_of(audience.CUSTOMER, "한도는 9999 입니다.", self._review())
        assert "9999" in r.ungrounded
