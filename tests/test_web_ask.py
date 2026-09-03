"""지식에 묻기 — 운영자 질의 콘솔 (FR-60, WBS-5.7.1).

§8.1 은 대시보드가 "조회 화면이 아니라 작업대"라고 못 박았지만 경계한 것은
**차트 구경 화면이 되는 것**이다. 같은 절이 이렇게도 적었다 — *"여기서 할 수
없는 일은 아무도 할 수 없는 일이 된다."* 지식베이스에 물어볼 수 있는 사람이
아무도 없었다.

**저장하지 않는 것이 이 요구의 절반이다.** 검수를 지나지 않은 산출이 답변
이력에 남으면 지표가 오염되고 게재 출구가 하나라는 규약이 흐려진다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind, KnowledgeItem, Provenance
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.web.app import create_app


def _settings(tmp_path, **over) -> Settings:  # noqa: ANN001, ANN003
    base = dict(
        _env_file=None,
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
    )
    return Settings(**(base | over))  # type: ignore[arg-type]


def _kb(tmp_path, title="주문 체결 확인 방법", body="inquire_fills 로 확인한다.") -> None:  # noqa: ANN001
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    repo.save(
        KnowledgeItem(
            title=title,
            body=body,
            provenance=[Provenance(commit="a" * 40, path="src/x.py")],
            invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90),
        )
    )


def _client(tmp_path, **over) -> TestClient:  # noqa: ANN001, ANN003
    return TestClient(create_app(_settings(tmp_path, **over)))


class TestScreen:
    def test_화면이_뜬다(self, tmp_path) -> None:
        _kb(tmp_path)
        r = _client(tmp_path).get("/ask")
        assert r.status_code == 200
        assert "지식에 묻기" in r.text

    def test_단계를_보지_않는다(self, tmp_path) -> None:
        # 지식베이스는 S0 부터 있고 이 화면은 아무것도 내보내지 않는다.
        # FR-59 의 점증은 **내보내는** 기능의 대기열을 다룬다.
        _kb(tmp_path)
        assert _client(tmp_path, stage="S0").get("/ask").status_code == 200

    def test_빈_질문은_묻지_않는다(self, tmp_path) -> None:
        _kb(tmp_path)
        r = _client(tmp_path).post("/ask", data={"question": "   "})
        assert "물어볼 것을 적는다" in r.text


class TestNothingIsStored:
    """**이것이 요구의 절반이다** — 묻는 자리이지 답하는 자리가 아니다."""

    def test_묻고_나도_답변_이력이_남지_않는다(self, tmp_path) -> None:
        _kb(tmp_path)
        client = _client(tmp_path)
        client.post("/ask", data={"question": "주문 체결을 어떻게 확인하나요"})

        conn = connect(tmp_path / "ops.sqlite3")
        initialize(conn)
        for table in ("answer_record", "ticket", "draft"):
            rows = conn.execute(
                "SELECT COUNT(*) n FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if rows["n"]:
                assert conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"] == 0, table
        conn.close()

    def test_지식베이스도_그대로다(self, tmp_path) -> None:
        _kb(tmp_path)
        repo = KnowledgeRepository(tmp_path / "knowledge")
        before = {s.item.id for s in repo.scan()[0]}

        _client(tmp_path).post("/ask", data={"question": "주문 체결"})

        assert {s.item.id for s in repo.scan()[0]} == before


class TestNoGrounding:
    """근거가 없으면 답을 만들지 않는다 (FR-18) — 그 사실이 화면에 보여야 한다."""

    def test_무관한_질문은_멈춘_이유를_보여_준다(self, tmp_path) -> None:
        _kb(tmp_path)
        r = _client(tmp_path).post("/ask", data={"question": "김치찌개 끓이는 법"})
        assert "답을 만들지 않았다" in r.text
        assert "티켓으로 갔을 것이다" in r.text

    def test_근거가_걸리면_목록에_보인다(self, tmp_path) -> None:
        # 모델이 없어도 조회까지는 돈다 — "지식베이스가 이만큼은 갖고 있다"도 답이다.
        _kb(tmp_path)
        r = _client(tmp_path).post("/ask", data={"question": "주문 체결 확인"})
        assert "주문 체결 확인 방법" in r.text
