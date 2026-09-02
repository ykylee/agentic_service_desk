"""WBS-4.2.7 — 대시보드 최소 (FR-44·59, §8.2·8.3·8.5.5).

**운영자는 감시자가 아니라 루프의 일부다** (§8.4). 여기서 처리되지 않으면 시스템은
사고 없이 조용히 성장을 멈춘다. 여기서 지키는 것은 셋.

    1. **켜지지 않은 대기열은 보여주지 않는다** (FR-59) — 여덟을 다 늘어놓으면
       실제로 밀린 것이 빈 화면들 사이에 묻힌다
    2. Q4 는 **양쪽을 나란히 놓고 파일을 지목**한다. 편집은 평소 도구로 (§8.5.5)
    3. 현황은 **자라고 있는가 썩고 있는가**에 답한다 (§8.3)
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.knowledge import contradiction
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.web.app import create_app
from agentic_service_desk.web.dashboard import Dashboard, queues_for_stage


def _settings(tmp_path, **over):  # noqa: ANN001, ANN202
    base = dict(
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
    )
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _repo(tmp_path) -> KnowledgeRepository:
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    return repo


def _item(repo, **over) -> KnowledgeItem:  # noqa: ANN001, ANN003
    base = dict(
        title="결재 한도 규칙",
        body="사람이 쓴 본문이다.",
        provenance=[Provenance(commit="a" * 40, path="limit.py")],
        invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90),
    )
    base.update(over)
    item = KnowledgeItem(**base)  # type: ignore[arg-type]
    repo.save(item)
    return item


class TestStageGating:
    """FR-59 — 켜진 단계의 것만."""

    def test_S0_는_둘뿐이다(self) -> None:
        assert [q.id for q in queues_for_stage("S0")] == ["Q4", "Q8"]

    def test_대기열마다_방치_비용이_붙어_있다(self) -> None:
        # **방치 비용이 우선순위다** — 나란히 늘어놓으면 무엇부터 볼지 모른다 (§8.2).
        assert all(q.neglect_cost for q in queues_for_stage("S5"))

    def test_S0_화면에_Q2_가_없다(self, tmp_path) -> None:
        html = TestClient(create_app(_settings(tmp_path))).get("/").text
        assert "Q4" in html and "Q8" in html
        assert "Q2" not in html


class TestKnowledgeStatus:
    """§8.3 — 지식이 자라고 있는가, 썩고 있는가."""

    def test_비어_있어도_죽지_않는다(self, tmp_path) -> None:
        # 지식베이스가 아직 만들어지지 않은 상태로 화면을 열 수 있다.
        board = Dashboard(repo=KnowledgeRepository(tmp_path / "없다"), conn=_conn(tmp_path))
        assert board.knowledge_status().total == 0

    def test_항목_수와_stale_비율을_센다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo)
        _item(repo, title="두 번째", stale=True)

        s = Dashboard(repo=repo, conn=conn).knowledge_status()
        assert s.total == 2
        assert s.stale == 1
        assert s.stale_ratio == 0.5

    def test_출처_구성을_센다(self, tmp_path) -> None:
        # 원천이 둘인데(D2) 한쪽으로 쏠리면 다른 쪽 수집이 막혔다는 신호다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo)
        _item(repo, title="QnA 에서 온 것", provenance=[Provenance(qna="Q-2")])

        s = Dashboard(repo=repo, conn=conn).knowledge_status()
        assert (s.from_source, s.from_qna) == (1, 1)
        assert s.source_ratio == 0.5

    def test_두_원천에_걸친_항목은_양쪽에_센다(self, tmp_path) -> None:
        # 개념 단위라 근거가 여러 원천에 걸칠 수 있다 (ADR-003).
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, provenance=[Provenance(commit="a" * 40), Provenance(qna="Q-2")])

        s = Dashboard(repo=repo, conn=conn).knowledge_status()
        assert (s.from_source, s.from_qna) == (1, 1)

    def test_최근_ingest_이력을_보여준다(self, tmp_path) -> None:
        # 커밋이 곧 이력이다. 단위는 묶음이다 (FR-5, 2026-09-03 개정).
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo)
        repo.commit("ingest: 신규 1 · 갱신 0")

        s = Dashboard(repo=repo, conn=conn).knowledge_status()
        assert s.recent_ingests[0][1] == "ingest: 신규 1 · 갱신 0"

    def test_읽을_수_없는_파일이_보인다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        (repo.root / "concepts").mkdir(parents=True, exist_ok=True)
        (repo.root / "concepts" / "깨진.md").write_text("frontmatter 가 없다\n")

        assert Dashboard(repo=repo, conn=conn).knowledge_status().broken_files

    def test_화면에_숫자가_뜬다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo)
        conn.close()

        html = TestClient(create_app(_settings(tmp_path))).get("/").text
        assert "지식베이스 현황" in html
        assert "미해결 모순" in html


class TestQ4:
    """§8.5.5 — 화면은 지목하고, 편집은 평소 도구로."""

    def _with_contradiction(self, tmp_path):  # noqa: ANN001, ANN202
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        item = _item(repo, edited_by_human=True)
        contradiction.record(
            conn,
            knowledge_item_id=item.id,
            proposed_title="직급 기준이다",
            proposed_body="에이전트가 주장한 본문이다.",
            provenance=[Provenance(qna="Q-2")],
        )
        return repo, conn, item

    def test_양쪽을_나란히_준다(self, tmp_path) -> None:
        # 판정하려면 둘 다 보여야 한다.
        repo, conn, item = self._with_contradiction(tmp_path)
        row = Dashboard(repo=repo, conn=conn).contradictions()[0]

        assert row.human_body == "사람이 쓴 본문이다."
        assert row.agent_body == "에이전트가 주장한 본문이다."
        assert row.item_id == item.id

    def test_어느_파일인지_지목한다(self, tmp_path) -> None:
        # 대시보드가 하는 일은 여기까지다 — 에디터를 만들지 않는다.
        repo, conn, _ = self._with_contradiction(tmp_path)
        row = Dashboard(repo=repo, conn=conn).contradictions()[0]
        assert row.item_path.endswith(".md")

    def test_에이전트_주장의_근거가_보인다(self, tmp_path) -> None:
        repo, conn, _ = self._with_contradiction(tmp_path)
        assert "Q-2" in Dashboard(repo=repo, conn=conn).contradictions()[0].agent_grounds

    def test_항목이_사라져도_줄이_사라지지_않는다(self, tmp_path) -> None:
        # 판정 대상이 없어졌다는 것 자체가 사람이 알아야 할 사실이다.
        repo, conn, item = self._with_contradiction(tmp_path)
        repo.find(item.id).path.unlink()

        row = Dashboard(repo=repo, conn=conn).contradictions()[0]
        assert row.missing_item
        assert row.agent_body  # 에이전트 쪽은 여전히 남아 있다

    def test_화면에_양쪽이_렌더링된다(self, tmp_path) -> None:
        repo, conn, _ = self._with_contradiction(tmp_path)
        conn.close()

        html = TestClient(create_app(_settings(tmp_path))).get("/queues/Q4").text
        assert "사람이 쓴 본문이다." in html
        assert "에이전트가 주장한 본문이다." in html
        assert "사람 쪽이 옳다" in html

    def test_판정하면_목록에서_빠진다(self, tmp_path) -> None:
        repo, conn, _ = self._with_contradiction(tmp_path)
        cid = Dashboard(repo=repo, conn=conn).contradictions()[0].id
        conn.close()

        client = TestClient(create_app(_settings(tmp_path)))
        res = client.post(
            f"/queues/Q4/{cid}/resolve", data={"resolution": "kept_human"}, follow_redirects=False
        )
        assert res.status_code == 303
        assert "미해결 모순이 없다" in client.get("/queues/Q4").text

    def test_모순이_없으면_그렇게_말한다(self, tmp_path) -> None:
        _repo(tmp_path)
        _conn(tmp_path).close()
        assert "미해결 모순이 없다" in TestClient(
            create_app(_settings(tmp_path))
        ).get("/queues/Q4").text


class TestQ8:
    def test_비어_있으면_비었다고_말한다(self, tmp_path) -> None:
        """WBS-4.5.4 로 미해결 종료 판정이 생겼으므로 **빈 목록은 이제 빈 목록이다.**

        그전까지는 "아직 판정 자체가 없다"고 적어 두었다 — 처리할 것이 없는 것과
        기능이 없는 것은 다르기 때문이다. 판정이 생긴 지금 그 문구를 남겨 두면
        **있는 기능을 없다고 말하게 된다.**
        """
        _repo(tmp_path)
        _conn(tmp_path).close()
        html = TestClient(create_app(_settings(tmp_path))).get("/queues/Q8").text
        assert "미해결로 종료된 QnA 가 없다" in html

    def test_미해결_종료된_QnA_가_올라온다(self, tmp_path) -> None:
        _repo(tmp_path)
        conn = _conn(tmp_path)
        conn.execute(
            "INSERT INTO qna_item (id, parent_question_id, state, opened_at) "
            "VALUES ('q-1', 'Q-7', '미해결종료', '2026-08-28')"
        )
        conn.commit()
        conn.close()

        html = TestClient(create_app(_settings(tmp_path))).get("/queues/Q8").text
        assert "Q-7" in html
