"""WBS-4.5.5 — 게재 판정 (FR-25·26·57, D31, §5.5.3~4, §8.6.3).

검수는 필수이지만 **누가 어느 강도로 보는지는 국면에 따라 다르다.** 여기서 지키는
것은 여섯.

    1. **위험 신호 일곱을 센다** (§5.5.4) — 모델을 부르지 않는다
    2. **신호가 없으면 자동, 있으면 사람** (FR-25, D31)
    3. **게재가 없는 단계에는 판정도 없다** — D31 은 S3 부터 (§1.5.3)
    4. **3국면만 규칙이 다르다** — 표본으로 본다 (FR-57)
    5. **사람에게 갈 때 "확인 중"을 올린다** (FR-26) — 침묵보다 낫다
    6. **자동 게재를 사람 판정으로 적지 않는다** — 반려율의 의미가 무너진다
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from agentic_service_desk.adapters.mock import BOT_ACCOUNT, MockParentSystem
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.search import Hit, Search
from agentic_service_desk.operations import intake, qna_state
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline import draft_store, gating, publication
from agentic_service_desk.pipeline.answer import (
    Analysis,
    AnswerPipeline,
    Confidence,
    Draft,
    Statement,
)
from agentic_service_desk.pipeline.review import Reviewer, Verdict

from conftest import FakeHarness

ACCOUNTS = frozenset({BOT_ACCOUNT})
PASSED = Verdict(passed=True, detail="근거와 일치")


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _repo(tmp_path, *, count: int = 2, stale: bool = False, periodic: bool = False):  # noqa: ANN001, ANN202
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    for i in range(count):
        repo.save(
            KnowledgeItem(
                id=f"k-{i + 1}",
                title=f"결재 승인 한도 규칙 {i + 1}",
                body="결재 승인 한도는 신청자의 부서 등급으로 결정된다.",
                provenance=[Provenance(commit="a" * 40, path="approval/limit.py")],
                invalidation=(
                    Invalidation(kind=InvalidationKind.PERIODIC, period_days=90)
                    if periodic
                    else Invalidation(
                        kind=InvalidationKind.LINKED, refs=("approval/limit.py",)
                    )
                ),
                stale=stale,
            )
        )
    repo.commit("시험용 지식 항목")
    return repo


def _hits(repo, *ids: str) -> list[Hit]:  # noqa: ANN001
    return [Hit(item=repo.find(i).item, path=repo.find(i).path) for i in ids]


def _draft(*ids: str, text: str = "결재 승인 한도는 부서 등급으로 결정됩니다.") -> Draft:
    return Draft(
        statements=(Statement(text, Confidence.CONFIRMED, tuple(ids)),),
        grounding=tuple(ids),
    )


def _assess(tmp_path, conn, repo, **over):  # noqa: ANN001, ANN202
    base = dict(
        draft=_draft("k-1", "k-2"),
        hits=_hits(repo, "k-1", "k-2"),
        analysis=Analysis(language="ko", similar_questions=("Q-9",)),
        verdict=PASSED,
        repo=repo,
        stage="S3",
        phase=1,
        draft_key="ad-1",
    )
    base.update(over)
    return gating.assess(conn, **base)


class TestSignals:
    """§5.5.4 — 일곱이 서로 다른 것을 잡는다."""

    def test_신호가_없으면_비어_있다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        assert _assess(tmp_path, conn, repo).signals == ()
        conn.close()

    def test_근거가_하나면_잡는다(self, tmp_path) -> None:
        # 교차 확인이 불가능하다.
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        a = _assess(
            tmp_path, conn, repo, draft=_draft("k-1"), hits=_hits(repo, "k-1")
        )
        assert gating.Signal.THIN_GROUNDING in a.signals
        conn.close()

    def test_stale_근거를_잡는다(self, tmp_path) -> None:
        # P4 직결 — 검수가 이미 반려했어야 하는 건이다.
        conn, repo = _conn(tmp_path), _repo(tmp_path, stale=True)
        assert gating.Signal.STALE_GROUNDING in _assess(tmp_path, conn, repo).signals
        conn.close()

    def test_주기형_만료_임박을_잡는다(self, tmp_path) -> None:
        """**주기형인 것 자체는 신호가 아니다** — 아직 유효한 동안은 다를 바 없다."""
        conn, repo = _conn(tmp_path), _repo(tmp_path, periodic=True)
        # 방금 커밋됐으므로 임박하지 않다.
        assert gating.Signal.EXPIRING not in _assess(tmp_path, conn, repo).signals
        # 주기의 80% 가 지나면 임박이다.
        later = datetime.now(UTC) + timedelta(days=80)
        assert gating.Signal.EXPIRING in _assess(tmp_path, conn, repo, now=later).signals
        conn.close()

    def test_근거에_없는_수치를_잡는다(self, tmp_path) -> None:
        # P1 직결. 검수의 기계적 검사를 그대로 쓴다 — 두 곳이 다른 답을 내면 안 된다.
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        a = _assess(
            tmp_path,
            conn,
            repo,
            draft=_draft("k-1", "k-2", text="한도는 300만원입니다."),
        )
        assert gating.Signal.UNGROUNDED_FACTS in a.signals
        conn.close()

    def test_새로운_유형을_잡는다(self, tmp_path) -> None:
        """1국면에는 거의 모든 질문이 여기 걸린다 — **그것이 §8.6.3 이 말한
        "자동 게재 대상이 적다"의 실체**이고, 지식이 쌓이면 저절로 꺼진다."""
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        a = _assess(
            tmp_path, conn, repo, analysis=Analysis(language="ko", similar_questions=())
        )
        assert gating.Signal.NOVEL in a.signals
        conn.close()

    def test_검수를_통과하지_못하면_잡는다(self, tmp_path) -> None:
        """**판정이 없는 것을 안전한 쪽으로 다룬다** — 검수가 없는 것과 통과한
        것은 다르고, 후자로 취급하면 "검증했다"는 라벨이 남는다 (§5.6.1)."""
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        assert gating.Signal.UNREVIEWED in _assess(
            tmp_path, conn, repo, verdict=None
        ).signals
        assert gating.Signal.UNREVIEWED in _assess(
            tmp_path, conn, repo, verdict=Verdict(passed=False, detail="근거 밖")
        ).signals
        conn.close()

    def test_과거_답변과_근거가_다르면_잡는다(self, tmp_path) -> None:
        """**답변 이력(§6.6)이 여기서 다시 값을 한다.**

        근거 버전 고정이 없으면 "그때 무엇에 기대어 답했는지"를 알 수 없어 비교
        자체가 불가능하다.
        """
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        past = "한도는 어떻게 정해지나요?"
        conn.execute(
            "INSERT INTO raw_question (id, body, asker_account, created_at, collected_at) "
            "VALUES ('Q-9', ?, 'emp-1', 'x', 'x')",
            (past,),
        )
        conn.execute(
            "INSERT INTO qna_item (id, parent_question_id, origin, state, opened_at) "
            "VALUES ('q-past', 'Q-9', 'parent', ?, 'x')",
            (qna_state.PUBLISHED,),
        )
        conn.execute(
            "INSERT INTO answer_record (id, qna_item_id, body, author_kind, state) "
            "VALUES ('ar-past', 'q-past', '옛 답변', 'bot', 'published')"
        )
        conn.execute(
            "INSERT INTO answer_grounding "
            "(answer_record_id, knowledge_item_id, pinned_commit) "
            "VALUES ('ar-past', 'k-1', 'c')"
        )
        conn.commit()
        similar = Analysis(language="ko", similar_questions=(past,))

        # 지금은 k-1·k-2 로 답한다 — 그때와 다르다.
        a = _assess(tmp_path, conn, repo, analysis=similar)
        assert gating.Signal.DIVERGENT in a.signals
        # 같은 근거면 신호가 아니다.
        same = _assess(
            tmp_path,
            conn,
            repo,
            analysis=similar,
            draft=_draft("k-1"),
            hits=_hits(repo, "k-1"),
        )
        assert gating.Signal.DIVERGENT not in same.signals
        conn.close()


class TestRouting:
    """FR-25 · D31 — 신호가 없으면 자동, 있으면 사람."""

    def test_신호가_없으면_자동이다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        assert _assess(tmp_path, conn, repo).route is gating.Route.AUTO
        conn.close()

    def test_하나라도_있으면_사람이다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        a = _assess(tmp_path, conn, repo, verdict=None)
        assert a.route is gating.Route.HUMAN
        assert "위험 신호" in a.reason
        conn.close()

    def test_1국면과_2국면의_판정이_같다(self, tmp_path) -> None:
        """**실수가 아니다.** §5.5.3 의 두 문장은 집행 형태로 옮기면 같아지고,
        실제 차이는 신호가 국면에 따라 저절로 꺼지는 데서 온다 — 여기에 임계를 더
        얹으면 그 자연스러운 변화 위에 인위적 계단을 포갠다."""
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        for phase in (1, 2):
            assert _assess(tmp_path, conn, repo, phase=phase).route is gating.Route.AUTO
            assert (
                _assess(tmp_path, conn, repo, phase=phase, verdict=None).route
                is gating.Route.HUMAN
            )
        conn.close()

    def test_S3_이전에는_판정하지_않는다(self, tmp_path) -> None:
        """D31 은 **S3 부터** 적용된다 (§1.5.3). S0~S2 는 게재 자체가 없다."""
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        for stage in ("S0", "S1", "S2"):
            a = _assess(tmp_path, conn, repo, stage=stage)
            assert a.route is gating.Route.NOT_PUBLISHING
            assert not a.auto
        conn.close()


class TestPhaseThree:
    """FR-57 — 3국면만 규칙이 다르다. 사람은 감사자로 물러난다."""

    def test_신호가_있어도_통과시킨다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        a = _assess(
            tmp_path,
            conn,
            repo,
            phase=3,
            sample_rate=0,
            analysis=Analysis(language="ko", similar_questions=()),
        )
        assert gating.Signal.NOVEL in a.signals
        assert a.route is gating.Route.AUTO
        conn.close()

    def test_반려는_여전히_사람에게_간다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        a = _assess(tmp_path, conn, repo, phase=3, sample_rate=0, verdict=None)
        assert a.route is gating.Route.HUMAN
        assert not a.sampled
        conn.close()

    def test_표본은_사람이_본다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        a = _assess(tmp_path, conn, repo, phase=3, sample_rate=1.0)
        assert a.route is gating.Route.HUMAN
        assert a.sampled
        assert "표본" in a.reason
        conn.close()

    def test_표본은_결정적이다(self, tmp_path) -> None:
        """난수를 쓰면 **재실행이 답변의 운명을 바꾼다** — 그러면 "왜 이건 사람에게
        갔는가"에 답할 수 없다."""
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        picks = {
            key: _assess(
                tmp_path, conn, repo, phase=3, sample_rate=0.5, draft_key=key
            ).sampled
            for key in (f"ad-{i}" for i in range(20))
        }
        again = {
            key: _assess(
                tmp_path, conn, repo, phase=3, sample_rate=0.5, draft_key=key
            ).sampled
            for key in picks
        }
        assert picks == again
        assert 0 < sum(picks.values()) < len(picks)  # 전부도 아니고 아무것도 아니지 않다
        conn.close()


class TestHoldingNotice:
    """FR-26 · §8.6.3 — 검수 대기 중임을 이용자에게 보여준다."""

    def _qna(self, conn, qid="q-1", parent="Q-1"):  # noqa: ANN001, ANN202
        conn.execute(
            "INSERT INTO qna_item (id, parent_question_id, origin, state, opened_at) "
            "VALUES (?, ?, 'parent', ?, 'x')",
            (qid, parent, qna_state.RECEIVED),
        )
        conn.commit()
        return qid

    def test_확인_중이_그_자리에_올라간다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._qna(conn)
        parent = MockParentSystem()
        held = publication.hold(conn, parent, "q-1", bot_accounts=ACCOUNTS)
        assert isinstance(held, publication.Held)

        posted = parent.list_answers("Q-1")[-1]
        assert "확인 중" in posted.body
        assert posted.author_account == BOT_ACCOUNT
        conn.close()

    def test_급하면_직접_물으라고_적는다(self) -> None:
        """감추면 이용자는 기다리다 결국 물어보고, 그때는 **이 시스템이 늦다는
        인상만 남는다.**"""
        assert "직접 문의" in publication.HOLDING_BODY

    def test_한_번만_올린다(self, tmp_path) -> None:
        # 매번 올리면 한 질문에 "확인 중"이 여러 개 달린다.
        conn = _conn(tmp_path)
        self._qna(conn)
        parent = MockParentSystem()
        publication.hold(conn, parent, "q-1", bot_accounts=ACCOUNTS)
        count = len(parent.list_answers("Q-1"))

        again = publication.hold(conn, parent, "q-1", bot_accounts=ACCOUNTS)
        assert isinstance(again, publication.Refused)
        assert len(parent.list_answers("Q-1")) == count
        conn.close()

    def test_승인하면_그_자리를_채운다(self, tmp_path) -> None:
        """**자리를 새로 잡지 않는다.** "확인 중"을 남겨 둔 채 답변을 하나 더 올리면
        이용자의 질문에 글이 둘 달리고 그중 하나는 영원히 "확인 중"이다."""
        conn = _conn(tmp_path)
        self._qna(conn)
        repo = _repo(tmp_path)
        parent = MockParentSystem()
        held = publication.hold(conn, parent, "q-1", bot_accounts=ACCOUNTS)
        count = len(parent.list_answers("Q-1"))

        draft_id = draft_store.save(
            conn, question="질문", draft=_draft("k-1", "k-2"), qna_item_id="q-1"
        )
        draft_store.decide(conn, draft_id, approved=True)
        result = publication.publish(
            conn, parent, draft_id, bot_accounts=ACCOUNTS, repo=repo
        )
        assert isinstance(result, publication.Published)
        assert result.parent_answer_id == held.parent_answer_id
        assert len(parent.list_answers("Q-1")) == count  # 글이 늘지 않았다

        filled = parent.list_answers("Q-1")[-1]
        assert "확인 중" not in filled.body
        assert "부서 등급" in filled.body
        conn.close()

    def test_모_시스템을_거치지_않은_질문에는_올리지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        conn.execute(
            "INSERT INTO qna_item (id, parent_question_id, origin, state, opened_at) "
            "VALUES ('q-m', NULL, 'manual', ?, 'x')",
            (qna_state.RECEIVED,),
        )
        conn.commit()
        result = publication.hold(
            conn, MockParentSystem(), "q-m", bot_accounts=ACCOUNTS
        )
        assert isinstance(result, publication.Refused)
        assert result.reason is publication.Refusal.NO_DESTINATION
        conn.close()


class TestAutoApproveRecord:
    """§5.5.6 — 자동 게재를 사람 판정으로 적지 않는다."""

    def test_판정_주체가_gate_다(self, tmp_path) -> None:
        """섞으면 반려율이 **사람이 에이전트를 얼마나 믿는가**를 더는 뜻하지 않는다 —
        사람이 보지도 않은 건이 분모에 들어가기 때문이다."""
        from agentic_service_desk.pipeline.review import distribution

        conn = _conn(tmp_path)
        draft_id = draft_store.save(conn, question="질문", draft=_draft("k-1", "k-2"))
        draft_store.auto_approve(conn, draft_id, detail="위험 신호가 없다.")

        assert draft_store.get(conn, draft_id).state == draft_store.APPROVED
        assert distribution(conn, reviewed_by="human").total == 0
        assert distribution(conn, reviewed_by="gate").total == 1
        conn.close()


class TestEndToEnd:
    """유입 → 판정 → 게재 / 확인 중까지 이어지는가."""

    def _pipeline(self, tmp_path, conn, repo):  # noqa: ANN001, ANN202
        payload = json.dumps(
            {
                "answerable": True,
                "statements": [
                    {
                        "text": "결재 승인 한도는 부서 등급으로 결정됩니다.",
                        "confidence": "확인됨",
                        "grounding": ["k-1", "k-2"],
                    }
                ],
                "unanswered": [],
            },
            ensure_ascii=False,
        )
        return AnswerPipeline(
            search=Search(repo=repo, conn=conn),
            conn=conn,
            harness=FakeHarness(*[payload] * 6),
        )

    def _raw(self, conn, qid="Q-1", body="결재 승인 한도는 어떻게 정해지나요?"):  # noqa: ANN001, ANN202
        conn.execute(
            "INSERT INTO raw_question (id, body, asker_account, created_at, collected_at) "
            "VALUES (?, ?, 'emp-1', '2026-08-01T09:00:00+00:00', 'x')",
            (qid, body),
        )
        conn.commit()

    def _gate(self, tmp_path, repo, parent, **over):  # noqa: ANN001, ANN202
        base = dict(
            parent=parent,
            repo=repo,
            bot_accounts=ACCOUNTS,
            stage="S3",
            phase=1,
        )
        base.update(over)
        return intake.Gate(**base)

    def test_신호가_없으면_바로_나간다(self, tmp_path) -> None:
        """**사람 대기열을 거치지 않는다** — D31 이 노린 것이 이 경로다.

        신호를 전부 끄려면 유사 질문이 있어야 하므로, 같은 말로 물은 옛 질문을
        하나 심는다. 1국면에 이런 건이 드물다는 것이 §8.6.3 의 완충이다.
        """
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        conn.execute(
            "INSERT INTO raw_question (id, body, asker_account, created_at, collected_at) "
            "VALUES ('Q-0', '결재 승인 한도 기준은 무엇인가요?', 'emp-0', 'a', 'a')"
        )
        # 이미 처리가 끝난 옛 질문이다 — 유입 대상이 아니다.
        conn.execute(
            "INSERT INTO qna_item (id, parent_question_id, origin, state, opened_at) "
            "VALUES ('q-old', 'Q-0', 'parent', ?, 'a')",
            (qna_state.RESOLVED,),
        )
        conn.commit()
        self._raw(conn)

        parent = MockParentSystem(seed=False)
        parent.add_question("Q-1", "결재 승인 한도는 어떻게 정해지나요?", "emp-1")
        intake.run(
            conn,
            pipeline=self._pipeline(tmp_path, conn, repo),
            reviewer=Reviewer(
                FakeHarness(json.dumps({"passed": True, "detail": "일치"}))
            ),
            gate=self._gate(tmp_path, repo, parent),
        )
        # Q2 를 거치지 않고 나갔다.
        assert draft_store.pending(conn) == []
        assert conn.execute("SELECT count(*) c FROM holding_notice").fetchone()["c"] == 0
        row = conn.execute("SELECT state FROM answer_record").fetchone()
        assert row["state"] == publication.PUBLISHED
        assert "부서 등급" in parent.list_answers("Q-1")[-1].body
        conn.close()

    def test_신호가_있으면_확인_중이_올라간다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        self._raw(conn)
        parent = MockParentSystem()
        # 유사 질문이 없으므로 NOVEL 이 걸린다 — 1국면의 전형이다.
        intake.run(
            conn,
            pipeline=self._pipeline(tmp_path, conn, repo),
            reviewer=Reviewer(
                FakeHarness(json.dumps({"passed": True, "detail": "일치"}))
            ),
            gate=self._gate(tmp_path, repo, parent),
        )
        assert len(draft_store.pending(conn)) == 1  # 사람에게 갔다
        notice = conn.execute("SELECT * FROM holding_notice").fetchone()
        assert notice is not None
        conn.close()

    def test_S2_에는_확인_중도_게재도_없다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        self._raw(conn)
        parent = MockParentSystem()
        before = len(parent.list_answers("Q-1"))
        intake.run(
            conn,
            pipeline=self._pipeline(tmp_path, conn, repo),
            gate=self._gate(tmp_path, repo, parent, stage="S2"),
        )
        assert len(draft_store.pending(conn)) == 1
        assert conn.execute("SELECT count(*) c FROM holding_notice").fetchone()["c"] == 0
        assert len(parent.list_answers("Q-1")) == before
        conn.close()

    def test_게이트가_없으면_초안만_쌓인다(self, tmp_path) -> None:
        conn, repo = _conn(tmp_path), _repo(tmp_path)
        self._raw(conn)
        intake.run(conn, pipeline=self._pipeline(tmp_path, conn, repo))
        assert len(draft_store.pending(conn)) == 1
        assert conn.execute("SELECT count(*) c FROM answer_record").fetchone()["c"] == 0
        conn.close()


class TestSelfSimilarity:
    """1단계 분석이 **자기 자신을 유사 질문으로 세지 않는다.**"""

    def test_자기_자신은_유사_질문이_아니다(self, tmp_path) -> None:
        """**라이브 배선에서 잡은 결함이다.**

        질문은 Raw Layer 에 적재된 뒤 파이프라인을 타므로(WBS-4.5.2) 그냥 세면 모든
        질문이 자기와 완전히 겹쳐 유사 질문이 항상 하나 이상이 된다. 그러면 게재
        판정의 `NOVEL` 이 영원히 꺼지고, **1국면에 자동 게재가 드물어야 한다는
        §8.6.3 의 완충이 통째로 사라진다.**
        """
        from agentic_service_desk.pipeline.answer import find_similar_questions

        conn = _conn(tmp_path)
        question = "결재 승인 한도는 어떻게 정해지나요?"
        conn.execute(
            "INSERT INTO raw_question (id, body, asker_account, created_at, collected_at) "
            "VALUES ('Q-1', ?, 'emp-1', 'a', 'a')",
            (question,),
        )
        conn.commit()
        assert find_similar_questions(conn, question) == ()

        conn.execute(
            "INSERT INTO raw_question (id, body, asker_account, created_at, collected_at) "
            "VALUES ('Q-2', '결재 승인 한도 기준은?', 'emp-2', 'a', 'a')"
        )
        conn.commit()
        assert find_similar_questions(conn, question) == ("결재 승인 한도 기준은?",)
        conn.close()


class TestQ2ShowsWhy:
    """§8.6.3 — 화면이 **무엇부터 볼지** 말해 준다."""

    def test_왜_사람에게_왔는지_적힌다(self, tmp_path) -> None:
        """에이전트 검수 반려와 **따로 든다** — 저쪽은 "이 문장이 근거로 뒷받침되지
        않는다"이고 이쪽은 "이 건은 사람이 봐야 한다"다."""
        conn = _conn(tmp_path)
        draft_id = draft_store.save(conn, question="질문", draft=_draft("k-1"))
        draft_store.note_signals(
            conn, draft_id, (str(gating.Signal.THIN_GROUNDING), str(gating.Signal.NOVEL))
        )
        d = draft_store.get(conn, draft_id)
        assert d.gate_signals == ("근거가 1개 이하", "새로운 유형의 질문이다")
        assert "게재 판정이 잡은 것" in d.look_here_first
        conn.close()


class TestDashboardBindsToLoopbackByDefault:
    """대시보드에는 인증이 없다 — 기본값이 넓으면 붙는 순간 누구나 누른다.

    읽기 전용 화면이 아니다: 승인(`/queues/Q2/{id}/decide`) · 게재
    (`/queues/Q3/{id}/publish`) · 모순 해결(`/queues/Q4/{id}/resolve`) ·
    국면 전진(`/phase/advance`) 이 전부 POST 다. **붙을 수 있는 사람은 곧
    누를 수 있는 사람이다.**
    """

    def test_기본은_루프백이다(self) -> None:
        from agentic_service_desk.config import Settings

        # `_env_file=None` 이 요점이다. `Settings()` 는 `.env` 를 읽으므로
        # 그대로 부르면 **개발자 기계의 설정이 기본값 행세를 한다** — 실제로
        # 이 시험을 처음 썼을 때 로컬 `.env` 의 tailnet 주소가 잡혔다.
        cfg = Settings(_env_file=None)
        assert cfg.web_host == "127.0.0.1"
        assert cfg.web_port == 8000

    def test_넓히는_것은_선언해야_된다(self) -> None:
        from agentic_service_desk.config import Settings

        assert Settings(web_host="100.64.0.1").web_host == "100.64.0.1"
