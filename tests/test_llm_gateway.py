"""WBS-4.1.3 — LLM 게이트웨이.

로직이 아니라 **안전장치**를 검증한다. NFR-1(소스코드 반출 금지)이 문서에만 있으면
언젠가 실수로 외부 엔드포인트가 들어가고, 그때는 이미 나간 뒤다.
"""

from __future__ import annotations

import time

import pytest

from agentic_service_desk.llm.arbiter import YieldSignal
from agentic_service_desk.llm.gateway import Priority


class TestLocalOnly:
    """엔드포인트 허용 판정은 `tests/test_llm_policy.py` 로 옮겼다.

    NFR-1 이 "로컬 엔드포인트"가 아니라 "실제 데이터 반출 금지"로 정확해지면서
    판정이 노출 상태에 따라 갈리기 때문이다 (ADR-005 §개발 환경).
    """


class TestYieldSignal:
    def test_표시가_없으면_기다리지_않는다(self, tmp_path) -> None:
        sig = YieldSignal(tmp_path / "online.marker")
        assert sig.online_is_waiting() is False
        assert sig.yield_if_needed(max_wait=0.1) is False

    def test_온라인이_쓰는_동안만_표시가_선다(self, tmp_path) -> None:
        sig = YieldSignal(tmp_path / "online.marker")
        with sig.online_in_use():
            assert sig.online_is_waiting() is True
        assert sig.online_is_waiting() is False

    def test_예외가_나도_표시를_치운다(self, tmp_path) -> None:
        # 표시가 남으면 배치가 최대 대기 시간만큼 굶는다.
        sig = YieldSignal(tmp_path / "online.marker")
        with pytest.raises(ValueError):
            with sig.online_in_use():
                raise ValueError("무언가 실패")
        assert sig.online_is_waiting() is False

    def test_낡은_표시는_무시한다(self, tmp_path) -> None:
        # 온라인 프로세스가 죽어 표시를 못 지운 경우, 배치가 영원히 기다리면 안 된다.
        marker = tmp_path / "online.marker"
        marker.write_text(f"999 {time.time() - 10_000}\n", encoding="utf-8")
        assert YieldSignal(marker).online_is_waiting() is False

    def test_깨진_표시도_무시한다(self, tmp_path) -> None:
        marker = tmp_path / "online.marker"
        marker.write_text("쓰레기\n", encoding="utf-8")
        assert YieldSignal(marker).online_is_waiting() is False

    def test_배치는_무한정_굶지_않는다(self, tmp_path) -> None:
        # 지식이 안 자라는 것도 실패다(§8.2). 양보에 상한을 둔다.
        marker = tmp_path / "online.marker"
        marker.write_text(f"1 {time.time()}\n", encoding="utf-8")
        sig = YieldSignal(marker)
        started = time.monotonic()
        assert sig.yield_if_needed(poll_seconds=0.05, max_wait=0.2) is True
        assert time.monotonic() - started < 1.0


class TestPriority:
    def test_온라인이_배치보다_앞선다(self) -> None:
        assert Priority.ONLINE < Priority.BATCH


class TestThinkingBlocks:
    """사고형 모델은 `<think>` 블록을 본문에 섞어 보낸다 (2026-08-28 MiniMax-M3 확인).

    그대로 두면 **사고 과정이 지식 항목이나 게재 답변에 실려 나간다.**
    """

    def test_사고_블록을_걷어낸다(self) -> None:
        from agentic_service_desk.llm.local import _strip_thinking

        raw = '<think>\n사용자가 OK 를 원한다.\n</think>\n\nOK'
        assert _strip_thinking(raw) == "OK"

    def test_여러_블록도_걷어낸다(self) -> None:
        from agentic_service_desk.llm.local import _strip_thinking

        assert _strip_thinking("<think>가</think>답<think>나</think>변") == "답변"

    def test_블록이_없으면_그대로다(self) -> None:
        from agentic_service_desk.llm.local import _strip_thinking

        assert _strip_thinking("그냥 답변") == "그냥 답변"

    def test_빈_본문도_안전하다(self) -> None:
        from agentic_service_desk.llm.local import _strip_thinking

        assert _strip_thinking("") == ""
