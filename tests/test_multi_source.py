"""모 시스템 저장소가 여럿일 때 (WBS-4.2, ADR-006).

모 시스템이 저장소 하나라는 보장이 없다. 나누는 것은 **원천을 읽는 진행 지점**뿐이고
**지식베이스는 하나로 남는다** — 개념이 한 자리에 모여야 저장소를 넘는 모순이 드러난다.

가장 중요한 시험 둘이다.

1. **커서는 저장소마다 따로 든다.** 합치면 A 의 해시로 B 의 변경분을 묻게 되고,
   그 물음에는 답이 없다.
2. **하나가 실패해도 다른 저장소의 커서는 옮겨진다.** 되감으면 멀쩡히 읽은 구간을
   다음 주기에 통째로 다시 태운다.
"""

from __future__ import annotations

import subprocess

from conftest import FakeHarness

from agentic_service_desk.adapters.mock import BOT_ACCOUNT
from agentic_service_desk.ingest.agent import IngestAgent
from agentic_service_desk.ingest.output_filter import OutputFilter
from agentic_service_desk.ingest.run import IngestRun
from agentic_service_desk.ingest.source import (
    MirrorSet,
    SourceMirror,
    build_mirrors,
    mirror_slug,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations.checkpoint import get_cursor, source_key
from agentic_service_desk.operations.schema import connect, initialize


def _origin(tmp_path, name: str, filename: str, body: str, message: str):  # noqa: ANN001, ANN202
    origin = tmp_path / name
    origin.mkdir()
    for args in (
        ["git", "init", "--quiet"],
        ["git", "config", "user.name", "t"],
        ["git", "config", "user.email", "t@t"],
    ):
        subprocess.run(args, cwd=origin, check=True)
    (origin / filename).write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=origin, check=True)
    return origin


def _mirrors(tmp_path) -> list[SourceMirror]:  # noqa: ANN001
    a = _origin(tmp_path, "alpha", "grade.py", "def grade(): ...\n", "등급 규칙 도입")
    b = _origin(tmp_path, "beta", "route.py", "def route(): ...\n", "라우팅 규칙 도입")
    built = build_mirrors([str(a), str(b)], tmp_path / "mirrors")
    for mirror in built:
        mirror.ensure_cloned()
    return built


def _item(title: str) -> str:
    return (
        f'{{"items": [{{"id": null, "title": "{title}", "body": "본문이다.",'
        f' "invalidation": {{"kind": "periodic", "period_days": 90}}}}]}}'
    )


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _run(tmp_path, harness, mirrors, conn):  # noqa: ANN001, ANN202
    return IngestRun(
        repo=KnowledgeRepository(tmp_path / "knowledge"),
        agent=IngestAgent(harness),
        conn=conn,
        output_filter=OutputFilter(frozenset({BOT_ACCOUNT})),
        mirrors=mirrors,
    )


class TestMirrorLayout:
    def test_저장소마다_자기_칸을_갖는다(self, tmp_path) -> None:
        # 한 칸을 나눠 쓰면 한 저장소의 클론에 다른 저장소를 fetch 하게 된다.
        mirrors = _mirrors(tmp_path)
        assert mirrors[0]._dir != mirrors[1]._dir  # noqa: SLF001

    def test_이름이_같아도_주소가_다르면_칸이_다르다(self, tmp_path) -> None:
        assert mirror_slug("git@x:a/parent.git") != mirror_slug("git@x:b/parent.git")

    def test_칸_이름에_저장소_이름이_남는다(self, tmp_path) -> None:
        # 사람이 var/ 를 열었을 때 어느 것이 무엇인지 알아야 한다.
        assert mirror_slug("git@x:team/parent-system.git").startswith("parent-system-")


class TestCursorsArePerRepository:
    def test_저장소마다_커서가_따로_생긴다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        mirrors = _mirrors(tmp_path)
        _run(tmp_path, FakeHarness(_item("등급"), _item("라우팅")), mirrors, conn).run()

        for mirror in mirrors:
            assert get_cursor(conn, source_key(mirror.repo_url)) == mirror.head()

    def test_두_저장소의_커서는_서로_다른_열쇠다(self, tmp_path) -> None:
        mirrors = _mirrors(tmp_path)
        assert source_key(mirrors[0].repo_url) != source_key(mirrors[1].repo_url)

    def test_두_번째_실행은_할_일이_없다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        mirrors = _mirrors(tmp_path)
        _run(tmp_path, FakeHarness(_item("등급"), _item("라우팅")), mirrors, conn).run()

        again = FakeHarness()
        result = _run(tmp_path, again, mirrors, conn).run()
        assert not result.changed
        assert again.prompts == []


class TestOneRepositoryFailingDoesNotRewindOthers:
    def test_실패한_저장소만_커서가_남는다(self, tmp_path) -> None:
        # 앞엣것이 터지고 뒤엣것이 성공한다. 되감으면 멀쩡히 읽은 구간을 다시 태운다.
        conn = _conn(tmp_path)
        mirrors = _mirrors(tmp_path)
        result = _run(
            tmp_path, FakeHarness("JSON 이 아니다", _item("라우팅")), mirrors, conn
        ).run()

        assert result.failures
        assert get_cursor(conn, source_key(mirrors[0].repo_url)) is None
        assert get_cursor(conn, source_key(mirrors[1].repo_url)) == mirrors[1].head()


class TestKnowledgeBaseStaysOne:
    def test_두_저장소가_한_커밋으로_들어온다(self, tmp_path) -> None:
        # 1 회 ingest = 1 커밋 (FR-5). 저장소 수만큼 커밋이 늘어나지 않는다.
        conn = _conn(tmp_path)
        result = _run(
            tmp_path, FakeHarness(_item("등급"), _item("라우팅")), _mirrors(tmp_path), conn
        ).run()

        assert result.created == 2
        assert result.commit


class TestConfigValuesAreRejectedInTheRun:
    """FR-9 의 두 번째 집행 지점이 ingest 경로에 실제로 걸려 있는가 (§2.2.2)."""

    def _repo_with_constant(self, tmp_path):  # noqa: ANN001, ANN202
        # **소스 파일이다** — 경로 필터는 이것을 거르지 않는다. 값이 여기 있다.
        origin = _origin(
            tmp_path,
            "svc",
            "limits.py",
            "COST_LIMIT_PAPER_KRW = 5_000\n\ndef enforce(u):\n    return u > COST_LIMIT_PAPER_KRW\n",
            "비용 한도 도입",
        )
        built = build_mirrors([str(origin)], tmp_path / "mirrors")
        built[0].ensure_cloned()
        return built

    def _proposal(self, body: str) -> str:
        return (
            f'{{"items": [{{"id": null, "title": "비용 한도", "body": "{body}",'
            f' "invalidation": {{"kind": "periodic", "period_days": 90}}}}]}}'
        )

    def test_설정값을_옮겨_적은_제안은_받지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        harness = FakeHarness(self._proposal("- `COST_LIMIT_PAPER_KRW`: `5_000`"))
        result = _run(tmp_path, harness, self._repo_with_constant(tmp_path), conn).run()

        assert result.created == 0
        assert result.dropped_config_values

    def test_무엇이_왜_걸렸는지_남긴다(self, tmp_path) -> None:
        # 배제가 조용하면 경계가 잘못 잡혔을 때 아무도 알아채지 못한다.
        conn = _conn(tmp_path)
        harness = FakeHarness(self._proposal("- `COST_LIMIT_PAPER_KRW`: `5_000`"))
        result = _run(tmp_path, harness, self._repo_with_constant(tmp_path), conn).run()

        assert "비용 한도" in result.dropped_config_values[0]
        assert "COST_LIMIT_PAPER_KRW=5_000" in result.dropped_config_values[0]

    def test_규칙을_설명하는_제안은_그대로_들어온다(self, tmp_path) -> None:
        # **이쪽이 지식이다.** 이것까지 막으면 코드를 설명하는 항목이 사라진다.
        conn = _conn(tmp_path)
        harness = FakeHarness(
            self._proposal("사용량이 COST_LIMIT_PAPER_KRW 를 넘으면 강제로 낮춘다.")
        )
        result = _run(tmp_path, harness, self._repo_with_constant(tmp_path), conn).run()

        assert result.created == 1
        assert not result.dropped_config_values

    def test_배제돼도_커서는_옮겨진다(self, tmp_path) -> None:
        # 읽은 것과 지식이 된 것은 다르다 — 배제는 처리 결과이지 실패가 아니다.
        conn = _conn(tmp_path)
        mirrors = self._repo_with_constant(tmp_path)
        harness = FakeHarness(self._proposal("- `COST_LIMIT_PAPER_KRW`: `5_000`"))
        _run(tmp_path, harness, mirrors, conn).run()

        assert get_cursor(conn, source_key(mirrors[0].repo_url)) == mirrors[0].head()


class TestDeadInvalidationAsksTheOwningRepository:
    """죽은 무효화 검사가 **주인 저장소에게** 묻는가 (Lint).

    경로가 *다른* 저장소에 있어도 stale 판정에는 닿지 않는다 — `_check_stale` 은
    출처 커밋을 가진 저장소의 변경분만 보기 때문이다. "어딘가에 있다"를 살아 있다고
    답하면 검사가 조용히 무력해진다.
    """

    def _lint(self, tmp_path, mirrors, refs, provenance_commit):  # noqa: ANN001, ANN202
        from agentic_service_desk.knowledge.item import (
            Invalidation,
            InvalidationKind,
            KnowledgeItem,
            Provenance,
        )
        from agentic_service_desk.knowledge.lint import Lint

        repo = KnowledgeRepository(tmp_path / "knowledge")
        repo.ensure_initialized()
        repo.save(
            KnowledgeItem(
                title="규칙",
                body="본문이다.",
                provenance=[Provenance(commit=provenance_commit, path="grade.py")],
                invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=list(refs)),
            )
        )
        return Lint(repo=repo, conn=_conn(tmp_path), mirror=MirrorSet(mirrors)).run()

    def test_주인_저장소에_있으면_살아_있다(self, tmp_path) -> None:
        mirrors = _mirrors(tmp_path)
        report = self._lint(tmp_path, mirrors, ["grade.py"], mirrors[0].head())
        assert not [f for f in report.findings if f.kind.value == "dead_invalidation"]

    def test_다른_저장소에만_있으면_죽은_것이다(self, tmp_path) -> None:
        # `route.py` 는 beta 에 있지만 출처는 alpha 다 — stale 판정에 영영 닿지 않는다.
        mirrors = _mirrors(tmp_path)
        report = self._lint(tmp_path, mirrors, ["route.py"], mirrors[0].head())
        found = [f for f in report.findings if f.kind.value == "dead_invalidation"]
        assert found and "route.py" in found[0].detail


class TestMirrorSet:
    def test_어느_저장소의_커밋이든_실재를_안다(self, tmp_path) -> None:
        mirrors = _mirrors(tmp_path)
        every = MirrorSet(mirrors)
        for mirror in mirrors:
            assert every.has_commit(mirror.head())

    def test_없는_커밋은_없다고_한다(self, tmp_path) -> None:
        assert not MirrorSet(_mirrors(tmp_path)).has_commit("0" * 40)

    def test_커밋의_주인_저장소에게_변경분을_묻는다(self, tmp_path) -> None:
        mirrors = _mirrors(tmp_path)
        every = MirrorSet(mirrors)
        assert every.changed_paths_since(mirrors[0].head()) == []

    def test_주인_없는_커밋에는_빈_목록을_준다(self, tmp_path) -> None:
        # "전부 바뀌었다"고 답하면 그 항목이 영원히 stale 이 된다.
        assert MirrorSet(_mirrors(tmp_path)).changed_paths_since("0" * 40) == []

    def test_하나라도_클론돼_있으면_검사한다(self, tmp_path) -> None:
        # 새로 붙인 저장소가 아직 클론 전이라고 검사를 통째로 끄면, 이미 쌓인
        # 지식의 참조 부재가 조용히 넘어간다.
        mirrors = _mirrors(tmp_path)
        mirrors.append(SourceMirror("git@x:team/not-yet.git", tmp_path / "none"))
        assert MirrorSet(mirrors).is_cloned
