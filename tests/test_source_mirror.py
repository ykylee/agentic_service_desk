"""WBS-4.2.1 — 소스 수집 (FR-1·2, ADR-006).

**실제 git 저장소를 만들어 시험한다.** 가짜로 대신하면 정작 확인해야 할 것 —
히스토리가 온전히 오는가, 증분 범위가 맞는가 — 을 검증하지 못한다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentic_service_desk.ingest.source import (
    ALLOWED_SUBCOMMANDS,
    GitCommandDenied,
    MirrorNotReady,
    SourceMirror,
)


def _run(*args: str, cwd: Path) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """모 시스템 저장소 흉내. 커밋 셋을 쌓는다."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _run("init", "-q", "-b", "main", cwd=repo)
    _run("config", "user.email", "t@example.com", cwd=repo)
    _run("config", "user.name", "테스터", cwd=repo)

    (repo / "approval.py").write_text("LIMIT = 100\n", encoding="utf-8")
    _run("add", ".", cwd=repo)
    _run("commit", "-q", "-m", "결재 한도 도입", "-m",
         "부서 등급별로 나누자는 논의가 있었으나 1차는 단일 값으로 간다.", cwd=repo)

    (repo / "notify.py").write_text("CHANNEL = 'mail'\n", encoding="utf-8")
    _run("add", ".", cwd=repo)
    _run("commit", "-q", "-m", "알림 채널 추가", cwd=repo)

    (repo / "approval.py").write_text("LIMIT = 300\n", encoding="utf-8")
    _run("add", ".", cwd=repo)
    _run("commit", "-q", "-m", "한도 상향", "-m", "재무팀 요청. 300 은 결재 규정 개정에 맞춘 값이다.", cwd=repo)
    return repo


@pytest.fixture
def mirror(origin: Path, tmp_path: Path) -> SourceMirror:
    m = SourceMirror(str(origin), tmp_path / "mirror")
    m.ensure_cloned()
    return m


class TestReadOnlyEnforcement:
    """**CO-1 을 코드가 지킨다.** 사람의 주의가 아니라."""

    def test_쓰기_명령이_허용_목록에_없다(self) -> None:
        for denied in ("push", "commit", "checkout", "reset", "rebase", "merge", "tag"):
            assert denied not in ALLOWED_SUBCOMMANDS

    def test_허용_목록에_없으면_거부한다(self, mirror: SourceMirror) -> None:
        with pytest.raises(GitCommandDenied, match="CO-1"):
            mirror._git("push", "origin", "main")

    def test_빈_명령도_거부한다(self, mirror: SourceMirror) -> None:
        with pytest.raises(GitCommandDenied):
            mirror._git()

    def test_거부_메시지가_허용_목록을_알려준다(self, mirror: SourceMirror) -> None:
        with pytest.raises(GitCommandDenied) as exc:
            mirror._git("commit", "-m", "x")
        assert "허용" in str(exc.value)


class TestClone:
    def test_bare_로_클론한다(self, mirror: SourceMirror, tmp_path: Path) -> None:
        # 작업 트리가 없으면 실수로 고칠 것도 없다.
        assert mirror.is_cloned
        assert not (tmp_path / "mirror" / ".git").exists()   # bare 는 .git 하위가 없다
        assert (tmp_path / "mirror" / "HEAD").exists()

    def test_히스토리가_잘리지_않는다(self, mirror: SourceMirror) -> None:
        # **shallow 를 쓰지 않는다** — 히스토리가 §2.2.1 의 원천이다.
        assert len(mirror.commits_since(None)) == 3

    def test_두_번_불러도_다시_클론하지_않는다(self, mirror: SourceMirror) -> None:
        head = mirror.head()
        mirror.ensure_cloned()
        assert mirror.head() == head

    def test_주소가_없으면_거부한다(self, tmp_path: Path) -> None:
        with pytest.raises(MirrorNotReady):
            SourceMirror("", tmp_path / "none").ensure_cloned()

    def test_클론_전에_fetch_하면_거부한다(self, tmp_path: Path, origin: Path) -> None:
        with pytest.raises(MirrorNotReady):
            SourceMirror(str(origin), tmp_path / "none").fetch()


class TestHistoryIsTheSource:
    """§2.2.1 — 히스토리가 "왜 그렇게 정했는가"의 1차 출처다."""

    def test_커밋_메시지_본문까지_가져온다(self, mirror: SourceMirror) -> None:
        # **제목만으로는 "왜"를 알 수 없다.**
        commits = mirror.commits_since(None)
        latest = commits[0]
        assert latest.subject == "한도 상향"
        assert "재무팀 요청" in latest.body
        assert "결재 규정 개정" in latest.full_message

    def test_본문에_줄바꿈이_있어도_레코드가_섞이지_않는다(self, mirror: SourceMirror) -> None:
        # 커밋 메시지에는 줄바꿈이 흔하다 — 구분자를 잘못 고르면 여기서 깨진다.
        commits = mirror.commits_since(None)
        assert len(commits) == 3
        assert all(c.sha and c.author and c.date for c in commits)

    def test_작성자와_시각을_가져온다(self, mirror: SourceMirror) -> None:
        assert mirror.commits_since(None)[0].author == "테스터"


class TestIncremental:
    """FR-2 — 최초 1회는 전체, 이후는 변경 범위만."""

    def test_커서가_없으면_전체_경로다(self, mirror: SourceMirror) -> None:
        assert set(mirror.changed_paths_since(None)) == {"approval.py", "notify.py"}

    def test_커서_이후_바뀐_것만_준다(self, mirror: SourceMirror) -> None:
        commits = mirror.commits_since(None)      # 최신순
        second = commits[1].sha                   # 알림 채널 추가
        assert mirror.changed_paths_since(second) == ["approval.py"]

    def test_커서가_HEAD_면_변경이_없다(self, mirror: SourceMirror) -> None:
        assert mirror.changed_paths_since(mirror.head()) == []

    def test_커서_이후_커밋만_준다(self, mirror: SourceMirror) -> None:
        second = mirror.commits_since(None)[1].sha
        assert [c.subject for c in mirror.commits_since(second)] == ["한도 상향"]


class TestReadContent:
    def test_현재_내용을_읽는다(self, mirror: SourceMirror) -> None:
        assert "LIMIT = 300" in mirror.read_file("approval.py")

    def test_과거_시점의_내용도_읽는다(self, mirror: SourceMirror) -> None:
        # 근거 버전 고정(D20)이 이것에 기댄다 — 답변 당시의 근거를 재현해야 한다.
        first = mirror.commits_since(None)[-1].sha
        assert "LIMIT = 100" in mirror.read_file("approval.py", at=first)


class TestFetch:
    def test_새_커밋을_가져온다(self, mirror: SourceMirror, origin: Path) -> None:
        before = mirror.head()
        (origin / "approval.py").write_text("LIMIT = 500\n", encoding="utf-8")
        _run("add", ".", cwd=origin)
        _run("commit", "-q", "-m", "한도 재상향", cwd=origin)

        mirror.fetch()
        assert mirror.head() != before
        assert mirror.changed_paths_since(before) == ["approval.py"]


class TestRelativePath:
    """설정 기본값이 상대 경로다 — 실제로 밟은 함정이다."""

    def test_상대_경로로도_클론된다(self, origin: Path, tmp_path: Path, monkeypatch) -> None:
        # `git clone` 의 cwd 를 부모로 두므로, 상대 경로를 그대로 넘기면
        # 그 cwd 기준으로 다시 풀려 `var/var/...` 가 만들어진다.
        monkeypatch.chdir(tmp_path)
        m = SourceMirror(str(origin), Path("var/source-mirror"))
        m.ensure_cloned()
        assert m.is_cloned
        assert (tmp_path / "var" / "source-mirror" / "HEAD").exists()
        assert not (tmp_path / "var" / "var").exists()

    def test_상대_경로로_fetch_까지_이어진다(self, origin: Path, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        m = SourceMirror(str(origin), Path("var/source-mirror"))
        m.ensure_cloned()
        m.fetch()
        assert len(m.commits_since(None)) == 3
