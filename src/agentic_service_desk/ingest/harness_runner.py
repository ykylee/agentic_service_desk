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

    err: str = ""
    """pi 의 stderr.

    **`rc=0` 인데 본문이 비어 오는 일이 있다.** 그때 이유가 적히는 자리가
    여기뿐인데, 예전에는 `returncode != 0` 일 때만 읽고 버렸다 — 빈 응답을
    로그에서 보고도 레이트리밋인지 다른 것인지 알 방법이 없었다.
    """

    @property
    def had_thinking(self) -> bool:
        return self.text != self.raw.strip()

    @property
    def all_thinking(self) -> bool:
        """**사고만 하다 끝났는가.**

        모델이 출력 예산을 전부 사고에 쓰고 잘리면(`stop=length`) 걷어낸 뒤
        본문이 빈 문자열이 된다. 그때 `raw` 는 수만 자로 가득 차 있으므로,
        "아무 말도 안 했다"와 "사고만 하다 잘렸다"를 **여기서 가른다.**

        둘을 뭉뚱그리면 로그에 `받은 것: ` 뒤가 빈 줄만 쌓이고, 원인을 찾는
        길이 막힌다 — 2026-08~09 에 실제로 그렇게 막혔다.
        """
        return not self.text.strip() and bool(self.raw.strip())


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


DEFAULT_TIMEOUT = 600.0
"""한 번의 pi 호출을 기다리는 한도(초).

**2026-08-31 실측** (stdin 을 고친 뒤, auto-trading 미러의 실제 파일로):
24,000자 묶음 하나가 **56초**, 12,000자가 48초, 6,000자가 52초. 셋 다 완결된
JSON 이 왔다. 소요는 **입력 크기가 아니라 생성량이 지배한다** — 묶음을 줄여도
호출당 시간은 그대로고 호출 수만 는다.

그러니 300초도 정상 호출에는 넉넉했다. 그런데도 올리는 이유는 한 가지다:
한도를 넘긴 파일 하나가 통째로 한 묶음이 되는 경우(`MAX_CHARS_PER_CALL`
주석)가 있고, 그때의 생성량은 위 실측 범위 밖이다. 여유는 성공한 호출에
아무 값도 물리지 않는다 — 한도는 **실패했을 때만** 시간을 쓴다.

중단 신호는 이 한도와 무관하다: SIGTERM 은 pi 를 함께 죽여 `code=143` 으로
즉시 돌아온다.
"""


class PiHarness:
    """pi 를 헤드리스로 부른다. **에이전트가 아니라 생성기로 쓴다.**"""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        provider: str = PROVIDER_NAME,
        executable: str = "pi",
        timeout: float = DEFAULT_TIMEOUT,
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
                # **표준입력을 닫는다.** 물려주면 pi 가 거기서 더 올 것을
                # 기다린다 — 프롬프트는 이미 인자로 다 줬는데도. 워커의 stdin 이
                # 열린 채 비어 있는 파이프면(백그라운드 기동에서 흔하다) 호출이
                # **영영 돌아오지 않고**, 한도가 다 찰 때까지 그 묶음이 멈춘다.
                # 2026-08-31 실측: 물려주면 90초 한도를 그대로 태웠고 닫으면
                # 같은 프롬프트가 1.2초에 왔다. EOF 인 stdin 은 문제가 없어
                # 앞선 실행에서 드러나지 않았다.
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise HarnessError(f"pi 를 찾을 수 없다: {self._exe}") from exc
        except subprocess.TimeoutExpired as exc:
            raise HarnessError(f"pi 가 {self._timeout}초 안에 끝나지 않았다") from exc

        if proc.returncode != 0:
            raise HarnessError(f"pi 실패 (code={proc.returncode}): {proc.stderr.strip()[:400]}")

        raw = proc.stdout
        return HarnessResult(text=strip_thinking(raw), raw=raw, err=proc.stderr.strip())
