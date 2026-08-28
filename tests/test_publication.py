"""WBS-4.5.1 — 게재 (XR-5, FR-24, PO-2, NFR-3).

검수를 통과한 답변이 모 시스템으로 나가는 자리다. 여기서 지키는 것은 다섯.

    1. **출구는 하나다** — `publish_answer` 를 부르는 모듈이 하나뿐임을 **세어서** 확인한다 (NFR-3)
    2. **작성 주체와 근거가 본문에 있다** (FR-24, PO-2)
    3. **승인되지 않은 것은 나가지 않는다** — 검수 없는 게재는 없다 (§5.1)
    4. **한 초안은 한 번만 나간다** — 게재는 되돌리기 어렵다 (§5.2)
    5. **봇 목록에 없는 계정으로는 나가지 않는다** — 나가면 자기 답변을 다시 배운다 (§5.3)
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from agentic_service_desk.adapters.mock import BOT_ACCOUNT, MockParentSystem
from agentic_service_desk.adapters.parent_system import ParentSystem
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline import draft_store, publication
from agentic_service_desk.pipeline.answer import Confidence, Draft, Statement

ACCOUNTS = frozenset({BOT_ACCOUNT})


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _qna(conn: sqlite3.Connection, qid: str, parent_question_id: str | None) -> str:
    conn.execute(
        "INSERT INTO qna_item (id, parent_question_id, origin, state, opened_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            qid,
            parent_question_id,
            "parent" if parent_question_id else "manual",
            "접수",
            "2026-08-28T00:00:00Z",
        ),
    )
    conn.commit()
    return qid


def _draft() -> Draft:
    return Draft(
        statements=(
            Statement(
                text="결재 한도는 부서 등급으로 정해집니다.",
                confidence=Confidence.CONFIRMED,
                grounding=("k-1",),
            ),
        ),
        grounding=("k-1",),
        unanswered=("현재 한도 값은 조회 대상이 아닙니다",),
    )


def _approved(conn: sqlite3.Connection, qna_item_id: str | None = "qna-1") -> str:
    draft_id = draft_store.save(
        conn,
        question="제 결재 한도가 왜 300만원인가요?",
        draft=_draft(),
        qna_item_id=qna_item_id,
    )
    draft_store.decide(conn, draft_id, approved=True)
    return draft_id


class TestSingleExit:
    """NFR-3 — 모든 게재가 한 경로를 지난다."""

    def test_단일_출구(self) -> None:
        """`publish_answer` 를 부르는 곳은 게재 관문 하나뿐이다.

        **문서로 적어 둔 규칙이 아니라 세어서 확인하는 사실이다.** 출구가 늘면
        정정에 쓸 게재 id 를 흘리는 문이 생기고, 그것은 조용하다 — 감사 기록에
        구멍이 났다는 사실 자체가 기록되지 않는다 (§9.6).
        """
        gate = Path("src/agentic_service_desk/pipeline/publication.py").resolve()
        callers = set()
        for path in Path("src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"publish_answer", "revise_answer"}
                ):
                    callers.add(path.resolve())
        assert callers == {gate}, f"게재 출구가 하나가 아니다: {sorted(callers)}"

    def test_mock_과_http_가_같은_계약을_만족한다(self) -> None:
        # `bot_account` 를 계약에 더했으므로 양쪽이 함께 따라와야 한다 (ADR-008).
        from agentic_service_desk.adapters.http import HttpParentSystem

        assert isinstance(MockParentSystem(seed=False), ParentSystem)
        assert isinstance(
            HttpParentSystem("http://parent.local", publish_account="svc"),
            ParentSystem,
        )


class TestComposedBody:
    """FR-24 · PO-2 — 게재된 답변에 작성 주체와 근거가 표시된다."""

    def test_작성_주체와_근거가_본문에_있다(self, tmp_path) -> None:
        """**모 시스템의 렌더링에 맡기지 않는다.**

        `grounding` 을 인자로도 넘기지만 PO-2 의 충족을 그쪽에 걸면 화면이 모 시스템
        것이라(§13) 우리가 지킬 수 없는 요구가 된다.
        """
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        parent = MockParentSystem()
        result = publication.publish(
            conn,
            parent,
            _approved(conn),
            bot_accounts=ACCOUNTS,
            titles={"k-1": "결재 한도 결정 규칙"},
        )
        assert isinstance(result, publication.Published)

        published = parent.list_answers("Q-1")[-1]
        assert published.author_account == BOT_ACCOUNT
        assert publication.ATTRIBUTION in published.body
        assert "결재 한도 결정 규칙" in published.body
        # 근거는 본문에도 있고 API 인자로도 간다. 본문 쪽이 PO-2 의 충족이고,
        # 인자 쪽은 모 시스템이 링크로 렌더링할 재료다.
        assert parent.grounding[published.id] == ["k-1"]
        conn.close()

    def test_모른다고_밝힌_경계가_이용자에게_간다(self) -> None:
        # 기본값은 부분 답변 + 경계 명시다 (§5.4.2). 경계가 안 실리면 절반이 사라진다.
        body = publication.compose(
            body="규칙은 이렇습니다.",
            grounding=("k-1",),
            titles={"k-1": "결재 한도 결정 규칙"},
            unanswered=("현재 한도 값은 조회 대상이 아닙니다",),
        )
        assert "현재 한도 값은 조회 대상이 아닙니다" in body

    def test_근거_강도는_싣지_않는다(self) -> None:
        # 검수자가 어디를 볼지 정하는 장치이지(§5.6.5) 이용자에게 줄 정보가 아니다.
        body = publication.compose(body="규칙은 이렇습니다.", grounding=("k-1",))
        for label in (Confidence.CONFIRMED, Confidence.INFERRED, Confidence.THIN):
            assert str(label) not in body

    def test_제목을_못_찾은_근거는_id_로_적힌다(self) -> None:
        # 빼면 근거가 실제보다 적어 보이고, 그 답변이 얇게 쓰인 것처럼 읽힌다.
        body = publication.compose(body="규칙은 이렇습니다.", grounding=("k-사라짐",))
        assert "k-사라짐" in body

    def test_해결_표시를_청한다(self) -> None:
        # 해결 표시율은 지식 성장의 선행 지표다 (§5.3.1.1). 안 눌리면 자격이 안 열린다.
        body = publication.compose(body="규칙은 이렇습니다.", grounding=("k-1",))
        assert "해결 표시" in body


class TestRefusals:
    """나가지 않는 경우. **전부 정상 결과다** — 예외가 아니라 판정이다."""

    def test_승인되지_않은_초안은_나가지_않는다(self, tmp_path) -> None:
        # 검수 없는 게재는 없다 (§5.1). 단계 자체가 필수다.
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        parent = MockParentSystem()
        before = len(parent.list_answers("Q-1"))
        draft_id = draft_store.save(
            conn, question="질문", draft=_draft(), qna_item_id="qna-1"
        )
        result = publication.publish(conn, parent, draft_id, bot_accounts=ACCOUNTS)
        assert isinstance(result, publication.Refused)
        assert result.reason is publication.Refusal.NOT_APPROVED
        assert len(parent.list_answers("Q-1")) == before
        conn.close()

    def test_반려된_초안도_나가지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        from agentic_service_desk.pipeline.review import Reject

        draft_id = draft_store.save(
            conn, question="질문", draft=_draft(), qna_item_id="qna-1"
        )
        draft_store.decide(
            conn, draft_id, approved=False, reason=Reject.P1, detail="근거 밖"
        )
        result = publication.publish(
            conn, MockParentSystem(), draft_id, bot_accounts=ACCOUNTS
        )
        assert isinstance(result, publication.Refused)
        assert result.reason is publication.Refusal.NOT_APPROVED
        conn.close()

    def test_모_시스템을_거치지_않은_질문은_게재처가_없다(self, tmp_path) -> None:
        """메신저 문의를 직접 등록한 건이다 (§1.4.3).

        **실패가 아니라 그 경로의 정상 종점이다** — 올릴 글이 애초에 없다.
        담당자가 메신저로 답하고, 이 시스템은 지식으로만 흡수한다.
        """
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", None)
        result = publication.publish(
            conn, MockParentSystem(), _approved(conn), bot_accounts=ACCOUNTS
        )
        assert isinstance(result, publication.Refused)
        assert result.reason is publication.Refusal.NO_DESTINATION
        conn.close()

    def test_봇_목록에_없는_계정으로는_나가지_않는다(self, tmp_path) -> None:
        """§5.3 이 무너지는 조건을 나가기 전에 막는다.

        목록 밖 계정으로 나간 답변은 다음 주기에 *사람 답변*으로 읽히고, 그러면
        산출물 필터가 통과시켜 **시스템이 자기 답변을 지식으로 다시 배운다**(W2).
        이 대조가 가능한 것은 출구가 하나이기 때문이다.
        """
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        parent = MockParentSystem()
        before = len(parent.list_answers("Q-1"))
        result = publication.publish(
            conn, parent, _approved(conn), bot_accounts=frozenset({"다른-계정"})
        )
        assert isinstance(result, publication.Refused)
        assert result.reason is publication.Refusal.UNIDENTIFIED_AUTHOR
        assert len(parent.list_answers("Q-1")) == before
        conn.close()


class TestExactlyOnce:
    """§5.2 — 게재는 되돌리기 어려운 대외 행위다."""

    def test_한_초안은_한_번만_나간다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        parent = MockParentSystem()
        draft_id = _approved(conn)
        first = publication.publish(conn, parent, draft_id, bot_accounts=ACCOUNTS)
        assert isinstance(first, publication.Published)
        count = len(parent.list_answers("Q-1"))

        second = publication.publish(conn, parent, draft_id, bot_accounts=ACCOUNTS)
        assert isinstance(second, publication.Refused)
        assert second.reason is publication.Refusal.ALREADY_OUT
        assert len(parent.list_answers("Q-1")) == count
        conn.close()

    def test_스키마가_중복_기록을_거부한다(self, tmp_path) -> None:
        """**코드 분기가 아니라 스키마가 지킨다.**

        관문의 사전 확인을 우회해도 두 번째 기록은 만들어지지 않는다.
        """
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        publication.publish(
            conn, MockParentSystem(), _approved(conn), bot_accounts=ACCOUNTS
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO answer_record "
                "(id, qna_item_id, draft_id, body, author_kind, state) "
                "VALUES (?, ?, (SELECT draft_id FROM answer_record LIMIT 1), "
                "?, ?, ?)",
                ("ar-dup", "qna-1", "본문", "bot", publication.IN_FLIGHT),
            )
        conn.close()

    def test_게재_상태가_QnA_항목에_반영된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        publication.publish(
            conn, MockParentSystem(), _approved(conn), bot_accounts=ACCOUNTS
        )
        state = conn.execute(
            "SELECT state FROM qna_item WHERE id = 'qna-1'"
        ).fetchone()["state"]
        assert state == publication.STATE_PUBLISHED_QNA
        conn.close()


class TestInFlight:
    """기록을 먼저 남기고 내보낸다 — 그 사이에서 죽었을 때."""

    class _Exploding:
        """게재 중 죽는 모 시스템. 나갔는지 알 수 없는 상황을 만든다."""

        bot_account = BOT_ACCOUNT

        def publish_answer(self, question_id, body, grounding):  # noqa: ANN001, ANN201
            raise ConnectionError("응답이 오지 않았다")

    def test_나갔는지_모르는_기록이_남는다(self, tmp_path) -> None:
        """**게재됐는데 기록이 없는 상태를 만들지 않는다.**

        반대 순서(내보내고 기록)를 택하면 그 답변은 아무도 정정할 수 없게 되는데
        화면에는 아무 표시도 나지 않는다. 이쪽으로 틀리는 편을 택했다.
        """
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        with pytest.raises(ConnectionError):
            publication.publish(
                conn, self._Exploding(), _approved(conn), bot_accounts=ACCOUNTS
            )
        rows = publication.unsettled(conn)
        assert len(rows) == 1
        assert rows[0].parent_question_id == "Q-1"
        conn.close()

    def test_사람이_확인하기_전에는_다시_보내지_않는다(self, tmp_path) -> None:
        # 추측해서 다시 보내면 같은 답변이 둘 달리고, 그것은 우리가 지울 수 없다.
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        draft_id = _approved(conn)
        with pytest.raises(ConnectionError):
            publication.publish(
                conn, self._Exploding(), draft_id, bot_accounts=ACCOUNTS
            )
        retry = publication.publish(
            conn, MockParentSystem(), draft_id, bot_accounts=ACCOUNTS
        )
        assert isinstance(retry, publication.Refused)
        assert retry.reason is publication.Refusal.IN_FLIGHT
        conn.close()

    def test_나갔음을_확인하면_게재_id_를_붙든다(self, tmp_path) -> None:
        # 정정 경로(XR-7)가 이 id 하나에 걸려 있다.
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        with pytest.raises(ConnectionError):
            publication.publish(
                conn, self._Exploding(), _approved(conn), bot_accounts=ACCOUNTS
            )
        record_id = publication.unsettled(conn)[0].record_id
        assert publication.settle(conn, record_id, parent_answer_id="A-99")

        row = publication.record(conn, record_id)
        assert row["state"] == publication.PUBLISHED
        assert row["parent_answer_id"] == "A-99"
        assert publication.unsettled(conn) == []
        conn.close()

    def test_나가지_않았음을_확인하면_다시_게재할_수_있다(self, tmp_path) -> None:
        """한 번의 통신 실패가 그 답변을 영영 막아서는 안 된다.

        부분 유니크 인덱스가 `abandoned` 를 제외하는 것이 이 경로다.
        """
        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        draft_id = _approved(conn)
        with pytest.raises(ConnectionError):
            publication.publish(
                conn, self._Exploding(), draft_id, bot_accounts=ACCOUNTS
            )
        publication.settle(
            conn, publication.unsettled(conn)[0].record_id, parent_answer_id=None
        )
        retry = publication.publish(
            conn, MockParentSystem(), draft_id, bot_accounts=ACCOUNTS
        )
        assert isinstance(retry, publication.Published)
        conn.close()


class TestApprovalPublishes:
    """FR-24 — 승인은 곧 게재다. 화면에서 끝까지 이어지는가."""

    def _app(self, tmp_path, **over):  # noqa: ANN001, ANN202
        from agentic_service_desk.config import Settings
        from agentic_service_desk.web.app import create_app

        base = dict(
            operations_db=tmp_path / "ops.sqlite3",
            knowledge_dir=tmp_path / "knowledge",
            stage="S2",
            parent_adapter="mock",
            bot_accounts=BOT_ACCOUNT,
        )
        base.update(over)
        return create_app(Settings(_env_file=None, **base))  # type: ignore[arg-type]

    def test_승인하면_모_시스템에_올라간다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        draft_id = draft_store.save(
            conn, question="질문", draft=_draft(), qna_item_id="qna-1"
        )
        conn.close()

        app = self._app(tmp_path)
        client = TestClient(app)
        res = client.post(f"/queues/Q2/{draft_id}/decide", data={"approved": "1"})
        assert res.status_code == 200
        assert "게재했다" in res.text

        conn = _conn(tmp_path)
        row = conn.execute(
            "SELECT state, parent_answer_id FROM answer_record"
        ).fetchone()
        assert row["state"] == publication.PUBLISHED
        assert row["parent_answer_id"]
        conn.close()

    def test_반려하면_나가지_않는다(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        draft_id = draft_store.save(
            conn, question="질문", draft=_draft(), qna_item_id="qna-1"
        )
        conn.close()

        client = TestClient(self._app(tmp_path))
        client.post(
            f"/queues/Q2/{draft_id}/decide", data={"approved": "0", "reason": "P1"}
        )

        conn = _conn(tmp_path)
        assert conn.execute("SELECT count(*) c FROM answer_record").fetchone()["c"] == 0
        conn.close()

    def test_게재하지_못하면_화면이_말해_준다(self, tmp_path) -> None:
        """**조용히 실패하지 않는다.**

        봇 목록이 비어 있으면 게재 계정을 대조할 수 없어 나가지 않는다 — 그 사실이
        누른 사람에게 닿지 않으면 승인만 쌓이고 답변은 나가지 않는다.
        """
        from fastapi.testclient import TestClient

        conn = _conn(tmp_path)
        _qna(conn, "qna-1", "Q-1")
        draft_id = draft_store.save(
            conn, question="질문", draft=_draft(), qna_item_id="qna-1"
        )
        conn.close()

        client = TestClient(self._app(tmp_path, bot_accounts=""))
        res = client.post(f"/queues/Q2/{draft_id}/decide", data={"approved": "1"})
        assert "게재하지 않았다" in res.text
        assert str(publication.Refusal.UNIDENTIFIED_AUTHOR) in res.text
