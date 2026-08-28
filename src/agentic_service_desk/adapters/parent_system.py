"""모 시스템 내부 API — 일곱 표면 (XR-1~7, D34).

**이 목록은 연동 명세가 아니라 이 시스템이 무엇을 알 수 있는지의 상한이다.**
표면을 좁히면 기능이 소리 없이 죽는다 — 파이프라인은 계속 돌고 지식도 계속 자라지만
오답을 섞은 채로.

대안 없는 항목은 `list_answers` 의 **작성자 계정** 하나다. 그것이 없으면 봇/사람을
가릴 수 없어 되먹임 차단(§5.3)이 통째로 무너진다. 2026-08-28 확보를 확인했다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentic_service_desk.adapters.contract import Answer, Followup, Question, Resolution


@runtime_checkable
class ParentSystem(Protocol):
    """모 시스템이 제공해야 하는 일곱 표면.

    구현체는 이 프로토콜을 만족하기만 하면 된다 — HTTP 든 테스트용 가짜든.

    `runtime_checkable` 을 붙인 이유는 **테스트가 계약 준수를 실제로 확인**하게
    하기 위해서다 (ADR-008). 문서로만 쓴 스키마는 어긋나지만, mock 과 HTTP 구현이
    같은 프로토콜을 만족하는 것을 시험이 지키면 갈아 끼울 때 터지지 않는다.
    """

    # --- 읽기 -------------------------------------------------------------

    def list_questions(self, since: str | None = None) -> list[Question]:
        """XR-1 — 질문 목록·상세. 파이프라인 1단계의 입력이다."""
        ...

    def list_answers(self, question_id: str) -> list[Answer]:
        """XR-2 — 답변 목록. **작성자 계정을 반드시 포함한다.**

        §5.3 되먹임 차단이 이 필드 하나에 의존한다. 봇이 쓴 답변을 봇이 다시 배우지
        않게 하는 판정이 여기서 나온다.
        """
        ...

    def list_followups(self, question_id: str) -> list[Followup]:
        """XR-3 — 후속 답글. §6 지속 추적과 파이프라인 재실행의 트리거다."""
        ...

    def get_resolution(self, question_id: str) -> Resolution:
        """XR-4 — 해결 표시 상태. **명시적 해결 판정**의 근거다 (D35).

        ingest 자격(§5.3.1)과 승격(§6.8)이 모두 여기서 갈린다.
        """
        ...

    # --- 쓰기 — 허용된 것은 셋뿐이다 (CO-2) --------------------------------

    @property
    def bot_account(self) -> str:
        """**이 어댑터가 게재에 쓰는 계정.** 표면이 아니라 어댑터가 밝히는 사실이다.

        계약에 둔 이유는 되먹임 차단이 여기 걸려 있기 때문이다. 우리가 어느 계정으로
        올리는지 **모르면**, 방금 올린 답변을 다음 주기에 사람 답변으로 읽는다 —
        `ASD_BOT_ACCOUNTS` 에 없는 계정으로 나가는 순간 §5.3 이 조용히 무너진다.
        게재 관문이 나가기 전에 이 값을 대조한다(NFR-3의 자리가 하나인 덕에 가능하다).

        읽기 쪽 목록(`bot_accounts`)과 다른 것이다. **읽을 때 거를 계정은 여럿**이고
        (계정을 갈아 끼워도 옛 산출물은 계속 걸러야 한다) **쓸 때 쓰는 계정은 하나**다.
        """
        ...

    def publish_answer(self, question_id: str, body: str, grounding: list[str]) -> str:
        """XR-5 — 답변 게재. 봇 계정으로 올리며 AI 작성임을 명시한다 (PO-2).

        `body` 는 **조립이 끝난 본문**이다 — 귀속 표시와 근거 목록이 이미 들어 있다.
        `grounding` 을 따로 받는 것은 모 시스템이 링크로 렌더링할 수 있게 하기
        위해서일 뿐, **PO-2 의 충족을 그 렌더링에 맡기지 않는다**: 화면은 모 시스템
        것이라(§13) 우리가 지킬 수 없는 요구가 되기 때문이다.
        """
        ...

    def revise_answer(self, answer_id: str, body: str, reason: str) -> None:
        """XR-7 — 답변 수정. **정정 경로다** (PO-1).

        조용히 고치지 않는다 — 정정했다는 사실과 무엇이 왜 바뀌었는지를 본문에 남긴다.
        이미 읽은 사람이 변경을 알 수 있어야 하기 때문이다.
        """
        ...

    def upsert_document(self, path: str, title: str, body: str) -> str:
        """XR-6 (문서 면) — 살아있는 문서를 만들거나 갱신한다. **멱등하다.**

        FAQ · 사용 가이드가 여기로 간다. 갱신이므로 stale 을 흡수할 수 있다(§7.3).
        """
        ...

    def create_publication(self, title: str, body: str) -> str:
        """XR-6 (발행 면) — 발행물의 새 회차를 만든다. **멱등하지 않다.**

        칼럼 · 뉴스레터가 여기로 간다. 회차는 되돌릴 수 없으므로 검수가 더 엄격하다
        (§5.5.5).

        타입이 넷이어도 **자리는 둘이고 연산도 둘**이다 (D46). 하나의 메서드에
        `kind` 를 넘기면 그 둘이 코드에서 흐려지므로 나눴다 (ADR-008).
        """
        ...


class NotConfigured(RuntimeError):
    """모 시스템 연동이 설정되지 않았다.

    기본값을 비워 두고 **동작을 거부**하는 쪽을 택했다. 설정을 잊었을 때 조용히
    빈 결과를 돌려주면 "질문이 없다"와 구분되지 않기 때문이다.
    """
