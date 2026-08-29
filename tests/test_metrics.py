"""WBS-4.5.8 — 지표와 현황 (FR-47·50·58, NFR-9, §6.3, §8.3, §5.6.6).

**현황 숫자는 주인공이 아니다** (§8.1) — 대기열이 왜 쌓였는지를 설명하는 보조
정보다. 여기서 지키는 것은 다섯.

    1. **핵심 지표 여섯을 센다** (FR-58)
    2. **분모가 0 이면 0% 가 아니라 없음이다** — 1국면의 정상을 고장으로 오판하지 않게
    3. **잴 수 없는 것을 세는 척하지 않는다** — 빈 값과 아직 만들지 않은 것은 다르다
    4. **현황 다섯 종이 모두 보인다** (FR-47)
    5. **개인 평가가 아니라 부하 지표다** (NFR-9)
"""

from __future__ import annotations

import sqlite3

from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations import qna_state
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline.review import ReviewInput, Reject, Verdict, record
from agentic_service_desk.web import metrics


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _qna(conn, qid, *, grade=None, state=qna_state.PUBLISHED, origin="parent"):  # noqa: ANN001, ANN202
    conn.execute(
        "INSERT INTO qna_item (id, parent_question_id, origin, state, resolution_grade, opened_at) "
        "VALUES (?, ?, ?, ?, ?, 'a')",
        (qid, f"Q-{qid}", origin, state, grade),
    )
    conn.commit()


def _published(conn, qid, record_id):  # noqa: ANN001, ANN202
    conn.execute(
        "INSERT INTO answer_record (id, qna_item_id, body, author_kind, state) "
        "VALUES (?, ?, '본문', 'bot', 'published')",
        (record_id, qid),
    )
    conn.commit()


def _review(conn, qid, *, by, passed):  # noqa: ANN001, ANN202
    record(
        conn,
        review=ReviewInput(draft_body="본문", grounding=("k-1",), source_text={}),
        verdict=Verdict(
            passed=passed, reason=None if passed else Reject.P1, checked_by=by
        ),
        qna_item_id=qid,
    )


def _by_label(conn) -> dict[str, metrics.Metric]:  # noqa: ANN001
    return {m.label: m for m in metrics.core(conn)}


class TestCoreMetrics:
    """FR-58 — 여섯. **하나로 뭉치면 실제 품질이 가려진다.**"""

    def test_여섯을_센다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        assert len(metrics.core(conn)) == 6
        assert set(_by_label(conn)) == {
            "명시적 해결률",
            "암묵적 해결 비율",
            "미해결 종료율",
            "해결 표시율",
            "커버리지",
            "검수 반려율",
        }
        conn.close()

    def test_등급이_분리_집계된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _qna(conn, "q-1", grade=qna_state.EXPLICIT)
        _qna(conn, "q-2", grade=qna_state.IMPLICIT)
        _qna(conn, "q-3", grade=qna_state.IMPLICIT)
        _qna(conn, "q-4", state=qna_state.UNRESOLVED_CLOSED)

        m = _by_label(conn)
        assert m["명시적 해결률"].value == 0.25
        assert m["암묵적 해결 비율"].value == 0.5
        assert m["미해결 종료율"].value == 0.25
        conn.close()

    def test_해결_표시율은_게재된_것만_분모다(self, tmp_path) -> None:
        """**선행 지표다** — 게재되지 않은 질문은 이용자가 누를 것도 없다."""
        conn = _conn(tmp_path)
        _qna(conn, "q-1", grade=qna_state.EXPLICIT)
        _qna(conn, "q-2")
        _qna(conn, "q-3")  # 게재되지 않았다
        _published(conn, "q-1", "ar-1")
        _published(conn, "q-2", "ar-2")

        m = _by_label(conn)["해결 표시율"]
        assert m.denominator == 2
        assert m.value == 0.5
        conn.close()

    def test_커버리지는_답을_만들_수_있었던_비율이다(self, tmp_path) -> None:
        from agentic_service_desk.pipeline import draft_store
        from agentic_service_desk.pipeline.answer import Confidence, Draft, Statement

        conn = _conn(tmp_path)
        _qna(conn, "q-1")
        _qna(conn, "q-2")
        draft_store.save(
            conn,
            question="질문",
            draft=Draft(
                statements=(Statement("본문", Confidence.CONFIRMED, ("k-1",)),),
                grounding=("k-1",),
            ),
            qna_item_id="q-1",
        )
        assert _by_label(conn)["커버리지"].value == 0.5
        conn.close()

    def test_반려율은_사유별_기록_위에서_난다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _qna(conn, "q-1")
        _review(conn, "q-1", by="human", passed=True)
        _review(conn, "q-1", by="human", passed=False)
        assert _by_label(conn)["검수 반려율"].value == 0.5
        conn.close()


class TestEmptyIsNotZero:
    """분모가 0 이면 **0% 가 아니라 없음**이다."""

    def test_아무것도_없으면_값이_없다(self, tmp_path) -> None:
        """0% 로 내면 "한 건도 해결되지 않았다"로 읽히는데, 실제로는 아직 아무 일도
        없었던 것이다 — **1국면의 정상을 고장으로 오판**하는 실패 방식이다."""
        conn = _conn(tmp_path)
        for m in metrics.core(conn):
            assert m.value is None
            assert m.percent == "—"
        conn.close()


class TestUnmeasurable:
    """잴 수 없는 것을 세는 척하지 않는다."""

    def test_무수정_승인_비율은_잴_수_없다고_말한다(self, tmp_path) -> None:
        """초안을 고치는 기능이 없어 **언제나 100% 로 나온다.**

        0 이나 100 을 내보내면 없는 신호가 있는 것처럼 읽힌다.
        """
        conn = _conn(tmp_path)
        rows = dict(metrics.agent_status(conn).rows)
        assert "잴 수 없다" in rows["무수정 승인 비율"]
        conn.close()

    def test_뽑힌_표본이_없는_것과_0퍼센트는_다르다(self, tmp_path) -> None:
        """장치는 생겼지만(4.8.4) **뽑힌 것과 판정한 것은 또 다르다.**"""
        conn = _conn(tmp_path)
        rows = dict(metrics.agent_status(conn).rows)
        assert "아직 뽑힌 표본이 없다" in rows["표본 재검증 일치율"]
        assert "0% 가 아니다" in rows["표본 재검증 일치율"]
        conn.close()

    def test_국면별_임계가_미정임을_밝힌다(self, tmp_path) -> None:
        """지어내 붙이면 **1국면의 정상 상태가 빨간불이 된다.**"""
        conn = _conn(tmp_path)
        rows = dict(metrics.phase_view(conn, stage="S3", seed=1).status.rows)
        assert "아직 정해지지 않았다" in rows["국면별 임계"]
        conn.close()

    def test_만든_것과_나간_것을_구분한다(self, tmp_path) -> None:
        # 게재는 4.6.3 이다 — 만든 것이 있어도 나간 것은 없다. 0 을 내면
        # "만들었는데 하나도 안 나갔다"로 읽힌다.
        from agentic_service_desk.content import registry

        conn = _conn(tmp_path)
        rows = dict(metrics.content_status(conn, registry.load()).rows)
        assert "시스템 사용 가이드" in rows  # 선언된 것은 말한다
        assert "아직 나간 것이 없다" in rows["게재"]
        assert "아직 한 번도 돌지 않았다" in rows["마지막 제작"]
        conn.close()


class TestAgentStatus:
    """§5.6.6 — 사람이 아니라 부하를 가리킨다."""

    def test_판정_주체가_셋으로_갈린다(self, tmp_path) -> None:
        """자동 게재를 사람 판정에 섞으면 **반려율의 의미가 무너진다.**"""
        conn = _conn(tmp_path)
        _qna(conn, "q-1")
        _review(conn, "q-1", by="agent", passed=True)
        _review(conn, "q-1", by="human", passed=False)
        _review(conn, "q-1", by="gate", passed=True)

        rows = dict(metrics.agent_status(conn).rows)
        assert "통과 1 · 반려 0" in rows["에이전트 검수"]
        assert "통과 0 · 반려 1" in rows["사람 검수"]
        assert "1건" in rows["게재 판정 통과"]
        conn.close()

    def test_일치율은_양쪽_판정이_다_있는_건만_센다(self, tmp_path) -> None:
        """사람이 아직 안 본 건을 분모에 넣으면 **대기열이 밀릴수록 일치율이
        떨어지는데**, 그것은 판정 품질과 무관하다."""
        conn = _conn(tmp_path)
        _qna(conn, "q-1")
        _qna(conn, "q-2")
        _review(conn, "q-1", by="agent", passed=True)
        _review(conn, "q-1", by="human", passed=True)
        _review(conn, "q-2", by="agent", passed=True)  # 사람이 아직 안 봤다

        assert metrics._agreement(conn) == 1.0
        conn.close()

    def test_부하_지표임을_화면이_말한다(self, tmp_path) -> None:
        # NFR-9 — 개인 평가로 쓰면 지표는 좋아지고 품질은 그대로다.
        conn = _conn(tmp_path)
        note = metrics.agent_status(conn).note
        assert "개인 평가가 아니라" in note
        conn.close()


class TestScreen:
    """FR-47 — 현황 다섯 종이 모두 보인다."""

    def _app(self, tmp_path, **over):  # noqa: ANN001, ANN202
        from agentic_service_desk.config import Settings
        from agentic_service_desk.web.app import create_app

        base = dict(
            operations_db=tmp_path / "ops.sqlite3",
            knowledge_dir=tmp_path / "knowledge",
            stage="S3",
        )
        base.update(over)
        return create_app(Settings(_env_file=None, **base))  # type: ignore[arg-type]

    def test_다섯이_모두_뜬다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        KnowledgeRepository(tmp_path / "knowledge").ensure_initialized()
        _conn(tmp_path).close()

        html = TestClient(self._app(tmp_path)).get("/status").text
        for title in (
            "지식베이스 현황",
            "QnA 처리 현황",
            "콘텐츠 현황",
            "에이전트 운영 현황",
            "국면 상태",
        ):
            assert title in html

    def test_대기열과_화면을_나눈다(self, tmp_path) -> None:
        """한 화면에 섞으면 **숫자가 대기열을 밀어내고**, 그러면 처리해야 할 것이
        현황 사이에 묻힌다 (§8.1)."""
        from fastapi.testclient import TestClient

        KnowledgeRepository(tmp_path / "knowledge").ensure_initialized()
        _conn(tmp_path).close()

        client = TestClient(self._app(tmp_path))
        assert "/status" in client.get("/").text
        assert "국면 상태" not in client.get("/").text

    def test_수동_등록이_W4_지표임을_적는다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        KnowledgeRepository(tmp_path / "knowledge").ensure_initialized()
        conn = _conn(tmp_path)
        _qna(conn, "q-1", origin="manual")
        conn.close()

        html = TestClient(self._app(tmp_path)).get("/status").text
        assert "W4" in html


class TestAgentVerdictIsRecorded:
    """라이브 지표가 잡은 결함 — 에이전트 판정이 검수 기록에 남지 않았다."""

    def test_초안을_올릴_때_함께_기록된다(self, tmp_path) -> None:
        """초안에만 적어 두면 판정이 **물건에는 있고 사건에는 없다.**

        그러면 반려 사유 분포(§5.5.6)와 자동·사람 일치율(§1.3.3)이 영원히 비어
        보인다 — 둘 다 `review` 표를 읽기 때문이다.
        """
        from agentic_service_desk.pipeline import draft_store
        from agentic_service_desk.pipeline.answer import Confidence, Draft, Statement
        from agentic_service_desk.pipeline.review import distribution

        conn = _conn(tmp_path)
        _qna(conn, "q-1")
        draft_store.save(
            conn,
            question="질문",
            draft=Draft(
                statements=(Statement("본문", Confidence.CONFIRMED, ("k-1",)),),
                grounding=("k-1",),
            ),
            verdict=Verdict(passed=False, reason=Reject.P1, checked_by="agent"),
            qna_item_id="q-1",
        )
        assert distribution(conn, reviewed_by="agent").rejected == 1
        conn.close()

    def test_검수기가_없으면_기록도_없다(self, tmp_path) -> None:
        """**검수가 없는 것과 통과한 것은 다르다** (§5.6.1) — 판정이 없으면
        기록도 없어야 분포가 거짓말하지 않는다."""
        from agentic_service_desk.pipeline import draft_store
        from agentic_service_desk.pipeline.answer import Confidence, Draft, Statement
        from agentic_service_desk.pipeline.review import distribution

        conn = _conn(tmp_path)
        _qna(conn, "q-1")
        draft_store.save(
            conn,
            question="질문",
            draft=Draft(
                statements=(Statement("본문", Confidence.CONFIRMED, ("k-1",)),),
                grounding=("k-1",),
            ),
            qna_item_id="q-1",
        )
        assert distribution(conn).total == 0
        conn.close()
