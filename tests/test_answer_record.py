"""WBS-4.5.3 — 답변 이력 (FR-28, D20, ADR-002 결정 3, §6.6).

게재된 답변의 **텍스트**는 모 시스템에 남는다. **무엇을 근거로 만들어졌는지**는
우리만 안다 — 남기지 않으면 나중에 아무도 알 수 없다.

여기서 지키는 것은 다섯.

    1. **링크가 아니라 버전을 고정한다** — 항목이 갱신돼도 당시 근거를 재현한다 (§6.6.2)
    2. **당시의 출처를 함께 박는다** — 지금 provenance 는 그때와 다를 수 있다
    3. **고정할 수 없으면 게재하지 않는다** — 없는 해시를 지어내지 않는다
    4. **게재 시점의 stale 여부를 남긴다** — "그때부터 틀렸다"를 셀 수 있게 (§6.6.2)
    5. **생성 주체를 남긴다** — 생성 시점의 모델이지 게재 시점이 아니다 (§6.6.1 필드 5)
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
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline import draft_store, publication
from agentic_service_desk.pipeline.answer import Confidence, Draft, Statement

ACCOUNTS = frozenset({BOT_ACCOUNT})
SOURCE_COMMIT = "b" * 40


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    c.execute(
        "INSERT INTO qna_item (id, parent_question_id, origin, state, opened_at) "
        "VALUES ('qna-1', 'Q-1', 'parent', '접수', '2026-08-28T00:00:00Z')"
    )
    c.commit()
    return c


def _repo(tmp_path, *, stale: bool = False, commit: bool = True):  # noqa: ANN001, ANN202
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    repo.save(
        KnowledgeItem(
            id="k-1",
            title="결재 한도 결정 규칙",
            body="결재 한도는 부서 등급으로 정해진다.",
            provenance=[Provenance(commit=SOURCE_COMMIT, path="approval/limit.py")],
            invalidation=Invalidation(
                kind=InvalidationKind.LINKED, refs=("approval/limit.py",)
            ),
            stale=stale,
        )
    )
    if commit:
        repo.commit("시험용 지식 항목")
    return repo


def _draft() -> Draft:
    return Draft(
        statements=(
            Statement("결재 한도는 부서 등급으로 정해집니다.", Confidence.CONFIRMED, ("k-1",)),
        ),
        grounding=("k-1",),
    )


def _publish(tmp_path, conn, repo, *, generated_by: str = ""):  # noqa: ANN001, ANN202
    draft_id = draft_store.save(
        conn,
        question="결재 한도는 어떻게 정해지나요?",
        draft=_draft(),
        qna_item_id="qna-1",
        generated_by=generated_by,
    )
    draft_store.decide(conn, draft_id, approved=True)
    return publication.publish(
        conn, MockParentSystem(), draft_id, bot_accounts=ACCOUNTS, repo=repo
    )


class TestVersionPin:
    """§6.6.2 — 링크가 아니라 버전을 고정한다."""

    def test_게재_시점의_지식베이스_커밋이_박힌다(self, tmp_path) -> None:
        """**원천 저장소 커밋이 아니다.**

        고정의 목적은 *그 시점의 항목 내용을 재현*하는 것이므로, 항목이 사는
        저장소의 커밋이어야 한다 (ADR-002 결정 3).
        """
        conn = _conn(tmp_path)
        repo = _repo(tmp_path)
        head = repo.head()
        result = _publish(tmp_path, conn, repo)
        assert isinstance(result, publication.Published)

        pinned = publication.grounding_of(conn, result.record_id)
        assert [p.item_id for p in pinned] == ["k-1"]
        assert pinned[0].pinned_commit == head
        assert pinned[0].pinned_commit != SOURCE_COMMIT
        conn.close()

    def test_항목이_갱신돼도_당시_근거가_남는다(self, tmp_path) -> None:
        """FR-28 의 확인 조건 그대로.

        링크만 두면 따라갔을 때 **지금의** 지식이 나올 뿐이다.
        """
        conn = _conn(tmp_path)
        repo = _repo(tmp_path)
        result = _publish(tmp_path, conn, repo)
        pinned_then = publication.grounding_of(conn, result.record_id)[0]

        # 항목을 갱신한다 — 출처가 다른 커밋으로 바뀐다.
        stored = repo.find("k-1")
        item = stored.item
        item.body = "결재 한도는 직급으로 정해진다."
        item.provenance = [Provenance(commit="c" * 40, path="approval/limit.py")]
        repo.save(item)
        repo.commit("항목 갱신")

        after = publication.grounding_of(conn, result.record_id)[0]
        assert after.pinned_commit == pinned_then.pinned_commit != repo.head()
        assert SOURCE_COMMIT in after.source
        assert "c" * 40 not in after.source
        conn.close()

    def test_당시의_출처가_함께_박힌다(self, tmp_path) -> None:
        """§6.6.1 필드 3 — "무엇이 바뀌어 틀리게 됐는가"에 답하려면 필요하다."""
        conn = _conn(tmp_path)
        result = _publish(tmp_path, conn, _repo(tmp_path))
        source = publication.grounding_of(conn, result.record_id)[0].source
        assert SOURCE_COMMIT in source
        assert "approval/limit.py" in source
        conn.close()


class TestUnpinnable:
    """고정할 수 없으면 나가지 않는다."""

    def test_커밋_없는_지식베이스로는_게재하지_않는다(self, tmp_path) -> None:
        """**없는 해시를 지어내지 않는다.**

        박아 두면 나중에 재현을 시도할 때에야 거짓이 드러나는데, 그때는 이미
        답변이 나가 있다.
        """
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        before = len(parent.list_answers("Q-1"))
        draft_id = draft_store.save(
            conn, question="질문", draft=_draft(), qna_item_id="qna-1"
        )
        draft_store.decide(conn, draft_id, approved=True)
        result = publication.publish(
            conn,
            parent,
            draft_id,
            bot_accounts=ACCOUNTS,
            repo=_repo(tmp_path, commit=False),
        )
        assert isinstance(result, publication.Refused)
        assert result.reason is publication.Refusal.UNPINNABLE
        assert len(parent.list_answers("Q-1")) == before
        # 기록도 남기지 않는다 — 시도조차 하지 않았다.
        assert conn.execute("SELECT count(*) c FROM answer_record").fetchone()["c"] == 0
        conn.close()

    def test_지식베이스가_아예_없어도_터지지_않는다(self, tmp_path) -> None:
        """"고정할 수 없다"가 "게재 중 사고"로 둔갑하면 사람이 헛걸음한다."""
        conn = _conn(tmp_path)
        draft_id = draft_store.save(
            conn, question="질문", draft=_draft(), qna_item_id="qna-1"
        )
        draft_store.decide(conn, draft_id, approved=True)
        result = publication.publish(
            conn,
            MockParentSystem(),
            draft_id,
            bot_accounts=ACCOUNTS,
            repo=KnowledgeRepository(tmp_path / "없는곳"),
        )
        assert isinstance(result, publication.Refused)
        assert result.reason is publication.Refusal.UNPINNABLE
        conn.close()


class TestStaleAtPublish:
    """§6.6.2 — "그때는 맞았다"와 "그때부터 틀렸다"를 가른다."""

    def test_게재_시점에_낡았으면_그렇게_남는다(self, tmp_path) -> None:
        """P4 검수가 막는 것이므로 **여기 참이 쌓이면 검수가 새고 있다는 뜻**이다."""
        conn = _conn(tmp_path)
        result = _publish(tmp_path, conn, _repo(tmp_path, stale=True))
        assert publication.grounding_of(conn, result.record_id)[0].stale
        conn.close()

    def test_멀쩡했으면_거짓이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        result = _publish(tmp_path, conn, _repo(tmp_path))
        assert not publication.grounding_of(conn, result.record_id)[0].stale
        conn.close()


class TestGeneratedBy:
    """§6.6.1 필드 5 — 어느 모델이 만들었는가 (ADR-005)."""

    def test_생성_시점의_모델이_남는다(self, tmp_path) -> None:
        """**게재 시점이 아니다.** 초안이 큐에 머무는 동안 설정이 바뀔 수 있고,
        그러면 모델 교체 추적이 어긋난다."""
        conn = _conn(tmp_path)
        result = _publish(tmp_path, conn, _repo(tmp_path), generated_by="MiniMax-M3")
        assert publication.record(conn, result.record_id)["generated_by"] == "MiniMax-M3"
        conn.close()

    def test_모르면_비워_둔다(self, tmp_path) -> None:
        # 모델 없이 만들어질 수 있는 초안은 없지만, 빈 문자열을 모델 이름으로
        # 남기면 나중에 그것을 모델로 세게 된다.
        conn = _conn(tmp_path)
        result = _publish(tmp_path, conn, _repo(tmp_path))
        assert publication.record(conn, result.record_id)["generated_by"] is None
        conn.close()

    def test_파이프라인이_설정에서_모델을_받는다(self, tmp_path) -> None:
        """**하네스에게 묻지 않는다** — 실행기마다 답이 달라진다."""
        from agentic_service_desk.knowledge.search import Search
        from agentic_service_desk.pipeline.answer import AnswerPipeline

        conn = _conn(tmp_path)
        pipeline = AnswerPipeline(
            search=Search(repo=_repo(tmp_path), conn=conn),
            conn=conn,
            generated_by="MiniMax-M3",
        )
        assert pipeline.run("결재 한도").generated_by == "MiniMax-M3"
        conn.close()


class TestStaleWiring:
    """§6.6.3 — 이것이 stale 전파의 배선이다."""

    def test_항목으로_게재_답변을_되찾는다(self, tmp_path) -> None:
        """항목이 낡으면 **그것으로 답한 게재물**이 정정 후보가 된다.

        전파 자체는 WBS-4.5.7 이 잇는다 — 여기서는 그 입구가 열려 있는지만 본다.
        """
        conn = _conn(tmp_path)
        result = _publish(tmp_path, conn, _repo(tmp_path))
        rows = publication.answered_with(conn, "k-1")
        assert [r["id"] for r in rows] == [result.record_id]
        assert publication.answered_with(conn, "k-없음") == []
        conn.close()

    def test_나가지_않은_답변은_되찾히지_않는다(self, tmp_path) -> None:
        """`in_flight` 는 **나갔는지 모르는** 것이다. 정정 후보로 올리면 나가지도
        않은 답변을 고치라고 말하게 된다."""
        conn = _conn(tmp_path)
        result = _publish(tmp_path, conn, _repo(tmp_path))
        conn.execute(
            "UPDATE answer_record SET state = ? WHERE id = ?",
            (publication.IN_FLIGHT, result.record_id),
        )
        conn.commit()
        assert publication.answered_with(conn, "k-1") == []
        conn.close()


def test_근거_기록이_게재와_한_트랜잭션이다(tmp_path) -> None:
    """**기록만 남고 근거가 비는 일이 없다.**

    비면 그 답변은 stale 전파에서 영영 빠지는데, 화면에는 정상 게재로 보인다.
    """
    conn = _conn(tmp_path)
    result = _publish(tmp_path, conn, _repo(tmp_path))
    row = publication.record(conn, result.record_id)
    count = conn.execute(
        "SELECT count(*) c FROM answer_grounding WHERE answer_record_id = ?",
        (result.record_id,),
    ).fetchone()["c"]
    assert row["state"] == publication.PUBLISHED
    assert count == len(json.loads('["k-1"]'))
    conn.close()
