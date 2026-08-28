"""WBS-4.1.3 — LLM 게이트웨이.

로직이 아니라 **안전장치**를 검증한다. NFR-1(소스코드 반출 금지)이 문서에만 있으면
언젠가 실수로 외부 엔드포인트가 들어가고, 그때는 이미 나간 뒤다.
"""

from __future__ import annotations

import time

import pytest

from agentic_service_desk.llm.arbiter import YieldSignal
from agentic_service_desk.llm.gateway import Priority
from agentic_service_desk.llm.local import RemoteEndpointRejected, assert_local_endpoint


class TestLocalOnly:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434",
            "http://127.0.0.1:8080",
            "http://192.168.1.50:8000",
            "http://10.0.0.5:9000",
            "http://gpu-box.local:11434",
        ],
    )
    def test_로컬은_통과한다(self, url: str) -> None:
        assert_local_endpoint(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "https://api.anthropic.com",
            "http://8.8.8.8:11434",
            "https://example.com/llm",
        ],
    )
    def test_외부는_거부한다(self, url: str) -> None:
        # NFR-1 — 지식 항목은 소스코드 파생이므로 외부 전송이 곧 반출이다.
        with pytest.raises(RemoteEndpointRejected):
            assert_local_endpoint(url)

    def test_판정이_애매하면_거부한다(self) -> None:
        # 반출은 되돌릴 수 없으므로 모호할 때는 막는 쪽으로 기운다.
        with pytest.raises(RemoteEndpointRejected):
            assert_local_endpoint("http://some-internal-host:8000")


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
