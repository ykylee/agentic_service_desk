"""pi 헤드리스 실행 (D5, ADR-009).

지식 구축은 **에이전트가 한다** — pi 하네스 위에서 돈다. pi 를 부르는 방식은
`-p`(출력 후 종료)이며, 제공자 설정은 `asd sync-harness` 가 생성한 것을 쓴다.

**출력은 반드시 정리해서 넘긴다.** pi 는 MiniMax 의 `reasoning_split` 을 보내지 않아
사고 블록이 본문에 그대로 온다(2026-08-28 확인) — 이 경로가 곧 지식베이스로 들어오는
길이므로, 여기서 걷어내지 않으면 사고 과정이 지식 항목이 된다.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from agentic_service_desk.llm.harness import PROVIDER_NAME
from agentic_service_desk.llm.text import strip_thinking


class HarnessError(RuntimeError):
    """pi 실행이 실패했다."""


@dataclass(frozen=True)
class HarnessResult:
    """한 번의 실행 결과."""

    text: str
    """정리된 본문. 사고 블록은 걷어냈다."""

    raw: str
    """원본. 무엇이 걷혔는지 추적해야 할 때를 위해 남긴다."""

    @property
    def had_thinking(self) -> bool:
        return self.text != self.raw.strip()


class PiHarness:
    """pi 를 헤드리스로 부른다."""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        provider: str = PROVIDER_NAME,
        executable: str = "pi",
        timeout: float = 300.0,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._provider = provider
        self._exe = executable
        self._timeout = timeout

    def run(self, prompt: str, *, cwd: str | None = None) -> HarnessResult:
        """프롬프트 하나를 돌리고 본문을 받는다.

        키는 **환경변수로만** 넘긴다 — 명령줄 인자로 주면 프로세스 목록에 노출된다.
        `models.json` 이 `$ASD_LLM_API_KEY` 를 참조하므로 이름이 맞아야 한다(ADR-009).
        """
        env = {**os.environ, "ASD_LLM_API_KEY": self._api_key}
        try:
            proc = subprocess.run(  # noqa: S603
                [self._exe, "-p", "--provider", self._provider, "--model", self._model, prompt],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=env,
                cwd=cwd,
                check=False,
            )
        except FileNotFoundError as exc:
            raise HarnessError(f"pi 를 찾을 수 없다: {self._exe}") from exc
        except subprocess.TimeoutExpired as exc:
            raise HarnessError(f"pi 가 {self._timeout}초 안에 끝나지 않았다") from exc

        if proc.returncode != 0:
            raise HarnessError(f"pi 실패 (code={proc.returncode}): {proc.stderr.strip()[:400]}")

        raw = proc.stdout
        return HarnessResult(text=strip_thinking(raw), raw=raw)
