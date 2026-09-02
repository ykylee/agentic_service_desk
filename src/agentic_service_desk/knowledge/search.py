"""지식베이스 조회 — 파이프라인 2단계의 엔진 (ADR-004, O31).

**검색 품질이 곧 커버리지다.** 근거를 찾지 못하면 답변이 만들어지지 않고 티켓으로
가므로(FR-18), 못 찾는 것과 없는 것이 결과적으로 같아진다.

설계를 좌우한 조건이 둘이다.

| 조건 | 함의 |
|---|---|
| 항목 수가 **수백~수천** (ADR-003) | 전수 임베딩이 부담되지 않는다 |
| **어휘 격차** (§2.5.4) — 이용자는 "결재", 코드는 `approval` | 키워드만으로는 못 찾는다 |

그래서 셋을 합친다 — **키워드 · 표현 사전 · 임베딩**, 그리고 LLM 재랭킹.

## 없는 것에도 대비한다

임베딩과 LLM 은 **있으면 쓰고 없으면 건너뛴다.** 개발 환경에서 임베딩이 레이트
리밋으로 막혀 있고(O57), 1국면에는 표현 사전이 아예 비어 있다(§1.3). 어느 하나가
빠졌다고 검색이 서면 그 환경에서는 아무것도 시험할 수 없다.

**표현 사전이 비어 커버리지가 낮은 것은 고장이 아니라 정상이다** (D14) — QnA 가
쌓여야 좋아지는 인덱스이기 때문이다.

## 언어별 도구를 쓰지 않는다

ADR-003 의 제약 1 이 여기에도 걸린다. 한국어 조사("결재를" vs "결재")를 스테머로
떼지 않고 **양방향 부분 일치**로 넘는다 — 질의 토큰이 문서 토큰을 품거나 그 반대면
맞은 것으로 본다. 형태소 분석기를 들이면 언어 수만큼 도구가 늘어난다.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agentic_service_desk.knowledge.item import KnowledgeItem
from agentic_service_desk.knowledge.repository import KnowledgeRepository, StoredItem

MIN_TOKEN = 2
"""이보다 짧은 토큰은 버린다. 한 글자는 어디에나 맞아 순위를 무너뜨린다."""

TITLE_WEIGHT = 3.0
"""제목에서 맞은 것은 본문에서 맞은 것보다 무겁다 — 제목이 곧 개념의 이름이다."""

VOCAB_WEIGHT = 2.0
"""표현 사전으로 맞은 것의 가중치. **QnA 원천의 고유 기여다** (§2.5.4)."""

MIN_VOCAB_MATCHES = 2
"""표현 사전이 걸리려면 이만큼은 맞아야 한다.

**한 낱말이 맞은 것은 개념이 같다는 증거가 아니다.** 질문에는 개념을 부르는 말만
있는 것이 아니라 질문의 틀("어떻게", "하나요")도 있어서, 하나만으로 걸리게 두면
아무 질문이나 아무 항목에 붙는다 — 라이브에서 "VPN 접속이 안 되는데 어떻게
하나요"가 "결재 한도 결정 규칙"에 걸렸고 이유가 "어떻게" 하나였다.

실제로 어휘 격차를 넘는 질의는 여러 낱말이 함께 맞는다 — "결재 반려 사유"는
셋이 맞는다. 둘을 요구하면 틀만 맞는 경우가 걸러지고 진짜 대응은 살아남는다.
"""

EMBEDDING_WEIGHT = 4.0
"""임베딩 유사도의 가중치. 표현이 달라도 같은 개념을 잡는다."""

_TOKEN = re.compile(r"[0-9A-Za-z_가-힣]+")


def tokenize(text: str) -> list[str]:
    """언어 중립 토큰화. **스테머도 형태소 분석기도 쓰지 않는다.**"""
    return [t.lower() for t in _TOKEN.findall(text) if len(t) >= MIN_TOKEN]


def _matches(query_token: str, doc_tokens: set[str]) -> bool:
    """조사·어미 변형을 **접두** 일치로 넘는다.

    양방향인 이유가 있다 — 질의가 "결재를"이고 문서가 "결재"일 수도, 그 반대일 수도
    있다. 한쪽만 보면 절반을 놓친다.

    **접두인 이유는 조사·어미가 뒤에 붙기 때문이다.** 넘으려는 것이 "결재/결재를"
    같은 변형이므로 어간이 앞에 온다는 것만 가정하면 충분하고, 그 이상을 받으면
    관계없는 낱말이 서로를 품는다. 2026-09-02 실지식베이스(185항목) 실측:
    아무 데나 걸리던 쌍은 **("백테스트", "테스트") · ("취소되지", "되지") ·
    ("한도는", "도는") 셋**이었고, 이 중 첫째가 "백테스트 결과는 어떻게 보나요"를
    `rotate_keys 테스트 시간 폭탄 fixture` 에 **제목 가중치 3.0** 으로 붙였다.
    제목에서 맞은 것으로 세어지므로 우연한 한 글자 겹침이 개념 이름의 일치와
    같은 무게를 받는다 — 그리고 조회에는 점수 하한이 없어(`find` 의 `if not hits`)
    그 한 건이 그대로 생성 단계로 간다.

    접두로 좁혀도 **정상 질의의 최상위는 그대로였다** — "주문 체결이 안 되는데요"
    6.0, "전략 시그널은 어떻게 만들어지나요" 4.0, "리스크 한도는 어떻게
    정해지나요" 3.0 이 모두 같은 항목을 같은 점수로 유지했다.

    한편 **낱말 하나만 맞은 것을 버리는 방법은 택하지 않았다.** 표현 사전이 쓰는
    `MIN_VOCAB_MATCHES` 와 같은 규칙인데, 키워드 다리에 얹으면 실측에서 정상
    질의가 죽는다 — 단일 낱말 질의("가드")가 근거 0건이 되고, "리스크 한도는…"의
    정답(`결재 한도 결정 규칙`, 3.0)이 관계없는 2.0 항목에 밀린다. 키워드 다리는
    **식별자 하나로 묻는 것**을 받아야 하는 자리라 표현 사전과 전제가 다르다.
    """
    if query_token in doc_tokens:
        return True
    return any(
        query_token.startswith(d) or d.startswith(query_token)
        for d in doc_tokens
        if len(d) >= MIN_TOKEN
    )


@dataclass
class Hit:
    """찾은 항목 하나."""

    item: KnowledgeItem
    path: Path
    score: float = 0.0
    matched_by: set[str] = field(default_factory=set)
    """`keyword` | `vocabulary` | `embedding`. **왜 걸렸는지가 재랭킹의 재료다.**"""

    @property
    def is_stale(self) -> bool:
        return self.item.stale


class VocabularyIndex:
    """사용자 표현 → 지식 항목 (ADR-004, §2.5.4).

    **소스코드는 개념이 무엇인지를 주고, QnA 는 이용자가 그것을 뭐라고 부르는지를
    준다.** 두 원천이 필요한 이유가 검색에서 한 번 더 드러나는 지점이다.

    별도의 추출 에이전트를 두지 않았다. **연결은 이미 있다** — QnA 에서 온 지식
    항목은 `provenance.qna` 로 원래 질문을 가리키므로, 그 질문의 표현이 곧 그 개념을
    부르는 말이다. 없는 장치를 만드는 대신 있는 것을 읽는다.

    1국면에는 비어 있고 **그것이 정상이다** (D14).
    """

    def __init__(self, by_item: dict[str, set[str]]) -> None:
        self._by_item = by_item

    def __len__(self) -> int:
        return sum(1 for terms in self._by_item.values() if terms)

    def terms_for(self, item_id: str) -> set[str]:
        return self._by_item.get(item_id, set())

    @classmethod
    def build(cls, conn: sqlite3.Connection, items: list[StoredItem]) -> VocabularyIndex:
        questions = _question_text_by_qna(conn)
        by_item: dict[str, set[str]] = {}
        for stored in items:
            terms: set[str] = set()
            for p in stored.item.provenance:
                if p.qna and p.qna in questions:
                    terms |= set(tokenize(questions[p.qna]))
            if terms:
                by_item[stored.item.id] = terms
        return cls(_drop_indistinct(by_item))


def _drop_indistinct(by_item: dict[str, set[str]]) -> dict[str, set[str]]:
    """여러 항목을 함께 가리키는 말을 버린다.

    질문에는 개념을 부르는 말만 있는 것이 아니라 **질문의 틀**도 있다 — "어떻게",
    "무엇", "하나요". 그런 말은 어느 개념에나 붙어서, 남겨 두면 아무 질문이나 아무
    항목에 걸린다.

    불용어 목록을 쓰지 않는다 — 언어마다 따로 만들어야 해서 ADR-003 제약 1 이
    깨진다. 대신 **몇 개 항목을 가리키는가**로 판정한다: 절반 넘는 항목을 가리키는
    말은 그 개념의 이름이 아니다. 언어를 몰라도 셀 수 있고 **QnA 가 쌓일수록
    정확해진다.**

    다만 항목이 몇 개뿐인 1국면에는 이 셈이 힘을 못 쓴다 — 틀 낱말이 아직 한 항목만
    가리키기 때문이다. 그 구간은 `MIN_VOCAB_MATCHES` 가 받친다.
    """
    if len(by_item) < 2:
        return by_item  # 셀 것이 없다. 한 항목뿐이면 모든 말이 그것만 가리킨다
    frequency: dict[str, int] = {}
    for terms in by_item.values():
        for term in terms:
            frequency[term] = frequency.get(term, 0) + 1
    limit = len(by_item) / 2
    return {
        item_id: {t for t in terms if frequency[t] <= limit}
        for item_id, terms in by_item.items()
    }


def _question_text_by_qna(conn: sqlite3.Connection) -> dict[str, str]:
    """QnA 식별자 → 질문 원문.

    원천이 둘이다 — 모 시스템에서 온 것과 담당자가 직접 등록한 것. 후자도 **이용자가
    쓴 말**이므로 표현 사전에 들어가야 한다.
    """
    texts: dict[str, str] = {}
    for row in conn.execute("SELECT id, title, body FROM raw_question"):
        texts[row["id"]] = f"{row['title'] or ''} {row['body']}"
    for row in conn.execute("SELECT qna_item_id, question FROM manual_entry"):
        texts[row["qna_item_id"]] = row["question"]
    return texts


class Search:
    """지식베이스를 찾는다."""

    def __init__(
        self,
        *,
        repo: KnowledgeRepository,
        conn: sqlite3.Connection,
        embeddings=None,  # noqa: ANN001 — EmbeddingProvider. 없으면 그 다리를 건너뛴다
        embedding_model: str = "",
    ) -> None:
        self._repo = repo
        self._conn = conn
        self._embeddings = embeddings
        self._embedding_model = embedding_model

    def find(self, query: str, *, limit: int = 5, include_stale: bool = True) -> list[Hit]:
        """질문으로 근거 후보를 찾는다.

        **못 찾으면 빈 목록이다.** 억지로 채우지 않는다 — 근거가 없으면 답을 만들지
        않고 티켓으로 보내는 것이 규칙이기 때문이다 (FR-18). 낮은 점수의 항목을
        끼워 넣으면 그 규칙이 무력해진다.
        """
        tokens = tokenize(query)
        if not tokens:
            return []

        items, _ = self._repo.scan()
        if not items:
            return []

        hits: dict[str, Hit] = {}
        self._by_keyword(tokens, items, hits)
        self._by_vocabulary(tokens, items, hits)
        self._by_embedding(query, items, hits)

        found = [h for h in hits.values() if h.score > 0]
        if not include_stale:
            found = [h for h in found if not h.is_stale]
        found.sort(key=lambda h: (-h.score, h.item.title))
        return found[:limit]

    # --- 키워드 ----------------------------------------------------------

    def _by_keyword(
        self, tokens: list[str], items: list[StoredItem], hits: dict[str, Hit]
    ) -> None:
        """정확한 식별자·에러 메시지에 강하다.

        개발자가 코드 용어로 물을 때 이 다리가 잡는다 — 임베딩만 쓰면 놓치는 쪽이다.
        """
        for stored in items:
            title_tokens = set(tokenize(stored.item.title))
            body_tokens = set(tokenize(stored.item.body))
            score = 0.0
            for token in set(tokens):
                if _matches(token, title_tokens):
                    score += TITLE_WEIGHT
                elif _matches(token, body_tokens):
                    score += 1.0
            if score:
                self._add(hits, stored, score, "keyword")

    # --- 표현 사전 --------------------------------------------------------

    def _by_vocabulary(
        self, tokens: list[str], items: list[StoredItem], hits: dict[str, Hit]
    ) -> None:
        """이용자가 그 개념을 뭐라고 부르는지로 찾는다 (§2.5.4)."""
        index = VocabularyIndex.build(self._conn, items)
        if not len(index):
            return  # 1국면에는 비어 있다. 고장이 아니다 (D14)
        for stored in items:
            terms = index.terms_for(stored.item.id)
            if not terms:
                continue
            matched = sum(1 for t in set(tokens) if _matches(t, terms))
            if matched >= MIN_VOCAB_MATCHES:
                self._add(hits, stored, VOCAB_WEIGHT * matched, "vocabulary")

    # --- 임베딩 -----------------------------------------------------------

    def _by_embedding(
        self, query: str, items: list[StoredItem], hits: dict[str, Hit]
    ) -> None:
        """표현이 다른 같은 개념을 잡는다.

        **없으면 건너뛴다.** 개발 환경에서 이 경로가 막혀 있고(O57), 서면 아무것도
        시험할 수 없다. 인덱스가 비어 있어도 마찬가지다 — 아직 안 만든 것이지
        틀린 것이 아니다.
        """
        if self._embeddings is None:
            return
        vectors = stored_vectors(self._conn)
        if not vectors:
            return
        try:
            from agentic_service_desk.llm.gateway import EmbeddingPurpose

            probe = self._embeddings.embed([query], EmbeddingPurpose.QUERY)[0]
        except Exception:  # noqa: BLE001 — 제공자가 어떤 오류를 낼지 우리가 정하지 않는다
            # 임베딩이 막혀도 키워드·표현 사전은 살아 있다. 검색 전체를 세우지 않는다.
            return
        for stored in items:
            vector = vectors.get(stored.item.id)
            if not vector:
                continue
            similarity = cosine(probe, vector)
            if similarity > 0:
                self._add(hits, stored, EMBEDDING_WEIGHT * similarity, "embedding")

    # --- 공통 ------------------------------------------------------------

    @staticmethod
    def _add(hits: dict[str, Hit], stored: StoredItem, score: float, how: str) -> None:
        hit = hits.get(stored.item.id)
        if hit is None:
            hit = Hit(item=stored.item, path=stored.path)
            hits[stored.item.id] = hit
        hit.score += score
        hit.matched_by.add(how)


# --- 임베딩 인덱스 ---------------------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return (dot / (na * nb)) if na and nb else 0.0


def stored_vectors(conn: sqlite3.Connection) -> dict[str, list[float]]:
    return {
        row["item_id"]: json.loads(row["vector"])
        for row in conn.execute("SELECT item_id, vector FROM knowledge_embedding")
    }


def rebuild_embedding_index(
    conn: sqlite3.Connection,
    repo: KnowledgeRepository,
    embeddings,  # noqa: ANN001 — EmbeddingProvider
    model: str,
) -> int:
    """인덱스를 **통째로** 다시 만든다 (ADR-004).

    증분 인덱싱의 복잡도를 지금 떠안지 않는다 — 항목이 수백~수천이라 전수가 부담되지
    않고, 부분 갱신은 "어느 항목이 낡은 벡터를 들고 있는가"를 계속 추적해야 한다.

    돌려주는 것은 색인한 항목 수다. 실패하면 예외가 그대로 올라간다 — 조용히 빈
    인덱스를 남기면 **검색이 임베딩 다리 없이 도는 것과 구분되지 않는다.**
    """
    from agentic_service_desk.llm.gateway import EmbeddingPurpose

    items, _ = repo.scan()
    if not items:
        return 0
    texts = [f"{s.item.title}\n\n{s.item.body}" for s in items]
    vectors = embeddings.embed(texts, EmbeddingPurpose.INDEX)

    now = datetime.now(UTC).isoformat()
    conn.execute("DELETE FROM knowledge_embedding")
    conn.executemany(
        "INSERT INTO knowledge_embedding (item_id, vector, model, built_at) "
        "VALUES (?, ?, ?, ?)",
        [
            (s.item.id, json.dumps(v), model, now)
            for s, v in zip(items, vectors, strict=True)
        ],
    )
    conn.commit()
    return len(items)


def rerank(hits: list[Hit], query: str, harness) -> list[Hit]:  # noqa: ANN001
    """LLM 이 후보를 다시 줄 세운다 (ADR-004 3단계).

    **없으면 원래 순서를 그대로 쓴다.** 재랭킹은 순위를 좋게 만드는 장치이지 결과를
    만들어 내는 장치가 아니므로, 빠져도 검색은 성립한다.

    모델이 목록에 없는 id 를 지어내면 무시한다 — 지어낸 근거가 답변에 실리는 것이
    §2.2.3 이 경계한 바로 그 고장이다.
    """
    if harness is None or len(hits) < 2:
        return hits
    listing = "\n".join(f"- {h.item.id}: {h.item.title}" for h in hits)
    prompt = (
        "질문에 답하는 데 실제로 쓸 수 있는 지식 항목만 골라 **유용한 순서대로** id 를 "
        "나열한다. 쓸 수 없는 것은 빼고, 목록에 없는 id 를 만들지 않는다.\n"
        'JSON 하나만 낸다: {"ids": ["k-...", "k-..."]}\n\n'
        f"질문: {query}\n\n후보:\n{listing}"
    )
    try:
        from agentic_service_desk.ingest.agent import extract_json

        ordered = extract_json(harness.run(prompt).text).get("ids") or []
    except Exception:  # noqa: BLE001
        return hits

    by_id = {h.item.id: h for h in hits}
    picked = [by_id[i] for i in ordered if i in by_id]
    # 모델이 빠뜨린 것은 뒤에 붙인다. 재랭킹이 후보를 **버리는** 장치가 되면
    # 근거가 있는데 없다고 판정되어 FR-18 이 잘못 발동한다.
    picked += [h for h in hits if h not in picked]
    return picked
