"""운영 DB 스키마 (ADR-002).

**컨셉의 결정이 열(column)로 드러나게** 썼다. 어느 필드가 왜 있는지는 주석이 밝힌다.

가장 중요한 것 넷.
    - `qna_item` 과 `ticket` 은 **별개 테이블**이며 상태가 서로를 결정하지 않는다 (D15)
    - `answer_grounding` 이 **근거 버전을 고정**한다 — 링크가 아니라 커밋 해시다 (D20)
    - `contradiction` 이 **에이전트의 진 쪽 주장을 보관**한다 (FR-6) — 덮어쓰지 않는 것과
      없던 일로 하는 것은 다르다
    - `raw_*` 는 **Raw Layer** 다 (FR-52). 수집된 원문이며 질의 대상이 아니라 ingest 입력이다.
      운영 테이블과 한 파일에 있지만 성격이 다르다 — 이쪽만 보존 기간이 걸린다 (PO-4)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
-- QnA 항목 — 대외 관점. "이용자에게 이 질문이 어떻게 되었는가" (D15)
CREATE TABLE IF NOT EXISTS qna_item (
    id                TEXT PRIMARY KEY,
    parent_question_id TEXT NOT NULL UNIQUE,  -- 모 시스템의 질문 id
    asker_id          TEXT,                   -- 사내 식별자. 지식 항목에는 넘어가지 않는다 (PO-3)
    state             TEXT NOT NULL,          -- 접수 | 게재됨 | 후속진행 | 사람대기 | 해결 | 미해결종료
    resolution_grade  TEXT,                   -- explicit | implicit — ingest 자격을 가른다 (D8)
    language          TEXT,                   -- 1단계에서 판정 (D53)
    opened_at         TEXT NOT NULL,
    closed_at         TEXT
);

-- 티켓 — 내부 관점. "우리에게 무슨 일이 남았는가" (D15)
-- 모든 QnA 가 티켓을 발행한다 (D19). 자동 처리분은 발행 즉시 auto_closed 다.
CREATE TABLE IF NOT EXISTS ticket (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,   -- qna | content | contradiction | correction (§6.4.3)
    qna_item_id TEXT,            -- QnA 유래일 때만. 없어도 된다 — 티켓 출처는 넷이다
    state       TEXT NOT NULL,   -- auto_closed | open | in_progress | held | closed (D33)
    opened_at   TEXT NOT NULL,   -- 경과 시간의 기준. SLA 는 두지 않는다 (D30)
    state_at    TEXT NOT NULL,   -- 지금 상태가 된 시각. **보류 해제 판정이 이것에 걸린다**
                                 -- (§6.7.1) — 보류로 바꾸기 *전에* 온 후속은 기다리던
                                 -- 응답이 아니다. opened_at 으로 재면 그것까지 센다
    closed_at   TEXT,
    FOREIGN KEY (qna_item_id) REFERENCES qna_item (id)
);

-- 종결 기록 — 지식 항목의 초안이다 (D18). 승격을 번역이 아니라 승인으로 만든다.
--
-- **`invalidation` 이 NULL 을 허용하는 것이 설계다** (§5.6.4). 에이전트가 채운 초안은
-- 이 칸을 비운 채로 저장되고, 사람이 채우기 전까지 티켓이 닫히지 않는다. NOT NULL 로
-- 두면 초안 자체를 저장할 수 없어 "비워 둔 칸"이라는 장치가 성립하지 않는다.
CREATE TABLE IF NOT EXISTS ticket_resolution (
    ticket_id           TEXT PRIMARY KEY,
    generalized_question TEXT NOT NULL,  -- 개인·상황 요소를 걷어낸 형태 (PO-3 를 여기서 집행한다)
    answer              TEXT NOT NULL,
    grounding           TEXT NOT NULL,   -- JSON — 근거 목록. 비어 있으면 초안이 아니다 (D3)
    invalidation        TEXT,            -- JSON. **비어 있는 것이 초안의 정상 상태다** (§5.6.4)
    invalidation_candidates TEXT,        -- JSON. 에이전트의 제안. **선택은 사람이 한다**
    cause               TEXT,
    scope               TEXT,
    recurrence          TEXT,
    drafted_by          TEXT NOT NULL DEFAULT 'agent',  -- agent | human
    confirmed_at        TEXT,            -- 사람이 무효화 조건을 채운 시각
    FOREIGN KEY (ticket_id) REFERENCES ticket (id)
);

-- 답변 이력 — 게재한 것과 그 근거 (D20)
-- 답변 본문은 모 시스템에 있지만 **무엇을 근거로 만들어졌는지는 우리만 안다.**
CREATE TABLE IF NOT EXISTS answer_record (
    id                TEXT PRIMARY KEY,
    qna_item_id       TEXT NOT NULL,
    parent_answer_id  TEXT,            -- 모 시스템에서 받은 게재 id. 정정(XR-7)에 쓴다
    body              TEXT NOT NULL,
    author_kind       TEXT NOT NULL,   -- bot | human — 되먹임 차단의 판정 근거 (D7)
    generated_by      TEXT,            -- 모델 식별자. 모델 교체 추적 (ADR-005)
    review_outcome    TEXT,            -- passed | rejected
    review_reason     TEXT,            -- P1~P8 — 사유별 분포가 신뢰 지표다 (§5.5.6)
    published_at      TEXT,
    corrected_at      TEXT,            -- 정정 시각 (PO-1)
    FOREIGN KEY (qna_item_id) REFERENCES qna_item (id)
);

-- 근거 버전 고정 (D20) — 이 표가 stale 전파의 배선이다.
-- 링크만 두면 지식이 갱신된 뒤 "당시 무엇을 근거로 답했는지"가 사라진다.
CREATE TABLE IF NOT EXISTS answer_grounding (
    answer_record_id  TEXT NOT NULL,
    knowledge_item_id TEXT NOT NULL,   -- 경로가 아니라 불변 id (ADR-002)
    pinned_commit     TEXT NOT NULL,   -- 답변 시점의 버전. 이것이 고정이다
    PRIMARY KEY (answer_record_id, knowledge_item_id),
    FOREIGN KEY (answer_record_id) REFERENCES answer_record (id)
);

-- 콘텐츠 발행 이력 (§7)
CREATE TABLE IF NOT EXISTS content_publication (
    id           TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,   -- faq | guide | column | newsletter
    nature       TEXT NOT NULL,   -- living | issued — 갱신인가 회차인가 (§7.3)
    destination  TEXT NOT NULL,   -- doc_surface | publication_surface (D46)
    path         TEXT,            -- 살아있는 문서의 자리
    published_at TEXT NOT NULL
);

-- ─── Raw Layer — QnA 원문 (FR-52) ────────────────────────────────────────
-- **수집된 그대로**를 담는다. 판정하지 않고, 해석하지 않고, 걸러내지 않는다.
-- 걸러내는 것은 ingest 입구의 산출물 필터 하나뿐이다 (NFR-4) — 여기서 미리
-- 버리면 **통계와 FAQ 후보까지 함께 사라진다** (§5.3). 지식으로 삼지 않는 것과
-- 기록하지 않는 것은 다르다.
--
-- 보존 기간이 걸리는 곳도 여기다 (PO-4 · FR-51). 지식은 남기고 원본은 만료시킨다.

CREATE TABLE IF NOT EXISTS raw_question (
    id           TEXT PRIMARY KEY,  -- 모 시스템의 질문 id. 우리가 부여하지 않는다
    title        TEXT,
    body         TEXT NOT NULL,
    asker_account TEXT NOT NULL,    -- 사내 식별자. 지식·콘텐츠로 넘어가지 않는다 (PO-3)
    created_at   TEXT NOT NULL,     -- 모 시스템 기준 시각. QnA 커서가 이 값을 따른다
    collected_at TEXT NOT NULL      -- 우리가 가져온 시각. 보존 만료의 기준이다 (FR-51)
);

-- 답변. **`author_account` 가 이 표에서 가장 중요한 열이다** (D7).
-- 없으면 봇과 사람을 가릴 수 없고, 그러면 §5.3 되먹임 차단이 통째로 무너진다.
-- NOT NULL 로 둔 것이 그 선언이다 — 계정을 모르는 답변은 적재 자체를 거부한다.
CREATE TABLE IF NOT EXISTS raw_answer (
    id             TEXT PRIMARY KEY,
    question_id    TEXT NOT NULL,
    body           TEXT NOT NULL,
    author_account TEXT NOT NULL,   -- 봇/사람 판정의 유일한 근거 (D7)
    created_at     TEXT NOT NULL,
    revised_at     TEXT,            -- 정정된 적이 있는가 (PO-1)
    collected_at   TEXT NOT NULL,
    FOREIGN KEY (question_id) REFERENCES raw_question (id)
);

-- 후속 답글. 답변과 나눠 두는 이유는 **의미가 다르기** 때문이다 —
-- 후속은 파이프라인 재실행의 트리거이고(D9), 답변은 우리가 만든 산출물이다.
CREATE TABLE IF NOT EXISTS raw_followup (
    id             TEXT PRIMARY KEY,
    question_id    TEXT NOT NULL,
    body           TEXT NOT NULL,
    author_account TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    collected_at   TEXT NOT NULL,
    FOREIGN KEY (question_id) REFERENCES raw_question (id)
);

-- 해결 표시와 그 **등급** (D8, §5.3.1).
-- 등급이 ingest 자격을 가르므로 해결 사실과 함께 반드시 남는다.
-- `qna_item.resolution_grade` 와 값이 겹쳐 보이지만 **출처가 다르다** — 저쪽은
-- 우리가 추적하는 상태이고, 이쪽은 **모 시스템이 알려준 사실**이다. 둘을 한 열로
-- 합치면 우리 판정(암묵적 해결 타임아웃)이 원문을 덮어쓰게 된다.
CREATE TABLE IF NOT EXISTS raw_resolution (
    question_id  TEXT PRIMARY KEY,
    resolved     INTEGER NOT NULL,  -- 0 | 1
    grade        TEXT,              -- explicit | implicit. 미해결이면 NULL (D8)
    method       TEXT,              -- user_marked | operator_closed (§5.3.1-1)
    resolved_by  TEXT,
    resolved_at  TEXT,
    collected_at TEXT NOT NULL,
    FOREIGN KEY (question_id) REFERENCES raw_question (id)
);

-- 모순 — 에이전트의 판단이 사람이 고친 항목과 어긋났다 (FR-6, D38).
-- **에이전트의 판단을 버리지 않는다.** 덮어쓰지 않는 것과 없던 일로 하는 것은 다르다 —
-- 버리면 사람이 무엇과 어긋났는지 볼 수 없어 판정 자체가 불가능해진다.
-- 사람 쪽은 지식 파일에 그대로 있고, 에이전트 쪽이 여기 남는다. 그것이 "양쪽을 남긴다"다.
--
-- 지식 파일이 아니라 여기에 두는 이유: 파일에 넣으면 **다음 ingest 가 그 대립 주장을
-- 본문으로 읽는다.** 원천이 아닌 것이 원천처럼 되돌아오는 경로를 만들지 않는다.
CREATE TABLE IF NOT EXISTS contradiction (
    id                TEXT PRIMARY KEY,
    knowledge_item_id TEXT NOT NULL,   -- 경로가 아니라 불변 id (ADR-002)
    ticket_id         TEXT,            -- Q4 대기열의 자리 (source=contradiction)
    proposed_title    TEXT NOT NULL,   -- 에이전트가 주장한 쪽
    proposed_body     TEXT NOT NULL,
    provenance        TEXT NOT NULL,   -- JSON — 그 주장의 근거
    detected_at       TEXT NOT NULL,
    state             TEXT NOT NULL,   -- open | resolved
    resolution        TEXT,            -- kept_human | took_agent | merged
    resolved_at       TEXT
);

-- Lint 소견 (FR-7).
-- **주기 실행이라 같은 소견이 매번 나온다.** 열쇠(kind + 대상)로 한 번만 열고,
-- 사람이 닫기 전까지 다시 열지 않는다 — 그러지 않으면 대기열이 같은 항목으로
-- 메워져 우선순위를 매길 수 없게 된다 (§8.6).
CREATE TABLE IF NOT EXISTS lint_finding (
    key        TEXT PRIMARY KEY,  -- kind + 대상. 같은 소견을 다시 열지 않기 위한 열쇠
    kind       TEXT NOT NULL,     -- broken_link | missing_reference
    subject    TEXT NOT NULL,     -- 지식 항목 id 또는 답변 이력 id
    detail     TEXT NOT NULL,
    ticket_id  TEXT,              -- Q5 대기열의 자리 (source=correction)
    first_seen TEXT NOT NULL,
    state      TEXT NOT NULL,     -- open | resolved
    resolved_at TEXT
);

-- 이미 ingest 한 답변 (FR-5 증분).
-- **커서가 아니라 목록인 이유가 있다.** 답변은 만들어진 지 한참 뒤에 ingest 자격을
-- 얻을 수 있다 — 이용자가 나중에 해결 표시를 누르면 그렇게 된다(§5.3.2). 시각
-- 커서를 쓰면 그 답변은 커서보다 오래됐다는 이유로 **영영 건너뛰어진다.**
CREATE TABLE IF NOT EXISTS ingested_answer (
    answer_id        TEXT PRIMARY KEY,
    knowledge_commit TEXT,   -- 지식 저장소의 커밋. 어느 ingest 가 이것을 읽었는가
    ingested_at      TEXT NOT NULL
);

-- 배치 진행 지점 (ADR-005 · ADR-006)
-- 배치는 중단 가능해야 하므로 어디까지 했는지를 남긴다.
CREATE TABLE IF NOT EXISTS ingest_checkpoint (
    kind       TEXT PRIMARY KEY,  -- source | qna
    cursor     TEXT NOT NULL,     -- 마지막 처리 커밋 해시 또는 QnA 시각
    updated_at TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """연결한다. **WAL 모드**로 연다 — 온라인과 배치가 동시에 접근한다 (ADR-002)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    """스키마를 만든다. 여러 번 불러도 안전하다."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()
