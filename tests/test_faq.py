"""WBS-4.7.1 — FAQ (FR-37, §7.2, §5.3).

FAQ 가 가이드와 다른 점 하나로 시험이 갈린다: **무엇을 다룰지와 무엇에 기대어 쓸지가
다른 원천이다.** 반복 질문 분포가 앞을 정하고, 지식베이스가 뒤를 정한다.

여기서 지키는 것은 여섯.

    1. **세는 것과 쓰는 것을 나눈다** (§5.3) — 봇이 답했든 아무도 답하지 않았든
       반복은 반복이지만, 봇의 미검증 답변이 FAQ 본문이 되지는 않는다
    2. **낱말 하나가 겹친 것은 같은 질문이 아니다** — 검색에서 이미 밟은 고장이다
    3. **묶음이 사슬처럼 번지지 않는다** — 대표와만 견준다
    4. **개인정보를 애초에 읽지 않는다** (PO-3) — `asker_account` 는 꺼내 오지 않는다
    5. **반복되는데 근거가 없으면 빼고, 뺐다고 말한다** — 지식 공백이다 (§6.2)
    6. **모르는 임계는 조용히 '안 찼다'가 되지 않는다**
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from agentic_service_desk.content import production, qna_stats, store
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

FAQ = load_registry().get("faq")


# --- 재료 -----------------------------------------------------------------


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _ask(
    conn: sqlite3.Connection,
    qid: str,
    body: str,
    *,
    account: str = "user-1",
    days_ago: int = 0,
    resolved: bool = False,
    title: str = "",
) -> None:
    when = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO raw_question (id, title, body, asker_account, created_at, "
        "collected_at) VALUES (?, ?, ?, ?, ?, ?)",
        (qid, title, body, account, when, when),
    )
    if resolved:
        conn.execute(
            "INSERT INTO raw_resolution (question_id, resolved, grade, collected_at) "
            "VALUES (?, 1, 'explicit', ?)",
            (qid, when),
        )
    conn.commit()


def _answer(conn: sqlite3.Connection, qid: str, *, account: str) -> None:
    """누가 답했는지는 **반복 세기와 무관하다** (§5.3)."""
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO raw_answer (id, question_id, body, author_account, created_at, "
        "collected_at) VALUES (?, ?, ?, ?, ?, ?)",
        (f"a-{qid}", qid, "봇이 쓴 답", account, now, now),
    )
    conn.commit()


def _manual(conn: sqlite3.Connection, item_id: str, question: str) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO qna_item (id, origin, state, opened_at) VALUES (?, 'manual', ?, ?)",
        (item_id, "접수", now),
    )
    conn.execute(
        "INSERT INTO manual_entry (qna_item_id, question, answer, registered_at) "
        "VALUES (?, ?, ?, ?)",
        (item_id, question, "담당자가 한 답", now),
    )
    conn.commit()


def _repo(tmp_path, *items: KnowledgeItem):  # noqa: ANN001, ANN202
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    for item in items:
        repo.save(item)
    repo.commit("적재")
    return repo


def _item(item_id: str, title: str, body: str, *, stale: bool = False) -> KnowledgeItem:
    return KnowledgeItem(
        id=item_id,
        title=title,
        body=body,
        provenance=[Provenance(commit="a" * 40, path="a.py")],
        invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("a.py",)),
        stale=stale,
    )


def _doc(title: str, body: str, grounding=("k-1",)) -> str:  # noqa: ANN001
    return json.dumps(
        {"title": title, "body": body, "grounding": list(grounding)}, ensure_ascii=False
    )


def _producer(conn, repo, harness=None):  # noqa: ANN001, ANN202
    return production.ContentProducer(
        conn=conn, repo=repo, harness=harness, generated_by="test-model"
    )


# --- 반복 질문 탐지 --------------------------------------------------------


class TestClustering:
    """같은 것을 묻는 질문을 묶는다. 형태소 분석기를 쓰지 않는다."""

    def test_조사가_달라도_같은_질문으로_묶인다(self, tmp_path) -> None:
        # "한도가"와 "한도는"은 서로를 품지 않아 부분 일치로는 못 넘는다 —
        # 앞이 어간이라는 것만 가정해 뒤 한 글자 차이를 받는다.
        conn = _conn(tmp_path)
        _ask(conn, "q-1", "결재 한도가 어떻게 정해지나요", days_ago=3)
        _ask(conn, "q-2", "결재 한도는 어떻게 정해지나요", days_ago=2)

        groups = qna_stats.detect(conn)
        assert len(groups) == 1
        assert groups[0].count == 2
        conn.close()

    def test_낱말_하나만_겹치면_묶이지_않는다(self, tmp_path) -> None:
        # 라이브에서 "VPN 접속이 안 되는데 어떻게 하나요"가 "결재 한도" 항목에
        # 걸렸고 이유가 "어떻게" 하나였다. 같은 고장을 여기서 막는다.
        conn = _conn(tmp_path)
        _ask(conn, "q-1", "VPN 접속이 안 되는데 어떻게 하나요", days_ago=2)
        _ask(conn, "q-2", "결재 한도 결정 규칙은 어떻게 되나요", days_ago=1)

        assert len(qna_stats.detect(conn)) == 2
        conn.close()

    def test_인사말이_붙어도_같은_질문이다(self, tmp_path) -> None:
        # **라이브에서 잡았다.** 양쪽 비율의 최솟값으로 재던 때 이 둘이 갈렸다 —
        # 늘어난 낱말이 자기 쪽 분모를 키워 같은 질문을 다른 질문으로 만들었다.
        # 덜 묶이면 임계에 영영 닿지 않아 FAQ 가 만들어지지 않는다 (O37).
        conn = _conn(tmp_path)
        _ask(conn, "q-1", "결재 한도가 어떻게 정해지나요", days_ago=3)
        _ask(conn, "q-2", "결재 한도는 어떻게 정해지는지 알고 싶습니다", days_ago=2)
        _ask(conn, "q-3", "제 결재 한도가 300만원인데 이 한도는 어떻게 정해지나요", days_ago=1)

        groups = qna_stats.detect(conn)
        assert len(groups) == 1
        assert groups[0].count == 3
        conn.close()

    def test_묶음이_사슬처럼_번지지_않는다(self, tmp_path) -> None:
        # 단일 연결 군집화는 A~B, B~C 를 이유로 A 와 C 를 붙이고, 그렇게 이어
        # 붙이면 큰 묶음 하나가 결국 전부를 삼킨다. 대표하고만 견준다.
        conn = _conn(tmp_path)
        _ask(conn, "q-1", "결재 한도 조회 방법", days_ago=3)
        _ask(conn, "q-2", "결재 한도 조회 화면 위치", days_ago=2)
        _ask(conn, "q-3", "화면 위치 변경 신청 절차", days_ago=1)

        groups = qna_stats.detect(conn)
        assert all(g.count <= 2 for g in groups)
        conn.close()

    def test_대표는_가장_최근_표현이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _ask(conn, "q-1", "결재 한도가 어떻게 정해지나요", days_ago=3)
        _ask(conn, "q-2", "결재 한도는 어떻게 정해지는지 궁금합니다", days_ago=1)

        group = qna_stats.detect(conn)[0]
        assert group.representative.endswith("궁금합니다")
        assert len(group.variants) == 2  # 일반화의 재료다 (PO-3)
        conn.close()

    def test_같은_입력은_같은_묶음을_낸다(self, tmp_path) -> None:
        # 화면의 후보가 볼 때마다 달라지면 운영자가 그것을 믿지 않는다.
        conn = _conn(tmp_path)
        for i in range(4):
            _ask(conn, f"q-{i}", "결재 한도가 어떻게 정해지나요", days_ago=4 - i)

        first = [g.count for g in qna_stats.detect(conn)]
        assert first == [g.count for g in qna_stats.detect(conn)]
        conn.close()


class TestWhatCounts:
    """§5.3 — 세는 것과 쓰는 것을 나눈다."""

    def test_봇이_답한_미해결_질문도_센다(self, tmp_path) -> None:
        # "무엇이 자주 묻히는가"는 작성자와 무관하다. 봇 답변을 통째로 없는 셈 치면
        # 대부분의 QnA 가 통계에서 사라지고 FAQ 는 영영 자라지 않는다.
        conn = _conn(tmp_path)
        for i in range(3):
            _ask(conn, f"q-{i}", "결재 한도가 어떻게 정해지나요", days_ago=3 - i)
            _answer(conn, f"q-{i}", account="svc-agentic-desk")

        assert qna_stats.peak(qna_stats.detect(conn)) == 3
        conn.close()

    def test_아무도_답하지_않은_질문도_센다(self, tmp_path) -> None:
        # 반복되는데 아직 아무도 못 푼 질문이 후보에서 빠지면, 가장 답이 필요한
        # 것이 가장 늦게 다뤄진다.
        conn = _conn(tmp_path)
        for i in range(3):
            _ask(conn, f"q-{i}", "결재 한도가 어떻게 정해지나요", days_ago=3 - i)

        group = qna_stats.detect(conn)[0]
        assert group.count == 3
        assert group.resolved_count == 0
        conn.close()

    def test_수동_등록_질문도_센다(self, tmp_path) -> None:
        # 메신저로 오간 반복이 통계에서 사라지면 수동 등록을 만든 의미가 없다 (§1.4.3).
        conn = _conn(tmp_path)
        _ask(conn, "q-1", "결재 한도가 어떻게 정해지나요", days_ago=2)
        _manual(conn, "qi-1", "결재 한도는 어떻게 정해지나요")

        assert qna_stats.peak(qna_stats.detect(conn)) == 2
        conn.close()

    def test_질문자_계정을_꺼내_오지_않는다(self, tmp_path) -> None:
        # FAQ 는 공개 문서다. 프롬프트에 넣지 않는 것보다 꺼내 오지 않는 것이
        # 확실하다 (PO-3).
        conn = _conn(tmp_path)
        _ask(conn, "q-1", "결재 한도가 어떻게 정해지나요", account="사번12345")

        loaded = qna_stats.load_questions(conn)
        assert not hasattr(loaded[0], "asker_account")
        assert "사번12345" not in loaded[0].text
        conn.close()


# --- 트리거 ---------------------------------------------------------------


class TestThreshold:
    """반복 횟수가 임계다 (O37) — 후보 건수가 아니다."""

    def _produced(self, conn, *, days_ago: int) -> None:  # noqa: ANN001
        store.record_run(
            conn, type_id="faq", outcome=store.Outcome.PRODUCED, generated=True
        )
        conn.execute(
            "UPDATE content_run SET last_generated_at = ? WHERE type_id = 'faq'",
            ((datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),),
        )
        conn.commit()

    def test_반복이_임계에_닿으면_주기_전에도_돈다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._produced(conn, days_ago=1)  # faq 는 7일 주기

        t = production.evaluate_trigger(conn, FAQ, signals={"repeat_questions": 3.0})
        assert t.due and t.threshold and not t.periodic
        assert "반복 질문" in t.reason
        conn.close()

    def test_임계에_못_닿으면_주기까지_기다린다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._produced(conn, days_ago=1)

        assert not production.evaluate_trigger(
            conn, FAQ, signals={"repeat_questions": 2.0}
        ).due
        conn.close()

    def test_모르는_임계는_조용히_넘어가지_않는다(self, tmp_path) -> None:
        # 모르는 임계를 False 로 두면 그 타입은 주기가 없을 때 영영 돌지 않는데,
        # 그 침묵은 "아직 안 찼다"와 구분되지 않는다.
        conn = _conn(tmp_path)
        self._produced(conn, days_ago=1)

        with pytest.raises(production.UnknownThreshold, match="repeat_questions"):
            production.evaluate_trigger(conn, FAQ, signals={})
        conn.close()

    def test_첫_제작은_임계와_무관하게_돈다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        assert production.evaluate_trigger(conn, FAQ, signals={}).due
        conn.close()


# --- 2단계 조회 ------------------------------------------------------------


class TestGrounding:
    """무엇을 다룰지는 반복이 정하고, 무엇에 기대어 쓸지는 지식베이스가 정한다."""

    def _asked(self, conn, times: int = 3) -> None:  # noqa: ANN001
        for i in range(times):
            _ask(conn, f"q-{i}", "결재 한도가 어떻게 정해지나요", days_ago=times - i)

    def test_반복_질문마다_근거가_붙는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._asked(conn)
        repo = _repo(tmp_path, _item("k-1", "결재 승인 한도 규칙", "한도는 부서 등급으로 결정된다."))

        material = _producer(conn, repo)._read_input(FAQ, qna_stats.detect(conn))
        assert len(material.covered) == 1
        group, items = material.covered[0]
        assert group.count == 3
        assert [i.id for i in items] == ["k-1"]
        conn.close()

    def test_근거를_못_찾은_반복_질문은_지식_공백이다(self, tmp_path) -> None:
        # 자주 묻는데 지식베이스가 모르는 자리다 (§6.2). 지어내서 채우지 않는다.
        conn = _conn(tmp_path)
        self._asked(conn)
        repo = _repo(tmp_path, _item("k-1", "VPN 접속 절차", "사내망 접속은 클라이언트로 한다."))

        material = _producer(conn, repo)._read_input(FAQ, qna_stats.detect(conn))
        assert not material.covered
        assert len(material.uncovered) == 1
        conn.close()

    def test_임계에_못_닿은_질문은_재료가_아니다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._asked(conn, times=2)  # 임계는 3
        repo = _repo(tmp_path, _item("k-1", "결재 승인 한도 규칙", "한도는 부서 등급으로 결정된다."))

        material = _producer(conn, repo)._read_input(FAQ, qna_stats.detect(conn))
        assert not material.covered and not material.uncovered
        assert material.repeats == 2
        conn.close()

    def test_낡은_근거는_빼고_뺐다고_남긴다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._asked(conn)
        repo = _repo(
            tmp_path,
            _item("k-1", "결재 승인 한도 규칙", "한도는 부서 등급으로 결정된다.", stale=True),
        )

        material = _producer(conn, repo)._read_input(FAQ, qna_stats.detect(conn))
        assert material.stale == ("k-1",)
        assert material.uncovered  # 낡은 것만 남으면 다루지 않는다
        conn.close()

    def test_한도를_넘긴_반복_질문은_조용히_잘리지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        for topic in range(production.MAX_QUESTIONS + 2):
            for i in range(3):
                _ask(
                    conn,
                    f"q-{topic}-{i}",
                    f"항목{topic:02d}조회 규칙{topic:02d}적용 절차{topic:02d}안내",
                    days_ago=3 - i,
                )
        repo = _repo(tmp_path, _item("k-1", "항목 조회", "조회는 화면에서 한다."))

        material = _producer(conn, repo)._read_input(FAQ, qna_stats.detect(conn))
        assert len(material.covered) + len(material.uncovered) == production.MAX_QUESTIONS
        assert material.deferred == 2
        conn.close()


# --- 3단계 생성 ------------------------------------------------------------


class TestPrompt:
    """재료의 모양이 주 입력에 따라 다르다."""

    def _covered(self, conn, repo):  # noqa: ANN001, ANN202
        for i in range(3):
            _ask(conn, f"q-{i}", "결재 한도가 어떻게 정해지나요", days_ago=3 - i)
        return _producer(conn, repo)._read_input(FAQ, qna_stats.detect(conn))

    def test_질문_아래에_그_근거가_붙는다(self, tmp_path) -> None:
        # 항목을 따로 늘어놓으면 어느 근거가 어느 질문의 것인지 모델이 짐작해야 하고,
        # 그 짐작이 틀리면 엉뚱한 근거를 단 문답이 나온다.
        conn = _conn(tmp_path)
        repo = _repo(tmp_path, _item("k-1", "결재 승인 한도 규칙", "한도는 부서 등급으로 결정된다."))
        material = self._covered(conn, repo)

        prompt = production.build_prompt(FAQ, material.items, None, covered=material.covered)
        assert "질문 1 — 3회 물음" in prompt
        assert prompt.index("결재 한도가") < prompt.index("k-1 — 결재 승인 한도 규칙")
        conn.close()

    def test_일반화를_요구한다(self, tmp_path) -> None:
        # FAQ 는 공개 문서라 물어본 사람의 사정이 실려서는 안 된다 (PO-3).
        conn = _conn(tmp_path)
        repo = _repo(tmp_path, _item("k-1", "결재 승인 한도 규칙", "한도는 부서 등급으로 결정된다."))
        material = self._covered(conn, repo)

        prompt = production.build_prompt(FAQ, material.items, None, covered=material.covered)
        assert "일반화해서 쓴다" in prompt
        assert "사번" in prompt
        conn.close()

    def test_근거가_답하지_못하는_질문은_빼라고_말한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        repo = _repo(tmp_path, _item("k-1", "결재 승인 한도 규칙", "한도는 부서 등급으로 결정된다."))
        material = self._covered(conn, repo)

        prompt = production.build_prompt(FAQ, material.items, None, covered=material.covered)
        assert "근거가 답하지 못하는 질문은 빼고 쓴다" in prompt
        conn.close()


class TestRun:
    """한 주기가 끝까지 돈다."""

    def _asked(self, conn, times: int = 3) -> None:  # noqa: ANN001
        for i in range(times):
            _ask(conn, f"q-{i}", "결재 한도가 어떻게 정해지나요", days_ago=times - i)

    def test_반복이_임계에_닿으면_초안이_Q3_에_오른다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._asked(conn)
        repo = _repo(tmp_path, _item("k-1", "결재 승인 한도 규칙", "한도는 부서 등급으로 결정된다."))
        harness = FakeHarness(_doc("자주 묻는 질문", "## 결재 한도는 어떻게 정해지나요\n부서 등급으로 정해진다."))

        result = _producer(conn, repo, harness).run(FAQ)

        assert result.outcome is store.Outcome.PRODUCED
        assert "반복 질문 1건" in result.detail
        draft = store.pending(conn, "faq")[0]
        assert draft.ticket_id  # Q3 는 작업 대기열이다 (FR-45)
        assert draft.grounding == ("k-1",)
        conn.close()

    def test_반복이_없으면_고장이_아니라_재료가_없는_것이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._asked(conn, times=1)
        repo = _repo(tmp_path, _item("k-1", "결재 승인 한도 규칙", "한도는 부서 등급으로 결정된다."))
        harness = FakeHarness(_doc("자주 묻는 질문", "본문"))

        result = _producer(conn, repo, harness).run(FAQ)

        assert result.outcome is store.Outcome.NO_GROUNDING
        assert "재료가 없는 것이지 고장이 아니다" in result.detail
        assert harness.prompts == []  # 모델을 부르지도 않는다
        conn.close()

    def test_지식_공백은_공백이라고_말한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._asked(conn)
        repo = _repo(tmp_path, _item("k-1", "VPN 접속 절차", "사내망 접속은 클라이언트로 한다."))
        harness = FakeHarness(_doc("자주 묻는 질문", "본문"))

        result = _producer(conn, repo, harness).run(FAQ)

        assert result.outcome is store.Outcome.NO_GROUNDING
        assert "지식 공백" in result.detail
        assert result.uncovered
        conn.close()

    def test_반복_임계로_돌면_낡은_근거를_기다리지_않는다(self, tmp_path) -> None:
        # 코드 변경 임계는 지식이 뒤처졌다는 신호라 기다리지만(§6.6.3), 반복 질문은
        # 사람들이 물었다는 신호다 — 낡은 것을 뺀 채로 만드는 것이 맞다.
        conn = _conn(tmp_path)
        self._asked(conn)
        repo = _repo(
            tmp_path,
            _item("k-1", "결재 승인 한도 규칙", "한도는 부서 등급으로 결정된다."),
            _item("k-2", "결재 한도 예외", "예외는 별도 승인으로 처리한다.", stale=True),
        )
        harness = FakeHarness(_doc("자주 묻는 질문", "부서 등급으로 정해진다."))

        result = _producer(conn, repo, harness).run(FAQ)

        assert result.outcome is store.Outcome.PRODUCED
        assert "k-2" in result.excluded_stale
        conn.close()

    def test_반복_질문이_없어도_주기가_차면_돌지만_만들지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        store.record_run(
            conn, type_id="faq", outcome=store.Outcome.PRODUCED, generated=True
        )
        conn.execute(
            "UPDATE content_run SET last_generated_at = ? WHERE type_id = 'faq'",
            ((datetime.now(UTC) - timedelta(days=10)).isoformat(),),
        )
        conn.commit()
        repo = _repo(tmp_path, _item("k-1", "결재 승인 한도 규칙", "한도는 부서 등급으로 결정된다."))
        harness = FakeHarness(_doc("자주 묻는 질문", "본문"))

        result = _producer(conn, repo, harness).run(FAQ)

        assert result.outcome is store.Outcome.NO_GROUNDING
        assert harness.prompts == []
        conn.close()
