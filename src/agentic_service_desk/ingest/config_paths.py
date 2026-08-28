"""설정 파일 배제 — FR-9 의 집행 지점 (§2.2.2, §2.3 B2).

**설정값은 지식이 아니라 현재 상태다.** "승인 한도는 부서 등급으로 결정된다"는
지식이고 "지금 이 부서의 한도는 300만 원"은 상태다. 후자를 굳히면 **굳는 순간
stale 이 되고**, 지식베이스가 설정값 미러가 되면서 stale 대기열이 폭주한다.

프롬프트로만 막지 않는 이유가 있다. 모델은 지시를 대체로 따르지만 대체로는 충분하지
않다 — 한 번 새면 그 값이 지식으로 굳고, 굳은 것은 아무도 틀렸다고 알려주지 않는다.
**경로 단위로 기계적으로 잘라내는 것**이 확정 가능한 집행이다.

경계는 **넉넉하게 잡았다.** 설정 파일이 아닌 것을 몇 개 놓치는 대가는 그 개념을
다른 파일에서 다시 만나는 것뿐이고, 반대 방향의 대가는 지식베이스 오염이다.
언어별 파서를 쓰지 않는다는 제약(ADR-003) 때문에 판정은 **경로 모양**만 본다.
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

#: 통째로 설정으로 보는 파일 이름.
CONFIG_FILENAMES = frozenset(
    {
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "uv.lock",
        "poetry.lock",
        "requirements.txt",
        "docker-compose.yml",
        "docker-compose.yaml",
        "dockerfile",
    }
)

#: 설정으로 보는 확장자. `.json` · `.yaml` 이 여기 있는 것은 **의도한 과잉 차단**이다 —
#: 스키마나 고정 데이터가 함께 잘리지만, 설정값이 새는 쪽보다 낫다.
CONFIG_SUFFIXES = frozenset(
    {".env", ".ini", ".cfg", ".conf", ".properties", ".toml", ".yaml", ".yml", ".json"}
)

#: 경로 패턴. 디렉터리 이름이 곧 용도를 말하는 자리들이다.
CONFIG_PATTERNS = (
    ".env*",
    "*/.env*",
    "config/*",
    "*/config/*",
    "configs/*",
    "*/configs/*",
    "settings/*",
    "*/settings/*",
    "*.env.*",
)


def is_config_path(path: str) -> bool:
    """이 경로를 원천에서 빼야 하는가 (FR-9)."""
    p = PurePosixPath(path)
    name = p.name.lower()
    if name in CONFIG_FILENAMES:
        return True
    if p.suffix.lower() in CONFIG_SUFFIXES:
        return True
    if name.startswith(".env"):
        return True
    return any(fnmatch.fnmatch(str(p).lower(), pattern) for pattern in CONFIG_PATTERNS)


def exclude_config_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    """원천 경로를 **남길 것과 뺀 것**으로 나눈다.

    뺀 목록을 함께 돌려주는 이유는, 배제가 조용하면 안 되기 때문이다. 무엇이 왜
    빠졌는지 로그에 남아야 경계가 잘못 잡혔을 때 사람이 알아챌 수 있다.
    """
    kept = [p for p in paths if not is_config_path(p)]
    dropped = [p for p in paths if is_config_path(p)]
    return kept, dropped
