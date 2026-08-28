"""모델 출력 정리.

**사고형 모델은 `<think>...</think>` 블록을 본문에 섞어 보낸다.** 그대로 두면 그
사고 과정이 지식 항목이나 게재 답변에 실려 나간다.

제공자가 옵션으로 분리해 주기도 하지만(MiniMax 의 `reasoning_split`) **그 옵션이
통하지 않는 경로가 있다.** 2026-08-28 확인 — pi 하네스는 그 파라미터를 보내지 않고,
pi 의 `--thinking off` 나 모델 설정의 `reasoning: false` 로도 꺼지지 않는다.

지식 구축이 pi 로 돌아가므로(D5) **이 경로가 곧 지식베이스로 들어오는 길**이다.
그래서 정리는 **출력을 받는 모든 곳**에서 한다 — 옵션에 기대지 않는다.
"""

from __future__ import annotations

import re

_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def strip_thinking(content: str) -> str:
    """사고 블록을 걷어낸다.

    닫히지 않은 블록은 건드리지 않는다 — 잘린 출력을 더 망가뜨리지 않기 위해서다.
    그런 출력은 애초에 쓸 수 없으므로 상위에서 실패로 다뤄야 한다.
    """
    return _THINK_BLOCK.sub("", content).strip()
