"""WBS-4.6.2 — 콘텐츠 제작 (FR-36·43, D50, §7.1·7.3).

**새 파이프라인이 아니다.** 답변과 같은 5단계를 트리거만 바꿔 탄다 (D10) — 여기가
1~3단계이고 검수·게재는 4.6.4·4.6.3 이 잇는다.

여기서 지키는 것은 여섯.

    1. **언어는 판정하지 않고 고정한다** (FR-43) — 살아있는 문서가 갱신마다 언어를
       바꾸면 안 된다
    2. **갱신은 직전 판본을 고친다** — 백지에서 다시 쓰면 diff 검수가 전문 검수가 된다
    3. **근거 없이 만들지 않는다** (D3) — 지어낸 근거는 문서가 나간 뒤 드러난다
    4. **낡은 근거는 빼되 코드 변경이 부른 주기는 기다린다** (§6.6.3 과 같은 순서)
    5. **바뀐 것이 없으면 초안을 만들지 않는다** — 대기열이 빈 판정으로 채워진다
    6. **진행 표시를 "내용이 바뀌었는가"에 걸지 않는다** — 걸면 매 주기 LLM 에 다시 실린다
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from agentic_service_desk.content import production, store
from agentic_service_desk.content.registry import load as load_registry
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations.schema import connect, initialize

from conftest import FakeHarness

GUIDE = load_registry().get("guide")
COLUMN = load_registry().get("column")


def _doc(title: str, body: str, grounding=("k-1",)) -> str:  # noqa: ANN001
    return json.dumps(
        {"title": title, "body": body, "grounding": list(grounding)}, ensure_ascii=False
    )


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _repo(tmp_path, *, stale: bool = False, qna_only: bool = False):  # noqa: ANN001, ANN202
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    provenance = (
        [Provenance(qna="q-1")] if qna_only else [Provenance(commit="a" * 40, path="a.py")]
    )
    repo.save(
        KnowledgeItem(
            id="k-1",
            title="결재 승인 한도 규칙",
            body="결재 승인 한도는 부서 등급으로 결정된다.",
            provenance=provenance,
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("a.py",)),
            stale=stale,
        )
    )
    repo.commit("적재")
    return repo


def _producer(tmp_path, conn, harness=None, **over):  # noqa: ANN001, ANN202
    return production.ContentProducer(
        conn=conn,
        repo=over.pop("repo", None) or _repo(tmp_path),
        harness=harness,
        generated_by=over.pop("generated_by", "test-model"),
    )


class TestLanguageIsFixed:
    """FR-43 · D55 — 콘텐츠는 1차 언어로 쓴다."""

    def test_근거가_영어여도_한국어로_쓴다(self, tmp_path) -> None:
        # 답변은 질문 언어로 쓰지만(FR-17) 콘텐츠는 고정이다. 근거 본문을 보고
        # 판정하면 영문 지식이 늘었을 때 **살아있는 문서 하나가 갱신마다 언어를
        # 바꾼다.**
        english = KnowledgeItem(
            id="k-1",
            title="Approval limit rule",
            body="The approval limit is determined by department grade.",
            provenance=[Provenance(commit="a" * 40, path="a.py")],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("a.py",)),
        )
        prompt = production.build_prompt(GUIDE, [english], None)

        assert "**한국어 로 쓴다.**" in prompt
        assert production.LANGUAGE == "한국어"


class TestFirstProduction:
    """첫 제작 — 기다릴 직전 판본이 없다."""

    def test_초안을_만들어_Q3_에_올린다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        p = _producer(tmp_path, conn, FakeHarness(_doc("사용 가이드", "본문이다.")))
        result = p.run(GUIDE)

        assert result.produced
        pending = store.pending(conn, "guide")
        assert len(pending) == 1
        assert pending[0].based_on is None  # 첫 제작이라 직전 판본이 없다
        assert pending[0].generated_by == "test-model"
        conn.close()

    def test_첫_제작은_언제나_돈다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        assert production.evaluate_trigger(conn, GUIDE).due
        conn.close()

    def test_사람_승인_전에는_나가지_않는다(self, tmp_path) -> None:
        # FR-39 — **콘텐츠는 국면과 무관하게 전수 사람 승인**이다. 여기서 나오는
        # 것은 초안일 뿐이고 게재 자리는 4.6.3 이 붙인다.
        conn = _conn(tmp_path)
        _producer(tmp_path, conn, FakeHarness(_doc("가이드", "본문"))).run(GUIDE)
        assert store.pending(conn, "guide")[0].state == store.PENDING
        assert store.current(conn, "guide") is None
        conn.close()


class TestGroundingIsRequired:
    """D3 — 근거 없이 만들지 않는다."""

    def test_근거가_없으면_만들지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        repo = KnowledgeRepository(tmp_path / "knowledge")
        repo.ensure_initialized()
        p = _producer(tmp_path, conn, FakeHarness(_doc("가이드", "본문")), repo=repo)

        assert p.run(GUIDE).outcome is store.Outcome.NO_GROUNDING
        assert store.pending(conn, "guide") == []
        conn.close()

    def test_QnA_에서만_온_지식으로는_가이드를_쓰지_않는다(self, tmp_path) -> None:
        # §7.2 — 가이드의 주 입력은 **소스코드 파생 지식**이다. 승격분만으로 쓴
        # 가이드는 사람들이 물어본 것의 모음이지 시스템 사용법이 아니다.
        conn = _conn(tmp_path)
        repo = _repo(tmp_path, qna_only=True)
        p = _producer(tmp_path, conn, FakeHarness(_doc("가이드", "본문")), repo=repo)

        assert p.run(GUIDE).outcome is store.Outcome.NO_GROUNDING
        conn.close()

    def test_지어낸_근거_id_는_버린다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        p = _producer(
            tmp_path, conn, FakeHarness(_doc("가이드", "본문", ("k-1", "k-없음")))
        )
        p.run(GUIDE)
        assert store.pending(conn, "guide")[0].grounding == ("k-1",)
        conn.close()

    def test_근거를_하나도_안_가리키면_초안이_아니다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        p = _producer(tmp_path, conn, FakeHarness(_doc("가이드", "본문", ("k-없음",))))

        assert p.run(GUIDE).outcome is store.Outcome.GENERATION_FAILED
        assert store.pending(conn, "guide") == []
        conn.close()


class TestUpdateIsNotRewrite:
    """§7.3 — 살아있는 문서의 재실행은 **같은 문서를 다시 씀**이다."""

    def _approved(self, tmp_path, conn, body: str = "옛 본문") -> str:  # noqa: ANN001
        draft_id = store.save(
            conn,
            type_id="guide",
            title="사용 가이드",
            body=body,
            grounding=("k-1",),
        )
        store.decide(conn, draft_id, approved=True)
        return draft_id

    def test_직전_판본이_프롬프트에_들어간다(self, tmp_path) -> None:
        # 백지에서 다시 쓰면 문서가 주기마다 통째로 달라지고, 그러면 **변경분
        # 검수가 전문 검수와 같아진다** — diff 가 곧 전문이다.
        conn = _conn(tmp_path)
        self._approved(tmp_path, conn, "옛 본문이다.")
        harness = FakeHarness(_doc("사용 가이드", "새 본문이다."))
        p = _producer(tmp_path, conn, harness)
        p.run(GUIDE)

        assert "직전 판본" in harness.prompts[0]
        assert "옛 본문이다." in harness.prompts[0]
        assert "고쳐서" in harness.prompts[0]
        conn.close()

    def test_승인된_것만_직전_판본이_된다(self, tmp_path) -> None:
        # 승인되지 않은 초안을 입력으로 삼으면 **사람이 보지 않은 글 위에 다음 글이
        # 쌓인다.**
        conn = _conn(tmp_path)
        store.save(conn, type_id="guide", title="가이드", body="검수 안 된 본문",
                   grounding=("k-1",))
        assert store.current(conn, "guide") is None
        conn.close()

    def test_갱신은_직전_판본을_가리킨다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        previous = self._approved(tmp_path, conn, "옛 본문이다.")
        p = _producer(tmp_path, conn, FakeHarness(_doc("가이드", "새 본문이다.")))
        p.run(GUIDE)

        assert store.pending(conn, "guide")[0].based_on == previous
        conn.close()

    def test_diff_를_세어서_만든다(self, tmp_path) -> None:
        # 모델에게 "무엇을 바꿨는가"를 묻지 않는다 — 그 답은 검증할 수 없고,
        # 검수자가 믿고 넘기면 표시가 없는 것과 같아진다.
        conn = _conn(tmp_path)
        self._approved(tmp_path, conn, "옛 본문이다.")
        result = _producer(
            tmp_path, conn, FakeHarness(_doc("가이드", "새 본문이다."))
        ).run(GUIDE)

        assert "-옛 본문이다." in result.diff
        assert "+새 본문이다." in result.diff
        conn.close()

    def test_바뀐_것이_없으면_초안을_만들지_않는다(self, tmp_path) -> None:
        # 대기열이 빈 판정으로 채워지면 실제로 볼 것이 그 사이에 묻힌다 (§8.6).
        conn = _conn(tmp_path)
        self._approved(tmp_path, conn, "같은 본문이다.")
        result = _producer(
            tmp_path, conn, FakeHarness(_doc("가이드", "같은 본문이다."))
        ).run(GUIDE)

        assert result.outcome is store.Outcome.UNCHANGED
        assert store.pending(conn, "guide") == []
        conn.close()

    def test_바뀌지_않아도_돈_것은_기록된다(self, tmp_path) -> None:
        # **진행 표시를 "내용이 바뀌었는가"에 걸지 않는다.** 걸면 바뀔 것이 없는
        # 타입이 매 주기 LLM 에 다시 실린다 — ingest 에서 이미 밟은 실패다.
        conn = _conn(tmp_path)
        self._approved(tmp_path, conn, "같은 본문이다.")
        _producer(tmp_path, conn, FakeHarness(_doc("가이드", "같은 본문이다."))).run(GUIDE)

        run = store.last_run(conn, "guide")
        assert run.outcome is store.Outcome.UNCHANGED
        assert run.last_generated_at is not None  # 주기 시계가 앞으로 갔다
        conn.close()


class TestStaleGrounding:
    """§6.6.3 — 지식이 먼저 따라잡아야 한다."""

    def test_낡은_항목은_근거에서_뺀다(self, tmp_path) -> None:
        # 답변은 질문에 답해야 해서 낡은 근거라도 쓰고 강도를 내리지만(§5.6.5),
        # **가이드는 무엇을 다룰지 고를 수 있다** — 다음 갱신에 들인다 (§7.3).
        conn = _conn(tmp_path)
        repo = _repo(tmp_path, stale=True)
        result = _producer(
            tmp_path, conn, FakeHarness(_doc("가이드", "본문")), repo=repo
        ).run(GUIDE)

        assert result.excluded_stale == ("k-1",)  # 뺐다는 사실을 남긴다
        assert result.outcome is store.Outcome.NO_GROUNDING
        conn.close()

    def test_코드_변경이_부른_주기는_기다린다(self, tmp_path) -> None:
        # 지식이 아직 그 커밋을 읽지 않았는데 돌리면 **같은 글이 나오고**, 그것을
        # 갱신이라 부르면 고쳤다는 기록만 남는다 (4.5.7 이 정정에서 정한 순서).
        conn = _conn(tmp_path)
        store.record_run(
            conn,
            type_id="guide",
            outcome=store.Outcome.PRODUCED,
            generated=True,
            commit="old-commit",
        )
        repo = KnowledgeRepository(tmp_path / "knowledge")
        repo.ensure_initialized()
        repo.save(
            KnowledgeItem(
                id="k-1", title="제목", body="본문",
                provenance=[Provenance(commit="a" * 40, path="a.py")],
                invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("a.py",)),
                stale=True,
            )
        )
        repo.save(
            KnowledgeItem(
                id="k-2", title="제목2", body="본문2",
                provenance=[Provenance(commit="b" * 40, path="b.py")],
                invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("b.py",)),
            )
        )
        repo.commit("적재")

        result = _producer(
            tmp_path, conn, FakeHarness(_doc("가이드", "본문", ("k-2",))), repo=repo
        ).run(GUIDE, source_commit="new-commit")

        assert result.outcome is store.Outcome.HELD
        assert store.pending(conn, "guide") == []
        conn.close()

    def test_기다려도_커서와_주기_시계는_옮기지_않는다(self, tmp_path) -> None:
        # 커서를 먼저 옮기면 그 코드 변경이 **아무것도 만들지 않은 채 소비된다.**
        conn = _conn(tmp_path)
        store.record_run(conn, type_id="guide", outcome=store.Outcome.PRODUCED,
                         generated=True, commit="old-commit")
        before = store.last_run(conn, "guide")
        repo = _repo(tmp_path, stale=True)
        _producer(tmp_path, conn, FakeHarness(_doc("가이드", "본문")), repo=repo).run(
            GUIDE, source_commit="new-commit"
        )
        after = store.last_run(conn, "guide")

        assert after.last_commit == "old-commit"
        assert after.last_generated_at == before.last_generated_at
        assert after.last_run_at >= before.last_run_at  # 본 것은 남는다
        conn.close()


class TestTrigger:
    """§7.5-2 — 주기와 임계."""

    def _produced(self, conn, days_ago: float, commit: str = "c1") -> None:  # noqa: ANN001
        store.record_run(conn, type_id="guide", outcome=store.Outcome.PRODUCED,
                         generated=True, commit=commit)
        conn.execute(
            "UPDATE content_run SET last_generated_at = ? WHERE type_id = 'guide'",
            ((datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),),
        )
        conn.commit()

    def test_주기가_차지_않으면_돌지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._produced(conn, days_ago=1)
        assert not production.evaluate_trigger(conn, GUIDE, source_commit="c1").due
        conn.close()

    def test_주기가_차면_돈다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._produced(conn, days_ago=20)  # guide 는 14일
        t = production.evaluate_trigger(conn, GUIDE, source_commit="c1")
        assert t.due and t.periodic
        conn.close()

    def test_코드가_바뀌면_주기_전에도_돈다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._produced(conn, days_ago=1, commit="c1")
        t = production.evaluate_trigger(conn, GUIDE, source_commit="c2")
        assert t.due and t.threshold and not t.periodic
        conn.close()

    def test_돌지_않은_주기는_기록하지_않는다(self, tmp_path) -> None:
        # 남기면 화면이 매 tick "방금 봤다"고 말해 실제로 무슨 일이 있었는지가 지워진다.
        conn = _conn(tmp_path)
        store.record_run(conn, type_id="guide", outcome=store.Outcome.NOT_DUE,
                         generated=False)
        assert store.last_run(conn, "guide") is None
        conn.close()

    def test_주기가_안_찼으면_초안도_안_만든다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._produced(conn, days_ago=1)
        harness = FakeHarness(_doc("가이드", "본문"))
        result = _producer(tmp_path, conn, harness).run(GUIDE, source_commit="c1")

        assert result.outcome is store.Outcome.NOT_DUE
        assert harness.prompts == []  # 모델을 부르지도 않는다
        conn.close()


class TestOneAtATime:
    """앞 초안이 검수 대기면 다시 만들지 않는다."""

    def test_대기_중인_초안이_있으면_멈춘다(self, tmp_path) -> None:
        # 한 타입이 Q3 를 채우면 다른 타입이 밀리고, 사람은 같은 문서의 여러 판본을
        # 차례로 보게 된다 (§6.2 가 답변에서 정한 것과 같다).
        conn = _conn(tmp_path)
        store.save(conn, type_id="guide", title="가이드", body="본문", grounding=("k-1",))
        harness = FakeHarness(_doc("가이드", "새 본문"))
        result = _producer(tmp_path, conn, harness).run(GUIDE)

        assert result.outcome is store.Outcome.PENDING_REVIEW
        assert harness.prompts == []
        conn.close()


class TestUnsupportedInput:
    """읽개는 주 입력 종류마다 하나다 — 타입마다가 아니다."""

    def test_QnA_통계_읽개가_없으면_조용히_건너뛰지_않는다(self, tmp_path) -> None:
        # 침묵은 "만들 것이 없다"와 구분되지 않아, 선언은 있는데 아무것도 안 나오는
        # 타입이 생긴다.
        conn = _conn(tmp_path)
        with pytest.raises(production.UnsupportedInput, match="4.7.1"):
            _producer(tmp_path, conn, FakeHarness()).run(COLUMN)
        conn.close()


class TestStageGate:
    """FR-59 · D49 — 켜진 단계에서만 돈다."""

    def test_콘텐츠는_S4_부터다(self) -> None:
        # 앞 단계 대기열이 밀려 있는데 새 대기열을 여는 것이 §1.5.4 가 금지한 것이다.
        assert production.CONTENT_STAGES == {"S4", "S5"}


class TestLiveDefects:
    """라이브가 잡은 것. **프롬프트로 부탁하고 코드가 지킨다.**"""

    def test_본문_맨_앞의_제목_줄을_걷어낸다(self, tmp_path) -> None:
        # 라이브에서 밟았다: 직전 판본을 `# 제목` + 본문으로 넘겼더니 모델이 그것을
        # 본문의 일부로 읽고 되받아 **갱신마다 제목이 한 줄씩 쌓였다.**
        conn = _conn(tmp_path)
        p = _producer(
            tmp_path, conn, FakeHarness(_doc("사용 가이드", "# 사용 가이드\n\n본문이다."))
        )
        p.run(GUIDE)
        assert store.pending(conn, "guide")[0].body == "본문이다."
        conn.close()

    def test_제목과_다른_머리말은_건드리지_않는다(self, tmp_path) -> None:
        # 문서가 스스로 고른 머리말이다.
        conn = _conn(tmp_path)
        p = _producer(
            tmp_path, conn, FakeHarness(_doc("사용 가이드", "# 개요\n\n본문이다."))
        )
        p.run(GUIDE)
        assert store.pending(conn, "guide")[0].body.startswith("# 개요")
        conn.close()

    def test_직전_판본을_제목_줄로_넘기지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft_id = store.save(conn, type_id="guide", title="사용 가이드",
                              body="옛 본문이다.", grounding=("k-1",))
        store.decide(conn, draft_id, approved=True)
        harness = FakeHarness(_doc("사용 가이드", "새 본문이다."))
        _producer(tmp_path, conn, harness).run(GUIDE)

        assert "직전 판본의 제목: 사용 가이드" in harness.prompts[0]
        assert "# 사용 가이드" not in harness.prompts[0]
        conn.close()

    def test_변경_비율을_센다(self, tmp_path) -> None:
        # 갱신은 고쳐 쓰기여야 하는데(§7.3) 모델은 근거가 그대로인 문단까지 다시
        # 쓴다 — 라이브에서 봤다. 막을 방법이 프롬프트뿐이면 **적어도 세어서
        # 검수자에게 말해 준다.**
        assert production.churn("a\nb\nc\nd", "a\nb\nc\nd") == 0.0
        assert production.churn("a\nb\nc\nd", "a\nb\nX\nY") == 0.5
        assert production.churn("", "") == 0.0

    def test_결과가_변경_비율을_들고_온다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft_id = store.save(conn, type_id="guide", title="가이드",
                              body="한 줄\n두 줄", grounding=("k-1",))
        store.decide(conn, draft_id, approved=True)
        result = _producer(
            tmp_path, conn, FakeHarness(_doc("가이드", "한 줄\n다른 줄"))
        ).run(GUIDE)

        assert result.churn == 0.5
        assert "50% 변경" in result.detail
        conn.close()
