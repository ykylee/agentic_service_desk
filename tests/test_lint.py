"""WBS-4.2.6 — Lint (FR-7·8, ADR-002 결정 4).

두 저장소를 잇는 출처 링크에는 **강제할 외래키가 없다** — 한쪽은 파일이고 한쪽은
DB 다. 그래서 무결성이 즉시 강제되지 않고 사후 검사로 지켜진다. 여기서 지키는 것은 넷.

    1. **아무것도 삭제하지 않는다.** 깨진 링크를 지우면 그 답변이 무엇에 근거했는지가
       함께 사라진다 — 답은 이미 사람에게 나갔는데 근거만 없어진다
    2. stale 은 **표시**다 (FR-8). 낡은 것과 틀린 것은 다르고 판정은 사람이 한다
    3. **커밋 기준 stale 판정** — 소스코드 원천이 있어서 시간이 아니라 커밋으로
       정확해진다는 것이 이 시스템의 우위다 (§4)
    4. 주기 실행이므로 **같은 소견이 대기열에 쌓이지 않는다**
"""

from __future__ import annotations

import os
import subprocess

from agentic_service_desk.ingest.source import SourceMirror
from agentic_service_desk.knowledge import contradiction, lint
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.lint import Kind, Lint
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations.schema import connect, initialize


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _repo(tmp_path) -> KnowledgeRepository:
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    return repo


def _item(repo: KnowledgeRepository, **over) -> KnowledgeItem:  # noqa: ANN003
    base = dict(
        title="결재 한도 규칙",
        body="등급으로 정해진다.",
        provenance=[Provenance(commit="a" * 40, path="limit.py")],
        invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90),
    )
    base.update(over)
    item = KnowledgeItem(**base)  # type: ignore[arg-type]
    repo.save(item)
    return item


def _commit_at(repo: KnowledgeRepository, message: str, when: str) -> None:
    """지식 저장소에 **날짜를 지정해** 커밋한다 — 주기형 stale 을 시험하려면 필요하다."""
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "add", "-A"], cwd=repo.root, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "--no-verify", "-m", message],
        cwd=repo.root,
        env=env,
        check=True,
    )


def _origin(tmp_path):  # noqa: ANN001, ANN202
    """실제 커밋이 있는 소스 저장소와 그 미러."""
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=origin, check=True)
    (origin / "limit.py").write_text("LIMIT = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "첫 커밋"], cwd=origin, check=True)
    mirror = SourceMirror(str(origin), tmp_path / "mirror")
    mirror.ensure_cloned()
    return origin, mirror


def _commit_more(origin, path: str = "limit.py", body: str = "LIMIT = 2\n") -> None:  # noqa: ANN001
    (origin / path).write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "한도를 고침"], cwd=origin, check=True)


class TestMissingReference:
    """참조 부재 — 출처 커밋이 저장소에 없다 (ADR-002 결정 4)."""

    def test_없는_커밋을_잡는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        item = _item(repo)  # 출처가 'aaa...' — 실재하지 않는다
        _, mirror = _origin(tmp_path)

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        found = [f for f in report.findings if f.kind is Kind.MISSING_REFERENCE]
        assert len(found) == 1
        assert found[0].subject == item.id

    def test_실재하는_커밋은_잡지_않는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _, mirror = _origin(tmp_path)
        _item(repo, provenance=[Provenance(commit=mirror.head(), path="limit.py")])

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        assert not [f for f in report.findings if f.kind is Kind.MISSING_REFERENCE]

    def test_미러가_없으면_검사하지_않는다(self, tmp_path) -> None:
        # 없는 것과 사라진 것은 다르다. 구분 못 한 채 전부를 올리면 대기열이
        # 거짓 소견으로 가득 찬다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo)
        report = Lint(repo=repo, conn=conn, mirror=None).run()
        assert report.findings == []


class TestDeadInvalidation:
    """죽은 무효화 — 조건이 **나타날 수 없는 경로**를 가리킨다 (2026-08-30 실데이터).

    출처는 코드가 정하고 `MISSING_REFERENCE` 가 본다. 무효화 refs 는 **모델이
    정하는데** 오랫동안 아무도 보지 않았다 — 실저장소 첫 수집에서 101개 중 23개가
    죽은 경로였다.

    죽은 ref 는 **없는 것보다 나쁘다**: 조건이 붙어 있어 살아 보이는데 교집합이
    영원히 비어 그 항목은 절대 stale 이 되지 않는다.

    가장 중요한 시험은 **지워진 파일을 죽었다고 부르지 않는다**는 것이다. 그것까지
    올리면 대기열이 거짓 소견으로 차고, 그러면 이 검사는 꺼지게 된다.
    """

    def _linked(self, repo, mirror, refs):  # noqa: ANN001, ANN202
        return _item(
            repo,
            provenance=[Provenance(commit=mirror.head(), path="limit.py")],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=list(refs)),
        )

    def test_지어낸_경로를_잡는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _, mirror = _origin(tmp_path)
        item = self._linked(repo, mirror, ["src/never/existed.py"])

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        found = [f for f in report.findings if f.kind is Kind.DEAD_INVALIDATION]
        assert len(found) == 1
        assert found[0].subject == item.id
        assert "src/never/existed.py" in found[0].detail

    def test_실재하는_경로는_잡지_않는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _, mirror = _origin(tmp_path)
        self._linked(repo, mirror, ["limit.py"])

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        assert not [f for f in report.findings if f.kind is Kind.DEAD_INVALIDATION]

    def test_지워진_파일은_죽은_것이_아니다(self, tmp_path) -> None:
        # **`git diff` 는 삭제도 낸다** — 오히려 그 삭제가 이 지식을 의심할 이유다.
        origin, mirror = _origin(tmp_path)
        (origin / "gone.py").write_text("X = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "추가"], cwd=origin, check=True)
        (origin / "gone.py").unlink()
        subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "삭제"], cwd=origin, check=True)
        mirror.fetch()

        repo, conn = _repo(tmp_path), _conn(tmp_path)
        self._linked(repo, mirror, ["gone.py"])

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        assert not [f for f in report.findings if f.kind is Kind.DEAD_INVALIDATION]

    def test_디렉터리는_나타날_수_없다(self, tmp_path) -> None:
        # 변경분은 **파일 경로**로 나오므로 디렉터리 이름은 교집합에 걸리지 않는다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        origin, mirror = _origin(tmp_path)
        (origin / "pkg").mkdir()
        (origin / "pkg" / "a.py").write_text("A = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "디렉터리"], cwd=origin, check=True)
        mirror.fetch()
        self._linked(repo, mirror, ["pkg/"])

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        assert [f for f in report.findings if f.kind is Kind.DEAD_INVALIDATION]

    def test_주기형은_보지_않는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _, mirror = _origin(tmp_path)
        _item(repo, provenance=[Provenance(commit=mirror.head(), path="limit.py")])

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        assert not [f for f in report.findings if f.kind is Kind.DEAD_INVALIDATION]

    def test_커밋_출처가_없는데_linked_면_잡는다(self, tmp_path) -> None:
        # **승격 경로 A 로 온 항목이 이렇다** — provenance 가 `qna` 하나다.
        # `linked` 판정은 출처 커밋 이후의 변경을 보므로, 커밋이 없으면 경로가
        # 맞든 틀리든 조건이 영영 발동하지 않는다. 2026-09-02 라이브에서 승격
        # 항목이 산문 refs 를 달고 `clean` 을 통과한 것이 이 자리였다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _, mirror = _origin(tmp_path)
        item = _item(
            repo,
            provenance=[Provenance(qna="q-1")],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=["limit.py"]),
        )

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        found = [f for f in report.findings if f.kind is Kind.DEAD_INVALIDATION]
        assert [f.subject for f in found] == [item.id]
        assert "커밋이 없는데" in found[0].detail

    def test_커밋_출처가_없어도_주기형이면_잡지_않는다(self, tmp_path) -> None:
        # 주기형은 시간으로 재므로 커밋이 필요 없다 — 이것이 정상 형태다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _, mirror = _origin(tmp_path)
        _item(
            repo,
            provenance=[Provenance(qna="q-1")],
            invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90),
        )

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        assert not [f for f in report.findings if f.kind is Kind.DEAD_INVALIDATION]

    def test_출처가_없으면_한_고장을_두_번_올리지_않는다(self, tmp_path) -> None:
        # 출처 커밋 자체가 없으면 `MISSING_REFERENCE` 가 이미 말한다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _, mirror = _origin(tmp_path)
        _item(
            repo,
            provenance=[Provenance(commit="a" * 40, path="limit.py")],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=["nope.py"]),
        )

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        kinds = [f.kind for f in report.findings]
        assert Kind.MISSING_REFERENCE in kinds
        assert Kind.DEAD_INVALIDATION not in kinds

    def test_미러가_없으면_검사하지_않는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(
            repo,
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=["nope.py"]),
        )
        report = Lint(repo=repo, conn=conn, mirror=None).run()
        assert report.findings == []

    def test_같은_소견을_다시_열지_않는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _, mirror = _origin(tmp_path)
        self._linked(repo, mirror, ["src/never/existed.py"])

        first = Lint(repo=repo, conn=conn, mirror=mirror).run()
        second = Lint(repo=repo, conn=conn, mirror=mirror).run()
        assert first.newly_opened == 1
        assert second.newly_opened == 0


class TestStale:
    """FR-8 — 출처 커밋이 낡으면 표시한다. **삭제하지 않는다.**"""

    def test_근거가_바뀌면_stale_이_붙는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        origin, mirror = _origin(tmp_path)
        item = _item(
            repo,
            provenance=[Provenance(commit=mirror.head(), path="limit.py")],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("limit.py",)),
        )
        _commit_more(origin)
        mirror.fetch()

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        assert item.id in report.marked_stale
        assert repo.find(item.id).item.stale is True

    def test_표시일_뿐_지우지_않는다(self, tmp_path) -> None:
        # 낡았다는 것과 틀렸다는 것은 다르고, 그 판정은 사람이 한다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        origin, mirror = _origin(tmp_path)
        item = _item(
            repo,
            provenance=[Provenance(commit=mirror.head(), path="limit.py")],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("limit.py",)),
        )
        _commit_more(origin)
        mirror.fetch()
        Lint(repo=repo, conn=conn, mirror=mirror).run()

        assert repo.find(item.id) is not None
        assert repo.find(item.id).item.body == "등급으로 정해진다."

    def test_stale_은_대기열로_가지_않는다(self, tmp_path) -> None:
        # Q5 는 "근거가 낡은 **게재 답변·살아있는 문서**"의 정정 후보이지 지식 항목
        # 자체가 아니다(§8.2). S0 에서는 Q5 가 화면에 뜨지도 않으므로(FR-59)
        # 티켓을 찍으면 **보이지 않는 대기열이 쌓인다.**
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        origin, mirror = _origin(tmp_path)
        item = _item(
            repo,
            provenance=[Provenance(commit=mirror.head(), path="limit.py")],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("limit.py",)),
        )
        _commit_more(origin)
        mirror.fetch()

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        assert item.id in report.marked_stale        # 표시는 된다 (FR-8)
        assert report.findings == []                 # 대기열로는 안 간다
        assert conn.execute("SELECT COUNT(*) FROM ticket").fetchone()[0] == 0

    def test_방금_갱신한_항목은_stale_이_아니다(self, tmp_path) -> None:
        # **출처는 갱신할 때마다 쌓인다.** 쌓인 것을 전부 보면 오래된 출처 이후로는
        # 당연히 경로가 바뀌었으므로 **갱신된 항목이 영원히 stale 이 된다** —
        # 그러면 stale 은 아무것도 가리키지 않고 Q5 는 거짓 소견으로 찬다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        origin, mirror = _origin(tmp_path)
        first = mirror.head()
        _commit_more(origin)
        mirror.fetch()
        latest = mirror.head()

        _item(
            repo,
            provenance=[
                Provenance(commit=first, path="limit.py"),   # 처음 지었을 때
                Provenance(commit=latest, path="limit.py"),  # 방금 다시 지었을 때
            ],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("limit.py",)),
        )

        assert Lint(repo=repo, conn=conn, mirror=mirror).run().marked_stale == []

    def test_갱신_뒤_또_바뀌면_다시_stale_이_된다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        origin, mirror = _origin(tmp_path)
        first = mirror.head()
        _commit_more(origin)
        mirror.fetch()
        latest = mirror.head()
        item = _item(
            repo,
            provenance=[
                Provenance(commit=first, path="limit.py"),
                Provenance(commit=latest, path="limit.py"),
            ],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("limit.py",)),
        )
        _commit_more(origin, body="LIMIT = 3\n")
        mirror.fetch()

        assert item.id in Lint(repo=repo, conn=conn, mirror=mirror).run().marked_stale

    def test_묶이지_않은_경로가_바뀌어도_조용하다(self, tmp_path) -> None:
        # **커밋 기준 판정의 값이 여기 있다** — 저장소가 바뀌었다는 이유만으로
        # 전부를 stale 로 만들면 대기열이 무의미해진다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        origin, mirror = _origin(tmp_path)
        _item(
            repo,
            provenance=[Provenance(commit=mirror.head(), path="limit.py")],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("limit.py",)),
        )
        _commit_more(origin, path="other.py", body="x\n")
        mirror.fetch()

        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        assert report.marked_stale == []

    def test_주기가_지나면_stale_이_붙는다(self, tmp_path) -> None:
        # periodic 은 묶을 대상이 없을 때의 대비책이라 시간으로 본다 (§6.5.3).
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        item = _item(
            repo, invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=30)
        )
        _commit_at(repo, "ingest: 첫 적재", "2020-01-01T00:00:00+00:00")

        report = Lint(repo=repo, conn=conn, mirror=None).run()
        assert item.id in report.marked_stale

    def test_커밋이_없어도_죽지_않는다(self, tmp_path) -> None:
        # 방금 만들어진 지식베이스에서 실제로 일어난다. 기준점이 없는 것이지
        # 잘못된 것이 아니다 — 여기서 터지면 Lint 가 첫 주기에 통째로 죽는다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=1))
        assert Lint(repo=repo, conn=conn, mirror=None).run().marked_stale == []

    def test_주기가_남았으면_조용하다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=365))
        repo.commit("ingest: 첫 적재")

        assert Lint(repo=repo, conn=conn, mirror=None).run().marked_stale == []

    def test_이미_표시된_항목을_다시_올리지_않는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=30))
        _commit_at(repo, "ingest: 첫 적재", "2020-01-01T00:00:00+00:00")
        Lint(repo=repo, conn=conn, mirror=None).run()

        assert Lint(repo=repo, conn=conn, mirror=None).run().marked_stale == []


class TestBrokenLink:
    """끊어진 링크 — 답변이 가리키는 지식 항목이 없다 (ADR-002 결정 4)."""

    def _answer_with_grounding(self, conn, item_id: str) -> None:  # noqa: ANN001
        conn.execute(
            "INSERT INTO qna_item (id, parent_question_id, state, opened_at) "
            "VALUES ('q-1', 'Q-1', '해결', '2026-08-28')"
        )
        conn.execute(
            "INSERT INTO answer_record (id, qna_item_id, body, author_kind) "
            "VALUES ('ar-1', 'q-1', '답변 본문', 'bot')"
        )
        conn.execute(
            "INSERT INTO answer_grounding (answer_record_id, knowledge_item_id, pinned_commit) "
            "VALUES ('ar-1', ?, ?)",
            (item_id, "b" * 40),
        )
        conn.commit()

    def test_사라진_근거_항목을_잡는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        self._answer_with_grounding(conn, "k-사라진항목")

        report = Lint(repo=repo, conn=conn, mirror=None).run()
        found = [f for f in report.findings if f.kind is Kind.BROKEN_LINK]
        assert len(found) == 1
        assert found[0].subject == "ar-1"

    def test_지우지_않고_Q5_로_올린다(self, tmp_path) -> None:
        # 지우면 그 답변이 무엇에 근거했는지가 함께 사라진다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        self._answer_with_grounding(conn, "k-사라진항목")
        Lint(repo=repo, conn=conn, mirror=None).run()

        assert conn.execute("SELECT COUNT(*) FROM answer_grounding").fetchone()[0] == 1
        row = conn.execute("SELECT * FROM ticket WHERE source = 'correction'").fetchone()
        assert row["state"] == "open"

    def test_살아_있는_근거는_잡지_않는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        item = _item(repo)
        self._answer_with_grounding(conn, item.id)

        report = Lint(repo=repo, conn=conn, mirror=None).run()
        assert not [f for f in report.findings if f.kind is Kind.BROKEN_LINK]


class TestOrphan:
    """고아 — 번들 목록에 없으면 OKF 소비자에게 **없는 것과 같다** (§3.1)."""

    def test_목록에_등재된다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        item = _item(repo)
        report = Lint(repo=repo, conn=conn, mirror=None).run()

        index = (repo.root / "index.md").read_text(encoding="utf-8")
        assert item.title in index
        assert item.id in index
        assert report.indexed == 1

    def test_stale_이_목록에_보인다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, stale=True)
        Lint(repo=repo, conn=conn, mirror=None).run()
        assert "*(stale)*" in (repo.root / "index.md").read_text(encoding="utf-8")

    def test_제목이_바뀌면_목록도_커밋된다(self, tmp_path) -> None:
        # 항목 수가 그대로여도 제목이 바뀌면 파일은 바뀐다. 커밋을 건너뛰면
        # **작업 트리가 계속 더러운 채로 남고** 다음 ingest 커밋에 섞여 들어간다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        item = _item(repo)
        Lint(repo=repo, conn=conn, mirror=None).run()

        item.title = "결재 한도는 직급으로 정해진다"
        repo.save(item, at=repo.find(item.id).path)
        repo.commit("ingest: 갱신")

        report = Lint(repo=repo, conn=conn, mirror=None).run()
        assert report.indexed == 0
        assert report.index_rewritten
        assert report.commit
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo.root, capture_output=True, text=True
        ).stdout
        assert status.strip() == ""  # 남는 변경이 없다

    def test_두_번_돌려도_다시_등재하지_않는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo)
        Lint(repo=repo, conn=conn, mirror=None).run()
        assert Lint(repo=repo, conn=conn, mirror=None).run().indexed == 0


class TestQueueDoesNotPileUp:
    """주기 실행이다. 같은 소견이 쌓이면 우선순위를 매길 수 없다 (§8.6)."""

    def test_같은_소견은_한_번만_열린다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo)
        _, mirror = _origin(tmp_path)

        first = Lint(repo=repo, conn=conn, mirror=mirror).run()
        second = Lint(repo=repo, conn=conn, mirror=mirror).run()

        assert first.newly_opened == 1
        assert second.newly_opened == 0
        assert conn.execute("SELECT COUNT(*) FROM ticket").fetchone()[0] == 1

    def test_처리하면_티켓도_닫힌다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo)
        _, mirror = _origin(tmp_path)
        Lint(repo=repo, conn=conn, mirror=mirror).run()

        key = lint.list_open(conn)[0]["key"]
        lint.resolve(conn, key)

        assert lint.list_open(conn) == []
        assert conn.execute("SELECT state FROM ticket").fetchone()["state"] == "closed"


class TestCompletionCriterion:
    """4.2 완료 조건 — **모순 미해결 0, 깨진 링크 0.**"""

    def test_아무_문제가_없으면_깨끗하다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _, mirror = _origin(tmp_path)
        _item(
            repo,
            provenance=[Provenance(commit=mirror.head(), path="limit.py")],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("limit.py",)),
        )
        assert Lint(repo=repo, conn=conn, mirror=mirror).run().clean

    def test_미해결_모순이_있으면_깨끗하지_않다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _, mirror = _origin(tmp_path)
        item = _item(
            repo,
            provenance=[Provenance(commit=mirror.head(), path="limit.py")],
            invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("limit.py",)),
        )
        contradiction.record(
            conn,
            knowledge_item_id=item.id,
            proposed_title="다른 주장",
            proposed_body="직급이다.",
            provenance=[Provenance(qna="Q-1")],
        )
        report = Lint(repo=repo, conn=conn, mirror=mirror).run()
        assert report.open_contradictions == 1
        assert not report.clean

    def test_읽을_수_없는_파일이_보고된다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        (repo.root / "concepts").mkdir(parents=True, exist_ok=True)
        (repo.root / "concepts" / "깨진.md").write_text("# frontmatter 가 없다\n")

        assert Lint(repo=repo, conn=conn, mirror=None).run().broken_files
