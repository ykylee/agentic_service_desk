"""설정 **값** 배제 — FR-9 의 두 번째 집행 지점 (§2.2.2).

`config_paths` 는 **경로**를 본다. 그것으로 잡히지 않는 길이 하나 남는다.

설정값이 설정 파일에만 있는 것이 아니다. **코드 안의 상수**로 선언되기도 하고,
커밋 메시지가 그 값을 그대로 옮겨 적기도 한다. 그 파일은 설정 파일이 아니라
소스 파일이므로 경로 필터를 그냥 지나간다 — 2026-08-30 실저장소 검증에서
실제로 밟았다. 뽑힌 항목의 본문이 이랬다.

    - `COST_LIMIT_PAPER_DEFAULT_KRW`: `5_000`
    - `COST_LIMIT_LIVE_DEFAULT_KRW`: `3_000`

§2.2.2 가 가른 것이 정확히 이것이다. **지식**은 "한도는 부서 등급으로 결정된다"이고
**현재 상태**는 "지금 이 부서의 한도는 300만 원이다"인데, 위 본문은 후자다.
굳는 순간 stale 이 되고, 그런 항목이 쌓이면 지식베이스가 설정값 미러가 된다.

**프롬프트로 막지 않는 이유는 `config_paths` 와 같다.** 모델은 지시를 대체로
따르지만 대체로는 충분하지 않고, 한 번 샌 값은 굳은 뒤 아무도 틀렸다고 알려주지
않는다.

**무엇을 세는가.** "값이 본문에 나오는가"로는 판정할 수 없다 — `0` 이나 `1` 은
어디에나 나오고, 그것을 걸면 멀쩡한 항목이 통째로 막힌다. 세는 것은 **짝**이다.

    원천이 `NAME = <리터럴>` 로 선언했고,
    제안 본문의 **한 줄에 그 NAME 과 그 리터럴이 함께** 있으면 → 설정값 미러다.

짝이라서 임계값이 필요 없다. 한 번이면 한 번이고, 이름 없이 값만 언급하는 서술은
(“한도를 넘으면 강제로 낮춘다”) 걸리지 않는다. **이름과 값을 같은 줄에 나란히
적는 것이 곧 현재 상태를 옮겨 적는 행위**이기 때문이다.

판정은 **경로와 마찬가지로 모양만 본다** — 언어별 파서를 두지 않는다 (ADR-003).
"""

from __future__ import annotations

import re

#: 상수 선언. **모양만 본다** — 대문자 이름에 리터럴을 대입하는 자리다.
#: `const` · `static final` · `var` 따위의 앞말은 건너뛰고 이름부터 잡는다.
_DECLARATION = re.compile(
    r"""
    (?<![\w.])                       # 이름 중간에서 잘라 잡지 않는다
    (?P<name>[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)   # UPPER_SNAKE — 상수의 관례
    \s*
    (?:
        : \s* [A-Za-z_][\w\[\]., |'"]*? \s* =   # 타입 주석이 붙은 대입
      | [:=]                                     # 그냥 대입, 또는 매핑의 열쇠
    )
    \s*
    (?P<value>
        -?\d[\d_]*(?:\.\d+)?         # 5_000 · 3.14 · -1
      | "[^"\n]{1,80}"               # "문자열"
      | '[^'\n]{1,80}'
      | true | false | True | False
    )
    """,
    re.VERBOSE,
)
"""**타입 주석을 건너뛰는 갈래가 따로 있다.** 2026-08-30 실저장소에서 밟았다 —
실제 선언이 `COST_LIMIT_PAPER_DEFAULT_KRW: int = 5_000` 이었고, `:` 다음에 리터럴을
기대한 첫 판이 `int` 에서 어긋나 **그 값이 그대로 지식이 됐다.** 모양만 보는 판정은
모양을 하나라도 놓치면 조용히 통과시킨다."""


def _forms(value: str) -> set[str]:
    """이 리터럴이 본문에 나타날 수 있는 모양들.

    `5_000` 은 본문에 `5_000` 으로도 `5000` 으로도 실린다. 따옴표는 벗긴다 —
    본문은 대개 백틱을 쓴다.
    """
    raw = value.strip()
    forms = {raw}
    if raw[:1] in {'"', "'"}:
        forms.add(raw[1:-1])
    if "_" in raw:
        forms.add(raw.replace("_", ""))
    return {f for f in forms if f}


def declared_values(text: str) -> dict[str, set[str]]:
    """원천이 선언한 `이름 → 값의 모양들`.

    같은 이름이 여러 번 선언되면 **모두 모은다** — 어느 쪽이 실렸든 짝이다.
    """
    found: dict[str, set[str]] = {}
    for match in _DECLARATION.finditer(text):
        found.setdefault(match.group("name"), set()).update(_forms(match.group("value")))
    return found


def merge_declared(parts: dict[str, set[str]], into: dict[str, set[str]]) -> None:
    """여러 원천의 선언을 한 자리에 모은다."""
    for name, values in parts.items():
        into.setdefault(name, set()).update(values)


def leaked_pairs(body: str, declared: dict[str, set[str]]) -> list[str]:
    """본문이 **같은 줄에** 이름과 값을 나란히 적은 자리 (FR-9 위반).

    돌려주는 것은 `이름=값` 목록이다 — 배제가 조용하면 안 되므로, 무엇이 왜
    걸렸는지 그대로 보고에 실린다.
    """
    if not declared:
        return []
    hits: list[str] = []
    seen: set[str] = set()
    for line in body.splitlines():
        for name, values in declared.items():
            if name not in line:
                continue
            for value in values:
                if value in line and f"{name}={value}" not in seen:
                    seen.add(f"{name}={value}")
                    hits.append(f"{name}={value}")
    return hits
