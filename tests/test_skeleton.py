"""골격이 실제로 서는지 확인한다 (WBS-4.1.1).

여기서 검증하는 것은 로직이 아니라 **구조**다 — 두 진입점이 뜨고, 설정이 안전한
기본값을 갖고, 단계별 대기열 노출(FR-59)이 지켜지는가.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.web.app import _queues_for_stage, create_app


def _settings(**over: object) -> Settings:
    return Settings(_env_file=None, **over)  # type: ignore[arg-type]


class TestSettings:
    def test_기본값은_대외_노출이_없는_단계다(self) -> None:
        # D49 — S0~S2 는 아무것도 내보내지 않는다. 설정을 잊어도 게재되지 않아야 한다.
        assert _settings().stage == "S0"

    def test_연동은_기본적으로_비어_있다(self) -> None:
        # 어댑터가 동작을 거부하게 한다. 빈 결과를 돌려주면 "질문이 없다"와 구분되지 않는다.
        cfg = _settings()
        assert cfg.parent_api_base_url == ""
        assert cfg.llm_base_url == ""

    def test_보존은_기본_무제한이다(self) -> None:
        # PO-4 — 사내 정책이 없어 두지 않기로 *결정* 했다. 값을 넣으면 만료가 켜진다.
        assert _settings().retention_days is None

    def test_저장소는_둘로_나뉜다(self) -> None:
        # D12 — 지식(파일+git)과 운영(DB)은 다른 자리에 산다.
        cfg = _settings()
        assert cfg.knowledge_dir != cfg.operations_db.parent / cfg.operations_db.name


class TestStageQueues:
    def test_S0_에는_대기열이_둘뿐이다(self) -> None:
        # FR-59 — 켜지지 않은 기능의 대기열은 표시하지 않는다.
        assert _queues_for_stage("S0") == ["Q4", "Q8"]

    def test_단계가_오를수록_대기열이_늘_뿐_줄지_않는다(self) -> None:
        prev: set[str] = set()
        for stage in ("S0", "S1", "S2", "S3", "S4", "S5"):
            cur = set(_queues_for_stage(stage))
            assert prev <= cur, f"{stage} 에서 대기열이 사라졌다"
            prev = cur

    def test_게재는_S3_부터다(self) -> None:
        # Q5(정정)·Q6(암묵적 해결 확인)은 게재가 있어야 생긴다.
        assert "Q5" not in _queues_for_stage("S2")
        assert "Q5" in _queues_for_stage("S3")

    def test_모르는_단계는_가장_안전한_쪽으로_떨어진다(self) -> None:
        assert _queues_for_stage("없는단계") == ["Q4", "Q8"]


class TestWebApp:
    def test_뜬다(self) -> None:
        client = TestClient(create_app(_settings()))
        assert client.get("/health").json()["status"] == "ok"

    def test_현재_단계와_대기열을_보여준다(self) -> None:
        body = TestClient(create_app(_settings(stage="S1"))).get("/").json()
        assert body["stage"] == "S1"
        assert body["queues"] == ["Q1", "Q4", "Q8"]


class TestWorker:
    def test_중단_요청을_받으면_멈춘다(self) -> None:
        # ADR-005 — 배치는 중단 가능해야 한다. 즉시 죽이지 않고 청크를 마치고 멈춘다.
        from agentic_service_desk.worker.runner import BatchRunner

        runner = BatchRunner(_settings())
        assert runner._stopping is False
        runner.request_stop(15, None)
        assert runner._stopping is True


class TestBoundaries:
    def test_어댑터_프로토콜이_일곱_표면을_선언한다(self) -> None:
        # D34 — 표면이 좁으면 기능이 소리 없이 죽는다. 목록이 줄지 않았는지 지킨다.
        from agentic_service_desk.adapters.parent_system import ParentSystem

        expected = {
            "list_questions",      # XR-1
            "list_answers",        # XR-2 — 대안 없는 항목
            "list_followups",      # XR-3
            "get_resolution",      # XR-4
            "publish_answer",      # XR-5
            "revise_answer",       # XR-7
            "publish_content",     # XR-6
        }
        assert expected <= set(dir(ParentSystem))

    def test_LLM_우선순위는_온라인이_먼저다(self) -> None:
        # ADR-005 — 온라인 지연이 곧 채택 실패(W4)로 이어진다.
        from agentic_service_desk.llm.gateway import Priority

        assert Priority.ONLINE < Priority.BATCH
