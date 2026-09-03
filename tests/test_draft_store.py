"""초안 보관 — 정제가 저장을 왕복하는가 (FR-61).

검수 대기열은 초안이 **DB 를 한 번 왕복하는 자리**다. 파이프라인이 아무리 잘
다듬어도 여기서 떨어지면 나가는 글은 원본이 된다.
"""

from __future__ import annotations

from agentic_service_desk.pipeline import draft_store
from agentic_service_desk.pipeline.answer import Confidence, Draft, Statement
from agentic_service_desk.operations.schema import connect, initialize


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c




class TestRenderedSurvivesTheStore:
    """정제가 저장을 왕복해야 게재에 닿는다 (FR-61).

    **여기서 떨어지면 정제가 화면에만 있고 나가는 글은 원본이다** — 질문한 사람에게
    내부 경로가 그대로 간다. 2026-09-03 에 파이프라인에만 넣고 저장을 잊어 그 상태로
    한 번 커밋됐다.
    """

    def test_정제된_글이_되살아난다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = Draft(
            statements=(Statement(text="내부 말로 쓴 진술", confidence=Confidence.CONFIRMED),),
            grounding=("k-1",),
            rendered="질문자가 읽을 말로 쓴 글입니다.",
        )
        draft_store.save(conn, question="어떻게 하나요", draft=draft)

        stored = draft_store.pending(conn)[0]
        assert stored.rendered == "질문자가 읽을 말로 쓴 글입니다."
        # **나가는 글은 정제된 쪽이다** — `Draft.body` 와 같은 규칙이어야 한다.
        assert stored.body == "질문자가 읽을 말로 쓴 글입니다."

    def test_정제되지_않았으면_원본이_나간다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = Draft(
            statements=(Statement(text="원본 진술", confidence=Confidence.CONFIRMED),),
            grounding=("k-1",),
        )
        draft_store.save(conn, question="어떻게 하나요", draft=draft)

        stored = draft_store.pending(conn)[0]
        assert stored.rendered == ""
        assert stored.body == "원본 진술"
