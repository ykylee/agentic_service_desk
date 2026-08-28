"""WBS-4.6.4 — 콘텐츠 검수 (FR-39·45, §5.5.5, §7.6.4).

**콘텐츠는 국면과 무관하게 전수 사람 승인이다.** 답변에 있는 자동 게재 관문이
여기에는 **아예 없다** — 빈도가 낮고, 노출이 넓고, 회수가 어렵다.

여기서 지키는 것은 여섯.

    1. **자동 승인 경로가 없다** (FR-39)
    2. 검수 범위를 **성격이 정한다** — 살아있는 문서는 변경분, 발행물은 전문 + 최종 확인
    3. 에이전트는 **판정하지 않고 지목한다** — 소견이 없다고 통과가 아니다
    4. **인용이 근거 원문과 어긋나면 지목한다** — 라이브에서 잡은 것이다
    5. **반려에는 사유가 필요하다** (§5.5.6)
    6. 콘텐츠 검수는 **답변 반려율에 섞이지 않는다** — 그 숫자의 뜻이 달라진다
"""

from __future__ import annotations

import sqlite3

import pytest

from agentic_service_desk.content import review as content_review
from agentic_service_desk.content import store
from agentic_service_desk.content.registry import load as load_registry
from agentic_service_desk.operations import promotion as promotion_domain
from agentic_service_desk.operations import ticket as ticket_domain
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline import review as review_domain
from agentic_service_desk.pipeline.review import Reject

GUIDE = load_registry().get("guide")
COLUMN = load_registry().get("column")
SOURCE = {"k-1": "결재 승인 한도 결재 승인 한도는 부서 등급으로 결정된다."}


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _draft(conn, body: str = "부서 등급으로 결정된다.", *, with_ticket: bool = True):  # noqa: ANN001, ANN202
    ticket_id = (
        ticket_domain.issue(conn, source=ticket_domain.Source.CONTENT).id
        if with_ticket
        else None
    )
    draft_id = store.save(
        conn,
        type_id="guide",
        title="사용 가이드",
        body=body,
        grounding=("k-1",),
        ticket_id=ticket_id,
    )
    return store.get(conn, draft_id)


class TestNoAutoApproval:
    """FR-39 — 전수 사람 승인. **자동 게재 관문이 없다.**"""

    def test_승인은_사람_판정으로만_기록된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn)
        content_review.decide(conn, GUIDE, draft, approved=True)

        rows = list(conn.execute("SELECT reviewed_by, kind FROM review"))
        assert [r["reviewed_by"] for r in rows] == ["human"]
        assert [r["kind"] for r in rows] == ["content"]
        conn.close()

    def test_반려에는_사유가_필요하다(self, tmp_path) -> None:
        # 사유 없는 반려는 기록으로 쓸 수 없다 (§5.5.6) — 분포가 없으면 반려율이
        # 읽히지 않는다.
        conn = _conn(tmp_path)
        with pytest.raises(ValueError, match="사유가 필요하다"):
            content_review.decide(conn, GUIDE, _draft(conn), approved=False)
        conn.close()

    def test_판정하면_초안이_대기열에서_빠진다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        content_review.decide(conn, GUIDE, _draft(conn), approved=True)
        assert store.pending(conn, "guide") == []
        assert store.current(conn, "guide") is not None  # 승인분이 다음 갱신의 입력이다
        conn.close()

    def test_반려된_것은_직전_판본이_되지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        content_review.decide(
            conn, GUIDE, _draft(conn), approved=False, reason=Reject.P2
        )
        assert store.current(conn, "guide") is None
        conn.close()


class TestScopeFollowsNature:
    """§5.5.5 · §7.3 — 검수 범위는 성격이 정한다."""

    def test_살아있는_문서는_변경분_발행물은_전문이다(self) -> None:
        from agentic_service_desk.content.registry import Scope

        assert GUIDE.review.scope is Scope.DIFF
        assert COLUMN.review.scope is Scope.FULL

    def test_발행물은_전문_검수에_최종_확인이_붙는다(self) -> None:
        # 발행물은 W3 를 해소할 수 없다 — 이미 읽힌 회차는 되돌아오지 않는다.
        assert content_review.awaiting_final_check(COLUMN)
        assert not content_review.awaiting_final_check(GUIDE)

    def test_최종_확인을_초안에_저장하지_않는다(self, tmp_path) -> None:
        # 저장하면 선언과 두 벌이 되고, 어긋나면 **낮은 쪽이 이긴다** —
        # 발행물이 최종 확인 없이 나가는 쪽으로 틀린다.
        conn = _conn(tmp_path)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(content_draft)")}
        assert "final_check" not in cols
        assert "scope" not in cols
        conn.close()


class TestAgentPointsNotJudges:
    """에이전트는 **어디를 먼저 볼지** 말해 줄 뿐이다."""

    def test_소견이_없어도_통과가_아니다(self, tmp_path) -> None:
        # 여기 없는 것은 기계가 확정할 수 없는 것뿐이고, 사람이 볼 이유는 그대로다.
        conn = _conn(tmp_path)
        findings = content_review.inspect(
            GUIDE, _draft(conn), source_text=SOURCE
        )
        assert findings.items == []
        assert "통과가 아니라" in findings.look_here_first
        conn.close()

    def test_낡은_근거를_지목한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        findings = content_review.inspect(
            GUIDE, _draft(conn), source_text=SOURCE, stale_ids=frozenset({"k-1"})
        )
        assert findings.items[0].reason is Reject.P4
        conn.close()

    def test_근거에_없는_수치를_지목한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn, "한도는 300만원이다.")
        findings = content_review.inspect(GUIDE, draft, source_text=SOURCE)
        assert any(f.reason is Reject.P1 for f in findings.items)
        conn.close()


class TestQuotationDrift:
    """라이브에서 잡은 것 — **인용은 원문 그대로라는 주장이다.**"""

    def test_근거에_없는_인용을_지목한다(self, tmp_path) -> None:
        # 근거 항목의 본문이 바뀌었는데 문서가 옛 문장을 그대로 인용한 채 남는다.
        conn = _conn(tmp_path)
        draft = _draft(conn, '"결재 한도는 직급으로 결정된다." 라고 정해져 있다.')
        findings = content_review.inspect(GUIDE, draft, source_text=SOURCE)

        assert any("없는 인용" in f.detail for f in findings.items)
        conn.close()

    def test_원문과_같은_인용은_지목하지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn, '"결재 승인 한도는 부서 등급으로 결정된다."')
        assert content_review.check_quotations(draft.body, SOURCE) is None
        conn.close()

    def test_짧은_따옴표는_인용으로_보지_않는다(self) -> None:
        # 낱말 하나를 강조한 따옴표까지 인용으로 보면 **소견이 소음이 되어 결국
        # 아무도 안 본다.**
        assert content_review.check_quotations('"등급"이 기준이다.', SOURCE) is None

    def test_공백만_다른_인용은_같게_본다(self) -> None:
        body = '"결재 승인  한도는\n부서 등급으로 결정된다."'
        assert content_review.check_quotations(body, SOURCE) is None


class TestPolicyVoice:
    """P8 — 정책 공지처럼 읽히는 서술 (§7.6.3·7.6.4)."""

    def test_칼럼에는_붙는다(self, tmp_path) -> None:
        # AI 가 "앞으로는 이렇게 하십시오"라고 쓰면 사내에서는 **정책 공지로
        # 읽히는데**, 이 시스템은 그런 권위를 가질 수 없다.
        conn = _conn(tmp_path)
        draft = _draft(conn, "결재선을 미리 확인하십시오.")
        findings = content_review.inspect(COLUMN, draft, source_text=SOURCE)
        assert any(f.reason is Reject.P8 for f in findings.items)
        conn.close()

    def test_가이드에는_붙지_않는다(self, tmp_path) -> None:
        # 선언이 정한다 (FR-42). 가이드에 붙이면 사용 설명의 "~해야 합니다"까지
        # 걸려 소견이 소음이 된다.
        conn = _conn(tmp_path)
        draft = _draft(conn, "결재선을 미리 확인하십시오.")
        findings = content_review.inspect(GUIDE, draft, source_text=SOURCE)
        assert not any(f.reason is Reject.P8 for f in findings.items)
        conn.close()

    def test_문장_형태만으로_판정한다(self) -> None:
        # 모델에 물으면 같은 문장이 볼 때마다 다르게 판정된다 (§7.6.4).
        assert content_review.check_policy_voice("반드시 결재선을 확인한다") is not None
        assert content_review.check_policy_voice("결재선은 자동으로 만들어진다") is None


class TestTicketIsClosed:
    """§6.4.3 — Q3 는 티켓을 낳고, 판정이 그것을 닫는다."""

    def test_판정하면_티켓이_닫힌다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn)
        content_review.decide(conn, GUIDE, draft, approved=True)

        t = ticket_domain.get(conn, draft.ticket_id)
        assert t.state is ticket_domain.State.CLOSED
        conn.close()

    def test_콘텐츠는_지식으로_승격되지_않는다(self, tmp_path) -> None:
        # **T4(자기 참조)** — 콘텐츠도 이 시스템이 쓴 것이라, 지식으로 되돌아오면
        # 자기 요약을 다시 배우는 순환이 된다 (§7.4).
        conn = _conn(tmp_path)
        draft = _draft(conn)
        content_review.decide(conn, GUIDE, draft, approved=True)

        assert ticket_domain.Source.CONTENT not in promotion_domain.PROMOTABLE_SOURCES
        assert promotion_domain.promote_if_eligible(conn, None, draft.ticket_id) is None
        conn.close()

    def test_티켓이_없어도_판정은_된다(self, tmp_path) -> None:
        # 티켓 배선 이전에 만들어진 초안이 있을 수 있다. 판정이 그것 때문에
        # 막히면 대기열이 영영 비지 않는다.
        conn = _conn(tmp_path)
        draft = _draft(conn, with_ticket=False)
        content_review.decide(conn, GUIDE, draft, approved=True)
        assert store.pending(conn, "guide") == []
        conn.close()


class TestNotMixedWithAnswers:
    """§5.5.6 — 콘텐츠 반려가 답변 반려율에 섞이면 그 숫자의 뜻이 달라진다."""

    def test_답변_반려율에_섞이지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        content_review.decide(
            conn, GUIDE, _draft(conn), approved=False, reason=Reject.P2
        )
        assert review_domain.distribution(conn).total == 0  # 기본이 answer 다
        assert review_domain.distribution(conn, kind="content").rejected == 1
        conn.close()

    def test_핵심_지표의_반려율도_답변만_센다(self, tmp_path) -> None:
        from agentic_service_desk.web import metrics

        conn = _conn(tmp_path)
        content_review.decide(
            conn, GUIDE, _draft(conn), approved=False, reason=Reject.P2
        )
        rate = {m.label: m for m in metrics.core(conn)}["검수 반려율"]
        assert rate.value is None  # 답변 검수는 아직 하나도 없다 — 0% 가 아니다
        conn.close()

    def test_답변_사유_목록에_P6_P8_이_없다(self) -> None:
        # 고를 수 없는 선택지가 화면에 늘면 무엇이 이 화면의 사유인지가 흐려진다.
        assert Reject.P8 not in review_domain.ANSWER_REASONS
        assert Reject.P8 in review_domain.COLUMN_REASONS


class TestScreen:
    """FR-45 — 작업 대기열이라 **항목마다 상세가 있다.**"""

    def _app(self, tmp_path):  # noqa: ANN001, ANN202
        from fastapi.testclient import TestClient

        from agentic_service_desk.config import Settings
        from agentic_service_desk.web.app import create_app

        cfg = Settings(
            _env_file=None,
            operations_db=tmp_path / "ops.sqlite3",
            knowledge_dir=tmp_path / "knowledge",
            stage="S4",
        )
        return TestClient(create_app(cfg))

    def test_목록이_먼저_볼_것을_말해_준다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _draft(conn)
        conn.close()

        body = self._app(tmp_path).get("/queues/Q3").text
        assert "사용 가이드" in body
        assert "첫 제작" in body
        assert "전수 사람 승인" in body

    def test_상세가_티켓_id_로_열린다(self, tmp_path) -> None:
        # 순위(`next_up`)가 가리키는 것이 티켓이므로 그 자리에서 바로 열려야 한다.
        conn = _conn(tmp_path)
        draft = _draft(conn)
        conn.close()

        body = self._app(tmp_path).get(f"/queues/Q3/{draft.ticket_id}").text
        assert "사용 가이드" in body
        assert "부서 등급으로 결정된다." in body

    def test_화면에서_승인하면_대기열이_빈다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = _draft(conn)
        conn.close()

        app = self._app(tmp_path)
        app.post(f"/queues/Q3/{draft.ticket_id}/decide", data={"approved": "1"})

        conn = _conn(tmp_path)
        assert store.pending(conn, "guide") == []
        assert store.current(conn, "guide").id == draft.id
        conn.close()

    def test_사유_없이_반려하면_되돌린다(self, tmp_path) -> None:
        # 사유 없는 반려는 기록으로 쓸 수 없다 (§5.5.6) — 조용히 반려하지 않는다.
        conn = _conn(tmp_path)
        draft = _draft(conn)
        conn.close()

        app = self._app(tmp_path)
        app.post(f"/queues/Q3/{draft.ticket_id}/decide", data={"approved": "0"})

        conn = _conn(tmp_path)
        assert len(store.pending(conn, "guide")) == 1  # 그대로 남았다
        conn.close()

    def test_순위가_Q3_상세로_보낸다(self, tmp_path) -> None:
        from agentic_service_desk.knowledge.repository import KnowledgeRepository
        from agentic_service_desk.web.dashboard import Dashboard

        conn = _conn(tmp_path)
        draft = _draft(conn)
        board = Dashboard(repo=KnowledgeRepository(tmp_path / "knowledge"), conn=conn)
        item = [i for i in board.next_up("S4") if i.queue.id == "Q3"][0]

        assert item.href == f"/queues/Q3/{draft.ticket_id}"
        conn.close()

    def test_상세가_없는_대기열은_목록으로_보낸다(self, tmp_path) -> None:
        # 예전에는 넷이 모두 티켓 화면으로 갔는데, 모순 티켓의 티켓 화면은
        # "종결 기록 초안을 기다리는 중"이라며 **하지 않아도 될 일을 시켰다.**
        from agentic_service_desk.knowledge.repository import KnowledgeRepository
        from agentic_service_desk.web.dashboard import Dashboard

        conn = _conn(tmp_path)
        ticket_domain.issue(conn, source=ticket_domain.Source.CONTRADICTION)
        board = Dashboard(repo=KnowledgeRepository(tmp_path / "knowledge"), conn=conn)
        item = [i for i in board.next_up("S4") if i.queue.id == "Q4"][0]

        assert item.href == "/queues/Q4"
        conn.close()
