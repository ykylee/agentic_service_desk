"""지식베이스 구축의 현황과 제어 (FR-63, §8.3).

**진행이 표준출력에만 있었다.** 워커는 묶음마다 `묶음 43/116` 을 찍지만 그것은
터미널을 보고 있는 사람에게만 있는 사실이고, 화면에 남는 것은 최근 커밋 몇 줄이다.
실측된 부트스트랩이 116묶음 · 4.96시간이므로 모 시스템이 붙는 날 운영자는
**반나절 동안 터미널을 지켜보는 것 말고 할 수 있는 일이 없다.**

**웹과 워커는 다른 프로세스다** (ADR-001) — 화면의 버튼이 함수를 부를 수 없다.
화면은 `build_control` 에 뜻을 적고 워커가 자기 주기에서 읽는다.

**중단은 신호가 아니라 상태다.** 신호로 두면 멈춘 다음 주기에 워커가 다시 시작해
멈춘 것이 아니게 된다. 그래서 `paused` 는 화면이 다시 풀어 줄 때까지 남는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_service_desk.ingest.harness_runner import DEFAULT_TIMEOUT as HARNESS_TIMEOUT

IDLE_STALE_SECONDS = 180
"""**놀고 있을 때** 이만큼 심박이 없으면 워커가 살아 있다고 말하지 않는다.

기본 주기가 60초이므로 세 주기의 여유다.
"""

BUSY_STALE_SECONDS = HARNESS_TIMEOUT + 300
"""**묶음 하나가 도는 중일 때**의 여유. 놀 때와 나눈 이유는 실물에서 밟았다.

심박은 묶음 **경계**에서만 찍힌다 — 모델 호출은 블로킹 서브프로세스라 그 사이에
심박이 낄 자리가 없다. 그런데 한 묶음의 실측 중앙값이 124초이고 한도는
`DEFAULT_TIMEOUT`(600초)이라, 놀 때의 임계(180초)를 그대로 쓰면 **묶음이 조금만
길어도 화면이 "워커가 떠 있지 않다"고 말한다.** 2026-09-03 라이브에서 묶음 2 가
도는 동안 실제로 그렇게 나왔다 — 워커도 pi 도 멀쩡히 살아 있었다.

**늑대를 부르는 화면은 곧 아무도 안 보는 화면이 된다.** 그래서 도는 중에는 한
번의 호출이 끝까지 갈 수 있는 시간만큼 기다린다.
"""


# --- 심박 ---------------------------------------------------------------------


@dataclass(frozen=True)
class Heartbeat:
    """워커가 살아 있는가, 지금 무엇을 하는가."""

    beat_at: str = ""
    stage: str = ""
    doing: str = ""
    busy: bool = False
    """묶음이 도는 중인가. **기다려 줄 시간을 정한다** — 그 사이에는 심박이 낄
    자리가 없다."""

    @property
    def seen(self) -> bool:
        return bool(self.beat_at)

    @property
    def age_seconds(self) -> float:
        return _age_seconds(self.beat_at)

    @property
    def tolerance(self) -> int:
        return BUSY_STALE_SECONDS if self.busy else IDLE_STALE_SECONDS

    @property
    def alive(self) -> bool:
        """**모르면 살아 있다고 하지 않는다.** 진행 0 이 "할 일이 없다"인지
        "아무도 안 돈다"인지 구분되지 않는 것이 이 화면이 생긴 이유다."""
        return self.seen and self.age_seconds <= self.tolerance

    @property
    def label(self) -> str:
        if not self.seen:
            return "워커가 한 번도 심박을 남기지 않았다 — `uv run asd-worker` 로 띄운다"
        age = _age_label(self.age_seconds)
        if not self.alive:
            return f"마지막 심박이 {age} 전이다 — 워커가 떠 있지 않다"
        return f"{age} 전 · {self.doing or '다음 주기를 기다린다'}"


def beat(conn: sqlite3.Connection, *, stage: str, doing: str = "") -> None:
    """워커가 자기 자리를 찍는다. **틱마다, 그리고 오래 걸리는 일 앞에서.**"""
    conn.execute(
        """
        INSERT INTO worker_heartbeat (id, beat_at, stage, doing) VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            beat_at = excluded.beat_at, stage = excluded.stage, doing = excluded.doing
        """,
        (_now(), stage, doing),
    )
    conn.commit()


def heartbeat(conn: sqlite3.Connection) -> Heartbeat:
    """심박. **도는 중인지 함께 본다** — 기다려 줄 시간이 그것으로 갈린다."""
    row = conn.execute("SELECT * FROM worker_heartbeat WHERE id = 1").fetchone()
    if row is None:
        return Heartbeat()
    return Heartbeat(
        beat_at=row["beat_at"],
        stage=row["stage"],
        doing=row["doing"],
        busy=running(conn) is not None,
    )


# --- 제어 ---------------------------------------------------------------------


@dataclass(frozen=True)
class Control:
    """구축을 세울 것인가, 지금 깨울 것인가."""

    paused: bool = False
    wake: bool = False
    changed_at: str = ""
    note: str = ""


def control(conn: sqlite3.Connection) -> Control:
    row = conn.execute("SELECT * FROM build_control WHERE id = 1").fetchone()
    if row is None:
        return Control()
    return Control(
        paused=bool(row["paused"]),
        wake=bool(row["wake"]),
        changed_at=row["changed_at"],
        note=row["note"],
    )


def _set_control(conn: sqlite3.Connection, *, paused: bool, wake: bool, note: str) -> None:
    conn.execute(
        """
        INSERT INTO build_control (id, paused, wake, changed_at, note) VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            paused = excluded.paused, wake = excluded.wake,
            changed_at = excluded.changed_at, note = excluded.note
        """,
        (int(paused), int(wake), _now(), note),
    )
    conn.commit()


def request_start(conn: sqlite3.Connection, *, note: str = "화면에서 시작") -> None:
    """지금 시작한다 — **멈춤을 풀고 잠을 깨운다.**

    워커는 주기 사이에 1초씩 자므로 이 표시는 최대 1초 안에 읽힌다. 주기를
    앞당기는 것이지 새 프로세스를 띄우는 것이 아니다 — 워커가 없으면 아무 일도
    일어나지 않고, 화면이 심박으로 그것을 말한다.
    """
    _set_control(conn, paused=False, wake=True, note=note)


def request_stop(conn: sqlite3.Connection, *, note: str = "화면에서 중단") -> None:
    """멈춘다 — **현재 묶음을 마치고**.

    묶음 한복판에서 끊지 않는 이유는 FR-5 다. 묶음마다 커밋이 남으므로 경계에서
    멈추면 디스크와 커밋이 어긋나지 않고, 커서는 옮기지 않았으니 다시 시작하면
    그 구간부터 이어 읽는다.
    """
    _set_control(conn, paused=True, wake=False, note=note)


def take_wake(conn: sqlite3.Connection) -> bool:
    """깨우라는 표시가 있으면 **집어서 내린다.** 워커만 부른다."""
    row = conn.execute("SELECT wake FROM build_control WHERE id = 1").fetchone()
    if row is None or not row["wake"]:
        return False
    conn.execute("UPDATE build_control SET wake = 0, changed_at = ? WHERE id = 1", (_now(),))
    conn.commit()
    return True


# --- 런 ------------------------------------------------------------------------

RUNNING, COMPLETED, STOPPED, FAILED = "running", "completed", "stopped", "failed"


@dataclass(frozen=True)
class Run:
    """ingest 한 번. 도는 중이면 `ended_at` 이 비어 있다."""

    id: int
    started_at: str
    updated_at: str
    ended_at: str | None
    outcome: str | None
    trigger: str
    repo_url: str
    chunks_done: int
    chunks_total: int
    note: str

    @property
    def running(self) -> bool:
        return self.ended_at is None

    @property
    def elapsed_seconds(self) -> float:
        """시작한 뒤로 흐른 시간. **도는 중이면 지금까지** 잰다.

        마지막 묶음 경계(`updated_at`)까지만 재면 **긴 묶음이 도는 동안 경과가
        멈춘다** — 2026-09-03 라이브에서 12분째 도는 런이 "0초"로 보였다. 진행이
        멈춘 것과 경과 표시가 멈춘 것은 화면에서 구분되지 않는다.
        """
        if self.running:
            return max(0.0, _age_seconds(self.started_at))
        return max(0.0, _between(self.started_at, self.ended_at or self.updated_at))

    @property
    def measured_seconds(self) -> float:
        """**묶음 경계까지만** 흐른 시간 — 평균을 내는 데 쓴다.

        지금까지로 재면 도는 중인 묶음의 시간이 이미 끝난 묶음들에 얹혀 평균이
        부풀고, 남은 시간이 묶음이 길어질수록 함께 늘어난다.
        """
        return max(0.0, _between(self.started_at, self.ended_at or self.updated_at))

    @property
    def ratio(self) -> float:
        """얼마나 왔는가. **끝낸 것으로 센다** — 시작한 것으로 세면 마지막 묶음이
        도는 동안 화면이 100% 를 보여 준다."""
        return (self.chunks_finished / self.chunks_total) if self.chunks_total else 0.0

    @property
    def chunks_finished(self) -> int:
        """**끝낸** 묶음 수.

        `chunks_done` 은 워커가 *지금 시작한* 묶음의 번호다 (`on_chunk` 가 묶음
        머리에서 불린다) — 도는 중이라면 끝난 것은 그보다 하나 적다. 이 구분을
        빠뜨리면 첫 묶음을 시작한 순간 "묶음당 1초"가 나오고, 화면이 남은 시간을
        1초로 말한다. 2026-09-03 라이브에서 실제로 그렇게 나왔다.
        """
        return max(0, self.chunks_done - 1) if self.running else self.chunks_done

    @property
    def seconds_per_chunk(self) -> float | None:
        """**평균이다. 중앙값이 아니다** — 묶음마다의 시각을 남기지 않는다.

        남은 시간을 재는 데는 평균으로 족하고, 묶음별 시각까지 남기면 긴 런 하나가
        표를 수백 줄로 채운다. 실측 중앙값이 필요하면 워커 로그가 진다.
        """
        if self.chunks_finished <= 0:
            return None
        return self.measured_seconds / self.chunks_finished

    @property
    def remaining_label(self) -> str:
        """얼마나 남았는가. **모르면 모른다고 한다.**"""
        per = self.seconds_per_chunk
        if not self.running or per is None or self.chunks_total <= 0:
            return ""
        left = max(0, self.chunks_total - self.chunks_finished)
        return f"남은 시간 약 {_age_label(left * per)}"

    @property
    def elapsed_label(self) -> str:
        return _age_label(self.elapsed_seconds)

    @property
    def outcome_label(self) -> str:
        if self.running:
            return "도는 중"
        return {
            COMPLETED: "완주",
            STOPPED: "중단 — 커서를 옮기지 않았다",
            FAILED: "실패",
        }.get(self.outcome or "", self.outcome or "—")


def start_run(conn: sqlite3.Connection, *, trigger: str = "schedule") -> int:
    """런을 연다. 진행이 붙기 전에 먼저 여는 이유는 **시작 시각이 있어야 경과를
    잴 수 있기** 때문이다."""
    now = _now()
    cur = conn.execute(
        "INSERT INTO ingest_run (started_at, updated_at, trigger) VALUES (?, ?, ?)",
        (now, now, trigger),
    )
    conn.commit()
    return int(cur.lastrowid)


def note_chunk(
    conn: sqlite3.Connection, run_id: int, *, repo_url: str, done: int, total: int
) -> None:
    """묶음 하나를 시작할 때마다 진행을 옮긴다."""
    conn.execute(
        "UPDATE ingest_run SET updated_at = ?, repo_url = ?, chunks_done = ?, "
        "chunks_total = ? WHERE id = ?",
        (_now(), repo_url, done, total, run_id),
    )
    conn.commit()


def end_run(conn: sqlite3.Connection, run_id: int, *, outcome: str, note: str = "") -> None:
    """런을 닫는다. **중단과 완주를 구분해서 남긴다** — 커밋 목록만으로는 갈리지 않는다."""
    now = _now()
    conn.execute(
        "UPDATE ingest_run SET updated_at = ?, ended_at = ?, outcome = ?, note = ? WHERE id = ?",
        (now, now, outcome, note[:500], run_id),
    )
    conn.commit()


def discard_run(conn: sqlite3.Connection, run_id: int) -> None:
    """읽은 것이 없는 런을 **이력에서 지운다.**

    워커는 주기마다 ingest 를 부르고 대개는 바뀐 것이 없다 — 그것까지 남기면
    하루에 1,440줄이 "바뀐 것이 없다"로 쌓여, 실제로 뭔가 읽은 런이 그 사이에
    묻힌다. 이 표가 답해야 할 물음은 "언제 무엇을 읽었나"이지 "몇 번 확인했나"가
    아니다. 확인했다는 사실은 **심박**이 진다.
    """
    conn.execute("DELETE FROM ingest_run WHERE id = ?", (run_id,))
    conn.commit()


def running(conn: sqlite3.Connection) -> Run | None:
    """지금 도는 런. **여럿일 수 없다** — 워커는 하나이고 틱은 순차다.

    그래도 가장 최근 것을 고르는 이유는 워커가 죽어 닫히지 않은 런이 남을 수
    있기 때문이다. 그 상태는 심박이 말해 준다.
    """
    row = conn.execute(
        "SELECT * FROM ingest_run WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return _run(row) if row else None


def abandon_stale(conn: sqlite3.Connection) -> int:
    """워커가 죽으며 남긴 런을 닫는다. **워커가 자기 기동 때 부른다.**

    남겨 두면 화면이 영원히 "도는 중"을 보여 준다 — 그것이 곧 거짓말이 되고,
    이 화면이 없애려던 바로 그 무지다.
    """
    rows = conn.execute("SELECT id FROM ingest_run WHERE ended_at IS NULL").fetchall()
    for row in rows:
        end_run(
            conn,
            int(row["id"]),
            outcome=FAILED,
            note="워커가 이 런을 닫지 않고 멈췄다 — 다음 기동에서 정리했다",
        )
    return len(rows)


def recent(conn: sqlite3.Connection, limit: int = 8) -> list[Run]:
    rows = conn.execute(
        "SELECT * FROM ingest_run ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_run(r) for r in rows]


def _run(row: sqlite3.Row) -> Run:
    return Run(
        id=int(row["id"]),
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        ended_at=row["ended_at"],
        outcome=row["outcome"],
        trigger=row["trigger"],
        repo_url=row["repo_url"],
        chunks_done=int(row["chunks_done"]),
        chunks_total=int(row["chunks_total"]),
        note=row["note"],
    )


# --- 커서 -----------------------------------------------------------------------


class RebuildRefused(RuntimeError):
    """지금은 커서를 지울 수 없다."""


def cursors(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """`(kind, cursor, updated_at)` — 어디까지 읽었는가 (ADR-006)."""
    rows = conn.execute(
        "SELECT kind, cursor, updated_at FROM ingest_checkpoint ORDER BY kind"
    ).fetchall()
    return [(r["kind"], r["cursor"], r["updated_at"]) for r in rows]


def rebuild(conn: sqlite3.Connection, kind: str = "") -> int:
    """커서를 지운다 — **처음부터 다시 읽는다.**

    **도는 중에는 거부한다.** 런이 끝나며 커서를 다시 쓰므로, 그때 지우면 지운 것이
    조용히 되살아나 운영자는 재구축을 눌렀는데 아무 일도 일어나지 않은 것으로 본다.

    지식 항목은 지우지 않는다. 다시 읽으면 같은 항목이 갱신되고, 사람이 고친 것은
    덮이지 않는다 (FR-6) — 지우는 것은 **진행 지점**뿐이다.
    """
    if running(conn) is not None:
        raise RebuildRefused(
            "런이 도는 중이다 — 먼저 중단한다. 지금 커서를 지우면 런이 끝나며 다시 쓴다"
        )
    if kind:
        cur = conn.execute("DELETE FROM ingest_checkpoint WHERE kind = ?", (kind,))
    else:
        cur = conn.execute("DELETE FROM ingest_checkpoint")
    conn.commit()
    return cur.rowcount


# --- 시각 -----------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse(when: str | None) -> datetime | None:
    if not when:
        return None
    try:
        parsed = datetime.fromisoformat(when)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _age_seconds(when: str | None) -> float:
    parsed = _parse(when)
    if parsed is None:
        return float("inf")
    return (datetime.now(UTC) - parsed).total_seconds()


def _between(start: str, end: str) -> float:
    a, b = _parse(start), _parse(end)
    if a is None or b is None:
        return 0.0
    return (b - a).total_seconds()


def _age_label(seconds: float) -> str:
    if seconds == float("inf"):
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}초"
    if seconds < 3600:
        return f"{seconds / 60:.0f}분"
    hours, minutes = divmod(int(seconds // 60), 60)
    return f"{hours}시간 {minutes}분" if minutes else f"{hours}시간"
