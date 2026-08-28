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

from agentic_service_desk.operations import migrations

SCHEMA_SQL = """
-- QnA 항목 — 대외 관점. "이용자에게 이 질문이 어떻게 되었는가" (D15)
CREATE TABLE IF NOT EXISTS qna_item (
    id                TEXT PRIMARY KEY,
    -- 모 시스템의 질문 id. **비어 있을 수 있다** — 담당자가 메신저 문의를 직접
    -- 등록한 건은 모 시스템을 거치지 않았다 (§1.4.3). UNIQUE 는 NULL 을 여럿
    -- 허용하므로 수동 등록끼리 부딪히지 않는다.
    parent_question_id TEXT UNIQUE,
    origin            TEXT NOT NULL DEFAULT 'parent',  -- parent | manual
                                              -- **수동 등록 건수가 W4(질문이 기록되지
                                              -- 않는다)의 유일한 간접 지표다** (§1.4.6)
    asker_id          TEXT,                   -- 사내 식별자. 지식 항목에는 넘어가지 않는다 (PO-3)
    state             TEXT NOT NULL,          -- 접수 | 게재됨 | 후속진행 | 사람대기 | 해결 | 미해결종료
    resolution_grade  TEXT,                   -- explicit | implicit — ingest 자격을 가른다 (D8)
    language          TEXT,                   -- 1단계에서 판정 (D53)
    opened_at         TEXT NOT NULL,
    closed_at         TEXT
);

-- 수동 등록 원문 (FR-10, §1.4.3).
-- 메신저로 오간 질문과 담당자의 답변을 **그대로** 담는다. 종결 기록의 일반화된 질문은
-- 이것을 가공한 결과이므로, 원문을 지우면 초안을 다시 만들 수 없다.
CREATE TABLE IF NOT EXISTS manual_entry (
    qna_item_id   TEXT PRIMARY KEY,
    question      TEXT NOT NULL,   -- 붙여넣은 질문 원문
    answer        TEXT NOT NULL,   -- 담당자가 실제로 한 답변
    registered_by TEXT,
    registered_at TEXT NOT NULL,
    FOREIGN KEY (qna_item_id) REFERENCES qna_item (id)
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
    confirmed_at        TEXT,            -- 무효화 조건이 채워진 시각
    promoted_item_id    TEXT,            -- 승격된 지식 항목의 불변 id (FR-15).
                                         -- 두 번 승격하지 않기 위한 표시이자
                                         -- "이 종결 기록이 무엇이 되었는가"의 답이다
    promoted_by         TEXT,            -- human | gate — **누가 올렸는가** (§6.8.4).
                                         -- 자동 승격분은 사람이 본 적이 없으므로
                                         -- 표본 재검증의 우선순위가 높다 (§6.8.4-a)
    promotion_declined_at TEXT,          -- 사람이 Q7 에서 "올리지 않는다"고 판정한 시각.
                                         -- 없으면 기각한 건이 **매 주기 다시 뜬다**
    FOREIGN KEY (ticket_id) REFERENCES ticket (id)
);

-- 답변 이력 — 게재한 것과 그 근거 (D20)
-- 답변 본문은 모 시스템에 있지만 **무엇을 근거로 만들어졌는지는 우리만 안다.**
CREATE TABLE IF NOT EXISTS answer_record (
    id                TEXT PRIMARY KEY,
    qna_item_id       TEXT NOT NULL,
    draft_id          TEXT,            -- 어느 초안이 나갔는가. 한 초안은 한 번만 나간다
    parent_answer_id  TEXT,            -- 모 시스템에서 받은 게재 id. 정정(XR-7)에 쓴다
    body              TEXT NOT NULL,   -- **조립된 게재 본문** — 귀속과 근거가 붙은 그대로 (PO-2)
    author_kind       TEXT NOT NULL,   -- bot | human — 되먹임 차단의 판정 근거 (D7)
    author_account    TEXT,            -- 어느 계정으로 나갔는가. 이것이 `bot` 의 실체다
    generated_by      TEXT,            -- 모델 식별자. 모델 교체 추적 (ADR-005)
    review_outcome    TEXT,            -- passed | rejected
    review_reason     TEXT,            -- P1~P8 — 사유별 분포가 신뢰 지표다 (§5.5.6)
    -- 게재 진행 상태. **기록을 먼저 남기고 내보내기 때문에** 필요하다 (§9.6 단일 출구).
    -- in_flight : 내보내려 했고 결과를 모른다. **사람이 봐야 한다**
    -- published : 나갔고 모 시스템 id 를 받았다
    -- abandoned : 나가지 않은 것으로 사람이 확인했다. 그 초안은 다시 시도할 수 있다
    -- corrected : 정정되어 뒤 기록에 자리를 넘겼다 (PO-1). **지우지 않는다** —
    --             그때 무엇에 기대어 답했는지가 정정으로 사라지면 D20 이 무의미해진다
    state             TEXT NOT NULL DEFAULT 'in_flight',
    attempted_at      TEXT,            -- 내보내려 한 시각. 게재 시각보다 **먼저** 적힌다
    published_at      TEXT,
    corrected_at      TEXT,            -- 정정 시각 (PO-1)
    FOREIGN KEY (qna_item_id) REFERENCES qna_item (id)
);

-- **한 초안은 한 번만 나간다 — 코드가 아니라 스키마가 지킨다.**
-- 게재는 되돌리기 어려운 대외 행위라(§5.2) 중복 게재를 코드 분기에 맡기지 않는다.
-- `abandoned` 를 제외하는 이유는, 나가지 않은 것으로 확인된 건은 **다시 시도할 수
-- 있어야** 하기 때문이다 — 제외하지 않으면 한 번의 통신 실패가 그 답변을 영영 막는다.
CREATE UNIQUE INDEX IF NOT EXISTS answer_record_one_per_draft
    ON answer_record (draft_id)
    WHERE draft_id IS NOT NULL AND state <> 'abandoned';

-- 답변 초안 — 검수를 기다리는 것 (Q2, §8.2).
-- **`review` 와 나눠 둔다.** 이쪽은 판정받는 *물건*이고 저쪽은 판정 *사건*이다 —
-- 한 초안이 에이전트 검수와 사람 검수를 차례로 받으면 사건은 둘, 물건은 하나다.
-- 진술을 통째로 담는 이유는 근거 강도 표시(§5.6.5)가 화면에서 살아 있어야 하기
-- 때문이다. 본문만 남기면 **어디를 먼저 볼지**가 사라진다.
CREATE TABLE IF NOT EXISTS answer_draft (
    id           TEXT PRIMARY KEY,
    qna_item_id  TEXT,
    question     TEXT NOT NULL,   -- 사람 검수자에게만 보인다. 에이전트는 못 본다 (FR-20)
    statements   TEXT NOT NULL,   -- JSON — 진술과 근거 강도
    grounding    TEXT NOT NULL,   -- JSON — 지식 항목 id
    unanswered   TEXT NOT NULL,   -- JSON — 모른다고 밝힌 경계 (FR-19)
    agent_outcome TEXT,           -- passed | rejected. 에이전트 검수 결과
    agent_reason TEXT,            -- P1~P5
    agent_detail TEXT,
    corrects     TEXT,            -- 정정 대상 `answer_record.id` (PO-1, FR-35).
                                  -- 있으면 **새로 올리지 않고 그 답변을 고친다** —
                                  -- 후속에 대한 새 답변과 정정은 다른 행위다
    gate_signals TEXT,            -- 게재 판정이 잡은 위험 신호 JSON (§5.5.4, FR-25).
                                  -- **왜 사람에게 왔는지**를 화면이 말해 주려면 필요하다 —
                                  -- 없으면 검수자가 매번 처음부터 훑는다 (§8.6.3)
    generated_by TEXT,            -- 어느 모델이 만들었는가 (§6.6.1 필드 5, ADR-005).
                                  -- **게재 시점이 아니라 생성 시점의 모델**이다 —
                                  -- 초안이 큐에 머무는 동안 설정이 바뀔 수 있고,
                                  -- 그러면 모델 교체 추적이 어긋난다
    state        TEXT NOT NULL,   -- pending | approved | rejected
    created_at   TEXT NOT NULL,
    decided_at   TEXT
);

-- 검수 기록 (FR-22, §5.5.6).
-- **반려된 초안도 남긴다.** 버리면 왜 반려됐는지의 분포를 잃고, 그 분포가 세 가지로
-- 쓰인다 — 지식 공백 탐지(P1·P5 가 몰리면 근거가 부족하다), 신뢰 계측(반려율은
-- 사유별 분포가 있어야 읽힌다), 그리고 2국면 자동 검수의 학습 자료.
CREATE TABLE IF NOT EXISTS review (
    id          TEXT PRIMARY KEY,
    qna_item_id TEXT,            -- 답변 검수일 때만. 콘텐츠 검수는 비어 있다
    -- 무엇을 검수했는가 — answer | content. **섞으면 §5.5.6 의 반려율이 무엇을
    -- 뜻하는지 달라진다**: 그 숫자는 "사람이 에이전트의 *답변*을 얼마나 믿는가"인데
    -- 콘텐츠 반려가 섞이면 두 가지가 한 비율에 눌린다. `checked_by` 로 자동 게재를
    -- 갈라 둔 것과 같은 이유다.
    kind        TEXT NOT NULL DEFAULT 'answer',
    outcome     TEXT NOT NULL,   -- passed | rejected
    reason      TEXT,            -- P1~P8. 통과에는 없다
    detail      TEXT,
    draft_body  TEXT NOT NULL,   -- 반려된 초안의 본문. 이것이 학습 자료가 된다
    grounding   TEXT NOT NULL,   -- JSON — 그때 무엇을 근거로 삼았는가
    reviewed_by TEXT NOT NULL,   -- agent | human
    reviewed_at TEXT NOT NULL
);

-- 근거 버전 고정 (D20, §6.6) — 이 표가 stale 전파의 배선이다.
-- 링크만 두면 지식이 갱신된 뒤 "당시 무엇을 근거로 답했는지"가 사라진다. 링크를
-- 따라가면 **지금의** 지식이 나올 뿐이다.
CREATE TABLE IF NOT EXISTS answer_grounding (
    answer_record_id  TEXT NOT NULL,
    knowledge_item_id TEXT NOT NULL,   -- 경로가 아니라 불변 id (ADR-002 결정 2)
    pinned_commit     TEXT NOT NULL,   -- §6.6.1 필드 4 — 게재 시점의 **지식베이스**
                                       -- 커밋. 원천 저장소 커밋이 아니다. 이것이 있어야
                                       -- 그 시점의 항목 내용을 재현할 수 있다
    source            TEXT NOT NULL DEFAULT '[]',  -- §6.6.1 필드 3 — 그 항목이 **당시** 무엇에서
                                       -- 유래했는가 (커밋·경로·QnA id) 의 JSON.
                                       -- 항목이 갱신되면 지금 provenance 는 달라지므로
                                       -- 여기 박아 둬야 "무엇이 바뀌어 틀리게 됐는가"에
                                       -- 답할 수 있다
    stale_at_publish  INTEGER NOT NULL DEFAULT 0,
                                       -- 게재 시점에 이미 stale 이었는가. **"그때는
                                       -- 맞았고 지금은 틀리다"와 "그때부터 틀렸다"를
                                       -- 가른다** (§6.6.2) — 전자는 정상적인 stale 이고
                                       -- 후자는 품질 결함이라 대응이 다르다.
                                       -- P4 검수가 막는 것이 이것이므로, 여기 1 이
                                       -- 쌓이면 검수가 새고 있다는 뜻이다
    PRIMARY KEY (answer_record_id, knowledge_item_id),
    FOREIGN KEY (answer_record_id) REFERENCES answer_record (id)
);

-- "확인 중" 게재 (FR-26, §8.6.3).
-- 검수를 기다리는 동안 **자리를 먼저 잡아 둔다** — 침묵보다 낫고, SLA 없이 경과
-- 시간을 드러내겠다는 방침과 일치한다. 승인되면 그 자리를 XR-7 로 채운다.
--
-- `answer_record` 와 나눠 두는 이유는 **답변 이력이 아니기 때문**이다. "확인 중"은
-- 답이 아니라 상태 표시이고, 여기 섞으면 자동 게재율·근거 기록 같은 지표가 상태
-- 표시까지 세게 된다.
CREATE TABLE IF NOT EXISTS holding_notice (
    qna_item_id      TEXT PRIMARY KEY,
    parent_answer_id TEXT NOT NULL,   -- 나중에 채울 자리 (XR-7)
    posted_at        TEXT NOT NULL,
    filled_at        TEXT,            -- 채워진 시각. 대기 시간이 여기서 나온다
    FOREIGN KEY (qna_item_id) REFERENCES qna_item (id)
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

-- 지식 항목의 임베딩 (ADR-004).
-- **항목 수가 수백~수천이라 전수 임베딩이 부담되지 않는다.** 증분 인덱싱의 복잡도를
-- 지금 떠안지 않고 ingest 주기에 맞춰 통째로 다시 만든다.
-- 벡터를 운영 DB 에 두는 이유는 지식 저장소가 git 이기 때문이다 — 생성물을 커밋하면
-- diff 가 벡터로 가득 차 "어느 커밋이 지식을 바꿨는가"가 읽히지 않는다.
CREATE TABLE IF NOT EXISTS knowledge_embedding (
    item_id   TEXT PRIMARY KEY,  -- 경로가 아니라 불변 id (ADR-002)
    vector    TEXT NOT NULL,     -- JSON 실수 배열
    model     TEXT NOT NULL,     -- 어느 모델로 만들었나. 바뀌면 통째로 다시 만든다
    built_at  TEXT NOT NULL
);

-- 콘텐츠 초안 — 검수를 기다린다 (Q3, FR-39, WBS-4.6.2).
-- **콘텐츠는 국면과 무관하게 전수 사람 승인**이므로 답변과 달리 자동 게재 관문이
-- 아예 없다. 여기 오면 사람이 본다.
--
-- 타입 선언을 이 표에 두지 않는다 — 선언은 파일이고(FR-42, §7.5) `type_id` 는
-- 그 선언을 가리키는 이름일 뿐이다. DB 에 복사해 두면 **두 벌이 어긋난다.**
CREATE TABLE IF NOT EXISTS content_draft (
    id           TEXT PRIMARY KEY,
    type_id      TEXT NOT NULL,   -- 레지스트리의 타입 id (faq | guide | column | ...)
    ticket_id    TEXT,            -- Q3 대기열의 자리 (source=content, §6.4.3).
                                  -- **Q3 는 작업 대기열이다** (FR-45) — 초안 하나가
                                  -- 처리 하나이고, 그 기록 단위가 티켓이다
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    grounding    TEXT NOT NULL,   -- JSON — 근거로 쓴 지식 항목 id
    based_on     TEXT,            -- 직전 판본 `content_draft.id`. **살아있는 문서의 갱신**
                                  -- 이라야 diff 검수가 성립한다 (§5.5.5) — 없으면 첫 제작
    state        TEXT NOT NULL,   -- pending | approved | rejected
    generated_by TEXT,            -- 생성 시점의 모델 (ADR-005)
    created_at   TEXT NOT NULL,
    decided_at   TEXT
);

-- 콘텐츠 제작 주기의 진행 표시 (WBS-4.6.2).
-- **"내용이 바뀌었는가"에 걸지 않는다.** 걸면 바뀔 것이 없는 타입이 매 주기 LLM 에
-- 다시 실리는데, ingest 에서 이미 밟은 실패다 — 돈 것은 바뀌지 않았어도 돈 것이다.
CREATE TABLE IF NOT EXISTS content_run (
    type_id           TEXT PRIMARY KEY,
    last_run_at       TEXT NOT NULL,  -- 마지막으로 **본** 시각. 화면이 이것을 말한다
    -- **트리거는 이것을 본다.** 본 시각으로 재면 기다리기로 한 주기가 시계를 앞으로
    -- 밀어, 근거가 낡아 한 번 미룬 타입이 **꼬박 한 주기를 더 기다린다.**
    -- 모델을 실제로 돌렸을 때만 앞으로 간다.
    last_generated_at TEXT,
    last_commit       TEXT,          -- 그때 본 소스 커서. 코드 변경 임계가 이것을 본다.
                                     -- 생성했을 때만 옮긴다 — 먼저 옮기면 그 변경이
                                     -- 아무것도 만들지 않은 채 소비된다
    outcome           TEXT NOT NULL, -- produced | unchanged | held | no_grounding | pending_review
    detail            TEXT
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
    """**새 DB 일 때만** 스키마를 만들고 버전을 찍는다 (ADR-010).

    기존 DB 에는 손대지 않는다. 예전에는 매번 `executescript` 를 돌렸는데, 그러면
    **표 추가는 조용히 되고 열 추가만 실패해** 이행이 반쪽이 된다 — 어느 쪽이
    자동이고 어느 쪽이 아닌지 아무도 기억하지 못한다. 지금은 스키마를 바꾸는 길이
    `asd migrate` 하나뿐이다.

    연결을 열 때마다 불리므로 싸야 한다. 새것인지 보는 질의 하나로 끝난다.
    """
    if not migrations.is_fresh(conn):
        return
    conn.executescript(SCHEMA_SQL)
    migrations.stamp(conn, migrations.schema_version(), "baseline")
    conn.commit()
