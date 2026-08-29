"""알림 — 대시보드를 열지 않아도 아는 유일한 경로 (WBS-4.8.2, ADR-007 결정 2, O28).

운영자는 **1인 겸업**이고 대시보드를 매일 연다는 보장이 없다(§8.6.3). 그런데 Q4·Q5 는
"틀린 내용이 지금 이 순간 노출되고 있다"는 뜻이라 방치 비용이 높고(§8.2), 국면 역행은
검수 강도가 되돌아갔다는 뜻이다 — **며칠 몰라도 되는 일이 아니다.**

## 채널은 하나다

ADR-007 이 알림 채널을 웹훅 하나로 제한했다. 메일·SMS·앱을 더하는 순간 **표면이
번지고**, 표면이 번지면 어느 채널이 살아 있는지 아무도 모르게 된다. 설정이 비어 있으면
대시보드 배너로만 알린다 — 배너는 웹훅이 있어도 함께 뜬다: 알림이 도착하지 않은 것과
경고가 없는 것을 화면에서 구분할 수 없으면 침묵이 안전으로 읽힌다.

## 세는 것만 보낸다

**질문 원문도 지식 본문도 싣지 않는다.** 웹훅이 닿는 곳은 사내 메신저일 가능성이
높지만(ADR-007 귀결) 그 채널이 어디에 보관되고 누구에게 보이는지는 우리가 통제하지
못한다. PO-3 은 지식 항목에서 개인 식별자를 걷어내는 규칙인데, 그 규칙이 지켜지는
자리 밖으로 원문을 내보내면 규칙을 우회한 것이 된다.

그래서 알림은 **무엇이 몇 건 밀렸는지**와 **얼마나 오래됐는지**만 말한다. 그것으로
충분하다 — 알림의 목적은 판단이 아니라 **대시보드를 열게 하는 것**이다.

## 시간으로 재지 건수로 재지 않는다

Q4·Q5 의 위험은 "몇 건인가"가 아니라 **"얼마나 오래 노출됐는가"**다. 건수로 임계를
잡으면 한 건짜리 모순이 한 달을 방치돼도 조용하고, 반대로 하루 만에 다섯 건이 잡히면
아직 볼 시간도 없었는데 알림이 간다.

## 같은 상황으로 도배하지 않는다

배치는 분 단위로 돈다. 성립할 때마다 보내면 하루에 수백 건이 가고, 그러면 사람은
**채널을 음소거한다** — 알림이 있으나 마나가 되는 가장 흔한 방식이다.

그래서 **지문(fingerprint)이 같으면 다시 보내지 않는다.** 지문은 "가장 오래 밀린 것이
무엇인가"다. 그것이 처리되면 다음으로 오래된 것이 임계를 넘을 때 새 알림이 간다 —
건수가 늘 때마다 보내지 않는 이유는, 그 사이 사람이 할 수 있는 일이 달라지지 않기
때문이다.

## 실패하면 보냈다고 적지 않는다

전송이 실패했는데 지문을 남기면 **그 경고는 영영 가지 않는다.** 중복 발송과 침묵 중
어느 쪽이 나쁜지는 분명하다 — 게재(§5.2)가 기록을 먼저 남기는 것과 반대 방향의
선택이고, 이유도 반대다: 알림은 되돌릴 수 없는 대외 행위가 아니다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from agentic_service_desk.knowledge import contradiction, lint
from agentic_service_desk.pipeline import correction

RISK_QUEUE = "risk_queue"
"""Q4·Q5 방치 — **틀린 내용이 지금 노출되고 있다** (§8.2)."""

PHASE_REGRESSION = "phase_regression"
"""국면 자동 후퇴 — 검수 강도와 자동 승격 범위가 함께 되돌아갔다 (§1.3.3-c)."""

REGRESSION_DAYS = 7
"""후퇴를 알리는 기간.

**후퇴는 사건이지 상태가 아니다.** 지금 몇 국면인가는 국면 화면이 말하고(§8.3), 여기서
알리는 것은 "방금 내려갔다"는 소식이다. 기한을 두지 않으면 석 달 전 후퇴가 배너에
그대로 붙어 있고, 늘 떠 있는 경고는 **배경이 되어 다음 경고까지 안 보이게 만든다.**

웹훅에는 영향이 없다 — 지문 대조가 이미 한 번만 보내게 한다.
"""


@dataclass(frozen=True)
class Alert:
    """알릴 것 하나. **본문에 원문이 없다** — 세는 것만 담는다."""

    kind: str
    fingerprint: str
    """같은 상황인지 가르는 값. **지문이 같으면 다시 보내지 않는다.**"""

    title: str
    body: str

    @property
    def text(self) -> str:
        return f"[Agentic Service Desk] {self.title}\n{self.body}"


def pending(
    conn: sqlite3.Connection, *, neglect_hours: int, now: datetime | None = None
) -> list[Alert]:
    """지금 성립하는 경고. **배너와 웹훅이 같은 것을 본다.**

    화면과 알림이 다른 셈을 쓰면, 웹훅을 받고 대시보드를 열었는데 아무 경고도 없는
    일이 생긴다 — 그 한 번으로 사람은 알림을 믿지 않게 된다.
    """
    moment = now or datetime.now(UTC)
    found = []
    queue = _risk_queue(conn, neglect_hours=neglect_hours, now=moment)
    if queue is not None:
        found.append(queue)
    regression = _regression(conn, now=moment)
    if regression is not None:
        found.append(regression)
    return found


def _risk_queue(
    conn: sqlite3.Connection, *, neglect_hours: int, now: datetime
) -> Alert | None:
    """Q4·Q5 가 임계보다 오래 밀렸는가.

    **둘을 한 알림으로 묶는다.** 방치 비용이 같은 종류라 사람이 할 일도 같다 —
    대시보드를 열어 보는 것이다. 나눠 보내면 같은 아침에 두 번 울린다.
    """
    if neglect_hours <= 0:
        return None
    rows = conn.execute(
        "SELECT 'Q4' AS queue, id AS ref, detected_at AS since FROM contradiction "
        "WHERE state = ? "
        "UNION ALL "
        "SELECT 'Q5' AS queue, key AS ref, first_seen AS since FROM lint_finding "
        "WHERE kind = ? AND state = ? "
        "ORDER BY since",
        (contradiction.OPEN, correction.KIND, lint.OPEN),
    ).fetchall()
    aged = [r for r in rows if _hours(r["since"], now) >= neglect_hours]
    if not aged:
        return None

    oldest = aged[0]
    counts = {"Q4": 0, "Q5": 0}
    for row in rows:
        counts[row["queue"]] = counts.get(row["queue"], 0) + 1
    days = _hours(oldest["since"], now) / 24
    return Alert(
        kind=RISK_QUEUE,
        # **가장 오래 밀린 것이 지문이다.** 건수를 넣으면 한 건 늘 때마다 울린다.
        fingerprint=f"{oldest['queue']}:{oldest['ref']}",
        title="위험 대기열이 밀려 있다",
        body=(
            f"Q4 모순 {counts['Q4']}건 · Q5 정정 후보 {counts['Q5']}건. "
            f"가장 오래된 것은 {days:.0f}일 전이다 ({oldest['queue']}). "
            "틀린 내용이 지금 이 순간 노출되고 있다는 뜻이다 — 대시보드를 연다."
        ),
    )


def _regression(conn: sqlite3.Connection, *, now: datetime) -> Alert | None:
    """방금 내려간 국면. **사람이 내린 결정은 알리지 않는다** — 본인이 눌렀다.

    **가장 최근 결정일 때만 알린다.** 그 뒤에 운영자가 다시 올렸다면 후퇴는 이미
    다뤄진 일이고, 그것을 계속 띄우면 화면이 지난 일을 말하게 된다.
    """
    row = conn.execute(
        "SELECT id, from_phase, to_phase, decided_by, reason, decided_at "
        "FROM phase_decision ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None or row["decided_by"] != "system":
        return None
    if _hours(row["decided_at"], now) > REGRESSION_DAYS * 24:
        return None
    return Alert(
        kind=PHASE_REGRESSION,
        fingerprint=str(row["id"]),
        title=f"국면이 {row['from_phase']} → {row['to_phase']} 로 자동 후퇴했다",
        body=(
            f"{row['reason']}. 검수 강도(FR-57)와 자동 승격 범위(§6.8.4-b)가 함께 "
            "되돌아갔다 — 승인을 기다리지 않는다."
        ),
    )


def _hours(since: str, now: datetime) -> float:
    try:
        started = datetime.fromisoformat(since)
    except (TypeError, ValueError):
        return 0.0
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return (now - started) / timedelta(hours=1)


# --- 보내기 -------------------------------------------------------------------


def unsent(conn: sqlite3.Connection, alerts: list[Alert]) -> list[Alert]:
    """아직 보내지 않은 것만. **지문이 같으면 뺀다.**"""
    seen = {
        (row["kind"], row["fingerprint"])
        for row in conn.execute("SELECT kind, fingerprint FROM alert_sent").fetchall()
    }
    return [a for a in alerts if (a.kind, a.fingerprint) not in seen]


def dispatch(
    conn: sqlite3.Connection, *, url: str, alerts: list[Alert], timeout: float = 10.0
) -> tuple[list[Alert], list[str]]:
    """보내고, **성공한 것만** 보냈다고 적는다.

    실패했는데 적으면 그 경고는 영영 가지 않는다. 반대로 중복은 다음 주기에 한 번 더
    울리는 것으로 끝난다 — 어느 쪽이 나쁜지는 분명하다.

    **웹훅이 없으면 아무것도 하지 않는다.** 배너는 이 함수와 무관하게 뜬다.
    """
    if not url:
        return [], []
    sent: list[Alert] = []
    failures: list[str] = []
    for alert in alerts:
        try:
            response = httpx.post(url, json={"text": alert.text}, timeout=timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # **배치를 멈추지 않는다.** 알림이 못 갔다고 수집·ingest 까지 서면
            # 부수적인 것이 본체를 세운다.
            failures.append(f"{alert.kind}: {exc}")
            continue
        mark_sent(conn, alert)
        sent.append(alert)
    return sent, failures


def mark_sent(conn: sqlite3.Connection, alert: Alert) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO alert_sent (kind, fingerprint, sent_at) VALUES (?, ?, ?)",
        (alert.kind, alert.fingerprint, datetime.now(UTC).isoformat()),
    )
    conn.commit()
