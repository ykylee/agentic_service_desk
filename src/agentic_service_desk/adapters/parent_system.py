"""모 시스템 내부 API — 일곱 표면 (XR-1~7, D34).

**이 목록은 연동 명세가 아니라 이 시스템이 무엇을 알 수 있는지의 상한이다.**
표면을 좁히면 기능이 소리 없이 죽는다 — 파이프라인은 계속 돌고 지식도 계속 자라지만
오답을 섞은 채로.

대안 없는 항목은 `list_answers` 의 **작성자 계정** 하나다. 그것이 없으면 봇/사람을
가릴 수 없어 되먹임 차단(§5.3)이 통째로 무너진다. 2026-08-28 확보를 확인했다.
"""

from __future__ import annotations

from typing import Any, Protocol


class ParentSystem(Protocol):
    """모 시스템이 제공해야 하는 일곱 표면.

    구현체는 이 프로토콜을 만족하기만 하면 된다 — HTTP 든 테스트용 가짜든.
    """

    # --- 읽기 -------------------------------------------------------------

    def list_questions(self, since: str | None = None) -> list[dict[str, Any]]:
        """XR-1 — 질문 목록·상세. 파이프라인 1단계의 입력이다."""
        ...

    def list_answers(self, question_id: str) -> list[dict[str, Any]]:
        """XR-2 — 답변 목록. **작성자 계정을 반드시 포함한다.**

        §5.3 되먹임 차단이 이 필드 하나에 의존한다. 봇이 쓴 답변을 봇이 다시 배우지
        않게 하는 판정이 여기서 나온다.
        """
        ...

    def list_followups(self, question_id: str) -> list[dict[str, Any]]:
        """XR-3 — 후속 답글. §6 지속 추적과 파이프라인 재실행의 트리거다."""
        ...

    def get_resolution(self, question_id: str) -> dict[str, Any]:
        """XR-4 — 해결 표시 상태. **명시적 해결 판정**의 근거다 (D35).

        ingest 자격(§5.3.1)과 승격(§6.8)이 모두 여기서 갈린다.
        """
        ...

    # --- 쓰기 — 허용된 것은 셋뿐이다 (CO-2) --------------------------------

    def publish_answer(self, question_id: str, body: str, grounding: list[str]) -> str:
        """XR-5 — 답변 게재. 봇 계정으로 올리며 AI 작성임을 명시한다 (PO-2)."""
        ...

    def revise_answer(self, answer_id: str, body: str, reason: str) -> None:
        """XR-7 — 답변 수정. **정정 경로다** (PO-1).

        조용히 고치지 않는다 — 정정했다는 사실과 무엇이 왜 바뀌었는지를 본문에 남긴다.
        이미 읽은 사람이 변경을 알 수 있어야 하기 때문이다.
        """
        ...

    def publish_content(self, kind: str, path: str, body: str) -> str:
        """XR-6 — 콘텐츠 게재.

        타입이 넷이어도 **자리는 둘이고 연산도 둘이다** (D46) —
        살아있는 문서는 문서 면에 upsert, 발행물은 발행 면에 create.
        """
        ...


class NotConfigured(RuntimeError):
    """모 시스템 연동이 설정되지 않았다.

    기본값을 비워 두고 **동작을 거부**하는 쪽을 택했다. 설정을 잊었을 때 조용히
    빈 결과를 돌려주면 "질문이 없다"와 구분되지 않기 때문이다.
    """
