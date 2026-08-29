"""WBS-4.7.3 — 뉴스레터 (FR-38, §7.2·§7.7.3).

칼럼과 같은 발행 면에 나가지만 **읽는 것이 다르다** — 한 주제가 아니라 **그 기간에
무슨 일이 있었는가**다.

여기서 지키는 것은 여섯.

    1. **요약은 세는 것이지 판단하는 것이 아니다** — 모델에게 기간이 어땠는지 묻지 않는다
    2. **새로 생긴 것과 고쳐진 것을 나눈다** — 한 말로 적으면 없던 것이 생긴 줄로 읽힌다
    3. **할 말이 없으면 내지 않는다** — 빈 회차도 회수할 수 없다
    4. **없는 것을 줄줄이 적지 않는다** — "0건이 게재됐다"는 사실이지만 소식이 아니다
    5. **센 사실은 초안에 박힌다** — 발행 뒤에 다시 세면 숫자가 달라진다
    6. **발송하지 않고 게시한다** (§7.7.3) — 수신자 명단이 필요 없다
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from agentic_service_desk.content import period, production, publication, store
from agentic_service_desk.content.registry import Input, Place, load as load_registry
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations.schema import connect, initialize

from conftest import FakeHarness

NEWSLETTER = load_registry().get("newsletter")


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _item(item_id: str, title: str, body: str, *, stale: bool = False) -> KnowledgeItem:
    return KnowledgeItem(
        id=item_id,
        title=title,
        body=body,
        provenance=[Provenance(commit="a" * 40, path="a.py")],
        invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("a.py",)),
        stale=stale,
    )


def _repo(tmp_path):  # noqa: ANN001, ANN202
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    return repo


def _since() -> str:
    return (datetime.now(UTC) - timedelta(days=30)).isoformat()


def _published_content(conn, type_id: str, *, days_ago: int = 3) -> None:  # noqa: ANN001
    when = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    draft_id = store.save(
        conn, type_id=type_id, title="지난 회차", body="본문", grounding=("k-1",)
    )
    conn.execute(
        "INSERT INTO content_publication (id, draft_id, type_id, place, body, "
        "grounding, state, attempted_at, published_at) "
        "VALUES (?, ?, ?, 'publication', '본문', '[]', 'published', ?, ?)",
        (f"cp-{type_id}-{days_ago}", draft_id, type_id, when, when),
    )
    conn.commit()


def _published_answer(conn, qid: str, *, days_ago: int = 3, resolved: bool = False) -> None:  # noqa: ANN001
    when = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO qna_item (id, state, resolution_grade, opened_at, closed_at) "
        "VALUES (?, '해결', ?, ?, ?)",
        (qid, "explicit" if resolved else None, when, when if resolved else None),
    )
    conn.execute(
        "INSERT INTO answer_record (id, qna_item_id, body, author_kind, state, "
        "published_at) VALUES (?, ?, '답', 'bot', 'published', ?)",
        (f"ar-{qid}", qid, when),
    )
    conn.commit()


def _doc(title: str, body: str, grounding=("k-1",), observations=()) -> str:  # noqa: ANN001
    return json.dumps(
        {
            "title": title,
            "body": body,
            "grounding": list(grounding),
            "observations": list(observations),
        },
        ensure_ascii=False,
    )


def _producer(conn, repo, harness=None):  # noqa: ANN001, ANN202
    return production.ContentProducer(
        conn=conn, repo=repo, harness=harness, generated_by="test-model"
    )


# --- 선언 --------------------------------------------------------------------


class TestDeclaration:
    """같은 발행 면, 다른 원천 (§7.2, §7.7.3)."""

    def test_칼럼과_다른_주_입력을_선언한다(self) -> None:
        assert NEWSLETTER.input is Input.PERIOD_SUMMARY
        assert load_registry().get("column").input is Input.BOTH

    def test_발송하지_않고_게시한다(self) -> None:
        # 발행 면이라 연산이 create 다 — 수신자 명단도 발송 경로도 없다 (§7.7.3).
        assert NEWSLETTER.destination.place is Place.PUBLICATION
        assert NEWSLETTER.destination.place.operation == "create"
        assert NEWSLETTER.destination.path == ""

    def test_발행물이라_전문_검수와_최종_확인이_하한이다(self) -> None:
        assert not NEWSLETTER.living
        assert NEWSLETTER.review.final_check


# --- 기간 요약 ---------------------------------------------------------------


class TestSummary:
    """세는 것이지 판단하는 것이 아니다."""

    def test_새로_생긴_것과_고쳐진_것을_나눈다(self, tmp_path) -> None:
        # 제목이 한국어인 것에 의미가 있다 — git 은 비 ASCII 경로를 따옴표로 감싸
        # 내놓으므로 `-z` 없이 받으면 **사실상 전부가 목록과 어긋난다**
        # (`staged_item_paths` 가 이미 밟은 자리다).
        conn = _conn(tmp_path)
        repo = _repo(tmp_path)
        repo.save(_item("k-1", "결재 한도", "부서 등급으로 정해진다."))
        repo.commit("새 항목")
        repo.save(_item("k-1", "결재 한도", "부서 등급으로 정해진다. 예외가 있다."))
        repo.commit("갱신")
        repo.save(_item("k-2", "휴가 승인", "팀장이 승인한다."))
        repo.commit("새 항목 둘")

        s = period.summarize(conn, repo, since=_since(), window_days=30)
        assert s.added == 2 and s.updated == 0  # k-1 은 기간 안에 생겼다
        assert "2건이 새로 생겼다" in s.facts[0].text
        conn.close()

    def test_바뀐_것이_없으면_요약할_것도_없다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        repo = _repo(tmp_path)

        s = period.summarize(conn, repo, since=_since(), window_days=30)
        assert s.items == [] and s.facts == ()
        conn.close()

    def test_게재된_콘텐츠를_타입까지_적는다(self, tmp_path) -> None:
        # 건수만으로는 소식이 아니다.
        conn = _conn(tmp_path)
        _published_content(conn, "guide")
        _published_content(conn, "column", days_ago=10)

        text = period.publication_fact(conn, _since(), window_days=30)
        assert "콘텐츠 2건이 게재됐다" in text
        assert "column 1건" in text and "guide 1건" in text
        conn.close()

    def test_해결은_명시적인_것만_센다(self, tmp_path) -> None:
        # 암묵적 해결을 섞으면 "해결됐다"가 "조용해졌다"와 같은 말이 된다 (§5.3.1).
        conn = _conn(tmp_path)
        _published_answer(conn, "qi-1", resolved=True)
        _published_answer(conn, "qi-2", resolved=False)

        text = period.answer_fact(conn, _since(), window_days=30)
        assert "답변 2건이 게재됐고" in text
        assert "1건이다" in text
        conn.close()

    def test_없는_것은_싣지_않는다(self, tmp_path) -> None:
        # "콘텐츠 0건이 게재됐다"는 사실이지만 소식이 아니다 — 없는 것을 줄줄이
        # 적으면 있는 것이 그 사이에 묻힌다.
        conn = _conn(tmp_path)
        repo = _repo(tmp_path)
        repo.save(_item("k-1", "결재 한도", "부서 등급으로 정해진다."))
        repo.commit("새 항목")

        s = period.summarize(conn, repo, since=_since(), window_days=30)
        assert len(s.facts) == 1  # 지식 변경 하나뿐
        assert "게재" not in s.facts[0].text
        conn.close()

    def test_기간_밖의_게재는_세지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _published_content(conn, "guide", days_ago=90)
        assert period.publication_fact(conn, _since(), window_days=30) == ""
        conn.close()


# --- 제작 --------------------------------------------------------------------


class TestProduce:
    """할 말이 없으면 내지 않는다."""

    def _ready(self, tmp_path, conn):  # noqa: ANN001, ANN202
        repo = _repo(tmp_path)
        repo.save(_item("k-1", "결재 한도", "부서 등급으로 정해진다."))
        repo.commit("새 항목")
        _published_content(conn, "guide")
        return repo

    def test_기간_요약이_초안에_박힌다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        repo = self._ready(tmp_path, conn)
        harness = FakeHarness(
            _doc("뉴스레터 1호", "이번 달에 지식 항목이 생겼다.", observations=("sum-1",))
        )

        result = _producer(conn, repo, harness).run(NEWSLETTER)

        assert result.outcome is store.Outcome.PRODUCED
        assert "센 사실 2건" in result.detail
        draft = store.pending(conn, "newsletter")[0]
        facts = store.facts_of(draft)
        assert [f.id for f in facts] == ["sum-1", "sum-2"]
        assert facts[0].cited and not facts[1].cited
        conn.close()

    def test_신고하지_않아도_본문이_그_숫자를_적었으면_싣는다(self, tmp_path) -> None:
        # **라이브에서 잡았다.** 본문이 요약 셋을 그대로 옮겨 적고도 신고하지 않아
        # **적힌 숫자의 출처가 근거 목록에서 빠졌다.** 부풀리는 것보다 빠뜨리는 쪽이
        # 나쁘다 — "5건"이라 적혀 있는데 출처가 없으면 근거 없이 나간 것과 같다.
        conn = _conn(tmp_path)
        repo = self._ready(tmp_path, conn)
        _published_content(conn, "guide", days_ago=5)  # sum-2 는 "2건" 이 된다
        _producer(
            conn,
            repo,
            FakeHarness(
                _doc("뉴스레터", "콘텐츠 2건이 게재됐다.", observations=())
            ),
        ).run(NEWSLETTER)

        facts = {f.id: f for f in store.facts_of(store.pending(conn, "newsletter")[0])}
        assert facts["sum-2"].cited  # "2건" 이 sum-2 에만 있다
        assert not facts["sum-1"].cited  # 지식 "1건" 은 본문에 없다
        conn.close()

    def test_여러_사실에_함께_있는_수치는_아무것도_가리키지_않는다(self, tmp_path) -> None:
        # 기간(30)이 모든 문장에 들어간다 — 그것으로 실리게 두면 전부가 실린다.
        conn = _conn(tmp_path)
        repo = self._ready(tmp_path, conn)
        _producer(
            conn,
            repo,
            FakeHarness(_doc("뉴스레터", "지난 30일 동안의 소식입니다.", observations=())),
        ).run(NEWSLETTER)

        facts = store.facts_of(store.pending(conn, "newsletter")[0])
        assert not any(f.cited for f in facts)
        conn.close()

    def test_바뀐_지식이_없으면_회차를_내지_않는다(self, tmp_path) -> None:
        # 발행 면에 빈 회차가 쌓이는 것도 회수할 수 없는 종류다.
        conn = _conn(tmp_path)
        repo = _repo(tmp_path)
        _published_content(conn, "guide")
        harness = FakeHarness(_doc("뉴스레터", "본문"))

        result = _producer(conn, repo, harness).run(NEWSLETTER)

        assert result.outcome is store.Outcome.NO_GROUNDING
        assert harness.prompts == []
        conn.close()

    def test_프롬프트가_센_사실을_번호와_함께_준다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        repo = self._ready(tmp_path, conn)
        material = _producer(conn, repo)._read_input(NEWSLETTER, [])

        prompt = production.build_prompt(
            NEWSLETTER, material.items, None, observations=material.observations
        )
        assert "이번 기간에 센 것:" in prompt
        assert "[sum-1]" in prompt
        assert "아래 요약에 있는 사실만 쓴다" in prompt
        assert "소식이지 논평이 아니다" in prompt
        conn.close()

    def test_칼럼_규칙이_붙지_않는다(self, tmp_path) -> None:
        # 뉴스레터는 권고를 쓰는 자리가 아니다 — 관찰의 조건부 허용도 여기 없다.
        conn = _conn(tmp_path)
        repo = self._ready(tmp_path, conn)
        material = _producer(conn, repo)._read_input(NEWSLETTER, [])

        prompt = production.build_prompt(
            NEWSLETTER, material.items, None, observations=material.observations
        )
        assert "관찰을 생략하고 조언만 남기면" not in prompt
        conn.close()

    def test_밝힌_사실만_나가는_글에_실린다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        repo = self._ready(tmp_path, conn)
        _producer(
            conn,
            repo,
            FakeHarness(_doc("뉴스레터", "지식 항목 1건이 생겼다.", observations=("sum-1",))),
        ).run(NEWSLETTER)
        draft = store.pending(conn, "newsletter")[0]

        body = publication.compose(NEWSLETTER, draft)
        # **뉴스레터에서는 "집계"다** — 같은 자리에 담기지만 관찰(권고의 근거)과
        # 그 기간에 센 것은 검수자가 볼 이유가 다르다.
        assert body.count("집계:") == 1
        assert "관찰:" not in body
        conn.close()
