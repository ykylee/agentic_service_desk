"""시험 전반이 함께 쓰는 것.

실제 LLM 을 부르는 시험은 두지 않는다 — 느리고, 비결정적이고, NFR-1 이 걸린 경로다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest
from agentic_service_desk.ingest.agent import DEFAULT_ATTEMPTS


@pytest.fixture(autouse=True)
def _no_ambient_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """환경의 `ASD_*` 를 걷어낸다.

    `Settings(_env_file=None)` 은 `.env` 만 막고 **실제 환경변수는 그대로 읽는다.**
    그래서 `ASD_KNOWLEDGE_DIR` 을 내보낸 셸에서 돌리면 기본값을 쓰는 시험이 남의
    디렉터리를 보고 엉뚱하게 실패한다 — 실제로 한 번 밟았고, 원인을 찾는 데
    시간이 걸렸다. 시험은 환경이 아니라 자기가 준 값으로만 돌아야 한다.
    """
    for name in [k for k in os.environ if k.startswith("ASD_")]:
        monkeypatch.delenv(name, raising=False)


@dataclass
class _Result:
    text: str
    raw: str


def failing(text: str = "JSON 이 아니다") -> tuple[str, ...]:
    """**한 호출이 끝내 실패한다**를 나타내는 응답 묶음.

    `IngestAgent` 는 형식을 어긴 출력을 다시 부르므로(`DEFAULT_ATTEMPTS`),
    실패를 응답 하나로 쓰면 두 번째 시도가 `FakeHarness` 의 기본 응답을 받아
    **성공해 버린다.** 횟수를 상수에서 끌어와 재시도 횟수가 바뀌어도 시험의
    뜻이 그대로이게 한다.
    """
    return (text,) * DEFAULT_ATTEMPTS


class FakeHarness:
    """정해진 응답을 차례로 돌려주는 하네스.

    `PiHarness` 와 같은 모양(`run(prompt) -> .text`)이라 갈아 끼울 수 있다.
    응답이 떨어지면 빈 결과를 돌려준다 — 호출 횟수를 미리 세지 않아도 되게.
    """

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def run(self, prompt: str, *, cwd: str | None = None) -> _Result:
        self.prompts.append(prompt)
        text = self._responses.pop(0) if self._responses else '{"items": []}'
        return _Result(text=text, raw=text)
