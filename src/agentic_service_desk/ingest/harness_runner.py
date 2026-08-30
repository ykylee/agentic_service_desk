"""pi 헤드리스 실행 (D5, ADR-009).

지식 구축은 **에이전트가 한다** — pi 하네스 위에서 돈다. pi 를 부르는 방식은
`-p`(출력 후 종료)이며, 제공자 설정은 `asd sync-harness` 가 생성한 것을 쓴다.

**출력은 반드시 정리해서 넘긴다.** pi 는 MiniMax 의 `reasoning_split` 을 보내지 않아
사고 블록이 본문에 그대로 온다(2026-08-28 확인) — 이 경로가 곧 지식베이스로 들어오는
길이므로, 여기서 걷어내지 않으면 사고 과정이 지식 항목이 된다.

**pi 를 에이전트가 아니라 생성기로 부른다** (2026-08-30 실운영에서 밟았다). pi 는
read·bash·edit 도구를 들고 `AGENTS.md`/`CLAUDE.md` 를 찾아 읽는 코딩 에이전트다.
아무것도 끄지 않고 부르면 **프롬프트로 준 원천 대신 작업 디렉터리를 뒤진다** —
실제로 이런 응답이 왔다.

    "...the recent commits a666f41, 56cff5f, b469db8 (all visible in `git log`)
     plus the knowledge module code (lint.py, config_values.py, policy.py, ...)"

**그것은 모 시스템이 아니라 이 앱 자신의 저장소다.** 지식 구축 에이전트가 우리를
읽고 있었다는 뜻이고, 설계가 QnA 쪽에서 막으려던 되먹임(§5.3, W2)이 소스 쪽으로
난 것이다. 게다가 도구를 쓰느라 턴을 소진해 **빈 응답과 잘린 JSON** 이 왔다 —
그 실행의 ingest 는 전부 실패했다.

그래서 **끌 수 있는 것은 다 끄고 빈 디렉터리에서 부른다.** 이 경로에 필요한 것은
문장 하나를 받아 문장 하나를 내는 것뿐이다.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
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


#: pi 를 **생성기로** 부르는 인자. 이 경로에 필요한 것은 문장 하나를 받아 문장
#: 하나를 내는 것뿐이고, 그 밖의 능력은 전부 위험이다.
#:
#: 각 줄이 막는 것이 다르다.
#:
#: - `--no-tools`          원천은 프롬프트로 준다. **뒤질 것이 있으면 뒤진다**
#: - `--no-context-files`  `AGENTS.md`/`CLAUDE.md` 를 찾아 읽지 않게
#: - `--no-extensions`     로컬에 무엇이 깔려 있느냐로 지식이 달라지지 않게
#: - `--no-skills`         〃
#: - `--no-prompt-templates` 〃
#: - `--no-session`        호출 사이에 상태가 남지 않게 — 묶음은 서로 독립이다
#:
#: **셋(확장·스킬·템플릿)을 함께 끄는 이유**는 재현성이다. 켜 두면 같은 원천이
#: 기계마다 다른 지식이 되고, 그 차이는 지식베이스에 남은 뒤에야 드러난다.
NON_AGENTIC = (
    "-p",
    "--no-tools",
    "--no-context-files",
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-session",
)


class PiHarness:
    """pi 를 헤드리스로 부른다. **에이전트가 아니라 생성기로 쓴다.**"""

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

        `cwd` 를 주지 않으면 **빈 임시 디렉터리**에서 돈다. 기본값을 "호출자의
        디렉터리"로 두면 그것이 곧 이 앱의 저장소이고, 거기서 pi 는 볼 것이 아주
        많다 — 그 실수는 조용하지 않고 **틀린 지식**으로 나타난다.
        """
        env = {**os.environ, "ASD_LLM_API_KEY": self._api_key}
        with tempfile.TemporaryDirectory(prefix="asd-ingest-") as empty:
            return self._invoke(prompt, cwd or empty, env)

    def _invoke(self, prompt: str, cwd: str, env: dict[str, str]) -> HarnessResult:
        try:
            proc = subprocess.run(  # noqa: S603
                [self._exe, *NON_AGENTIC, "--provider", self._provider,
                 "--model", self._model, prompt],
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
