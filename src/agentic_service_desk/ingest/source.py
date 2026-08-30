"""모 시스템 소스 저장소 수집 (FR-1·2, ADR-006).

**원천은 코드만이 아니라 코드 + 히스토리다** (D16). 커밋 메시지와 PR 설명이
"왜 그렇게 정했는가"의 1차 출처이므로(§2.2.1), 히스토리가 잘리면 원천의 절반이 사라진다.

두 가지를 구조로 지킨다.

**읽기 전용** (CO-1). `git` 하위 명령을 **허용 목록으로 제한**한다. 소스코드에 쓰지
않는다는 제약을 사람의 주의가 아니라 코드가 지킨다.

**bare 클론.** 작업 트리를 두지 않는다 — 없으면 실수로 고칠 것도 없다. 파일 내용은
`git show <ref>:<path>` 로 읽는다.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class GitCommandDenied(RuntimeError):
    """허용되지 않은 git 하위 명령이다 — CO-1 위반 시도다."""


class MirrorNotReady(RuntimeError):
    """클론이 아직 없다."""


@dataclass(frozen=True)
class Commit:
    """커밋 하나. **메시지가 본체다** — 그것이 "왜"의 출처다 (§2.2.1)."""

    sha: str
    author: str
    date: str
    subject: str
    body: str

    @property
    def full_message(self) -> str:
        return f"{self.subject}\n\n{self.body}".strip()


#: git 하위 명령 허용 목록. **읽기만 있다** (CO-1).
#: `push` · `commit` · `checkout` 이 없는 것이 이 목록의 요점이다.
ALLOWED_SUBCOMMANDS = frozenset(
    {"clone", "fetch", "log", "diff", "rev-parse", "ls-tree", "show", "cat-file"}
)

#: `git log` 출력 구분자. 커밋 메시지에 줄바꿈이 들어가므로 흔한 문자를 쓸 수 없다.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"


class SourceMirror:
    """모 시스템 소스의 읽기 전용 사본."""

    def __init__(self, repo_url: str, mirror_dir: Path) -> None:
        self._repo_url = repo_url
        # **절대 경로로 고정한다.** `git clone` 을 부를 때 cwd 를 부모로 두는데,
        # 상대 경로를 그대로 넘기면 그 cwd 기준으로 다시 풀려 `var/var/...` 가 된다.
        # 설정 기본값이 상대 경로(`var/source-mirror`)라 실제로 밟는 함정이다.
        self._dir = Path(mirror_dir).resolve()

    @property
    def repo_url(self) -> str:
        """이 미러가 어느 저장소의 사본인가. **커서 열쇠가 여기서 나온다.**"""
        return self._repo_url

    # --- git 호출 --------------------------------------------------------

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        """git 을 부른다. **허용 목록에 없으면 거부한다.**

        인자를 쉘에 넘기지 않는다 — 저장소 URL 이나 경로에 특수문자가 있어도
        해석되지 않게 하기 위해서다.
        """
        if not args:
            raise GitCommandDenied("하위 명령이 없다")
        if args[0] not in ALLOWED_SUBCOMMANDS:
            raise GitCommandDenied(
                f"허용되지 않은 git 하위 명령이다: {args[0]!r}. "
                f"CO-1 — 모 시스템 소스코드에 쓰지 않는다. "
                f"허용: {sorted(ALLOWED_SUBCOMMANDS)}"
            )
        result = subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=cwd or (self._dir if self._dir.exists() else None),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git {args[0]} 실패: {result.stderr.strip()}")
        return result.stdout

    # --- 클론과 갱신 ------------------------------------------------------

    @property
    def is_cloned(self) -> bool:
        return (self._dir / "HEAD").exists()

    def ensure_cloned(self) -> None:
        """없으면 클론한다. **shallow 를 쓰지 않는다.**

        `--depth` 를 주면 오래된 커밋 메시지가 잘리고, 그것이 §2.2.1 의 원천이다.
        저장소가 커서 부담이 되더라도 히스토리를 자르는 것은 원천을 자르는 것이다.
        """
        if self.is_cloned:
            return
        if not self._repo_url:
            raise MirrorNotReady("소스 저장소 주소가 설정되지 않았다")
        self._dir.parent.mkdir(parents=True, exist_ok=True)
        self._git("clone", "--bare", self._repo_url, str(self._dir), cwd=self._dir.parent)

    def fetch(self) -> None:
        """최신을 가져온다. fetch 주기가 곧 **stale 감지 지연**이다 (ADR-006)."""
        if not self.is_cloned:
            raise MirrorNotReady("클론이 없다. ensure_cloned() 를 먼저 부른다")
        self._git("fetch", "--prune", "origin", "+refs/heads/*:refs/heads/*")

    # --- 읽기 ------------------------------------------------------------

    def head(self) -> str:
        """현재 커서. 증분의 기준점이다."""
        return self._git("rev-parse", "HEAD").strip()

    def commits_since(self, cursor: str | None) -> list[Commit]:
        """`cursor` 이후의 커밋. cursor 가 없으면 전체다 (최초 1회, FR-2).

        메시지 본문까지 가져온다 — 제목만으로는 "왜"를 알 수 없다.
        """
        fmt = _FIELD_SEP.join(["%H", "%an", "%aI", "%s", "%b"]) + _RECORD_SEP
        rng = f"{cursor}..HEAD" if cursor else "HEAD"
        raw = self._git("log", f"--format={fmt}", rng)
        commits: list[Commit] = []
        for record in raw.split(_RECORD_SEP):
            record = record.strip("\n")
            if not record:
                continue
            sha, author, date, subject, body = record.split(_FIELD_SEP)
            commits.append(
                Commit(sha=sha, author=author, date=date, subject=subject, body=body.strip())
            )
        return commits

    def changed_paths_since(self, cursor: str | None) -> list[str]:
        """`cursor` 이후 바뀐 경로. cursor 가 없으면 전체 경로다 (FR-2).

        **증분의 단위는 커밋이다** (ADR-006) — 이 목록이 ingest 범위가 된다.
        """
        if cursor is None:
            return self.all_paths()
        raw = self._git("diff", "--name-only", f"{cursor}..HEAD")
        return [line for line in raw.splitlines() if line]

    def all_paths(self) -> list[str]:
        """현재 HEAD 의 모든 경로."""
        raw = self._git("ls-tree", "-r", "--name-only", "HEAD")
        return [line for line in raw.splitlines() if line]

    def has_commit(self, sha: str) -> bool:
        """이 커밋이 저장소에 실재하는가 (ADR-002 결정 4 — 참조 부재 검사).

        출처가 가리키는 커밋이 사라지면 **그 지식이 무엇에 근거했는지 알 수 없게
        된다.** 강제 푸시나 히스토리 재작성으로 실제로 일어난다.
        """
        if not sha:
            return False
        try:
            self._git("cat-file", "-e", f"{sha}^{{commit}}")
        except RuntimeError:
            return False
        return True

    def read_file(self, path: str, at: str = "HEAD") -> str:
        """파일 내용. bare 클론이라 작업 트리가 아니라 객체에서 읽는다."""
        return self._git("show", f"{at}:{path}")


def mirror_slug(repo_url: str) -> str:
    """저장소 주소를 디렉터리 한 칸 이름으로 줄인다.

    **읽을 수 있는 이름 + 짧은 해시**다. 이름만 쓰면 `.../a/parent` 와
    `.../b/parent` 가 같은 칸을 가리켜 **한 저장소의 클론에 다른 저장소를 fetch**
    하게 되고, 해시만 쓰면 사람이 `var/` 를 열었을 때 어느 것이 무엇인지 모른다.
    """
    name = repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    name = re.sub(r"[^0-9A-Za-z._-]", "-", name).strip("-") or "repo"
    digest = hashlib.sha256(repo_url.encode("utf-8")).hexdigest()[:8]
    return f"{name}-{digest}"


def build_mirrors(repo_urls: Sequence[str], base_dir: Path) -> list[SourceMirror]:
    """저장소마다 **자기 칸을 가진** 미러를 만든다.

    한 칸을 나눠 쓸 수 없다 — bare 클론은 저장소 하나의 사본이고, 두 저장소를
    같은 칸에 넣으면 커밋 이력이 뒤섞여 **어느 항목이 어느 저장소에서 왔는지**를
    되찾을 수 없다.
    """
    return [SourceMirror(url, Path(base_dir) / mirror_slug(url)) for url in repo_urls]


class MirrorSet:
    """미러 여럿을 **하나처럼** 보이게 한다 (Lint 용).

    Lint 가 묻는 것은 "이 출처 커밋이 실재하는가"와 "그 뒤에 무엇이 바뀌었는가"다.
    커밋은 **저장소 하나에만 속하므로** 물음마다 주인을 찾아 그쪽에 넘기면 된다.
    미러가 하나뿐이던 때와 답이 같아야 하고, 그래서 인터페이스를 맞췄다.
    """

    def __init__(self, mirrors: Sequence[SourceMirror]) -> None:
        self._mirrors = [m for m in mirrors]

    @property
    def is_cloned(self) -> bool:
        """**하나라도** 클론돼 있는가.

        전부를 요구하지 않는 이유가 있다 — 새 저장소를 붙인 첫 주기에는 그것만
        아직 클론 전일 수 있는데, 그때 검사를 통째로 끄면 **이미 쌓인 지식의
        참조 부재가 조용히 넘어간다.**
        """
        return any(m.is_cloned for m in self._mirrors)

    def owner(self, sha: str) -> SourceMirror | None:
        """이 커밋을 가진 미러. 없으면 `None` — 참조 부재다."""
        for mirror in self._mirrors:
            if mirror.is_cloned and mirror.has_commit(sha):
                return mirror
        return None

    def has_commit(self, sha: str) -> bool:
        return self.owner(sha) is not None

    def changed_paths_since(self, cursor: str) -> list[str]:
        """그 커밋을 가진 저장소에서 이후 바뀐 경로.

        주인이 없으면 **빈 목록이다** — 없는 커밋을 기준으로 "전부 바뀌었다"고
        답하면 그 항목이 영원히 stale 이 된다. 커밋이 사라진 것은 참조 부재
        검사가 따로 말한다.
        """
        mirror = self.owner(cursor)
        return mirror.changed_paths_since(cursor) if mirror else []
