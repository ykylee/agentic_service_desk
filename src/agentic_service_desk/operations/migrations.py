"""스키마 이행 (ADR-010).

운영 DB 는 SQLite 파일 하나다(ADR-002). 지금까지는 `CREATE TABLE IF NOT EXISTS` 만
있어서 **표는 생기지만 열은 생기지 않았다** — 옛 DB 로 새 코드를 돌리면 배치 한복판에서
`no column named ...` 로 터진다. 개발 중에는 파일을 지우면 됐지만, **실데이터를 켜는
순간 그 방법이 사라진다.**

## 세 가지를 정했다

**① 버전은 번호이고, 계단은 명시적이다.** `schema_version` 에 적용 이력이 쌓이고,
각 이행은 번호와 이름을 가진 `Migration` 이다. 열 추가만 다루는 자동 대조로는
**데이터 채우기와 제약 변경을 표현할 수 없어**, 언젠가 손으로 SQL 을 짜게 된다.

**② 사람이 명령으로 올린다** (`asd migrate`). 자동 적용을 택하지 않은 이유는 셋이다.

    - 온라인·배치가 **다른 프로세스**다. 동시에 뜨면 누가 올릴지 경쟁한다
    - 이행은 **되돌리기 어려운 행위**다 — 게재와 같은 종류이고, 조용히 일어나면 안 된다
    - 운영자가 곧 개발자다(D30). 명령 하나를 더 치는 비용이 낮다

**③ 버전이 안 맞으면 기동을 거부한다.** 지금도 결국 터지지만 터지는 자리가 배치
한복판이라, 그때까지 수집한 것과 못 한 것이 섞인다. 설정 누락을 기동에서 드러내는
것과 같은 이유다.

## 선언과 계단이 어긋나지 않게 한다

`SCHEMA_SQL`(현재 스키마의 선언)과 마이그레이션(기존 DB 를 그 선언에 맞추는 계단)은
**두 벌이다.** 어긋나면 새로 설치한 환경과 이행한 환경의 스키마가 달라지는데, 그것은
한참 뒤 엉뚱한 곳에서 드러난다.

그래서 `asd migrate` 는 적용 뒤 **결과 스키마를 선언과 대조**한다. 다르면 통째로
롤백하고 무엇이 다른지 말한다 — 규칙을 두 벌 쓰는 대가를 대조로 갚는 것은 이
저장소가 산출물 필터에서 이미 한 방식이다.

## 이행 경로 이전의 DB 는 받지 않는다

`schema_version` 이 없는데 표는 있는 DB 는 **버전을 알 수 없다.** 짐작해서 번호를
찍으면 그 뒤의 모든 이행이 어긋나므로 거부한다. 지금은 개발 단계라 지우면 되고,
**이 한 번의 단절이 이 모듈을 지금 만드는 이유**다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

BASELINE = 1
"""이행 경로를 도입한 시점의 스키마 (WBS-4.5 S3 까지). 이 번호가 바닥이다."""


@dataclass(frozen=True)
class Migration:
    """계단 하나.

    문장 목록인 것이 요점이다 — 함수로 두면 무엇이 실행되는지 읽으려고 코드를
    따라가야 하는데, 이행은 **읽고 승인하는 것**이 사람의 일이다.
    """

    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=2,
        name='holding_notice — "확인 중" 게재 (WBS-4.5.5, FR-26)',
        statements=(
            """
            CREATE TABLE IF NOT EXISTS holding_notice (
                qna_item_id      TEXT PRIMARY KEY,
                parent_answer_id TEXT NOT NULL,
                posted_at        TEXT NOT NULL,
                filled_at        TEXT,
                FOREIGN KEY (qna_item_id) REFERENCES qna_item (id)
            )
            """,
        ),
    ),
    Migration(
        version=3,
        name="answer_draft.gate_signals — 왜 사람에게 왔는가 (WBS-4.5.5)",
        statements=("ALTER TABLE answer_draft ADD COLUMN gate_signals TEXT",),
    ),
    Migration(
        version=4,
        name="ticket_resolution 승격 판정 — 누가 올렸는가 · 기각 (WBS-4.5.6)",
        statements=(
            "ALTER TABLE ticket_resolution ADD COLUMN promoted_by TEXT",
            "ALTER TABLE ticket_resolution ADD COLUMN promotion_declined_at TEXT",
        ),
    ),
    Migration(
        version=5,
        name="answer_draft.corrects — 정정 대상 (WBS-4.5.7, PO-1)",
        statements=("ALTER TABLE answer_draft ADD COLUMN corrects TEXT",),
    ),
    Migration(
        version=6,
        name="content_draft · content_run — 콘텐츠 제작 (WBS-4.6.2, FR-36·39)",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS content_draft (
                id           TEXT PRIMARY KEY,
                type_id      TEXT NOT NULL,
                title        TEXT NOT NULL,
                body         TEXT NOT NULL,
                grounding    TEXT NOT NULL,
                based_on     TEXT,
                state        TEXT NOT NULL,
                generated_by TEXT,
                created_at   TEXT NOT NULL,
                decided_at   TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS content_run (
                type_id           TEXT PRIMARY KEY,
                last_run_at       TEXT NOT NULL,
                last_generated_at TEXT,
                last_commit       TEXT,
                outcome           TEXT NOT NULL,
                detail            TEXT
            )
            """,
        ),
    ),
    Migration(
        version=7,
        name="review.kind · content_draft.ticket_id — 콘텐츠 검수 (WBS-4.6.4, FR-39)",
        statements=(
            "ALTER TABLE review ADD COLUMN kind TEXT NOT NULL DEFAULT 'answer'",
            "ALTER TABLE content_draft ADD COLUMN ticket_id TEXT",
        ),
    ),
    Migration(
        version=8,
        name="content_publication — 콘텐츠 게재 (WBS-4.6.3, XR-6)",
        statements=(
            # **자리표를 실제 표로 바꾼다.** 골격 단계(WBS-4.1.4)에 열 여섯짜리
            # `content_publication` 이 있었지만 **한 번도 쓰인 적이 없다** — 게재가
            # 없었으므로 행이 하나도 들어가지 않았고, 그래서 지워도 잃을 것이 없다.
            # 열을 하나씩 맞춰 가는 것보다 통째로 다시 만드는 편이 읽기 쉽다.
            "DROP TABLE IF EXISTS content_publication",
            """
            CREATE TABLE IF NOT EXISTS content_publication (
                id            TEXT PRIMARY KEY,
                draft_id      TEXT NOT NULL,
                type_id       TEXT NOT NULL,
                place         TEXT NOT NULL,
                path          TEXT,
                parent_ref    TEXT,
                body          TEXT NOT NULL,
                grounding     TEXT NOT NULL,
                pinned_commit TEXT,
                state         TEXT NOT NULL,
                attempted_at  TEXT NOT NULL,
                published_at  TEXT,
                FOREIGN KEY (draft_id) REFERENCES content_draft (id)
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS content_publication_one_per_draft
                ON content_publication (draft_id)
                WHERE state <> 'abandoned'
            """,
        ),
    ),
    Migration(
        version=9,
        name="content_draft.observations · agent_findings — 칼럼 (WBS-4.7.2, FR-40·41)",
        statements=(
            # **관찰은 근거와 다른 열에 둔다.** grounding 에는 stale 판정과 지식베이스
            # 커밋 고정이 걸려 있는데 관찰은 갱신되는 항목이 아니라 그 시점의 사실이다.
            "ALTER TABLE content_draft ADD COLUMN observations TEXT",
            # NULL 은 "아직 안 봤다", '[]' 는 "봤는데 소견이 없다" — 기본값을 주지
            # 않는 것이 그 구분의 집행이다.
            "ALTER TABLE content_draft ADD COLUMN agent_findings TEXT",
        ),
    ),
    Migration(
        version=10,
        name="phase_state · phase_decision · phase_observation — 국면 판정 (WBS-4.8.1, FR-49)",
        statements=(
            # **국면의 SSOT 가 설정에서 DB 로 옮겨 온다.** 이행이 값을 심지 않는
            # 이유가 있다 — 씨앗은 `ASD_PHASE` 이고 그 값은 이행 시점이 아니라
            # 프로세스가 처음 국면을 물을 때 읽힌다. 여기서 1 을 박아 두면 3국면으로
            # 돌던 환경이 이행 한 번으로 조용히 1국면이 된다.
            """
            CREATE TABLE IF NOT EXISTS phase_state (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                phase      INTEGER NOT NULL,
                since      TEXT NOT NULL,
                decided_by TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS phase_decision (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                from_phase INTEGER,
                to_phase   INTEGER NOT NULL,
                decided_by TEXT NOT NULL,
                reason     TEXT NOT NULL,
                decided_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS phase_observation (
                observed_on TEXT NOT NULL,
                metric      TEXT NOT NULL,
                value       REAL,
                denominator INTEGER NOT NULL DEFAULT 0,
                unavailable TEXT NOT NULL DEFAULT '',
                window_days INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY (observed_on, metric)
            )
            """,
        ),
    ),
)
"""적용 순서대로. **번호는 `BASELINE + 1` 부터 하나씩 는다.**

계단을 놓을 때는 `SCHEMA_SQL` 도 **함께** 고친다 — 새로 설치한 환경은 선언으로
만들어지고 기존 환경은 계단으로 올라오므로, 한쪽만 고치면 환경마다 스키마가 달라진다.
`asd migrate` 가 적용 뒤 그 둘을 대조하고 다르면 되돌린다 (ADR-010 결정 4).
"""


def schema_version() -> int:
    """이 코드가 요구하는 버전. **마이그레이션 목록에서 나온다** — 따로 적어 두면
    올리는 것을 잊는다."""
    return MIGRATIONS[-1].version if MIGRATIONS else BASELINE


class SchemaProblem(RuntimeError):
    """스키마가 코드와 맞지 않는다. **기동을 멈춘다.**"""


class SchemaUnknown(SchemaProblem):
    """버전을 알 수 없다 — 이행 경로 도입 이전의 DB 다."""


class SchemaOutdated(SchemaProblem):
    """DB 가 코드보다 뒤에 있다. `asd migrate` 로 올린다."""


class SchemaAhead(SchemaProblem):
    """DB 가 코드보다 앞서 있다 — 코드를 되돌렸는데 DB 는 그대로인 경우다.

    **내려가는 계단은 두지 않았다.** 롤백은 되돌릴 수 없는 데이터 변형을 포함할 수
    있어 기계가 판단할 일이 아니다.
    """


class SchemaMismatch(SchemaProblem):
    """이행 결과가 선언(`SCHEMA_SQL`)과 다르다."""


# --- 버전 읽기·쓰기 -----------------------------------------------------------

_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""


def ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(_VERSION_TABLE)


def is_fresh(conn: sqlite3.Connection) -> bool:
    """아무것도 없는 DB 인가. **표 하나라도 있으면 새것이 아니다.**"""
    count = conn.execute(
        "SELECT count(*) c FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()["c"]
    return count == 0


def current_version(conn: sqlite3.Connection) -> int | None:
    """지금 DB 의 버전. **`None` 은 '0' 이 아니라 '모른다'** 다.

    둘을 섞으면 이행 경로 이전의 DB 에 계단을 처음부터 놓으려 하고, 그러면 이미
    있는 표를 다시 만들다 터지거나 더 나쁘게는 절반만 적용된다.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if exists is None:
        return None
    row = conn.execute("SELECT max(version) AS v FROM schema_version").fetchone()
    return row["v"]


def stamp(conn: sqlite3.Connection, version: int, name: str) -> None:
    """적용 이력을 남긴다. **덮어쓰지 않고 쌓는다** — 언제 무엇이 올라갔는지가
    사고를 되짚을 때 유일한 단서다."""
    ensure_version_table(conn)
    conn.execute(
        "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
        (version, name, datetime.now(UTC).isoformat()),
    )


# --- 관문 ---------------------------------------------------------------------


def require_current(conn: sqlite3.Connection) -> None:
    """기동해도 되는가. **아니면 여기서 멈춘다.**

    배치 한복판에서 터지면 그때까지 수집한 것과 못 한 것이 섞이고, 로그를 뒤져야
    원인을 안다. 설정 누락을 기동에서 드러내는 것과 같은 이유다.
    """
    required = schema_version()
    version = current_version(conn)

    if version is None:
        raise SchemaUnknown(
            "운영 DB 의 스키마 버전을 알 수 없다 — 이행 경로(ADR-010) 도입 이전에 "
            "만들어진 파일이다. 짐작해서 번호를 찍으면 그 뒤의 모든 이행이 어긋나므로 "
            "받지 않는다. 개발 중이라면 파일을 지우고 다시 만든다."
        )
    if version < required:
        waiting = ", ".join(f"{m.version:03d}" for m in pending(conn)) or "-"
        raise SchemaOutdated(
            f"스키마 버전이 {version} 인데 코드는 {required} 를 요구한다. "
            f"`asd migrate` 를 먼저 돌린다. (대기: {waiting})"
        )
    if version > required:
        raise SchemaAhead(
            f"스키마 버전이 {version} 인데 코드는 {required} 를 요구한다 — "
            "DB 가 코드보다 앞서 있다. 코드를 되돌렸다면 그 버전의 코드로 돌리거나, "
            "**내려가는 계단은 없으므로** 무엇을 되돌릴지 사람이 정해야 한다."
        )


def pending(conn: sqlite3.Connection) -> list[Migration]:
    """아직 적용되지 않은 계단. 버전을 모르면 **빈 목록이 아니라 거부**가 답이므로,
    그 판정은 `require_current` 가 한다."""
    version = current_version(conn)
    if version is None:
        return []
    return [m for m in MIGRATIONS if m.version > version]


# --- 적용 ---------------------------------------------------------------------


@dataclass
class ApplyReport:
    applied: list[Migration] = field(default_factory=list)
    from_version: int | None = None
    to_version: int | None = None
    differences: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.differences


def apply(conn: sqlite3.Connection, *, dry_run: bool = False) -> ApplyReport:
    """대기 중인 계단을 순서대로 올린다.

    **한 트랜잭션이다.** SQLite 는 DDL 도 트랜잭션 안에서 돌므로, 중간에 실패하면
    절반만 적용된 스키마가 남지 않는다 — 절반 적용은 터지는 것보다 나쁘다.

    적용이 끝나면 **결과를 선언과 대조**한다. 다르면 되돌린다: `SCHEMA_SQL` 과
    계단이 어긋난 채로 두면 새로 설치한 환경과 이행한 환경의 스키마가 달라지고,
    그 차이는 한참 뒤 엉뚱한 곳에서 드러난다.
    """
    version = current_version(conn)
    if version is None:
        raise SchemaUnknown(
            "스키마 버전을 알 수 없어 어디서부터 올릴지 정할 수 없다 (ADR-010). "
            "개발 중이라면 파일을 지우고 다시 만든다."
        )

    report = ApplyReport(from_version=version, to_version=version)
    todo = pending(conn)
    if dry_run:
        report.applied = todo
        report.to_version = todo[-1].version if todo else version
        return report

    if todo:
        conn.execute("BEGIN")
        try:
            for migration in todo:
                for statement in migration.statements:
                    conn.execute(statement)
                stamp(conn, migration.version, migration.name)
                report.applied.append(migration)
            report.differences = verify(conn)
            if report.differences:
                conn.execute("ROLLBACK")
                report.applied = []
                return report
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        report.to_version = todo[-1].version
    else:
        report.differences = verify(conn)
    return report


# --- 선언과의 대조 -------------------------------------------------------------


def verify(conn: sqlite3.Connection) -> list[str]:
    """지금 스키마가 `SCHEMA_SQL` 선언과 같은가. 다른 점을 사람이 읽을 말로 돌려준다.

    빈 DB 에 선언을 적용해 만든 것과 비교한다 — **선언이 유일한 기준**이고 계단은
    거기 닿기 위한 수단이다.
    """
    from agentic_service_desk.operations.schema import SCHEMA_SQL

    expected = sqlite3.connect(":memory:")
    expected.row_factory = sqlite3.Row
    expected.executescript(SCHEMA_SQL)
    expected.execute(_VERSION_TABLE)
    try:
        return _diff(_shape(expected), _shape(conn))
    finally:
        expected.close()


def _shape(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """표 → 열 → 형. **인덱스와 열 정의를 함께 본다.**

    `sqlite_master.sql` 원문을 그대로 비교하지 않는 이유는 공백과 주석이 달라도
    스키마는 같기 때문이다 — 그런 차이로 거부하면 사람이 대조를 믿지 않게 된다.
    """
    shape: dict[str, dict[str, str]] = {}
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for table in tables:
        columns = conn.execute(f"PRAGMA table_info({table['name']})").fetchall()
        shape[table["name"]] = {
            c["name"]: f"{c['type']}|notnull={c['notnull']}|pk={c['pk']}"
            for c in columns
        }
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    shape["·인덱스"] = {i["name"]: "" for i in indexes}
    return shape


def _diff(
    expected: dict[str, dict[str, str]], actual: dict[str, dict[str, str]]
) -> list[str]:
    problems: list[str] = []
    for table in sorted(set(expected) | set(actual)):
        want, have = expected.get(table), actual.get(table)
        if want is None:
            problems.append(f"{table}: 선언에 없는 표가 DB 에 있다")
            continue
        if have is None:
            problems.append(f"{table}: 선언에 있는 표가 DB 에 없다")
            continue
        for column in sorted(set(want) | set(have)):
            if column not in have:
                problems.append(f"{table}.{column}: 선언에 있는데 DB 에 없다")
            elif column not in want:
                problems.append(f"{table}.{column}: DB 에 있는데 선언에 없다")
            elif want[column] != have[column]:
                problems.append(
                    f"{table}.{column}: 정의가 다르다 "
                    f"(선언 {want[column]} · DB {have[column]})"
                )
    return problems
