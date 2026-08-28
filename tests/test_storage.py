"""WBS-4.1.4 — 저장소 골격.

지식(파일+git)과 운영(DB)이 나뉘어 있는지, 그리고 **컨셉의 제약이 자료구조로
집행되는지**를 본다.
"""

from __future__ import annotations

import pytest

from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.layout import ensure_bundle, is_reserved
from agentic_service_desk.operations.schema import connect, initialize


class TestProvenance:
    def test_출처_없이는_만들_수_없다(self) -> None:
        # D3 — 출처는 메타데이터가 아니라 1급 시민이다.
        with pytest.raises(ValueError):
            Provenance()

    def test_커밋이나_QnA_중_하나면_된다(self) -> None:
        assert Provenance(commit="a1b2c3d").commit == "a1b2c3d"
        assert Provenance(qna="Q-1").qna == "Q-1"


class TestInvalidation:
    def test_연결형은_묶을_대상이_필요하다(self) -> None:
        with pytest.raises(ValueError):
            Invalidation(kind=InvalidationKind.LINKED)

    def test_주기형은_주기가_필요하다(self) -> None:
        with pytest.raises(ValueError):
            Invalidation(kind=InvalidationKind.PERIODIC)

    def test_둘_다_유효하게_만들_수_있다(self) -> None:
        assert Invalidation(kind=InvalidationKind.LINKED, refs=("src/a.py",))
        assert Invalidation(kind=InvalidationKind.PERIODIC, period_days=180)


class TestKnowledgeItem:
    def _item(self, **over: object) -> KnowledgeItem:
        base = dict(
            title="결재 한도가 결정되는 규칙",
            body="부서 등급에 따라 정해진다.",
            provenance=[Provenance(commit="a1b2c3d", path="src/approval/limit.py")],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("src/approval/limit.py",)),
        )
        base.update(over)
        return KnowledgeItem(**base)  # type: ignore[arg-type]

    def test_id_는_경로와_무관한_불변값이다(self) -> None:
        # ADR-002 — 입도 조정으로 파일이 이동해도 답변 이력의 링크가 살아남아야 한다.
        a, b = self._item(), self._item()
        assert a.id != b.id
        assert a.id.startswith("k-")

    def test_OKF_타입을_갖는다(self) -> None:
        assert self._item().okf_type == "knowledge"

    def test_사람_편집_표시는_기본이_꺼져_있다(self) -> None:
        # D38 — 표시가 있으면 에이전트가 덮어쓰지 않고 모순으로 올린다.
        assert self._item().edited_by_human is False


class TestLayout:
    def test_번들_뼈대를_만든다(self, tmp_path) -> None:
        root = tmp_path / "knowledge"
        ensure_bundle(root)
        assert (root / "index.md").exists()
        assert (root / "log.md").exists()

    def test_두_번_불러도_덮어쓰지_않는다(self, tmp_path) -> None:
        root = tmp_path / "knowledge"
        ensure_bundle(root)
        (root / "index.md").write_text("사람이 고친 내용", encoding="utf-8")
        ensure_bundle(root)
        assert (root / "index.md").read_text(encoding="utf-8") == "사람이 고친 내용"

    def test_예약_파일은_지식_항목이_아니다(self, tmp_path) -> None:
        # OKF §3.1 — index.md 와 log.md 는 어느 계층에서든 개념으로 쓸 수 없다.
        assert is_reserved(tmp_path / "index.md")
        assert is_reserved(tmp_path / "sub" / "log.md")
        assert not is_reserved(tmp_path / "approval-limit.md")


class TestOperationsSchema:
    def test_스키마가_만들어진다(self, tmp_path) -> None:
        conn = connect(tmp_path / "ops.sqlite3")
        initialize(conn)
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {
            "qna_item", "ticket", "ticket_resolution",
            "answer_record", "answer_grounding",
            "content_publication", "ingest_checkpoint",
        } <= names

    def test_여러_번_초기화해도_안전하다(self, tmp_path) -> None:
        conn = connect(tmp_path / "ops.sqlite3")
        initialize(conn)
        initialize(conn)

    def test_WAL_모드로_열린다(self, tmp_path) -> None:
        # ADR-002 — 온라인과 배치가 동시에 접근한다.
        conn = connect(tmp_path / "ops.sqlite3")
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

    def test_QnA_항목과_티켓은_별개_테이블이다(self, tmp_path) -> None:
        # D15 — 서로 다른 질문에 답하므로 상태가 서로를 결정하지 않는다.
        conn = connect(tmp_path / "ops.sqlite3")
        initialize(conn)
        qna_cols = {r["name"] for r in conn.execute("PRAGMA table_info(qna_item)")}
        ticket_cols = {r["name"] for r in conn.execute("PRAGMA table_info(ticket)")}
        assert "state" in qna_cols and "state" in ticket_cols
        # 티켓은 QnA 없이도 존재할 수 있다 — 출처가 넷이기 때문이다 (§6.4.3)
        assert conn.execute("PRAGMA table_info(ticket)").fetchall()
        nullable = {r["name"] for r in conn.execute("PRAGMA table_info(ticket)") if not r["notnull"]}
        assert "qna_item_id" in nullable

    def test_근거는_버전으로_고정된다(self, tmp_path) -> None:
        # D20 — 링크만 두면 지식이 갱신된 뒤 당시 근거를 재현할 수 없다.
        conn = connect(tmp_path / "ops.sqlite3")
        initialize(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(answer_grounding)")}
        assert {"knowledge_item_id", "pinned_commit"} <= cols

    def test_답변_이력이_작성_주체를_남긴다(self, tmp_path) -> None:
        # D7 — 되먹임 차단의 판정 근거다.
        conn = connect(tmp_path / "ops.sqlite3")
        initialize(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(answer_record)")}
        assert "author_kind" in cols
