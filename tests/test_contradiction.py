"""WBS-4.2.5 — 모순 표시와 사람 편집 보존 (FR-6·54, D37·D38).

여기서 지키는 것은 셋이다.

    1. **덮어쓰지 않는 것과 없던 일로 하는 것은 다르다** — 에이전트의 판단이 남아야
       사람이 무엇과 어긋났는지 보고 판정할 수 있다
    2. 모순은 **티켓**이다 (§6.4.4 작업). 그리고 같은 항목에 **쌓이지 않는다**
    3. 사람 편집 세 조건은 **커밋 시점에** 강제된다 — 그래야 "반영되지 않는다"가
       그대로 이뤄지면서 아무것도 잃지 않는다
"""

from __future__ import annotations

import subprocess
import sys

from agentic_service_desk.adapters.mock import BOT_ACCOUNT, MockParentSystem
from agentic_service_desk.ingest.agent import IngestAgent
from agentic_service_desk.ingest.output_filter import OutputFilter
from agentic_service_desk.ingest.qna import QnaCollector
from agentic_service_desk.ingest.run import IngestRun
from agentic_service_desk.knowledge import contradiction
from agentic_service_desk.knowledge.human_edit import (
    is_ingest_commit,
    verify_edit,
)
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations.schema import connect, initialize

from conftest import FakeHarness


def _item_json(title: str, item_id: str | None = None, body: str = "에이전트의 판단.") -> str:
    ident = f'"{item_id}"' if item_id else "null"
    return (
        f'{{"items": [{{"id": {ident}, "title": "{title}", "body": "{body}",'
        f' "invalidation": {{"kind": "periodic", "period_days": 90}}}}]}}'
    )


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _run(tmp_path, harness, conn):  # noqa: ANN001, ANN202
    return IngestRun(
        repo=KnowledgeRepository(tmp_path / "knowledge"),
        agent=IngestAgent(harness),
        conn=conn,
        output_filter=OutputFilter(frozenset({BOT_ACCOUNT})),
    )


def _knowledge_with_human_edit(tmp_path, conn, parent):  # noqa: ANN001, ANN202
    """지식 항목 하나를 만들고 사람이 고친 상태로 둔다."""
    QnaCollector(parent, conn).collect()
    _run(tmp_path, FakeHarness(_item_json("결재 한도 규칙")), conn).run()

    repo = KnowledgeRepository(tmp_path / "knowledge")
    stored = repo.load_all()[0]
    stored.item.edited_by_human = True
    stored.item.body = "사람이 고친 본문 — 등급이 아니라 직급이다."
    repo.save(stored.item, at=stored.path)
    return repo, stored.item.id


def _more_qna(conn, parent, qid: str = "Q-9") -> None:
    """새 QnA 를 하나 더 흘려 넣는다 — ingest 가 다시 돌 거리를 만든다.

    **같은 `parent` 를 이어 쓴다.** mock 은 인스턴스마다 답변 id 를 A-1 부터 다시
    세므로, 새로 만들면 이미 ingest 표시가 붙은 id 와 부딪혀 그 답변이 조용히
    건너뛰어진다. 실제 모 시스템에서는 id 가 전역으로 유일하다.
    """
    parent._add_question(qid, "새 질문", "emp-1")
    parent._add_answer(qid, "사람 답변", "emp-999")
    QnaCollector(parent, conn).collect()


class TestBothSidesSurvive:
    """FR-6 — 양쪽을 남기고 모순 표시한다."""

    def test_사람의_글이_남는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        repo, item_id = _knowledge_with_human_edit(tmp_path, conn, parent)
        _more_qna(conn, parent)

        _run(tmp_path, FakeHarness(_item_json("바뀐 제목", item_id=item_id)), conn).run()
        assert "사람이 고친 본문" in repo.find(item_id).item.body

    def test_에이전트의_판단도_남는다(self, tmp_path) -> None:
        # **덮어쓰지 않는 것과 없던 일로 하는 것은 다르다.** 버리면 사람이 무엇과
        # 어긋났는지 볼 수 없어 판정 자체가 불가능해진다.
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        _, item_id = _knowledge_with_human_edit(tmp_path, conn, parent)
        _more_qna(conn, parent)

        result = _run(
            tmp_path,
            FakeHarness(_item_json("바뀐 제목", item_id=item_id, body="등급이 맞다.")),
            conn,
        ).run()

        assert result.held_for_human == [item_id]
        opened = contradiction.list_open(conn)
        assert len(opened) == 1
        assert opened[0].proposed_body == "등급이 맞다."
        assert opened[0].knowledge_item_id == item_id

    def test_에이전트_주장의_근거도_남는다(self, tmp_path) -> None:
        # 근거 없이 주장만 남으면 판정할 수 없다 (D3).
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        _, item_id = _knowledge_with_human_edit(tmp_path, conn, parent)
        _more_qna(conn, parent)
        _run(tmp_path, FakeHarness(_item_json("가", item_id=item_id)), conn).run()

        assert contradiction.list_open(conn)[0].provenance[0].qna == "Q-9"


class TestQueue:
    """모순은 판정이 아니라 작업이다 (§6.4.4)."""

    def test_티켓이_함께_발행된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        _, item_id = _knowledge_with_human_edit(tmp_path, conn, parent)
        _more_qna(conn, parent)
        _run(tmp_path, FakeHarness(_item_json("가", item_id=item_id)), conn).run()

        row = conn.execute("SELECT * FROM ticket WHERE source = 'contradiction'").fetchone()
        assert row["state"] == "open"
        # QnA 에서 온 티켓이 아니다 — 티켓 출처가 넷이라는 것이 여기서 쓰인다 (§6.4.1).
        assert row["qna_item_id"] is None

    def test_같은_항목에_모순이_쌓이지_않는다(self, tmp_path) -> None:
        # ingest 는 주기마다 돈다. 매번 티켓을 찍으면 대기열이 같은 항목으로 메워져
        # 1인 겸업이 소화할 수 없게 된다 (§8.6).
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        _, item_id = _knowledge_with_human_edit(tmp_path, conn, parent)

        for i in range(3):
            _more_qna(conn, parent, f"Q-1{i}")
            _run(tmp_path, FakeHarness(_item_json("가", item_id=item_id)), conn).run()

        assert len(contradiction.list_open(conn)) == 1
        assert conn.execute("SELECT COUNT(*) FROM ticket").fetchone()[0] == 1

    def test_판정하면_티켓도_닫힌다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        _, item_id = _knowledge_with_human_edit(tmp_path, conn, parent)
        _more_qna(conn, parent)
        _run(tmp_path, FakeHarness(_item_json("가", item_id=item_id)), conn).run()

        opened = contradiction.list_open(conn)[0]
        contradiction.resolve(conn, opened.id, resolution="kept_human")

        assert contradiction.list_open(conn) == []
        assert conn.execute("SELECT state FROM ticket").fetchone()["state"] == "closed"

    def test_판정_뒤에는_다시_열릴_수_있다(self, tmp_path) -> None:
        # 판정한 뒤 원천이 또 바뀌면 그것은 새로운 모순이다.
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        _, item_id = _knowledge_with_human_edit(tmp_path, conn, parent)
        _more_qna(conn, parent, "Q-20")
        _run(tmp_path, FakeHarness(_item_json("가", item_id=item_id)), conn).run()
        contradiction.resolve(conn, contradiction.list_open(conn)[0].id, resolution="kept_human")

        _more_qna(conn, parent, "Q-21")
        _run(tmp_path, FakeHarness(_item_json("나", item_id=item_id)), conn).run()
        assert len(contradiction.list_open(conn)) == 1


class TestThreeConditions:
    """FR-54 — 셋 없이는 편집이 반영되지 않는다."""

    def _item(self, **over) -> KnowledgeItem:  # noqa: ANN003
        base = dict(
            title="가",
            body="나",
            provenance=[Provenance(commit="a1b2c3d")],
            invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90),
            edited_by_human=True,
        )
        base.update(over)
        return KnowledgeItem(**base)  # type: ignore[arg-type]

    def test_셋을_다_지키면_통과한다(self) -> None:
        before = self._item(edited_by_human=False)
        after = self._item(
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("src/a.py",))
        )
        assert verify_edit(
            after=after, before=before, commit_message="등급이 아니라 직급 기준이라 고쳤다"
        )

    def test_사유가_없으면_거부한다(self) -> None:
        v = verify_edit(after=self._item(), before=None, commit_message="")
        assert not v
        assert any("조건 1" in x for x in v.violations)

    def test_사유가_빈약하면_거부한다(self) -> None:
        # 빈 메시지만 막으면 조건 1 은 `-m "수정"` 한 번으로 무력해진다.
        assert not verify_edit(after=self._item(), before=None, commit_message="수정")
        assert not verify_edit(after=self._item(), before=None, commit_message="fix")

    def test_무효화_조건을_안_고치면_거부한다(self) -> None:
        before = self._item(edited_by_human=False)
        v = verify_edit(
            after=self._item(), before=before, commit_message="등급이 아니라 직급 기준이다"
        )
        assert any("조건 2" in x for x in v.violations)

    def test_새_항목은_무효화_갱신을_묻지_않는다(self) -> None:
        # 갱신할 이전 값이 없다.
        v = verify_edit(after=self._item(), before=None, commit_message="새 개념을 손으로 적었다")
        assert v.accepted

    def test_사람_표시가_없으면_거부한다(self) -> None:
        # 표시가 없으면 다음 ingest 가 자기가 쓴 것으로 착각하고 그냥 덮는다.
        v = verify_edit(
            after=self._item(edited_by_human=False),
            before=None,
            commit_message="등급이 아니라 직급 기준이라 고쳤다",
        )
        assert any("조건 3" in x for x in v.violations)

    def test_ingest_커밋은_이_검사의_대상이_아니다(self) -> None:
        assert is_ingest_commit("ingest: 신규 2 · 갱신 1")
        assert not is_ingest_commit("등급이 아니라 직급 기준이라 고쳤다")


class TestHookIsReal:
    """훅이 실제로 커밋을 막는가."""

    def _repo(self, tmp_path) -> KnowledgeRepository:
        conn = _conn(tmp_path)
        QnaCollector(MockParentSystem(), conn).collect()
        _run(tmp_path, FakeHarness(_item_json("결재 한도 규칙")), conn).run()
        return KnowledgeRepository(tmp_path / "knowledge")

    def test_훅은_commit_msg_다(self, tmp_path) -> None:
        # pre-commit 시점에는 **이번 커밋의 메시지가 아직 없다.** COMMIT_EDITMSG 에는
        # 직전 것(대개 ingest 커밋)이 남아 있어 검사가 통째로 면제된다 — 훅이 걸려
        # 있는데 아무것도 막지 않는 상태가 된다.
        repo = self._repo(tmp_path)
        hook = repo.root / ".git" / "hooks" / "commit-msg"
        assert hook.exists()
        assert hook.stat().st_mode & 0o111
        # PATH 를 타지 않는다 — 운영자는 가상환경 밖 셸에서 커밋한다.
        assert sys.executable in hook.read_text(encoding="utf-8")

    def test_ingest_는_훅을_지나지_않는다(self, tmp_path) -> None:
        # 훅은 사람의 편집에 세 조건을 묻는 장치다. ingest 가 통과하려고
        # edited_by_human 을 켜면 정반대의 고장이 생긴다.
        repo = self._repo(tmp_path)
        assert repo.load_all()[0].item.edited_by_human is False

    def test_세_조건을_어긴_편집은_거부된다(self, tmp_path) -> None:
        repo = self._repo(tmp_path)
        stored = repo.load_all()[0]
        stored.path.write_text(
            stored.path.read_text(encoding="utf-8").replace("에이전트의 판단.", "손으로 고쳤다."),
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=repo.root, check=True)
        problems = repo.verify_staged_edits("수정")

        assert problems
        assert "조건 1" in problems[0]
        assert "조건 3" in problems[0]

    def test_한국어_경로가_검사에서_빠지지_않는다(self, tmp_path) -> None:
        # git 은 기본적으로 비 ASCII 경로를 이스케이프해 내놓는다. 그대로 쓰면
        # **지식 항목 제목이 대부분 한국어라 사실상 전부가 검사에서 빠진다.**
        repo = self._repo(tmp_path)
        stored = repo.load_all()[0]
        assert "결재" in stored.path.name  # 전제
        stored.path.write_text(
            stored.path.read_text(encoding="utf-8").replace("판단.", "고침."), encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo.root, check=True)

        assert repo.staged_item_paths() == [stored.path]

    def test_실제_커밋이_막힌다(self, tmp_path) -> None:
        # 훅이 걸려 있는 것과 실제로 막는 것은 다르다.
        repo = self._repo(tmp_path)
        stored = repo.load_all()[0]
        stored.path.write_text(
            stored.path.read_text(encoding="utf-8").replace("판단.", "고침."), encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo.root, check=True)

        done = subprocess.run(
            ["git", "commit", "-m", "수정"],
            cwd=repo.root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode != 0
        assert "조건 3" in done.stderr
        # **아무것도 잃지 않는다** — 작업 트리의 수정은 그대로 있다.
        assert "고침." in stored.path.read_text(encoding="utf-8")

    def test_세_조건을_지킨_편집은_통과한다(self, tmp_path) -> None:
        repo = self._repo(tmp_path)
        stored = repo.load_all()[0]
        stored.item.body = "손으로 고쳤다."
        stored.item.edited_by_human = True
        stored.item.invalidation = Invalidation(
            kind=InvalidationKind.LINKED, refs=("src/approval/limit.py",)
        )
        repo.save(stored.item, at=stored.path)
        subprocess.run(["git", "add", "-A"], cwd=repo.root, check=True)

        assert repo.verify_staged_edits("등급이 아니라 직급 기준이라 고쳤다") == []
