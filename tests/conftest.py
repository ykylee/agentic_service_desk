"""시험 전반이 함께 쓰는 것.

실제 LLM 을 부르는 시험은 두지 않는다 — 느리고, 비결정적이고, NFR-1 이 걸린 경로다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Result:
    text: str
    raw: str


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
