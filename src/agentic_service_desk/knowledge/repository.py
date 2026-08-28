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
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentic_service_desk.knowledge.human_edit import (
    format_violations,
    is_ingest_commit,
    verify_edit,
)
from agentic_service_desk.knowledge.item import KnowledgeItem
from agentic_service_desk.knowledge.layout import ensure_bundle, is_reserved
from agentic_service_desk.knowledge.serialize import (
    MalformedItem,
    from_markdown,
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
        self.install_hook()

    def commit(self, message: str) -> str | None:
        """바뀐 것을 **한 커밋으로** 남긴다. 바뀐 것이 없으면 `None`.

        빈 커밋을 만들지 않는 이유는, ingest 가 아무것도 바꾸지 않은 주기까지 이력에
        남으면 **"어느 커밋이 지식을 바꿨는가"를 세는 일이 무의미해지기** 때문이다.

        `--no-verify` 를 쓴다. 훅은 **사람의 편집**에 세 조건을 묻는 장치인데(FR-54),
        이 경로는 사람이 아니라 우리 코드이고 불변식은 이미 코드가 지킨다. 훅을
        통과시키려 ingest 가 `edited_by_human` 을 켜면 **다음 ingest 가 자기 글을
        사람 글로 착각하는** 정반대의 고장이 생긴다.
        """
        self._git("add", "-A")
        staged = self._git("diff", "--cached", "--name-only").strip()
        if not staged:
            return None
        self._git("commit", "--quiet", "--no-verify", "-m", message)
        return self._git("rev-parse", "HEAD").strip()

    # --- 사람 편집 검사 (FR-54) -------------------------------------------

    def install_hook(self) -> Path:
        """`commit-msg` 훅을 건다. **덮어써도 되는 파일이다** — 우리가 만든 저장소다.

        훅으로 막는 이유는 요구사항이 "세 가지 없이는 편집이 *반영되지 않는다*" 이기
        때문이다. 커밋을 막으면 그 말이 그대로 이뤄지면서 **아무것도 잃지 않는다** —
        작업 트리의 수정은 그대로 있고, 빠진 것을 채워 다시 커밋하면 된다.

        > **`pre-commit` 이 아니라 `commit-msg` 다.** 조건 1 이 커밋 메시지인데,
        > `pre-commit` 이 도는 시점에는 **이번 커밋의 메시지가 아직 없다** —
        > `.git/COMMIT_EDITMSG` 에는 직전 커밋의 것이 남아 있고, 그 직전은 대개
        > ingest 커밋이라 검사가 통째로 면제된다. 즉 훅은 걸려 있는데 아무것도
        > 막지 않는다. `commit-msg` 는 메시지 파일을 인자로 받으므로 그 구멍이 없다.

        `ingest` 는 `--no-verify` 로 지나간다 — 그것은 사람의 편집이 아니다.

        **인터프리터 절대 경로를 박아 둔다.** `asd` 콘솔 스크립트를 부르면 운영자가
        가상환경 밖 셸에서 커밋할 때 `PATH` 에 없다. 그러면 훅이 127 로 죽어 커밋은
        막히지만 **이유가 "asd: not found" 로 나와** 무엇이 문제인지 알 수 없다.
        `sys.executable` 은 이 패키지가 설치된 그 인터프리터라 `PATH` 를 타지 않는다.
        """
        hook = self._root / ".git" / "hooks" / "commit-msg"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "#!/bin/sh\n"
            "# 생성물이다 — 지식베이스의 사람 편집 세 조건을 검사한다 (FR-54, §8.5.3).\n"
            "# ingest 는 --no-verify 로 지나간다: 사람의 편집이 아니다.\n"
            f'exec "{sys.executable}" -m agentic_service_desk.cli verify-edit'
            ' --root "$(git rev-parse --show-toplevel)" --message-file "$1"\n',
            encoding="utf-8",
        )
        hook.chmod(0o755)
        return hook

    def staged_item_paths(self) -> list[Path]:
        """커밋 대상 중 지식 항목 파일.

        **`-z` 로 받는다.** git 은 기본적으로 비 ASCII 경로를 따옴표로 감싸
        `"\352\262\260..."` 처럼 이스케이프해서 내놓는다. 그대로 경로로 쓰면 그런
        파일은 존재하지 않는 것이 되고, **지식 항목 제목이 대부분 한국어라 사실상
        전부가 검사에서 빠진다** — 훅이 걸려 있는데 아무것도 막지 않는 상태가 된다.
        """
        raw = self._git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACM")
        out = []
        for name in raw.split("\0"):
            if not name.endswith(".md"):
                continue
            path = self._root / name
            if not is_reserved(path) and path.exists():
                out.append(path)
        return out

    def previous_version(self, path: Path) -> KnowledgeItem | None:
        """HEAD 에 있던 모습. 새 파일이면 `None`."""
        rel = path.resolve().relative_to(self._root).as_posix()
        result = subprocess.run(  # noqa: S603
            ["git", "show", f"HEAD:{rel}"],
            cwd=self._root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        try:
            return from_markdown(result.stdout)
        except MalformedItem:
            return None

    def verify_staged_edits(self, commit_message: str) -> list[str]:
        """커밋 대상의 사람 편집을 검사한다. 어긴 것들의 설명을 돌려준다.

        읽을 수 없는 파일은 여기서 막지 않는다 — **직렬화가 이미 거부**하므로 그
        메시지를 두 번 말하지 않는다.
        """
        if is_ingest_commit(commit_message):
            return []
        problems: list[str] = []
        for path in self.staged_item_paths():
            try:
                after = read_item(path)
            except MalformedItem as exc:
                problems.append(f"{path.name} — 지식 항목으로 읽을 수 없다: {exc}")
                continue
            verdict = verify_edit(
                after=after,
                before=self.previous_version(path),
                commit_message=commit_message,
            )
            if not verdict:
                problems.append(format_violations(path.name, verdict))
        return problems

    @staticmethod
    def read_commit_message(message_file: Path) -> str:
        """`commit-msg` 훅이 넘겨준 메시지 파일을 읽는다.

        주석 줄(`#`)은 git 이 커밋에 넣지 않으므로 여기서도 뺀다 — 남겨 두면
        템플릿 주석만으로 조건 1 의 길이를 채울 수 있다.
        """
        if not message_file.exists():
            return ""
        lines = [
            line
            for line in message_file.read_text(encoding="utf-8").splitlines()
            if not line.startswith("#")
        ]
        return "\n".join(lines).strip()

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

    def last_commit_date(self, path: Path) -> str | None:
        """이 파일이 마지막으로 커밋된 시각 (ISO).

        **주기형 무효화 조건의 기준점이다** (§6.5.3). 항목에 "마지막으로 확인한 날"
        필드를 따로 두지 않는 이유는, 지식이 git 위에 살기 때문에 그 답을 이미
        저장소가 알고 있어서다 — 필드를 더하면 두 곳이 어긋날 자리가 생긴다.
        """
        rel = path.resolve().relative_to(self._root).as_posix()
        try:
            out = self._git("log", "-1", "--format=%aI", "--", rel).strip()
        except KnowledgeRepoError:
            # 아직 커밋이 하나도 없는 저장소다 — `git log` 가 실패한다. 방금 만들어진
            # 지식베이스에서 실제로 일어나며, 여기서 터지면 **Lint 가 첫 주기에
            # 통째로 죽는다.** 기준점이 없는 것이지 잘못된 것이 아니다.
            return None
        return out or None

    # --- 로그 ------------------------------------------------------------

    def append_log(self, line: str) -> None:
        """`log.md` 에 한 줄 (llm-wiki). **1 회 ingest = 1 항목.**"""
        log = self._root / "log.md"
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"- {stamp} — {line}\n")
