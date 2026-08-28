"""지식베이스 디렉터리 배치 — OKF 번들 (FR-55, §3).

    var/knowledge/
      index.md        예약 — 디렉터리 목록 (OKF §3.1)
      log.md          예약 — 날짜별 변경 기록. 1 ingest = 1 항목 (llm-wiki)
      <area>/<slug>.md   지식 항목

`index.md` 와 `log.md` 는 **어느 계층에서든 개념으로 쓸 수 없다** — OKF 예약 파일이다.
"""

from __future__ import annotations

from pathlib import Path

RESERVED_FILENAMES = frozenset({"index.md", "log.md"})


def ensure_bundle(root: Path) -> None:
    """번들 뼈대를 만든다. 이미 있으면 건드리지 않는다."""
    root.mkdir(parents=True, exist_ok=True)
    index = root / "index.md"
    if not index.exists():
        index.write_text(
            "# Knowledge Bundle\n\n"
            "이 디렉터리는 OKF Knowledge Bundle 이다. 지식 항목만 담으며 "
            "운영 데이터(티켓·QnA 기록)는 포함하지 않는다.\n",
            encoding="utf-8",
        )
    log = root / "log.md"
    if not log.exists():
        log.write_text("# Ingest Log\n\n", encoding="utf-8")


def is_reserved(path: Path) -> bool:
    """예약 파일인가. 지식 항목으로 취급하면 안 된다."""
    return path.name in RESERVED_FILENAMES
