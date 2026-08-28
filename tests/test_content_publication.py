"""WBS-4.6.3 — 콘텐츠 게재 (XR-6, §7.7, D46).

**출구는 하나다.** 그리고 자리가 둘이라 성질이 갈린다 — 문서 면은 upsert 라
멱등하고, 발행 면은 create 라 그렇지 않다.

여기서 지키는 것은 여섯.

    1. **승인된 것만 나간다** (FR-39) — 자동 게재 경로가 없다
    2. **발행물은 최종 확인 없이 나가지 않는다** (§5.5.5)
    3. **기록을 먼저 남기고 내보낸다** — 뒤집으면 나갔는데 기록이 없다
    4. **문서 면의 미확정은 스스로 낫는다** (멱등), **발행 면은 사람이 본다**
    5. **한 초안은 한 번만 나간다**
    6. **귀속을 본문에 싣는다** — 모 시스템의 렌더링에 맡기지 않는다 (PO-2)
"""

from __future__ import annotations

import sqlite3

import pytest

from agentic_service_desk.adapters.mock import MockParentSystem
from agentic_service_desk.content import publication, review as content_review, store
from agentic_service_desk.content.registry import load as load_registry
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations.schema import connect, initialize

TYPES = load_registry()
GUIDE = TYPES.get("guide")
COLUMN = TYPES.get("column")


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _draft(conn, type_id: str = "guide", *, approve: bool = True):  # noqa: ANN001, ANN202
    ticket_id = ticket_domain.issue(conn, source=ticket_domain.Source.CONTENT).id
    draft_id = store.save(
        conn,
        type_id=type_id,
        title="사용 가이드",
        body="결재 한도는 부서 등급으로 결정된다.",
        grounding=("k-1",),
        ticket_id=ticket_id,
    )
    if approve:
        store.decide(conn, draft_id, approved=True)
    return store.get(conn, draft_id)


class TestOnlyApprovedGoesOut:
    """FR-39 — 전수 사람 승인. **자동으로 나가는 경로가 없다.**"""

    def test_검수_대기_중인_것은_나가지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn, approve=False)
        with pytest.raises(publication.NotApproved):
            publication.publish(conn, MockParentSystem(), GUIDE, draft)
        conn.close()

    def test_반려된_것도_나가지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn, approve=False)
        store.decide(conn, draft.id, approved=False)
        with pytest.raises(publication.NotApproved):
            publication.publish(conn, MockParentSystem(), GUIDE, store.get(conn, draft.id))
        conn.close()


class TestFinalCheck:
    """§5.5.5 · §7.3 — 발행물은 되돌릴 수 없다."""

    def test_발행물은_최종_확인_없이_나가지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn, "column")
        with pytest.raises(publication.FinalCheckMissing):
            publication.publish(conn, MockParentSystem(), COLUMN, draft)
        conn.close()

    def test_최종_확인을_거치면_나간다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        draft = _draft(conn, "column")
        record = publication.publish(
            conn, parent, COLUMN, draft, final_check_by="human"
        )
        assert record.state == publication.PUBLISHED
        assert len(parent.publications) == 1
        conn.close()

    def test_살아있는_문서에는_그_단계가_없다(self, tmp_path) -> None:
        # 틀리면 다음 갱신이 고친다 (§7.3) — 확인을 하나 더 두면 갱신마다 두 번
        # 누르게 되고, 1인 겸업에게 그 중복이 곧 대기열 정체다.
        conn = _conn(tmp_path)
        record = publication.publish(conn, MockParentSystem(), GUIDE, _draft(conn))
        assert record.state == publication.PUBLISHED
        conn.close()

    def test_배치는_발행물을_대신_누르지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _draft(conn, "column")
        assert publication.retriable(conn, TYPES) == []
        conn.close()


class TestPlaceDecidesOperation:
    """§7.7.1 · D46 — 자리가 곧 연산이다."""

    def test_문서_면은_upsert_라_멱등하다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        publication.publish(conn, parent, GUIDE, _draft(conn))
        publication.publish(conn, parent, GUIDE, _draft(conn))

        # 두 판본이 **같은 경로 하나**를 덮었다 — 회차가 쌓이지 않는다.
        assert list(parent.documents) == ["guide/index"]
        conn.close()

    def test_발행_면은_회차가_쌓인다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        publication.publish(conn, parent, COLUMN, _draft(conn, "column"), final_check_by="h")
        publication.publish(conn, parent, COLUMN, _draft(conn, "column"), final_check_by="h")
        assert len(parent.publications) == 2
        conn.close()

    def test_경로는_선언에서_온다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        publication.publish(conn, parent, GUIDE, _draft(conn))
        assert GUIDE.destination.path in parent.documents
        conn.close()


class TestRecordBeforeSend:
    """§9.6 — 기록을 먼저 남기고 내보낸다."""

    def test_어댑터가_터져도_기록이_남는다(self, tmp_path) -> None:
        # 순서를 뒤집으면 **나갔는데 기록이 없는 상태**가 생기고, 그것은 조용하다.
        class _Broken(MockParentSystem):
            def upsert_document(self, path, title, body):  # noqa: ANN001, ANN201
                raise RuntimeError("모 시스템이 응답하지 않는다")

        conn = _conn(tmp_path)
        draft = _draft(conn)
        with pytest.raises(RuntimeError):
            publication.publish(conn, _Broken(), GUIDE, draft)

        record = publication.of_draft(conn, draft.id)
        assert record is not None
        assert record.state == publication.IN_FLIGHT
        conn.close()

    def test_문서_면의_미확정은_스스로_낫는다(self, tmp_path) -> None:
        # **멱등하므로 다시 보내면 된다.** 멱등한 연산에까지 답변 게재의 조심성을
        # 그대로 옮기면 고칠 수 있는 것을 사람에게 미룬다.
        class _Broken(MockParentSystem):
            def upsert_document(self, path, title, body):  # noqa: ANN001, ANN201
                raise RuntimeError("끊겼다")

        conn = _conn(tmp_path)
        draft = _draft(conn)
        with pytest.raises(RuntimeError):
            publication.publish(conn, _Broken(), GUIDE, draft)

        assert [d.id for d in publication.retriable(conn, TYPES)] == [draft.id]
        assert publication.unsettled(conn, TYPES) == []  # 사람을 부르지 않는다

        record = publication.publish(conn, MockParentSystem(), GUIDE, draft)
        assert record.state == publication.PUBLISHED
        conn.close()

    def test_발행_면의_미확정은_사람이_본다(self, tmp_path) -> None:
        # 다시 보내면 **회차가 둘 생기고 우리는 그것을 지울 수 없다.**
        class _Broken(MockParentSystem):
            def create_publication(self, title, body):  # noqa: ANN001, ANN201
                raise RuntimeError("끊겼다")

        conn = _conn(tmp_path)
        draft = _draft(conn, "column")
        with pytest.raises(RuntimeError):
            publication.publish(conn, _Broken(), COLUMN, draft, final_check_by="h")

        assert len(publication.unsettled(conn, TYPES)) == 1
        assert publication.retriable(conn, TYPES) == []
        conn.close()

    def test_사람이_확인해_닫는다(self, tmp_path) -> None:
        class _Broken(MockParentSystem):
            def create_publication(self, title, body):  # noqa: ANN001, ANN201
                raise RuntimeError("끊겼다")

        conn = _conn(tmp_path)
        draft = _draft(conn, "column")
        with pytest.raises(RuntimeError):
            publication.publish(conn, _Broken(), COLUMN, draft, final_check_by="h")

        record = publication.unsettled(conn, TYPES)[0]
        publication.settle(conn, record.id, published=False)
        assert publication.unsettled(conn, TYPES) == []
        conn.close()


class TestOncePerDraft:
    """**한 초안은 한 번만 나간다.**"""

    def test_같은_초안을_두_번_내보내지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn)
        publication.publish(conn, MockParentSystem(), GUIDE, draft)
        with pytest.raises(publication.AlreadyPublished):
            publication.publish(conn, MockParentSystem(), GUIDE, draft)
        conn.close()

    def test_나간_것은_다시_시도_대상이_아니다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        publication.publish(conn, MockParentSystem(), GUIDE, _draft(conn))
        assert publication.retriable(conn, TYPES) == []
        conn.close()


class TestAttribution:
    """PO-2 — 귀속을 본문에 싣는다."""

    def test_본문에_AI_작성_표기와_근거가_붙는다(self, tmp_path) -> None:
        # **모 시스템의 렌더링에 맡기지 않는다.** 화면은 저쪽 것이라 그쪽에 걸면
        # 우리가 지킬 수 없는 요구가 된다.
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        publication.publish(conn, parent, GUIDE, _draft(conn))

        body = parent.documents["guide/index"]
        assert publication.ATTRIBUTION in body
        assert "근거: k-1" in body
        conn.close()

    def test_기록과_나간_본문이_같다(self, tmp_path) -> None:
        # 두 벌로 만들면 **무엇이 나갔는지**와 **무엇을 남겼는지**가 갈린다.
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        draft = _draft(conn)
        publication.publish(conn, parent, GUIDE, draft)

        stored = conn.execute(
            "SELECT body FROM content_publication WHERE draft_id = ?", (draft.id,)
        ).fetchone()["body"]
        assert stored in parent.documents["guide/index"]
        conn.close()


class TestSingleExit:
    """**출구는 하나다** — 문서로 적어 둔 규칙이 아니라 세어서 확인하는 사실이다."""

    def test_어댑터의_콘텐츠_쓰기를_부르는_파일이_하나뿐이다(self) -> None:
        from pathlib import Path

        src = Path("src/agentic_service_desk")
        callers = {
            path.relative_to(src).as_posix()
            for path in src.rglob("*.py")
            for op in ("upsert_document(", "create_publication(")
            if op in path.read_text(encoding="utf-8")
            and path.name not in {"parent_system.py", "mock.py", "http.py"}
        }
        assert callers == {"content/publication.py"}


class TestSelfReferenceIsBlocked:
    """T4 (§7.4) — 콘텐츠가 지식으로 되돌아오면 자기 요약을 다시 배운다."""

    def test_모_시스템에서_문서를_읽어_오는_표면이_없다(self) -> None:
        # **표면의 부재가 집행 지점이다.** 게재한 콘텐츠를 도로 읽을 문이 없으므로
        # 그것이 수집을 거쳐 ingest 로 돌아올 경로 자체가 없다 (§9.8.1 의 여섯 표면).
        from agentic_service_desk.adapters.parent_system import ParentSystem

        readers = {
            name
            for name in dir(ParentSystem)
            if name.startswith(("list_", "get_"))
        }
        assert readers == {"list_questions", "list_answers", "list_followups", "get_resolution"}

    def test_게재한_콘텐츠는_승격_대상이_아니다(self, tmp_path) -> None:
        from agentic_service_desk.operations import promotion as promotion_domain

        conn = _conn(tmp_path)
        draft = _draft(conn, approve=False)
        content_review.decide(conn, GUIDE, draft, approved=True)
        publication.publish(conn, MockParentSystem(), GUIDE, store.get(conn, draft.id))

        assert promotion_domain.promote_if_eligible(conn, None, draft.ticket_id) is None
        conn.close()


class TestScreen:
    """승인 화면에서 바로 나간다 — 살아있는 문서에 한해."""

    def _app(self, tmp_path, **over):  # noqa: ANN001, ANN202
        from fastapi.testclient import TestClient

        from agentic_service_desk.config import Settings
        from agentic_service_desk.web.app import create_app

        base = dict(
            operations_db=tmp_path / "ops.sqlite3",
            knowledge_dir=tmp_path / "knowledge",
            stage="S4",
            parent_adapter="mock",
        )
        base.update(over)
        return TestClient(create_app(Settings(_env_file=None, **base)))

    def test_승인하면_문서_면으로_나간다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn, approve=False)
        conn.close()

        app = self._app(tmp_path)
        body = app.post(
            f"/queues/Q3/{draft.ticket_id}/decide",
            data={"approved": "1"},
            follow_redirects=True,
        ).text
        assert "문서 면에 게재했다" in body

        conn = _conn(tmp_path)
        assert publication.of_draft(conn, draft.id).state == publication.PUBLISHED
        conn.close()

    def test_연동이_없으면_승인은_되고_게재만_밀린다(self, tmp_path) -> None:
        # 승인은 우리 안의 일이고 게재는 바깥으로 나가는 일이다 — 모 시스템이
        # 설정되지 않았다고 **판정이 통째로 실패하면 대기열이 막힌다.**
        conn = _conn(tmp_path)
        draft = _draft(conn, approve=False)
        conn.close()

        app = self._app(tmp_path, parent_adapter="http")
        body = app.post(
            f"/queues/Q3/{draft.ticket_id}/decide",
            data={"approved": "1"},
            follow_redirects=True,
        ).text
        assert "게재하지 못했다" in body
        assert "다음 배치가 다시 시도한다" in body

        conn = _conn(tmp_path)
        assert store.current(conn, "guide") is not None  # 승인은 남았다
        conn.close()

    def test_발행물은_승인만으로_나가지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn, "column", approve=False)
        conn.close()

        app = self._app(tmp_path)
        app.post(f"/queues/Q3/{draft.ticket_id}/decide", data={"approved": "1"})
        body = app.get("/queues/Q3").text

        assert "발행 직전 최종 확인" in body
        conn = _conn(tmp_path)
        assert publication.of_draft(conn, draft.id) is None
        conn.close()

    def test_최종_확인_버튼이_발행한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn, "column", approve=False)
        conn.close()

        app = self._app(tmp_path)
        app.post(f"/queues/Q3/{draft.ticket_id}/decide", data={"approved": "1"})
        body = app.post(
            f"/queues/Q3/{draft.ticket_id}/publish", follow_redirects=True
        ).text
        assert "발행 면에 게재했다" in body

    def test_방금_승인한_초안이_그_자리에서_나간다(self, tmp_path) -> None:
        # 손에 든 초안은 `pending` 이던 시점의 값이다 — 그대로 넘기면 게재 관문이
        # "승인되지 않았다"며 막고, **방금 승인한 사람에게는 이유를 알 수 없는
        # 거절**이 된다.
        conn = _conn(tmp_path)
        draft = _draft(conn, approve=False)
        conn.close()

        body = self._app(tmp_path).post(
            f"/queues/Q3/{draft.ticket_id}/decide",
            data={"approved": "1"},
            follow_redirects=True,
        ).text
        assert "자동으로 나가는 경로가 없다" not in body  # NotApproved 로 막히지 않았다
        assert "게재했다" in body

    def test_화면에서_나가도_근거_버전이_박힌다(self, tmp_path) -> None:
        # 화면 승인이 **보통의 경로**인데 배치에서만 박으면 그 칸은 실제로는 늘
        # 비어 있다 — 기록된 듯 보이지만 아무것도 재현할 수 없다. 라이브에서 잡았다.
        from agentic_service_desk.knowledge.item import (
            Invalidation,
            InvalidationKind,
            KnowledgeItem,
            Provenance,
        )
        from agentic_service_desk.knowledge.repository import KnowledgeRepository

        repo = KnowledgeRepository(tmp_path / "knowledge")
        repo.ensure_initialized()
        repo.save(
            KnowledgeItem(
                id="k-1", title="제목", body="본문",
                provenance=[Provenance(commit="a" * 40, path="a.py")],
                invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("a.py",)),
            )
        )
        repo.commit("적재")

        conn = _conn(tmp_path)
        draft = _draft(conn, approve=False)
        conn.close()

        self._app(tmp_path).post(
            f"/queues/Q3/{draft.ticket_id}/decide", data={"approved": "1"}
        )

        conn = _conn(tmp_path)
        pinned = conn.execute(
            "SELECT pinned_commit FROM content_publication WHERE draft_id = ?",
            (draft.id,),
        ).fetchone()["pinned_commit"]
        assert pinned == repo.head()
        conn.close()
