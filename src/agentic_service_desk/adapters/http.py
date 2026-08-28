"""실제 모 시스템 연동 자리 (ADR-008).

**아직 비어 있다.** 모 시스템에 API 가 생기면 여기를 채운다 — 계약(`contract.py`)과
프로토콜(`parent_system.py`)은 이미 정해져 있으므로, 이 클래스가 프로토콜을
만족하기만 하면 mock 과 갈아 끼울 수 있다.

연결 단계에서 조정될 것들 (ADR-008 귀결)
    - 인증 방식
    - 페이지네이션 — `list_questions(since)` 가 한 번에 다 주지 못할 수 있다
    - 응답 형태 — 실제 게시판 스키마가 계약을 조금 밀어낼 수 있다
"""

from __future__ import annotations

import httpx

from agentic_service_desk.adapters.contract import Answer, Followup, Question, Resolution
from agentic_service_desk.adapters.parent_system import NotConfigured


class HttpParentSystem:
    """모 시스템 내부 API 클라이언트."""

    def __init__(
        self, base_url: str, *, publish_account: str = "", timeout: float = 30.0
    ) -> None:
        if not base_url:
            raise NotConfigured(
                "모 시스템 API 주소가 없다. 설정 `ASD_PARENT_API_BASE_URL` 을 채우거나 "
                "개발 중이면 `ASD_PARENT_ADAPTER=mock` 을 쓴다."
            )
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._publish_account = publish_account

    @property
    def bot_account(self) -> str:
        """게재에 쓰는 계정 (`ASD_PUBLISH_ACCOUNT`).

        **비어 있으면 빈 문자열을 돌려준다 — 여기서 막지 않는다.** 읽기 경로도 이
        클래스를 쓰므로 생성 시점에 거부하면 게재와 무관한 수집까지 멈춘다.
        거부는 게재 관문 한 곳에서 한다 (NFR-3).
        """
        return self._publish_account

    def list_questions(self, since: str | None = None) -> list[Question]:
        raise NotImplementedError("모 시스템에 API 가 생기면 채운다 (ADR-008)")

    def list_answers(self, question_id: str) -> list[Answer]:
        raise NotImplementedError("모 시스템에 API 가 생기면 채운다 (ADR-008)")

    def list_followups(self, question_id: str) -> list[Followup]:
        raise NotImplementedError("모 시스템에 API 가 생기면 채운다 (ADR-008)")

    def get_resolution(self, question_id: str) -> Resolution:
        raise NotImplementedError("모 시스템에 API 가 생기면 채운다 (ADR-008)")

    def publish_answer(self, question_id: str, body: str, grounding: list[str]) -> str:
        raise NotImplementedError("모 시스템에 API 가 생기면 채운다 (ADR-008)")

    def revise_answer(self, answer_id: str, body: str, reason: str) -> None:
        raise NotImplementedError("모 시스템에 API 가 생기면 채운다 (ADR-008)")

    def upsert_document(self, path: str, title: str, body: str) -> str:
        raise NotImplementedError("모 시스템에 API 가 생기면 채운다 (ADR-008)")

    def create_publication(self, title: str, body: str) -> str:
        raise NotImplementedError("모 시스템에 API 가 생기면 채운다 (ADR-008)")
