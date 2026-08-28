"""개발·테스트용 모 시스템 (ADR-008).

**빈 값을 돌려주는 스텁이 아니다.** 시나리오를 담아 §5.3 되먹임 차단의 모든 분기를
실제로 시험할 수 있게 한다.

    1. 답변 없는 질문        — 파이프라인이 답해야 할 것
    2. 봇 답변 + 명시적 해결 — ingest 자격 **있음**
    3. 봇 답변 + 미해결      — ingest 자격 **없음**
    4. 사람 답변             — 언제나 자격 있음
    5. 후속이 달린 질문      — 파이프라인 재실행 대상

프로덕션에서 이것이 도는 사고를 막기 위해 **설정에서 명시적으로 고를 때만** 쓰인다.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

from agentic_service_desk.adapters.contract import (
    Answer,
    Followup,
    Question,
    Resolution,
    ResolutionMethod,
)

BOT_ACCOUNT = "svc-agentic-desk"
"""이 시스템이 게재에 쓰는 계정. 되먹임 차단의 식별 기준이다."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MockParentSystem:
    """인메모리 모 시스템. 계약(Protocol)을 만족한다."""

    def __init__(self, *, seed: bool = True) -> None:
        self._questions: dict[str, Question] = {}
        self._answers: dict[str, list[Answer]] = {}
        self._followups: dict[str, list[Followup]] = {}
        self._resolutions: dict[str, Resolution] = {}
        self._documents: dict[str, str] = {}
        self._publications: list[tuple[str, str]] = []
        self._ids = itertools.count(1)
        if seed:
            self._seed()

    # --- 시나리오 -------------------------------------------------------

    def _seed(self) -> None:
        # 1. 답변 없는 질문 — 파이프라인이 답해야 한다
        self._add_question("Q-1", "결재가 왜 반려되나요?", "emp-100")

        # 2. 봇 답변 + 명시적 해결 — ingest 자격 있음 (D8)
        self._add_question("Q-2", "승인 한도는 어떻게 정해지나요?", "emp-101")
        self._add_answer("Q-2", "부서 등급에 따라 정해집니다.", BOT_ACCOUNT)
        self._resolutions["Q-2"] = Resolution(
            question_id="Q-2",
            resolved=True,
            method=ResolutionMethod.USER_MARKED,
            resolved_by="emp-101",
            resolved_at=_now(),
        )

        # 3. 봇 답변 + 미해결 — 자격 없음. 이 건이 필터의 핵심 시험이다
        self._add_question("Q-3", "알림이 안 오는데요?", "emp-102")
        self._add_answer("Q-3", "설정을 확인해 보세요.", BOT_ACCOUNT)

        # 4. 사람 답변 — 언제나 자격 있음
        self._add_question("Q-4", "휴가 신청이 사라졌어요.", "emp-103")
        self._add_answer("Q-4", "임시저장 목록에 있습니다.", "emp-999")

        # 5. 후속이 달린 질문 — 파이프라인 재실행 대상 (D9)
        self._add_question("Q-5", "권한 요청은 어디서 하나요?", "emp-104")
        self._add_answer("Q-5", "설정 > 권한에서 신청합니다.", BOT_ACCOUNT)
        self._followups.setdefault("Q-5", []).append(
            Followup(
                id="F-1",
                question_id="Q-5",
                body="그 메뉴가 안 보이는데요?",
                author_account="emp-104",
                created_at=_now(),
            )
        )

    def _add_question(self, qid: str, body: str, asker: str) -> None:
        self._questions[qid] = Question(
            id=qid, body=body, asker_account=asker, created_at=_now()
        )

    def _add_answer(self, qid: str, body: str, author: str) -> None:
        self._answers.setdefault(qid, []).append(
            Answer(
                id=f"A-{next(self._ids)}",
                question_id=qid,
                body=body,
                author_account=author,
                created_at=_now(),
            )
        )

    # --- 읽기 (XR-1~4) ---------------------------------------------------

    def list_questions(self, since: str | None = None) -> list[Question]:
        items = list(self._questions.values())
        if since:
            items = [q for q in items if q.created_at > since]
        return items

    def list_answers(self, question_id: str) -> list[Answer]:
        return list(self._answers.get(question_id, []))

    def list_followups(self, question_id: str) -> list[Followup]:
        return list(self._followups.get(question_id, []))

    def get_resolution(self, question_id: str) -> Resolution:
        return self._resolutions.get(
            question_id, Resolution(question_id=question_id, resolved=False)
        )

    # --- 쓰기 (XR-5·7·6) — 허용된 것은 셋뿐이다 (CO-2) ---------------------

    def publish_answer(self, question_id: str, body: str, grounding: list[str]) -> str:
        if question_id not in self._questions:
            raise KeyError(f"없는 질문이다: {question_id}")
        self._add_answer(question_id, body, BOT_ACCOUNT)
        return self._answers[question_id][-1].id

    def revise_answer(self, answer_id: str, body: str, reason: str) -> None:
        """정정. **조용히 고치지 않는다** — 사유를 본문에 남긴다(PO-1)."""
        for qid, answers in self._answers.items():
            for i, a in enumerate(answers):
                if a.id == answer_id:
                    answers[i] = Answer(
                        id=a.id,
                        question_id=qid,
                        body=f"{body}\n\n---\n*정정: {reason}*",
                        author_account=a.author_account,
                        created_at=a.created_at,
                        revised_at=_now(),
                    )
                    return
        raise KeyError(f"없는 답변이다: {answer_id}")

    def upsert_document(self, path: str, title: str, body: str) -> str:
        """문서 면 — 살아있는 문서. **멱등하다** (D46)."""
        self._documents[path] = f"# {title}\n\n{body}"
        return path

    def create_publication(self, title: str, body: str) -> str:
        """발행 면 — 발행물. 회차가 누적된다. 멱등하지 않다 (D46)."""
        self._publications.append((title, body))
        return f"P-{len(self._publications)}"

    # --- 시험 편의 -------------------------------------------------------

    @property
    def documents(self) -> dict[str, str]:
        return dict(self._documents)

    @property
    def publications(self) -> list[tuple[str, str]]:
        return list(self._publications)
