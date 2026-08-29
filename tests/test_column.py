"""WBS-4.7.2 — 칼럼 (FR-40·41, §7.6).

칼럼은 세 등급으로 갈린다 — **해설**은 허용, **권고**는 관찰을 함께 밝히는 조건으로
허용, **의견**은 금지다. 그 둘이 읽개의 둘과 짝을 이룬다: 해설은 지식베이스에,
권고는 관찰에 기댄다.

여기서 지키는 것은 여섯.

    1. **`both` 는 반쪽으로 돌지 않는다** — 관찰 없이 쓴 권고는 그 순간 의견이다
    2. **관찰은 기간 안에서 센다** — "지난 30일 동안"이 말이 되려면 그 안에서 세야 한다
    3. **한 번은 일화이고 두 번이 관찰이다** — 한 건으로 쓴 조언은 형식만 남는다
    4. **관찰이 없으면 없다고 말한다** — 조용히 빼면 모델이 밝힐 것 없는 권고를 쓴다
    5. **관찰은 초안에 박힌다** — 지금 다시 세면 숫자가 달라져 검수가 대조할 수 없다
    6. **의미 판정은 소견이지 판정이 아니고, 안 돌았으면 안 돌았다고 말한다**
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from agentic_service_desk.content import production, publication, qna_stats, store
from agentic_service_desk.content import review as content_review
from agentic_service_desk.content.registry import load as load_registry
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline.review import Reject

from conftest import FakeHarness

COLUMN = load_registry().get("column")
GUIDE = load_registry().get("guide")


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _ask(conn, qid: str, body: str, *, days_ago: int) -> None:  # noqa: ANN001
    when = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO raw_question (id, title, body, asker_account, created_at, "
        "collected_at) VALUES (?, '', ?, 'user-1', ?, ?)",
        (qid, body, when, when),
    )
    conn.commit()


def _item(item_id: str, title: str, body: str, *, qna: bool = False) -> KnowledgeItem:
    provenance = (
        [Provenance(qna="q-old")] if qna else [Provenance(commit="a" * 40, path="a.py")]
    )
    return KnowledgeItem(
        id=item_id,
        title=title,
        body=body,
        provenance=provenance,
        invalidation=Invalidation(kind=InvalidationKind.LINKED, refs=("a.py",)),
    )


def _repo(tmp_path, *items: KnowledgeItem):  # noqa: ANN001, ANN202
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    for item in items:
        repo.save(item)
    repo.commit("적재")
    return repo


def _producer(conn, repo, harness=None):  # noqa: ANN001, ANN202
    return production.ContentProducer(
        conn=conn, repo=repo, harness=harness, generated_by="test-model"
    )


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


def _observed(conn, times: int = 4, *, days_ago: int = 5) -> None:  # noqa: ANN001
    for i in range(times):
        _ask(conn, f"q-{i}", "결재 반려 사유는 어디서 확인하나요", days_ago=days_ago)


# --- both 읽개 ---------------------------------------------------------------


class TestBothReader:
    """지식베이스와 관찰을 함께 읽는다 (§7.2, §7.6)."""

    def test_지식_항목과_관찰을_함께_준다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _observed(conn)
        repo = _repo(tmp_path, _item("k-1", "결재 반려 사유 코드", "R01 은 증빙 누락이다."))

        material = _producer(conn, repo)._read_input(COLUMN, qna_stats.detect(conn))
        assert [i.id for i in material.items] == ["k-1"]
        assert len(material.observations) == 1
        assert material.observations[0].count == 4
        conn.close()

    def test_QnA_에서_올라온_지식도_해설의_재료다(self, tmp_path) -> None:
        # 가이드는 소스코드 파생 지식만 쓰지만(§7.2), 칼럼의 해설은 "왜 그렇게
        # 정해졌는가"를 다루므로 티켓 해결에서 올라온 항목이 그 자리의 재료다.
        conn = _conn(tmp_path)
        repo = _repo(tmp_path, _item("k-9", "휴가 승인 절차", "팀장이 승인한다.", qna=True))

        material = _producer(conn, repo)._read_input(COLUMN, [])
        assert [i.id for i in material.items] == ["k-9"]
        conn.close()

    def test_기간_밖의_질문은_관찰이_아니다(self, tmp_path) -> None:
        # "지난 30일 동안"이 말이 되려면 그 안에서 세야 한다 (§7.6.2).
        conn = _conn(tmp_path)
        _observed(conn, days_ago=90)
        repo = _repo(tmp_path, _item("k-1", "결재 반려 사유 코드", "R01 은 증빙 누락이다."))

        groups = qna_stats.detect(conn, since=production._window_start(COLUMN))
        material = _producer(conn, repo)._read_input(COLUMN, groups)
        assert material.observations == ()
        conn.close()

    def test_한_번_물은_것은_관찰이_아니다(self, tmp_path) -> None:
        # 한 건은 분포가 아니라 사례다. 그것으로 조언을 쓰면 관찰을 밝힌 형식만 남는다.
        conn = _conn(tmp_path)
        _observed(conn, times=1)
        repo = _repo(tmp_path, _item("k-1", "결재 반려 사유 코드", "R01 은 증빙 누락이다."))

        material = _producer(conn, repo)._read_input(COLUMN, qna_stats.detect(conn))
        assert material.observations == ()
        conn.close()

    def test_FAQ_는_기간_창을_두지_않는다(self) -> None:
        # 살아있는 문서라 오래전부터 반복된 것도 지금 자주 묻히면 여전히 FAQ 다.
        assert production._window_start(load_registry().get("faq")) is None
        assert production._window_start(COLUMN) is not None


# --- 프롬프트 ----------------------------------------------------------------


class TestPrompt:
    """쓸 수 있는 것과 쓸 수 없는 것을 말해 준다."""

    def _material(self, tmp_path, conn, *, observed: bool = True):  # noqa: ANN001, ANN202
        if observed:
            _observed(conn)
        repo = _repo(tmp_path, _item("k-1", "결재 반려 사유 코드", "R01 은 증빙 누락이다."))
        return _producer(conn, repo)._read_input(COLUMN, qna_stats.detect(conn))

    def test_세_등급을_밝힌다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        m = self._material(tmp_path, conn)
        prompt = production.build_prompt(COLUMN, m.items, None, observations=m.observations)

        assert "해설" in prompt and "권고" in prompt and "의견" in prompt
        assert "쓰지 않는다" in prompt
        conn.close()

    def test_관찰을_함께_밝히라고_말한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        m = self._material(tmp_path, conn)
        prompt = production.build_prompt(COLUMN, m.items, None, observations=m.observations)

        assert "관찰을 생략하고 조언만 남기면 그 순간 의견이 된다" in prompt
        assert "4건 있었다" in prompt  # 무엇을 관찰했는지가 목록으로 실린다
        conn.close()

    def test_관찰이_없으면_없다고_말한다(self, tmp_path) -> None:
        # 조용히 빼면 모델은 관찰이 있는지 없는지 모른 채 권고를 쓰고, 그 권고는
        # 관찰을 밝히지 않았으므로 의견이 된다.
        conn = _conn(tmp_path)
        m = self._material(tmp_path, conn, observed=False)
        prompt = production.build_prompt(COLUMN, m.items, None, observations=m.observations)

        assert "권고를 쓰지 않고 해설만 쓴다" in prompt
        conn.close()

    def test_가이드에는_칼럼_규칙이_붙지_않는다(self, tmp_path) -> None:
        # 선언이 정한다 (FR-42).
        conn = _conn(tmp_path)
        prompt = production.build_prompt(GUIDE, [_item("k-1", "가", "나")], None)
        assert "의견" not in prompt
        conn.close()


# --- 관찰이 박힌다 -----------------------------------------------------------


class TestPinnedObservation:
    """발행물은 회수할 수 없다 — 그때 무엇을 세었는지가 남아야 한다."""

    def _produced(self, tmp_path, conn, body: str, observations=("obs-1",)):  # noqa: ANN001, ANN202
        _observed(conn)
        repo = _repo(tmp_path, _item("k-1", "결재 반려 사유 코드", "R01 은 증빙 누락이다."))
        harness = FakeHarness(_doc("칼럼", body, observations=observations))
        return _producer(conn, repo, harness).run(COLUMN)

    def test_관찰이_초안에_박힌다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        result = self._produced(tmp_path, conn, "지난 30일 동안 문의가 4건 있었다.")

        assert result.outcome is store.Outcome.PRODUCED
        assert "센 사실 1건" in result.detail
        draft = store.pending(conn, "column")[0]
        assert draft.observations[0]["count"] == 4
        conn.close()

    def test_관찰의_수치는_근거_없는_수치가_아니다(self, tmp_path) -> None:
        # **핵심이다.** 관찰을 검사에 넣지 않으면 "4건"의 4 가 근거 원문에 없는
        # 수치가 되어 P1 이 FR-41 을 지킨 문장을 지목한다.
        conn = _conn(tmp_path)
        self._produced(tmp_path, conn, "지난 30일 동안 이 문의가 4건 있었다.")
        draft = store.pending(conn, "column")[0]

        findings = content_review.inspect(
            COLUMN,
            draft,
            source_text={"k-1": "결재 반려 사유 코드\n\nR01 은 증빙 누락이다."},
        )
        assert not [f for f in findings.items if f.reason is Reject.P1]
        conn.close()

    def test_관찰에_없는_수치는_여전히_걸린다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._produced(tmp_path, conn, "지난 30일 동안 이 문의가 77건 있었다.")
        draft = store.pending(conn, "column")[0]

        findings = content_review.inspect(
            COLUMN,
            draft,
            source_text={"k-1": "결재 반려 사유 코드\n\nR01 은 증빙 누락이다."},
        )
        assert [f for f in findings.items if f.reason is Reject.P1]
        conn.close()

    def test_나간_글에도_밝힌_관찰이_함께_실린다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        self._produced(tmp_path, conn, "지난 30일 동안 이 문의가 4건 있었다.")
        draft = store.pending(conn, "column")[0]

        body = publication.compose(COLUMN, draft)
        assert publication.ATTRIBUTION in body
        assert "관찰: 지난 30일 동안" in body
        conn.close()

    def test_쓰지_않은_관찰은_근거_목록에_실리지_않는다(self, tmp_path) -> None:
        # **라이브에서 잡았다.** 본 관찰을 전부 실었더니 글이 다루지도 않은 관찰이
        # 근거로 붙었다 — 읽는 사람은 그 조언이 그것에 기댄 줄로 읽고, 검수자도
        # 밝혀진 줄로 본다 (§5.6.1).
        conn = _conn(tmp_path)
        self._produced(tmp_path, conn, "반려 사유는 코드로 기록된다.", observations=())
        draft = store.pending(conn, "column")[0]

        # 검수는 전부를 본다 — 지어낸 관찰을 가려내려면 그래야 한다.
        assert draft.observations
        assert "관찰:" not in publication.compose(COLUMN, draft)
        conn.close()

    def test_목록에_없는_관찰_번호는_버린다(self) -> None:
        doc = production.parse_document(
            _doc("칼럼", "본문", observations=("obs-1", "obs-99")),
            {"k-1"},
            allowed_observations={"obs-1"},
        )
        assert doc.cited == ("obs-1",)


# --- 의미 판정 ---------------------------------------------------------------


class TestSemantic:
    """P6·P7 은 모델이 보고, 그 결과는 소견이지 판정이 아니다."""

    def _pending(self, tmp_path, conn, body: str):  # noqa: ANN001, ANN202
        _observed(conn)
        repo = _repo(tmp_path, _item("k-1", "결재 반려 사유 코드", "R01 은 증빙 누락이다."))
        _producer(conn, repo, FakeHarness(_doc("칼럼", body))).run(COLUMN)
        return store.pending(conn, "column")[0]

    def test_선언이_의미_판정_대상을_정한다(self) -> None:
        assert content_review.needs_semantic(COLUMN)
        assert not content_review.needs_semantic(GUIDE)

    def test_아직_안_돌았으면_안_돌았다고_말한다(self, tmp_path) -> None:
        # "봤는데 없다"와 섞으면 판정이 돌지 않은 초안이 통과한 것처럼 보인다.
        conn = _conn(tmp_path)
        draft = self._pending(tmp_path, conn, "반려 사유는 코드로 기록된다.")

        findings = content_review.inspect(COLUMN, draft, source_text={})
        assert findings.pending_semantic
        assert "아직 돌지 않았다" in findings.look_here_first
        conn.close()

    def test_소견이_박히고_화면_소견에_얹힌다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = self._pending(tmp_path, conn, "이 방식이 더 낫습니다.")
        harness = FakeHarness(
            json.dumps(
                {
                    "findings": [
                        {"reason": "P6", "quote": "이 방식이 더 낫습니다", "detail": "가치 판단이다"}
                    ]
                },
                ensure_ascii=False,
            )
        )

        content_review.inspect_semantically(
            conn, COLUMN, draft, harness=harness, source_text={}
        )
        again = store.get(conn, draft.id)
        findings = content_review.inspect(COLUMN, again, source_text={})

        assert not findings.pending_semantic
        assert any(f.reason is Reject.P6 for f in findings.items)
        assert "이 방식이 더 낫습니다" in findings.look_here_first
        conn.close()

    def test_소견이_없어도_봤다는_사실은_남는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        draft = self._pending(tmp_path, conn, "반려 사유는 코드로 기록된다.")

        content_review.inspect_semantically(
            conn,
            COLUMN,
            draft,
            harness=FakeHarness(json.dumps({"findings": []})),
            source_text={},
        )
        again = store.get(conn, draft.id)
        assert again.agent_findings == ()
        assert not content_review.inspect(COLUMN, again, source_text={}).pending_semantic
        conn.close()

    def test_판정에_실패하면_박지_않는다(self, tmp_path) -> None:
        # 빈 소견으로 박으면 "봤는데 없다"가 되어 돌지 않은 판정이 통과한 것처럼 보인다.
        conn = _conn(tmp_path)
        draft = self._pending(tmp_path, conn, "반려 사유는 코드로 기록된다.")

        result = content_review.inspect_semantically(
            conn, COLUMN, draft, harness=FakeHarness("JSON 이 아니다"), source_text={}
        )
        assert result is None
        assert store.get(conn, draft.id).agent_findings is None
        conn.close()

    def test_프롬프트에_관찰이_함께_간다(self, tmp_path) -> None:
        # FR-41 의 "관찰을 함께 밝혔는가"는 초안만 봐서는 판정할 수 없다.
        conn = _conn(tmp_path)
        draft = self._pending(tmp_path, conn, "반려 사유는 코드로 기록된다.")

        prompt = content_review.build_semantic_prompt(draft, {"k-1": "원문"})
        assert "obs-1" in prompt
        assert "4건 있었다" in prompt
        assert "관찰 없는 권고" in prompt
        conn.close()

    def test_모르는_사유는_사유_없는_소견으로_남는다(self) -> None:
        # 여기서 나오는 것은 반려가 아니라 지목이므로, 분류가 안 되어도
        # "여기를 보라"는 값은 남는다.
        parsed = content_review.parse_semantic(
            json.dumps({"findings": [{"reason": "P9", "detail": "무언가"}]})
        )
        assert parsed == [{"reason": "", "detail": "무언가"}]
