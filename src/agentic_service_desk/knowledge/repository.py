"""지식베이스 저장소 — 파일 + git (D12, FR-5).

**1 회 ingest = 1 커밋** 이다 (llm-wiki 운영 모델, O30). 커밋 하나가 "이 원천을 읽고
지식이 이만큼 바뀌었다"를 통째로 담으므로, 나중에 **어느 커밋이 어느 지식을 만들었는지**
되짚을 수 있다. 항목마다 커밋하면 그 대응이 흩어진다.

> **여기서는 git 에 쓴다.** `ingest/source.py` 의 `SourceMirror` 는 하위 명령 허용
> 목록으로 쓰기를 막는데(CO-1), 그것은 **모 시스템 소스**라서다. 이 저장소는 우리
> 것이고 쓰는 것이 목적이다. 두 저장소를 다른 클래스로 나눠 둔 이유가 이것이다 —
> 한쪽 규칙이 다른 쪽으로 새지 않는다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentic_service_desk.knowledge.item import KnowledgeItem
from agentic_service_desk.knowledge.layout import ensure_bundle, is_reserved
from agentic_service_desk.knowledge.serialize import (
    MalformedItem,
    read_item,
    slugify,
    write_item,
)


class KnowledgeRepoError(RuntimeError):
    """지식 저장소 조작이 실패했다."""


@dataclass(frozen=True)
class StoredItem:
    """저장된 항목과 그 자리."""

    item: KnowledgeItem
    path: Path


class KnowledgeRepository:
    """지식 항목이 사는 곳."""

    def __init__(self, root: Path, *, area: str = "concepts") -> None:
        self._root = Path(root).resolve()
        self._area = area

    @property
    def root(self) -> Path:
        return self._root

    # --- git -------------------------------------------------------------

    def _git(self, *args: str) -> str:
        result = subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=self._root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise KnowledgeRepoError(f"git {args[0]} 실패: {result.stderr.strip()}")
        return result.stdout

    def ensure_initialized(self) -> None:
        """번들 뼈대와 git 저장소를 만든다. 여러 번 불러도 안전하다."""
        ensure_bundle(self._root)
        if not (self._root / ".git").exists():
            self._git("init", "--quiet")
            # 커밋 신원을 저장소에 박아 둔다. 배치가 도는 환경에 전역 설정이 없을 수
            # 있는데, 그때 git 은 커밋을 거부한다 — 지식이 파일로는 써졌는데 이력이
            # 남지 않는 상태가 가장 나쁘다.
            self._git("config", "user.name", "agentic-service-desk")
            self._git("config", "user.email", "svc-agentic-desk@localhost")

    def commit(self, message: str) -> str | None:
        """바뀐 것을 **한 커밋으로** 남긴다. 바뀐 것이 없으면 `None`.

        빈 커밋을 만들지 않는 이유는, ingest 가 아무것도 바꾸지 않은 주기까지 이력에
        남으면 **"어느 커밋이 지식을 바꿨는가"를 세는 일이 무의미해지기** 때문이다.
        """
        self._git("add", "-A")
        staged = self._git("diff", "--cached", "--name-only").strip()
        if not staged:
            return None
        self._git("commit", "--quiet", "-m", message)
        return self._git("rev-parse", "HEAD").strip()

    # --- 항목 ------------------------------------------------------------

    def scan(self) -> tuple[list[StoredItem], list[str]]:
        """읽히는 항목과 **깨진 파일의 사유**를 함께 돌려준다.

        깨진 파일에서 멈추지 않는 이유는 ingest 가 그것 하나로 통째로 서면 안 되기
        때문이다. 그렇다고 조용히 건너뛰지도 않는다 — 항목이 사라진 것을 아무도 모르면
        다음 ingest 가 같은 개념을 새로 만들어 **중복 항목이 생긴다.** 목록을 들고
        나가고, 나중에 Lint(WBS-4.2.6)가 이것을 대기열로 받는다.
        """
        items: list[StoredItem] = []
        broken: list[str] = []
        for path in sorted(self._root.rglob("*.md")):
            if is_reserved(path):
                continue
            try:
                items.append(StoredItem(item=read_item(path), path=path))
            except MalformedItem as exc:
                broken.append(f"{path.relative_to(self._root)}: {exc}")
        return items, broken

    def load_all(self) -> list[StoredItem]:
        """읽히는 항목만. 깨진 것이 궁금하면 `scan()` 을 쓴다."""
        return self.scan()[0]

    def index(self) -> list[tuple[str, str]]:
        """`(id, title)` 목록. **에이전트에게 "이미 아는 것"을 알려주는 재료다.**

        이것이 없으면 에이전트는 매번 새로 만들어 같은 개념이 여러 항목으로 갈린다
        (ingest 절차 2단계 — 대상 항목 식별).
        """
        return [(s.item.id, s.item.title) for s in self.load_all()]

    def path_for(self, item: KnowledgeItem) -> Path:
        return self._root / self._area / f"{slugify(item.title)}.md"

    def find(self, item_id: str) -> StoredItem | None:
        for stored in self.load_all():
            if stored.item.id == item_id:
                return stored
        return None

    def save(self, item: KnowledgeItem, *, at: Path | None = None) -> Path:
        """항목을 쓴다. 제목이 바뀌어 자리가 옮겨지면 **옛 파일을 지운다.**

        지우지 않으면 같은 `id` 를 가진 파일이 둘이 되고, `find()` 가 어느 쪽을
        돌려줄지 알 수 없어진다.
        """
        target = self.path_for(item)
        write_item(target, item)
        if at is not None and at.resolve() != target.resolve() and at.exists():
            at.unlink()
        return target

    # --- 로그 ------------------------------------------------------------

    def append_log(self, line: str) -> None:
        """`log.md` 에 한 줄 (llm-wiki). **1 회 ingest = 1 항목.**"""
        log = self._root / "log.md"
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"- {stamp} — {line}\n")
