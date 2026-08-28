"""WBS-4.5.7 — stale 전파와 정정 (FR-34·35, PO-1, §5.2 W3, §6.6.3).

§5.2 의 **W3(게재 후 진실 변화)** 은 지금까지 원칙으로만 있었다. 소스코드가 바뀌면
이미 게재된 답변이 틀린 것이 되는데 그 사실이 아무에게도 닿지 않았다.

여기서 지키는 것은 여섯.

    1. **세 단계가 이어진다** — 커밋 → 지식 stale → 게재 답변 Q5 (FR-34)
    2. **지식이 먼저 따라잡아야 다시 만든다** — 낡은 채로 돌리면 같은 답이 나온다
    3. **원 답변을 고치고 정정 사실을 남긴다** (PO-1, FR-35)
    4. **정정도 게재다** — 검수를 건너뛰지 않는다 (§5.1)
    5. **옛 기록을 지우지 않는다** — 지우면 D20 이 무의미해진다
    6. **소견은 정정하거나 무시할 때만 닫힌다** — 지식 갱신만으로는 닫히지 않는다
"""

from __future__ import annotations

import json
import sqlite3

from agentic_service_desk.adapters.mock import BOT_ACCOUNT, MockParentSystem
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.search import Search
from agentic_service_desk.operations import qna_state
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline import correction, draft_store, publication
from agentic_service_desk.pipeline.answer import (
    AnswerPipeline,
    Confidence,
    Draft,
    Statement,
)

from conftest import FakeHarness

ACCOUNTS = frozenset({BOT_ACCOUNT})
QUESTION = "결재 승인 한도는 어떻게 정해지나요?"


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    c.execute(
        "INSERT INTO raw_question (id, body, asker_account, created_at, collected_at) "
        "VALUES ('Q-1', ?, 'emp-1', 'a', 'a')",
        (QUESTION,),
    )
    c.execute(
        "INSERT INTO qna_item (id, parent_question_id, origin, state, opened_at) "
        "VALUES ('q-1', 'Q-1', 'parent', ?, 'a')",
        (qna_state.PUBLISHED,),
    )
    c.commit()
    return c


def _repo(tmp_path, *, stale: bool = False, body: str = "결재 승인 한도는 부서 등급으로 결정된다."):  # noqa: ANN001, ANN202
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    for i in (1, 2):
        repo.save(
            KnowledgeItem(
                id=f"k-{i}",
                title=f"결재 승인 한도 규칙 {i}",
                body=body,
                provenance=[Provenance(commit="a" * 40, path="approval/limit.py")],
                invalidation=Invalidation(
                    kind=InvalidationKind.LINKED, refs=("approval/limit.py",)
                ),
                stale=stale,
            )
        )
    repo.commit("적재")
    return repo


def _mark_stale(repo, *ids: str, stale: bool = True) -> None:  # noqa: ANN001
    for item_id in ids:
        stored = repo.find(item_id)
        stored.item.stale = stale
        repo.save(stored.item, at=stored.path)
    repo.commit("stale 표시")


def _draft(*ids: str, text: str = "결재 승인 한도는 부서 등급으로 결정됩니다.") -> Draft:
    return Draft(
        statements=(Statement(text, Confidence.CONFIRMED, tuple(ids)),),
        grounding=tuple(ids),
    )


def _published(tmp_path, conn, repo, parent) -> str:  # noqa: ANN001
    """이미 나가 있는 답변 하나를 만든다."""
    draft_id = draft_store.save(
        conn, question=QUESTION, draft=_draft("k-1", "k-2"), qna_item_id="q-1"
    )
    draft_store.decide(conn, draft_id, approved=True)
    result = publication.publish(
        conn, parent, draft_id, bot_accounts=ACCOUNTS, repo=repo
    )
    assert isinstance(result, publication.Published)
    return result.record_id


def _pipeline(tmp_path, conn, repo, text: str = "결재 승인 한도는 직급으로 결정됩니다."):  # noqa: ANN001, ANN202
    payload = json.dumps(
        {
            "answerable": True,
            "statements": [
                {"text": text, "confidence": "확인됨", "grounding": ["k-1", "k-2"]}
            ],
            "unanswered": [],
        },
        ensure_ascii=False,
    )
    return AnswerPipeline(
        search=Search(repo=repo, conn=conn), conn=conn, harness=FakeHarness(*[payload] * 5)
    )


class TestThreeSteps:
    """FR-34 — 세 단계가 모두 이어져야 코드 변경이 게재물까지 도달한다."""

    def test_낡은_근거를_쓴_게재_답변이_Q5_로_온다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        record_id = _published(tmp_path, conn, repo, MockParentSystem())
        assert correction.pending(conn) == []

        _mark_stale(repo, "k-1")
        report = correction.propagate(conn, repo)
        assert report.opened == [record_id]

        [c] = correction.pending(conn)
        assert c.record_id == record_id
        assert c.stale_items == ("k-1",)
        assert c.question == QUESTION
        conn.close()

    def test_Q5_티켓이_발행된다(self, tmp_path) -> None:
        # 정정은 **작업**이지 판정이 아니다 (§6.4.4) — 티켓이 붙는다.
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _published(tmp_path, conn, repo, MockParentSystem())
        _mark_stale(repo, "k-1")
        correction.propagate(conn, repo)

        [c] = correction.pending(conn)
        assert ticket_domain.get(conn, c.ticket_id).source is (
            ticket_domain.Source.CORRECTION
        )
        conn.close()

    def test_나가지_않은_답변은_전파되지_않는다(self, tmp_path) -> None:
        """`in_flight` 는 **나갔는지 모르는** 것이다 — 노출되고 있다고 말할 수 없다."""
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        record_id = _published(tmp_path, conn, repo, MockParentSystem())
        conn.execute(
            "UPDATE answer_record SET state = ? WHERE id = ?",
            (publication.IN_FLIGHT, record_id),
        )
        conn.commit()
        _mark_stale(repo, "k-1")
        assert correction.propagate(conn, repo).opened == []
        conn.close()

    def test_같은_답변을_두_번_올리지_않는다(self, tmp_path) -> None:
        # 매 주기 티켓을 찍으면 대기열이 같은 항목으로 메워진다 (§8.6).
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _published(tmp_path, conn, repo, MockParentSystem())
        _mark_stale(repo, "k-1")
        assert len(correction.propagate(conn, repo).opened) == 1
        assert correction.propagate(conn, repo).opened == []
        assert len(correction.pending(conn)) == 1
        conn.close()

    def test_낡은_근거가_늘면_목록이_갱신된다(self, tmp_path) -> None:
        """화면이 옛 목록을 보여주면 **사람이 고칠 범위를 잘못 잡는다.**"""
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _published(tmp_path, conn, repo, MockParentSystem())
        _mark_stale(repo, "k-1")
        correction.propagate(conn, repo)

        _mark_stale(repo, "k-2")
        correction.propagate(conn, repo)
        assert correction.pending(conn)[0].stale_items == ("k-1", "k-2")
        conn.close()


class TestKnowledgeFirst:
    """지식이 먼저 따라잡아야 다시 만든다."""

    def test_근거가_아직_낡으면_만들지_않는다(self, tmp_path) -> None:
        """**낡은 항목으로 다시 돌리면 같은 답이 나온다.**

        그것을 정정이라 부르면 고쳤다는 기록만 남고 내용은 그대로가 되어, 이미 읽은
        사람에게 거짓 신호를 보내는 셈이다.
        """
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _published(tmp_path, conn, repo, MockParentSystem())
        _mark_stale(repo, "k-1")
        correction.propagate(conn, repo)

        assert correction.pending(conn)  # Q5 에는 떠 있다
        assert correction.ready(conn, repo) == []  # 그러나 아직 만들 수 없다
        conn.close()

    def test_갱신되면_만들_수_있다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _published(tmp_path, conn, repo, MockParentSystem())
        _mark_stale(repo, "k-1")
        correction.propagate(conn, repo)

        _mark_stale(repo, "k-1", stale=False)  # ingest 가 따라잡았다
        assert len(correction.ready(conn, repo)) == 1
        conn.close()

    def test_지식_갱신만으로는_소견이_닫히지_않는다(self, tmp_path) -> None:
        """**낡은 것은 지식이 아니라 그 지식으로 만든 답변이다.**

        지식이 새로워져도 이미 나간 글은 그대로다 — 소견을 닫는 것은 정정하거나
        무시하는 행위뿐이다.
        """
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _published(tmp_path, conn, repo, MockParentSystem())
        _mark_stale(repo, "k-1")
        correction.propagate(conn, repo)

        _mark_stale(repo, "k-1", stale=False)
        correction.propagate(conn, repo)
        assert len(correction.pending(conn)) == 1
        conn.close()


class TestCorrect:
    """PO-1 · FR-35 — 원 답변을 고치고 정정 사실을 남긴다."""

    def _ready(self, tmp_path):  # noqa: ANN001, ANN202
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        parent = MockParentSystem()
        record_id = _published(tmp_path, conn, repo, parent)
        _mark_stale(repo, "k-1")
        correction.propagate(conn, repo)
        _mark_stale(repo, "k-1", stale=False)
        return conn, repo, parent, record_id

    def test_정정_초안은_원_답변을_가리킨다(self, tmp_path) -> None:
        conn, repo, _, record_id = self._ready(tmp_path)
        [c] = correction.ready(conn, repo)
        draft_id = correction.draft_correction(
            conn, c, pipeline=_pipeline(tmp_path, conn, repo)
        )
        assert draft_store.get(conn, draft_id).corrects == record_id
        conn.close()

    def test_새_글을_올리지_않고_그_답변을_고친다(self, tmp_path) -> None:
        """후속 답글로만 정정하면 **원 답변이 틀린 채 남아** 나중에 읽는 사람이
        정정을 놓친다."""
        conn, repo, parent, record_id = self._ready(tmp_path)
        before = len(parent.list_answers("Q-1"))
        original = publication.record(conn, record_id)["parent_answer_id"]

        [c] = correction.ready(conn, repo)
        draft_id = correction.draft_correction(
            conn, c, pipeline=_pipeline(tmp_path, conn, repo)
        )
        draft_store.decide(conn, draft_id, approved=True)
        result = publication.publish(
            conn, parent, draft_id, bot_accounts=ACCOUNTS, repo=repo
        )
        assert isinstance(result, publication.Published)
        assert result.parent_answer_id == original
        assert len(parent.list_answers("Q-1")) == before  # 글이 늘지 않았다
        conn.close()

    def test_무엇이_왜_바뀌었는지_본문에_남는다(self, tmp_path) -> None:
        """**기계가 채운다** — 근거 버전 고정(D20)이 어느 근거가 낡았는지 알려준다."""
        conn, repo, parent, _ = self._ready(tmp_path)
        [c] = correction.ready(conn, repo)
        draft_id = correction.draft_correction(
            conn, c, pipeline=_pipeline(tmp_path, conn, repo)
        )
        draft_store.decide(conn, draft_id, approved=True)
        publication.publish(conn, parent, draft_id, bot_accounts=ACCOUNTS, repo=repo)

        revised = parent.list_answers("Q-1")[-1]
        assert "직급으로 결정됩니다" in revised.body
        assert "근거가 낡아" in revised.body
        assert "k-1" in revised.body
        assert revised.revised_at is not None
        conn.close()

    def test_옛_기록을_지우지_않는다(self, tmp_path) -> None:
        """지우면 **그때 무엇에 기대어 답했는지**가 사라져 D20 이 무의미해진다."""
        conn, repo, parent, record_id = self._ready(tmp_path)
        [c] = correction.ready(conn, repo)
        draft_id = correction.draft_correction(
            conn, c, pipeline=_pipeline(tmp_path, conn, repo)
        )
        draft_store.decide(conn, draft_id, approved=True)
        new_id = publication.publish(
            conn, parent, draft_id, bot_accounts=ACCOUNTS, repo=repo
        ).record_id

        old = publication.record(conn, record_id)
        assert old["state"] == publication.CORRECTED
        assert old["corrected_at"]
        assert publication.grounding_of(conn, record_id)  # 그때의 근거가 남아 있다
        assert new_id != record_id
        conn.close()

    def test_정정되면_Q5_에서_빠진다(self, tmp_path) -> None:
        conn, repo, parent, _ = self._ready(tmp_path)
        [c] = correction.ready(conn, repo)
        draft_id = correction.draft_correction(
            conn, c, pipeline=_pipeline(tmp_path, conn, repo)
        )
        draft_store.decide(conn, draft_id, approved=True)
        publication.publish(conn, parent, draft_id, bot_accounts=ACCOUNTS, repo=repo)
        assert correction.pending(conn) == []
        conn.close()

    def test_정정한_답변은_다시_추적_대상이_된다(self, tmp_path) -> None:
        # 새 기록이 `published` 이므로 그 근거가 낡으면 또 Q5 로 온다.
        conn, repo, parent, _ = self._ready(tmp_path)
        [c] = correction.ready(conn, repo)
        draft_id = correction.draft_correction(
            conn, c, pipeline=_pipeline(tmp_path, conn, repo)
        )
        draft_store.decide(conn, draft_id, approved=True)
        new_id = publication.publish(
            conn, parent, draft_id, bot_accounts=ACCOUNTS, repo=repo
        ).record_id

        _mark_stale(repo, "k-2")
        assert correction.propagate(conn, repo).opened == [new_id]
        conn.close()

    def test_질문_원문이_없으면_만들지_않는다(self, tmp_path) -> None:
        """무엇에 답하는지 모르는 채로 다시 쓰면 **정정이 아니라 지어내는 것**이다."""
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _published(tmp_path, conn, repo, MockParentSystem())
        _mark_stale(repo, "k-1")
        correction.propagate(conn, repo)
        _mark_stale(repo, "k-1", stale=False)
        conn.execute("DELETE FROM raw_question")
        conn.commit()

        [c] = correction.ready(conn, repo)
        assert correction.draft_correction(
            conn, c, pipeline=_pipeline(tmp_path, conn, repo)
        ) is None
        conn.close()


class TestIgnore:
    """§8.2 의 "무시" — 근거는 낡았지만 답변은 여전히 맞다."""

    def test_무시하면_Q5_에서_빠진다(self, tmp_path) -> None:
        """항목이 낡은 것과 그것으로 만든 답이 틀린 것은 다르다 — 경로가 바뀌었을
        뿐 내용이 그대로일 수 있다."""
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        record_id = _published(tmp_path, conn, repo, MockParentSystem())
        _mark_stale(repo, "k-1")
        correction.propagate(conn, repo)

        correction.ignore(conn, record_id)
        assert correction.pending(conn) == []
        conn.close()

    def test_정정과_무시가_기록에서_갈린다(self, tmp_path) -> None:
        """**무시가 잦으면 stale 판정이 과하다는 신호다** — 그것을 읽으려면 둘이
        갈려 있어야 한다."""
        from agentic_service_desk.operations import resolution as resolution_domain

        conn, repo = _conn(tmp_path), _repo(tmp_path)
        record_id = _published(tmp_path, conn, repo, MockParentSystem())
        _mark_stale(repo, "k-1")
        correction.propagate(conn, repo)
        [c] = correction.pending(conn)

        correction.ignore(conn, record_id)
        assert "무시함" in resolution_domain.get(conn, c.ticket_id).answer
        conn.close()


class TestQ5Screen:
    """§8.2 — 방치 비용이 높은 대기열이다."""

    def _app(self, tmp_path):  # noqa: ANN001, ANN202
        from agentic_service_desk.config import Settings
        from agentic_service_desk.web.app import create_app

        return create_app(
            Settings(  # type: ignore[arg-type]
                _env_file=None,
                operations_db=tmp_path / "ops.sqlite3",
                knowledge_dir=tmp_path / "knowledge",
                stage="S3",
            )
        )

    def test_지식이_아직_안_따라잡았음을_밝힌다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        conn, repo = _conn(tmp_path), _repo(tmp_path)
        _published(tmp_path, conn, repo, MockParentSystem())
        _mark_stale(repo, "k-1")
        correction.propagate(conn, repo)
        conn.close()

        html = TestClient(self._app(tmp_path)).get("/queues/Q5").text
        assert "지식이 먼저" in html
        assert QUESTION in html

    def test_무시_버튼이_소견을_닫는다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        conn, repo = _conn(tmp_path), _repo(tmp_path)
        record_id = _published(tmp_path, conn, repo, MockParentSystem())
        _mark_stale(repo, "k-1")
        correction.propagate(conn, repo)
        conn.close()

        client = TestClient(self._app(tmp_path))
        client.post(f"/queues/Q5/{record_id}/ignore")

        conn = _conn2 = connect(tmp_path / "ops.sqlite3")
        assert correction.pending(conn) == []
        conn.close()
