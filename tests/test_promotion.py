"""WBS-4.3.4 — 승격 경로 A (FR-15, D40, §6.8).

**1국면에서는 티켓 해결의 승격이 지식 성장의 주 경로다.** 종결 기록을 처음부터 지식
항목의 초안 형식으로 받아 뒀으므로(§6.5.1) 여기서 하는 일은 번역이 아니라 옮겨 담는
것이다.

여기서 지키는 것은 넷.

    1. **Q7 을 거치지 않는다** — 사람이 무효화 조건을 채운 것이 곧 승격 승인이다.
       또 승인을 붙이면 이중 승인이고 1인 겸업에게 그 중복이 대기열 정체다 (§6.8.1)
    2. **모순·Lint 티켓은 승격 대상이 아니다** — 이미 있는 지식 중 하나를 고르거나
       정합성을 되돌리는 일이지 새 지식을 만드는 일이 아니다
    3. 사람이 고른 **무효화 조건이 그대로 간다**
    4. **다시 쓰지 않는다** — 사람이 승인한 것과 지식이 된 것이 같아야 한다
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.knowledge import contradiction
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations import manual_entry, promotion, resolution, ticket
from agentic_service_desk.operations.drafter import Drafter
from agentic_service_desk.operations.promotion import NotPromotable
from agentic_service_desk.operations.resolution import Ground, GroundKind
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.web.app import create_app

from conftest import FakeHarness

DRAFT = """
{"generalized_question": "승인 한도는 무엇으로 결정되는가",
 "answer": "부서 등급으로 결정된다.",
 "grounding": [{"kind": "person", "ref": "인사팀 확인"}],
 "invalidation_candidates": [{"kind": "periodic", "period_days": 180}],
 "cause": "개인별 지정이라 인사이동마다 손이 갔다"}
"""


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _repo(tmp_path) -> KnowledgeRepository:
    return KnowledgeRepository(tmp_path / "knowledge")


def _settings(tmp_path, **over):  # noqa: ANN001, ANN202
    base = dict(
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
        stage="S1",
    )
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _closed_ticket(conn, invalidation=None):  # noqa: ANN001
    """등록 → 초안 → 무효화 조건 → 종결까지 간 티켓."""
    r = manual_entry.register(
        conn, question="제 승인 한도가 왜 300만원인가요?", answer="부서 등급으로 정해집니다."
    )
    Drafter(FakeHarness(DRAFT)).run(conn)
    resolution.confirm(
        conn,
        r.ticket_id,
        invalidation=invalidation
        or Invalidation(kind=InvalidationKind.PERIODIC, period_days=180),
    )
    ticket.transition(conn, r.ticket_id, ticket.State.CLOSED)
    return r


class TestNoQ7:
    """§6.8.1 — A 는 이미 사람 손을 거쳤다."""

    def test_종결하면_지식_항목이_생긴다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        r = _closed_ticket(conn)

        p = promotion.promote(conn, repo, r.ticket_id)
        assert p.item.title == "승인 한도는 무엇으로 결정되는가"
        assert p.path.exists()

    def test_승격_사실이_기록에_남는다(self, tmp_path) -> None:
        # 두 번 올리지 않기 위한 표시이자 "무엇이 되었는가"의 답이다.
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        r = _closed_ticket(conn)
        p = promotion.promote(conn, repo, r.ticket_id)

        assert resolution.get(conn, r.ticket_id).promoted_item_id == p.item.id

    def test_두_번_올리지_않는다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        r = _closed_ticket(conn)
        promotion.promote(conn, repo, r.ticket_id)

        with pytest.raises(NotPromotable, match="이미 승격"):
            promotion.promote(conn, repo, r.ticket_id)
        assert len(repo.load_all()) == 1

    def test_커밋이_남는다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        r = _closed_ticket(conn)
        assert promotion.promote(conn, repo, r.ticket_id).commit


class TestEligibility:
    def test_안_닫힌_티켓은_올리지_않는다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        r = manual_entry.register(conn, question="질문", answer="답")
        Drafter(FakeHarness(DRAFT)).run(conn)

        with pytest.raises(NotPromotable, match="종결"):
            promotion.promote(conn, repo, r.ticket_id)

    def test_무효화_조건이_비면_애초에_닫히지도_않는다(self, tmp_path) -> None:
        """FR-14 를 지키는 것은 **두 겹**이다.

        `promote()` 도 무효화 조건을 확인하지만 그 검사는 정상 경로에서 걸리지
        않는다 — 티켓이 닫히려면 이미 채워져 있어야 하기 때문이다. 즉 검증할 수
        없는 지식은 **승격 앞이 아니라 종결 앞에서** 멈춘다.
        """
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        r = manual_entry.register(conn, question="질문", answer="답")
        Drafter(FakeHarness(DRAFT)).run(conn)

        with pytest.raises(ticket.ResolutionRequired):
            ticket.transition(conn, r.ticket_id, ticket.State.CLOSED)
        with pytest.raises(NotPromotable, match="종결"):
            promotion.promote(conn, repo, r.ticket_id)

    def test_모순_티켓은_승격_대상이_아니다(self, tmp_path) -> None:
        # "판정: kept_human" 같은 것이 지식 항목이 되면 안 된다.
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        c = contradiction.record(
            conn,
            knowledge_item_id="k-abc",
            proposed_title="가",
            proposed_body="나",
            provenance=[Provenance(qna="Q-1")],
        )
        contradiction.resolve(conn, c.id, resolution="kept_human")

        with pytest.raises(NotPromotable, match="QnA 유래"):
            promotion.promote(conn, repo, c.ticket_id)

    def test_자격이_없으면_조용히_넘어간다(self, tmp_path) -> None:
        # 닫히는 티켓이 모두 승격 대상은 아니므로 호출부가 매번 따지지 않아도 된다.
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        c = contradiction.record(
            conn, knowledge_item_id="k-abc", proposed_title="가", proposed_body="나",
            provenance=[Provenance(qna="Q-1")],
        )
        contradiction.resolve(conn, c.id, resolution="kept_human")

        assert promotion.promote_if_eligible(conn, repo, c.ticket_id) is None


class TestMapping:
    """§6.5.1 — 다시 쓰지 않는다. 승인한 것과 지식이 된 것이 같아야 한다."""

    def test_일반화된_질문이_제목이고_답이_본문이다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        r = _closed_ticket(conn)
        item = promotion.promote(conn, repo, r.ticket_id).item

        record = resolution.get(conn, r.ticket_id)
        assert item.title == record.generalized_question
        assert item.body.startswith(record.answer)

    def test_선택_필드는_있으면_덧붙는다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        r = _closed_ticket(conn)
        assert "## 원인" in promotion.promote(conn, repo, r.ticket_id).item.body

    def test_없는_선택_필드로_빈_절을_만들지_않는다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        r = _closed_ticket(conn)
        body = promotion.promote(conn, repo, r.ticket_id).item.body
        assert "## 적용 범위" not in body

    def test_사람이_고른_무효화_조건이_그대로_간다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        chosen = Invalidation(kind=InvalidationKind.LINKED, refs=("src/approval/limit.py",))
        r = _closed_ticket(conn, invalidation=chosen)

        assert promotion.promote(conn, repo, r.ticket_id).item.invalidation == chosen

    def test_사람이_고친_것으로_표시된다(self, tmp_path) -> None:
        # 표시하지 않으면 다음 ingest 가 **사람이 고른 무효화 조건을 자기 기본값으로
        # 갈아 치운다** — 강제 입력으로 얻은 것이 조용히 사라진다.
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        r = _closed_ticket(conn)
        assert promotion.promote(conn, repo, r.ticket_id).item.edited_by_human


class TestProvenance:
    """D3 — 출처는 코드가 붙인다."""

    def _record(self, **over):  # noqa: ANN003, ANN202
        base = dict(
            ticket_id="t-1",
            generalized_question="가",
            answer="나",
            grounding=(Ground(kind=GroundKind.PERSON, ref="담당자 확인"),),
            invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90),
        )
        base.update(over)
        return resolution.Resolution(**base)  # type: ignore[arg-type]

    def test_QnA_출처가_언제나_붙는다(self) -> None:
        p = promotion.to_provenance(self._record(), qna_item_id="q-1")
        assert p == [Provenance(qna="q-1")]

    def test_코드_근거는_경로로_남되_버전을_고정하지_않는다(self) -> None:
        # 담당자가 "이 파일이 그렇게 동작한다"고 말한 것이지 우리가 그 커밋을 읽은
        # 것이 아니다. 모르는 것을 아는 척하면 Lint 의 참조 부재 검사가 헛돈다.
        p = promotion.to_provenance(
            self._record(grounding=(Ground(kind=GroundKind.CODE, ref="src/a.py"),)),
            qna_item_id="q-1",
        )
        assert p == [Provenance(qna="q-1", path="src/a.py")]
        assert all(x.commit is None for x in p)

    def test_커밋_근거는_커밋으로_간다(self) -> None:
        p = promotion.to_provenance(
            self._record(grounding=(Ground(kind=GroundKind.COMMIT, ref="a1b2c3d"),)),
            qna_item_id="q-1",
        )
        assert Provenance(commit="a1b2c3d") in p

    def test_출처_없는_항목은_만들어질_수_없다(self) -> None:
        assert promotion.to_provenance(self._record(), qna_item_id=None)


class TestThroughTheScreen:
    """무효화 조건을 채우는 한 번의 동작으로 지식까지 간다."""

    def test_채우면_지식_항목이_생긴다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = manual_entry.register(
            conn, question="제 한도가 왜 300만원인가요?", answer="부서 등급으로 정해집니다."
        )
        Drafter(FakeHarness(DRAFT)).run(conn)
        conn.close()

        client = TestClient(create_app(_settings(tmp_path)))
        client.post(f"/queues/Q1/{r.ticket_id}/confirm", data={"choice": "0"})

        items = _repo(tmp_path).load_all()
        assert len(items) == 1
        assert items[0].item.title == "승인 한도는 무엇으로 결정되는가"
        assert items[0].item.edited_by_human

    def test_화면이_승격_결과를_말한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = manual_entry.register(conn, question="질문입니다", answer="답입니다")
        Drafter(FakeHarness(DRAFT)).run(conn)
        conn.close()

        client = TestClient(create_app(_settings(tmp_path)))
        client.post(f"/queues/Q1/{r.ticket_id}/confirm", data={"choice": "0"})

        html = client.get(f"/queues/Q1/{r.ticket_id}").text
        assert "지식 항목이 되었다" in html
        assert "Q7 을 거치지 않는다" in html

    def test_지식베이스_현황에_잡힌다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        r = manual_entry.register(conn, question="질문입니다", answer="답입니다")
        Drafter(FakeHarness(DRAFT)).run(conn)
        conn.close()

        client = TestClient(create_app(_settings(tmp_path)))
        client.post(f"/queues/Q1/{r.ticket_id}/confirm", data={"choice": "0"})

        html = client.get("/").text
        assert "지식 항목" in html
        assert "QnA 1" in html  # 출처 구성 — QnA 에서 온 항목 하나
