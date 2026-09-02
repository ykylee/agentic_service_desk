"""WBS-4.2.4 — 한 번의 ingest (FR-5, NFR-4).

여기서 지키는 것은 넷이다.

    1. **1 회 ingest = 1 커밋** — 항목이 몇 개 바뀌든 커밋은 하나다
    2. **진행 표시는 커밋 뒤에** — 먼저 옮기면 중단 시 지식에 구멍이 생긴다
    3. **QnA 원천은 산출물 필터를 지나서만** 온다 (NFR-4)
    4. **사람이 고친 항목을 덮어쓰지 않는다** (D38)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentic_service_desk.adapters.mock import BOT_ACCOUNT, MockParentSystem
from agentic_service_desk.ingest.agent import IngestAgent
from agentic_service_desk.ingest.output_filter import OutputFilter
from agentic_service_desk.ingest.qna import QnaCollector
from agentic_service_desk.ingest.run import IngestRun, _chunks
from agentic_service_desk.ingest.agent import SourceMaterial
from agentic_service_desk.ingest.source import SourceMirror
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations.checkpoint import get_cursor, source_key
from agentic_service_desk.operations.schema import connect, initialize

from conftest import FakeHarness, failing


def _item_json(title: str, item_id: str | None = None, body: str = "본문이다.") -> str:
    ident = f'"{item_id}"' if item_id else "null"
    return (
        f'{{"items": [{{"id": {ident}, "title": "{title}", "body": "{body}",'
        f' "invalidation": {{"kind": "periodic", "period_days": 90}}}}]}}'
    )


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _run(tmp_path, harness, *, mirror=None, conn=None, exclude=()):  # noqa: ANN001, ANN202
    return IngestRun(
        repo=KnowledgeRepository(tmp_path / "knowledge"),
        agent=IngestAgent(harness),
        conn=conn if conn is not None else _conn(tmp_path),
        output_filter=OutputFilter(frozenset({BOT_ACCOUNT})),
        mirrors=[mirror] if mirror else [],
        exclude=exclude,
    )


def _collected(tmp_path, parent=None):  # noqa: ANN001, ANN202
    conn = _conn(tmp_path)
    QnaCollector(parent or MockParentSystem(), conn).collect()
    return conn


class TestOneIngestOneCommit:
    def test_바뀐_것이_없으면_커밋하지_않는다(self, tmp_path) -> None:
        # 빈 커밋이 쌓이면 "어느 커밋이 지식을 바꿨는가"를 세는 일이 무의미해진다.
        result = _run(tmp_path, FakeHarness()).run()
        assert result.commit is None
        assert not result.changed

    def test_항목이_여럿이어도_커밋은_하나다(self, tmp_path) -> None:
        conn = _collected(tmp_path)
        harness = FakeHarness(_item_json("개념 하나"), _item_json("개념 둘"))
        result = _run(tmp_path, harness, conn=conn).run()

        assert result.created == 2
        assert result.commit
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=tmp_path / "knowledge",
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert len(log.strip().splitlines()) == 1

    def test_log_md_에_한_줄_남는다(self, tmp_path) -> None:
        conn = _collected(tmp_path)
        _run(tmp_path, FakeHarness(_item_json("개념 하나")), conn=conn).run()
        log = (tmp_path / "knowledge" / "log.md").read_text(encoding="utf-8")
        assert "신규 1 · 갱신 0" in log

    def test_지식_항목이_파일로_남는다(self, tmp_path) -> None:
        conn = _collected(tmp_path)
        _run(tmp_path, FakeHarness(_item_json("결재 한도")), conn=conn).run()
        files = list((tmp_path / "knowledge").rglob("*.md"))
        names = {f.name for f in files}
        assert "결재-한도.md" in names


class TestProvenance:
    def test_QnA_에서_온_항목은_질문_id_를_출처로_갖는다(self, tmp_path) -> None:
        conn = _collected(tmp_path)
        _run(tmp_path, FakeHarness(_item_json("개념")), conn=conn).run()
        repo = KnowledgeRepository(tmp_path / "knowledge")
        item = repo.load_all()[0].item
        assert item.provenance[0].qna in {"Q-2", "Q-4"}

    def test_출처_없는_항목이_만들어지지_않는다(self, tmp_path) -> None:
        # FR-4 — 검증 기준이 "출처 없는 항목 0건" 이다.
        conn = _collected(tmp_path)
        _run(tmp_path, FakeHarness(_item_json("가"), _item_json("나")), conn=conn).run()
        repo = KnowledgeRepository(tmp_path / "knowledge")
        assert all(s.item.provenance for s in repo.load_all())


class TestFilterIsTheOnlyDoor:
    """NFR-4 — QnA 원천은 산출물 필터를 지나서만 온다."""

    def test_차단된_답변은_에이전트에_닿지_않는다(self, tmp_path) -> None:
        conn = _collected(tmp_path)
        harness = FakeHarness(*[_item_json(f"개념 {i}") for i in range(5)])
        _run(tmp_path, harness, conn=conn).run()

        # 통과분은 Q-2 · Q-4 둘뿐이다. Q-3 · Q-5 는 봇 + 미해결이라 막힌다.
        assert len(harness.prompts) == 2
        joined = "\n".join(harness.prompts)
        assert "알림이 안 오는데요?" not in joined  # Q-3
        assert "승인 한도는 어떻게 정해지나요?" in joined  # Q-2

    def test_한_번_읽은_답변은_다시_읽지_않는다(self, tmp_path) -> None:
        conn = _collected(tmp_path)
        first = FakeHarness(_item_json("가"), _item_json("나"))
        _run(tmp_path, first, conn=conn).run()

        second = FakeHarness(_item_json("다"))
        result = _run(tmp_path, second, conn=conn).run()
        assert second.prompts == []
        assert not result.changed


class TestProgressIsMarkedEvenWhenNothingIsLearned:
    """**읽은 것과 지식이 된 것은 다르다.**

    라이브 실행에서 잡힌 것이다 — 내용 없는 봇 답변("설정을 확인해 보세요")에서
    모델이 개념을 뽑지 않았고, 그러자 그 답변이 **매 주기 LLM 에 다시 실렸다.**
    """

    def test_제안이_없어도_같은_답변을_다시_읽지_않는다(self, tmp_path) -> None:
        conn = _collected(tmp_path)
        first = FakeHarness('{"items": []}', '{"items": []}')
        result = _run(tmp_path, first, conn=conn).run()
        assert not result.changed
        assert len(first.prompts) == 2

        second = FakeHarness()
        _run(tmp_path, second, conn=conn).run()
        assert second.prompts == []

    def test_뽑을_개념이_없어도_소스_커서는_옮겨진다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        mirror = TestSourceIngest()._mirror(tmp_path)
        result = _run(tmp_path, FakeHarness('{"items": []}'), mirror=mirror, conn=conn).run()

        assert not result.changed
        assert get_cursor(conn, source_key(mirror.repo_url)) == mirror.head()

    def test_실패한_답변은_다시_읽는다(self, tmp_path) -> None:
        # 진행 표시는 성공한 호출에만 붙는다.
        conn = _collected(tmp_path)
        first = FakeHarness(*failing(), '{"items": []}')
        result = _run(tmp_path, first, conn=conn).run()
        assert len(result.failures) == 1

        second = FakeHarness()
        _run(tmp_path, second, conn=conn).run()
        assert len(second.prompts) == 1  # 실패한 하나만 다시 온다


class TestHumanEditsAreNotOverwritten:
    """D38 — 에이전트는 사람이 고친 것을 덮어쓰지 않는다."""

    def test_사람이_고친_항목은_건드리지_않는다(self, tmp_path) -> None:
        conn = _collected(tmp_path)
        _run(tmp_path, FakeHarness(_item_json("개념")), conn=conn).run()

        repo = KnowledgeRepository(tmp_path / "knowledge")
        stored = repo.load_all()[0]
        stored.item.edited_by_human = True
        stored.item.body = "사람이 고친 본문"
        repo.save(stored.item, at=stored.path)

        # 같은 항목을 갱신하라는 제안이 와도 덮어쓰지 않는다.
        parent = MockParentSystem()
        parent._add_question("Q-9", "새 질문", "emp-1")
        parent._add_answer("Q-9", "사람 답변", "emp-999")
        QnaCollector(parent, conn).collect()

        harness = FakeHarness(_item_json("바뀐 제목", item_id=stored.item.id))
        result = _run(tmp_path, harness, conn=conn).run()

        assert result.held_for_human == [stored.item.id]
        assert repo.find(stored.item.id).item.body == "사람이 고친 본문"


class TestSourceIngest:
    def _mirror(self, tmp_path) -> SourceMirror:
        origin = tmp_path / "origin"
        origin.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=origin, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=origin, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=origin, check=True)
        (origin / "limit.py").write_text("LIMIT_BY_GRADE = True\n")
        (origin / "app.yaml").write_text("limit: 3000000\n")
        subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "한도 계산을 부서 등급으로 바꿈"],
            cwd=origin,
            check=True,
        )
        mirror = SourceMirror(str(origin), tmp_path / "mirror")
        mirror.ensure_cloned()
        return mirror

    def _mirror_with_binary(self, tmp_path) -> SourceMirror:
        origin = tmp_path / "origin-bin"
        origin.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=origin, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=origin, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=origin, check=True)
        (origin / "limit.py").write_text("LIMIT_BY_GRADE = True\n")
        # 실제 저장소에 있던 모양 — 테스트 픽스처로 커밋된 zip.
        (origin / "bundle.zip").write_bytes(b"PK\x03\x04\x98\xff\x00binary")
        subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "번들을 픽스처로 넣음"],
            cwd=origin,
            check=True,
        )
        mirror = SourceMirror(str(origin), tmp_path / "mirror-bin")
        mirror.ensure_cloned()
        return mirror

    def test_글자가_아닌_파일은_워커를_죽이지_않는다(self, tmp_path) -> None:
        # UnicodeDecodeError 는 ValueError 라 RuntimeError 핸들러를 지나쳐
        # 워커 프로세스까지 올라간다. 죽는 자리가 커밋 앞이라 그 런의 수집분이
        # 통째로 사라지고, 커서가 그대로라 다시 띄워도 같은 자리에서 또 죽는다.
        conn = _conn(tmp_path)
        mirror = self._mirror_with_binary(tmp_path)
        result = _run(
            tmp_path, FakeHarness(_item_json("한도 규칙")), mirror=mirror, conn=conn
        ).run()

        assert result.unreadable_paths == ["bundle.zip"]
        assert result.created == 1  # 나머지 원천은 그대로 읽혔다
        assert get_cursor(conn, source_key(mirror.repo_url)) == mirror.head()

    def test_선언된_패턴은_원천에서_빠진다(self, tmp_path) -> None:
        # 이 배제는 코드가 아니라 사람이 정한다 — 무엇이 모 시스템의 것이 아닌지는
        # 사람만 안다(벤더링된 남의 소스, 메타 계층).
        conn = _conn(tmp_path)
        mirror = self._mirror_with_meta(tmp_path)
        result = _run(
            tmp_path,
            FakeHarness(_item_json("한도 규칙")),
            mirror=mirror,
            conn=conn,
            exclude=("vendor/*",),
        ).run()

        assert result.excluded_paths == ["vendor/copied.py"]
        assert result.created == 1  # 남은 원천은 그대로 읽혔다

    def test_배제하지_않으면_다_읽는다(self, tmp_path) -> None:
        # 기본은 "선언하지 않은 경로는 읽는다" 다. 조용히 빠지는 것이 없어야 한다.
        result = _run(
            tmp_path,
            FakeHarness(_item_json("한도 규칙")),
            mirror=self._mirror_with_meta(tmp_path),
            conn=_conn(tmp_path),
        ).run()
        assert result.excluded_paths == []

    def _mirror_with_meta(self, tmp_path) -> SourceMirror:
        origin = tmp_path / "origin-meta"
        origin.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=origin, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=origin, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=origin, check=True)
        (origin / "limit.py").write_text("LIMIT_BY_GRADE = True\n")
        (origin / "vendor").mkdir()
        (origin / "vendor" / "copied.py").write_text("SOMEONE_ELSES = True\n")
        subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "벤더링된 사본을 넣음"],
            cwd=origin,
            check=True,
        )
        mirror = SourceMirror(str(origin), tmp_path / "mirror-meta")
        mirror.ensure_cloned()
        return mirror

    def test_설정_파일이_원천에서_빠진다(self, tmp_path) -> None:
        # FR-9 — 설정값은 굳는 순간 stale 이 된다.
        harness = FakeHarness(_item_json("한도 규칙"))
        result = _run(tmp_path, harness, mirror=self._mirror(tmp_path)).run()

        assert "app.yaml" in result.dropped_config_paths
        assert "limit: 3000000" not in harness.prompts[0]
        assert "LIMIT_BY_GRADE" in harness.prompts[0]

    def test_커밋_메시지가_원천에_실린다(self, tmp_path) -> None:
        harness = FakeHarness(_item_json("한도 규칙"))
        _run(tmp_path, harness, mirror=self._mirror(tmp_path)).run()
        assert "한도 계산을 부서 등급으로 바꿈" in harness.prompts[0]

    def test_커서는_커밋이_끝난_뒤에_옮겨진다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        mirror = self._mirror(tmp_path)
        assert get_cursor(conn, source_key(mirror.repo_url)) is None

        result = _run(tmp_path, FakeHarness(_item_json("한도 규칙")), mirror=mirror, conn=conn).run()

        assert result.commit
        assert get_cursor(conn, source_key(mirror.repo_url)) == mirror.head()

    def test_에이전트가_터지면_커서를_옮기지_않는다(self, tmp_path) -> None:
        # 옮기면 그 구간을 영영 건너뛰고, 지식에 구멍이 생기는데 아무도 모른다.
        conn = _conn(tmp_path)
        mirror = self._mirror(tmp_path)
        result = _run(tmp_path, FakeHarness(*failing("JSON 이 아닌 응답")), mirror=mirror, conn=conn).run()

        assert result.failures
        assert get_cursor(conn, source_key(mirror.repo_url)) is None

    def test_두_번째_실행은_할_일이_없다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        mirror = self._mirror(tmp_path)
        _run(tmp_path, FakeHarness(_item_json("한도 규칙")), mirror=mirror, conn=conn).run()

        second = FakeHarness(_item_json("또"))
        result = _run(tmp_path, second, mirror=mirror, conn=conn).run()
        assert second.prompts == []
        assert not result.changed


class TestChunking:
    def test_한도를_넘으면_나눈다(self, tmp_path) -> None:
        material = SourceMaterial(
            commit="a", files=(("a.py", "x" * 100), ("b.py", "y" * 100))
        )
        assert len(_chunks(material, max_chars=150)) == 2

    def test_파일_하나가_한도를_넘어도_쪼개지_않는다(self, tmp_path) -> None:
        # 반쪽짜리 코드에서 뽑은 개념은 틀리기 쉽고, 틀렸다는 것이 드러나지 않는다.
        material = SourceMaterial(commit="a", files=(("a.py", "x" * 1000),))
        chunks = _chunks(material, max_chars=10)
        assert len(chunks) == 1
        assert chunks[0].files[0][1] == "x" * 1000

    def test_커밋_메시지는_나뉘어_실리고_하나도_빠지지_않는다(self, tmp_path) -> None:
        """**규약이 바뀌었다** (2026-09-02). 예전에는 모든 묶음에 통째로 실렸다.

        실측: 묶음 하나의 프롬프트 74,694자 중 메시지가 40,410자(54%)로 원천
        파일(20,170자)의 두 배였고, 116묶음이면 같은 것을 4.6M자 다시 보냈다.
        메시지는 맥락이 아니라 **원천**이라(D16) 한 번 읽히면 족하다.
        """
        msgs = tuple(f"메시지{i}" for i in range(5))
        material = SourceMaterial(
            commit="a", messages=msgs, files=(("a.py", "x" * 100), ("b.py", "y" * 100))
        )
        chunks = _chunks(material, max_chars=150)
        assert len(chunks) == 2

        실린것 = [m for c in chunks for m in c.messages]
        assert sorted(실린것) == sorted(msgs)  # 하나도 빠지지 않는다
        assert len(실린것) == len(set(실린것))  # 두 번 실리지도 않는다

    def test_묶음보다_메시지가_적으면_앞쪽만_받는다(self, tmp_path) -> None:
        material = SourceMaterial(
            commit="a",
            messages=("하나뿐",),
            files=(("a.py", "x" * 100), ("b.py", "y" * 100)),
        )
        chunks = _chunks(material, max_chars=150)
        assert chunks[0].messages == ("하나뿐",)
        assert chunks[1].messages == ()  # 나머지는 코드만으로 읽힌다

    def test_파일이_없으면_메시지만_담긴_묶음_하나다(self, tmp_path) -> None:
        # 삭제만 있었던 구간이다 — 커밋 메시지에는 "왜 지웠는가"가 남아 있다.
        material = SourceMaterial(commit="a", messages=("왜 지웠는가",), files=())
        chunks = _chunks(material, max_chars=150)
        assert len(chunks) == 1
        assert chunks[0].messages == ("왜 지웠는가",)
