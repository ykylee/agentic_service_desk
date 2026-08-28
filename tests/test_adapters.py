"""WBS-4.1.2 — 모 시스템 어댑터 (mock 기반, ADR-008).

**mock 이 프로토콜을 만족하는 것이 곧 계약의 검증**이다. 문서로만 쓴 스키마는
어긋나지만, 실제로 도는 구현이 프로토콜을 만족하면 그렇지 않다.

시나리오가 §5.3 되먹임 차단의 모든 분기를 덮는지도 여기서 지킨다 — 그래야
4.2.3 필터를 실제로 시험할 수 있다.
"""

from __future__ import annotations

import pytest

from agentic_service_desk.adapters.contract import ResolutionMethod
from agentic_service_desk.adapters.factory import build_parent_system
from agentic_service_desk.adapters.mock import BOT_ACCOUNT, MockParentSystem
from agentic_service_desk.adapters.parent_system import NotConfigured, ParentSystem
from agentic_service_desk.config import Settings


def _settings(**over: object) -> Settings:
    return Settings(_env_file=None, **over)  # type: ignore[arg-type]


class TestContractSatisfied:
    def test_mock_이_프로토콜을_만족한다(self) -> None:
        # 이것이 계약의 검증이다 (ADR-008).
        assert isinstance(MockParentSystem(), ParentSystem)

    def test_http_구현도_같은_프로토콜을_만족한다(self) -> None:
        # 채워지지 않았어도 형태는 맞아야 한다 — 나중에 갈아 끼우기 위해서다.
        from agentic_service_desk.adapters.http import HttpParentSystem

        assert isinstance(HttpParentSystem("http://parent.local"), ParentSystem)


class TestScenarios:
    """§5.3 되먹임 차단의 모든 분기가 시드에 있는가."""

    def setup_method(self) -> None:
        self.parent = MockParentSystem()

    def test_답변_없는_질문이_있다(self) -> None:
        assert self.parent.list_answers("Q-1") == []

    def test_봇_답변에_명시적_해결이_붙은_건이_있다(self) -> None:
        # ingest 자격 있음 (D8). 배제 해제의 조건이다.
        answers = self.parent.list_answers("Q-2")
        assert answers[0].author_account == BOT_ACCOUNT
        res = self.parent.get_resolution("Q-2")
        assert res.is_explicit is True
        assert res.method is ResolutionMethod.USER_MARKED

    def test_봇_답변에_해결이_없는_건이_있다(self) -> None:
        # 필터의 핵심 시험 — 이것이 지식이 되면 오답이 굳는다.
        assert self.parent.list_answers("Q-3")[0].author_account == BOT_ACCOUNT
        assert self.parent.get_resolution("Q-3").is_explicit is False

    def test_사람_답변이_있다(self) -> None:
        assert self.parent.list_answers("Q-4")[0].author_account != BOT_ACCOUNT

    def test_후속이_달린_질문이_있다(self) -> None:
        # D9 — 파이프라인 재실행의 트리거다.
        assert len(self.parent.list_followups("Q-5")) == 1


class TestResolution:
    def test_모_시스템이_주는_해결은_전부_명시적이다(self) -> None:
        # 암묵적 해결은 모 시스템이 아는 사실이 아니라 우리가 타임아웃으로 판정한다.
        p = MockParentSystem()
        assert p.get_resolution("Q-2").is_explicit is True

    def test_해결_표시가_없으면_명시적이_아니다(self) -> None:
        assert MockParentSystem().get_resolution("Q-3").is_explicit is False


class TestWrites:
    def setup_method(self) -> None:
        self.parent = MockParentSystem()

    def test_게재는_봇_계정으로_나간다(self) -> None:
        aid = self.parent.publish_answer("Q-1", "이렇게 하시면 됩니다.", ["k-1"])
        posted = self.parent.list_answers("Q-1")[0]
        assert posted.id == aid
        assert posted.author_account == BOT_ACCOUNT

    def test_없는_질문에는_게재할_수_없다(self) -> None:
        with pytest.raises(KeyError):
            self.parent.publish_answer("없는질문", "본문", [])

    def test_정정은_사유를_본문에_남긴다(self) -> None:
        # PO-1 — 조용히 고치면 이미 읽은 사람이 잘못된 내용을 갖고 간다.
        aid = self.parent.publish_answer("Q-1", "옛 내용", [])
        self.parent.revise_answer(aid, "새 내용", "근거가 stale 되었다")
        revised = self.parent.list_answers("Q-1")[0]
        assert "새 내용" in revised.body
        assert "근거가 stale" in revised.body
        assert revised.revised_at is not None

    def test_문서_면은_멱등하다(self) -> None:
        # D46 — 살아있는 문서는 upsert 다.
        self.parent.upsert_document("guide/approval", "결재 가이드", "첫 판")
        self.parent.upsert_document("guide/approval", "결재 가이드", "둘째 판")
        assert len(self.parent.documents) == 1
        assert "둘째 판" in self.parent.documents["guide/approval"]

    def test_발행_면은_회차가_누적된다(self) -> None:
        # D46 — 발행물은 create 다. 되돌릴 수 없다.
        self.parent.create_publication("8월 뉴스레터", "본문")
        self.parent.create_publication("9월 뉴스레터", "본문")
        assert len(self.parent.publications) == 2


class TestFactory:
    def test_기본은_실제_연동이다(self) -> None:
        # ADR-008 — mock 이 프로덕션에서 도는 사고를 막는다.
        assert _settings().parent_adapter == "http"

    def test_연동_주소가_없으면_거부한다(self) -> None:
        # 빈 결과를 돌려주면 "질문이 없다"와 구분되지 않는다.
        with pytest.raises(NotConfigured):
            build_parent_system(_settings())

    def test_mock_은_명시적으로_골라야_하고_경고를_남긴다(self) -> None:
        with pytest.warns(RuntimeWarning, match="mock"):
            parent = build_parent_system(_settings(parent_adapter="mock"))
        assert isinstance(parent, MockParentSystem)
