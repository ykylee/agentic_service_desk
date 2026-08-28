"""Lint — 지식베이스 정합성 검사 (FR-7·8, §4).

llm-wiki 의 3연산 중 셋째다. Ingest 가 지식을 짓고 Query 가 꺼내 쓴다면, Lint 는
**쌓인 것이 아직 말이 되는지** 본다. 주기적으로 돌며 다섯 가지를 본다.

| 검사 | 무엇이 잘못됐는가 | 어디로 |
|---|---|---|
| 모순 미해결 | 사람과 에이전트의 판단이 어긋난 채 남아 있다 | Q4 (이미 티켓이 있다) |
| stale | 출처 커밋 이후 근거가 바뀌었다 | 항목에 **표시** + 현황의 stale 비율 |
| 고아 | 번들 목록에 등재되지 않아 **소비자에게 보이지 않는다** | 목록 재생성 |
| 끊어진 링크 | 답변이 가리키는 지식 항목이 없다 | Q5 |
| 참조 부재 | 지식 항목의 출처 커밋이 저장소에 없다 | Q5 |

**stale 은 대기열로 가지 않는다.** Q5 는 "근거가 낡은 **게재 답변·살아있는 문서**"의
정정 후보이지 지식 항목 자체가 아니다(§8.2). 지식 항목의 stale 은 §8.3 의 **현황
지표**이며, 그것이 게재물로 번져 Q5 가 되는 것은 stale 전파(WBS-4.5.7)의 몫이다.
지금 티켓을 찍으면 S0 에서는 Q5 가 화면에 뜨지도 않아(FR-59) **보이지 않는 대기열이
쌓인다.**

**아무것도 삭제하지 않는다.** 깨진 링크를 지우면 그 답변이 무엇에 근거했는지가
함께 사라진다 — 답은 이미 사람에게 나갔는데 근거만 없어지는 것이다. 지우는 대신
**정정 후보로 올린다** (ADR-002 결정 4).

**stale 도 삭제가 아니라 표시다** (FR-8). 낡았다는 것과 틀렸다는 것은 다르고,
그 판정은 사람이 한다.

> 두 저장소를 잇는 출처 링크에는 **강제할 외래키가 없다** — 한쪽은 파일이고 한쪽은
> DB 이기 때문이다. 그래서 무결성이 즉시 강제되지 않고 **사후 검사로** 지켜진다.
> 검사 주기가 곧 어긋난 채 지나는 시간이다.
"""

from __future__ import annotations

import enum
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agentic_service_desk.ingest.source import SourceMirror
from agentic_service_desk.knowledge import contradiction
from agentic_service_desk.knowledge.item import InvalidationKind, KnowledgeItem
from agentic_service_desk.knowledge.repository import KnowledgeRepository, StoredItem
from agentic_service_desk.operations import ticket as ticket_domain

CORRECTION = ticket_domain.Source.CORRECTION
"""티켓 출처. Q5(정정 후보) 대기열이 이것으로 걸린다 (§6.4.3)."""

OPEN = "open"
RESOLVED = "resolved"


class Kind(enum.StrEnum):
    """대기열로 올라가는 소견의 종류.

    stale 이 여기 없는 것이 요점이다 — 그것은 대기열이 아니라 현황 지표다.
    """

    BROKEN_LINK = "broken_link"
    MISSING_REFERENCE = "missing_reference"


@dataclass(frozen=True)
class Finding:
    """소견 하나."""

    kind: Kind
    subject: str
    """지식 항목 id 또는 답변 이력 id."""

    detail: str

    @property
    def key(self) -> str:
        """같은 소견을 다시 열지 않기 위한 열쇠.

        주기 실행이라 고쳐지기 전까지 매번 나온다. 매번 티켓을 찍으면 **대기열이 같은
        항목으로 메워져 우선순위를 매길 수 없다** (§8.6) — 모순 쪽과 같은 이유다.
        """
        return f"{self.kind}:{self.subject}"


@dataclass
class LintReport:
    """한 번의 Lint 가 무엇을 봤는가."""

    open_contradictions: int = 0
    findings: list[Finding] = field(default_factory=list)
    newly_opened: int = 0
    marked_stale: list[str] = field(default_factory=list)
    indexed: int = 0
    """번들 목록에 새로 등재한 항목 수 (고아 해소)."""

    index_rewritten: bool = False
    """목록 파일이 실제로 바뀌었는가.

    등재 수와 따로 두는 이유가 있다 — 항목 수가 그대로여도 제목이나 stale 표시가
    바뀌면 파일은 바뀐다. 이것을 보지 않고 커밋을 건너뛰면 **작업 트리가 계속
    더러운 채로 남고**, 그 변경이 다음 ingest 커밋에 섞여 들어간다.
    """

    broken_files: list[str] = field(default_factory=list)
    commit: str | None = None

    @property
    def clean(self) -> bool:
        """4.2 완료 조건이 묻는 것 — **모순 미해결 0, 깨진 링크 0.**"""
        return not self.open_contradictions and not self.findings

    def summary(self) -> str:
        return (
            f"모순 {self.open_contradictions} · 소견 {len(self.findings)}"
            f" (새로 연 것 {self.newly_opened}) · stale {len(self.marked_stale)}"
        )


def _now() -> datetime:
    return datetime.now(UTC)


class Lint:
    """지식베이스를 훑는다."""

    def __init__(
        self,
        *,
        repo: KnowledgeRepository,
        conn: sqlite3.Connection,
        mirror: SourceMirror | None = None,
    ) -> None:
        self._repo = repo
        self._conn = conn
        self._mirror = mirror

    def run(self) -> LintReport:
        self._repo.ensure_initialized()
        report = LintReport()

        stored, report.broken_files = self._repo.scan()
        report.open_contradictions = len(contradiction.list_open(self._conn))

        report.findings.extend(self._check_references(stored))
        self._check_stale(stored, report)
        report.findings.extend(self._check_broken_links(stored))
        report.indexed, report.index_rewritten = self._rebuild_index(stored)

        report.newly_opened = self._open_findings(report.findings)
        if report.marked_stale or report.index_rewritten:
            self._repo.append_log(f"lint — {report.summary()}")
            report.commit = self._repo.commit(f"lint: {report.summary()}")
        return report

    # --- 참조 부재 --------------------------------------------------------

    def _check_references(self, stored: list[StoredItem]) -> list[Finding]:
        """지식 항목의 출처 커밋이 저장소에 실재하는가 (ADR-002 결정 4).

        미러가 없으면 **검사하지 않는다** — 없는 것과 사라진 것은 다르고, 구분하지
        못한 채 전부를 참조 부재로 올리면 대기열이 거짓 소견으로 가득 찬다.
        """
        if self._mirror is None or not self._mirror.is_cloned:
            return []
        findings: list[Finding] = []
        seen: dict[str, bool] = {}
        for s in stored:
            missing = [
                p.commit
                for p in s.item.provenance
                if p.commit and not seen.setdefault(p.commit, self._mirror.has_commit(p.commit))
            ]
            if missing:
                findings.append(
                    Finding(
                        kind=Kind.MISSING_REFERENCE,
                        subject=s.item.id,
                        detail=(
                            f"출처 커밋이 저장소에 없다: {', '.join(c[:8] for c in missing)}. "
                            f"이 지식이 무엇에 근거했는지 확인할 수 없다"
                        ),
                    )
                )
        return findings

    # --- stale ------------------------------------------------------------

    def _check_stale(self, stored: list[StoredItem], report: LintReport) -> None:
        """근거가 낡았는가 (FR-8). **표시만 한다 — 삭제하지도, 대기열로 올리지도 않는다.**

        `linked` 는 커밋 기준으로 본다 — 출처 커밋 이후 그 경로가 바뀌었는가.
        **소스코드 원천이 있어서 시간이 아니라 커밋으로 판정된다**는 것이 일반 위키
        대비 이 시스템의 우위다 (§4).

        `periodic` 은 묶을 대상이 없을 때의 대비책이므로 시간으로 본다.
        """
        changed_cache: dict[str, set[str]] = {}
        for s in stored:
            if s.item.stale:
                continue  # 이미 표시돼 있다
            if not self._stale_reason(s, changed_cache):
                continue
            s.item.stale = True
            self._repo.save(s.item, at=s.path)
            report.marked_stale.append(s.item.id)

    def _stale_reason(self, s: StoredItem, cache: dict[str, set[str]]) -> str | None:
        inv = s.item.invalidation
        if inv.kind is InvalidationKind.LINKED:
            return self._linked_stale_reason(s.item, cache)
        if inv.period_days:
            confirmed = self._repo.last_commit_date(s.path)
            if not confirmed:
                return None
            due = datetime.fromisoformat(confirmed) + timedelta(days=inv.period_days)
            if _now() >= due:
                return (
                    f"재확인 주기 {inv.period_days}일이 지났다 "
                    f"(마지막 확인 {confirmed[:10]})"
                )
        return None

    def _linked_stale_reason(
        self, item: KnowledgeItem, cache: dict[str, set[str]]
    ) -> str | None:
        """**가장 최근에 반영한 출처 커밋** 하나만 본다.

        출처는 갱신할 때마다 쌓인다 — 그것이 "근거가 여러 커밋에 걸친다"(ADR-003)를
        지키는 방식이다. 그런데 **쌓인 것을 전부 보면 갱신된 항목이 영원히 stale
        이 된다**: 오래된 출처 커밋 이후로는 당연히 그 경로가 바뀌었기 때문이다.
        방금 최신 커밋으로 다시 지은 항목까지 낡았다고 부르면 stale 은 아무것도
        가리키지 않게 되고 Q5 는 전부 거짓 소견으로 찬다.

        묻는 것은 "이 항목을 마지막으로 지은 뒤에 근거가 또 바뀌었는가"이므로
        마지막 출처가 답을 안다. 목록의 순서가 곧 반영 순서다 — 갱신은 기존 출처
        뒤에 새 것을 덧붙인다.
        """
        if self._mirror is None or not self._mirror.is_cloned:
            return None
        refs = set(item.invalidation.refs)
        if not refs:
            return None
        latest = next(
            (
                p.commit
                for p in reversed(item.provenance)
                if p.commit and self._mirror.has_commit(p.commit)
            ),
            None,
        )
        if latest is None:
            return None
        if latest not in cache:
            cache[latest] = set(self._mirror.changed_paths_since(latest))
        touched = refs & cache[latest]
        if not touched:
            return None
        return f"출처 커밋 {latest[:8]} 이후 근거가 바뀌었다: {', '.join(sorted(touched))}"

    # --- 끊어진 링크 ------------------------------------------------------

    def _check_broken_links(self, stored: list[StoredItem]) -> list[Finding]:
        """답변 이력이 가리키는 지식 항목이 실재하는가 (ADR-002 결정 4).

        **삭제하지 않고 Q5 로 올린다.** 링크가 끊겼다는 것은 근거를 알 수 없게 됐다는
        뜻이고, 그 답변은 이미 사람에게 나갔으므로 재검토 대상이다.
        """
        alive = {s.item.id for s in stored}
        rows = self._conn.execute(
            "SELECT answer_record_id, knowledge_item_id, pinned_commit FROM answer_grounding"
        ).fetchall()
        return [
            Finding(
                kind=Kind.BROKEN_LINK,
                subject=row["answer_record_id"],
                detail=(
                    f"게재한 답변의 근거 지식 항목이 없다: {row['knowledge_item_id']} "
                    f"(고정 커밋 {row['pinned_commit'][:8]}). 재검토가 필요하다"
                ),
            )
            for row in rows
            if row["knowledge_item_id"] not in alive
        ]

    # --- 고아 ------------------------------------------------------------

    def _rebuild_index(self, stored: list[StoredItem]) -> tuple[int, bool]:
        """번들 목록(`index.md`)을 다시 쓴다 (OKF §3.1).

        **고아는 목록에 없는 항목이다.** OKF 소비자는 이 목록으로 번들을 훑으므로,
        등재되지 않은 항목은 파일이 있어도 **없는 것과 같다.**

        찾아서 대기열로 올리는 대신 **그 자리에서 고친다.** 사람이 판단할 것이 없는
        고장이기 때문이다 — 대기열은 판단이 필요한 것만 담아야 소화된다 (§8.6).
        돌려주는 것은 `(새로 등재된 수, 파일이 바뀌었는가)` 다.
        """
        index = self._repo.root / "index.md"
        before = index.read_text(encoding="utf-8") if index.exists() else ""
        listed = sum(1 for line in before.splitlines() if line.startswith("- ["))

        lines = [
            "# Knowledge Bundle",
            "",
            "이 디렉터리는 OKF Knowledge Bundle 이다. 지식 항목만 담으며 "
            "운영 데이터(티켓·QnA 기록)는 포함하지 않는다.",
            "",
            f"## 항목 {len(stored)}건",
            "",
        ]
        for s in sorted(stored, key=lambda x: x.item.title):
            rel = s.path.relative_to(self._repo.root).as_posix()
            mark = " *(stale)*" if s.item.stale else ""
            lines.append(f"- [{s.item.title}]({rel}) — `{s.item.id}`{mark}")
        after = "\n".join(lines) + "\n"

        if after == before:
            return 0, False
        index.write_text(after, encoding="utf-8")
        return max(0, len(stored) - listed), True

    # --- 대기열 ----------------------------------------------------------

    def _open_findings(self, findings: list[Finding]) -> int:
        """소견을 Q5 로 올린다. **이미 열려 있으면 다시 열지 않는다.**"""
        opened = 0
        now = _now().isoformat()
        for finding in findings:
            exists = self._conn.execute(
                "SELECT 1 FROM lint_finding WHERE key = ? AND state = ?", (finding.key, OPEN)
            ).fetchone()
            if exists:
                continue
            ticket_id = ticket_domain.issue(self._conn, source=CORRECTION).id
            self._conn.execute(
                "INSERT INTO lint_finding "
                "(key, kind, subject, detail, ticket_id, first_seen, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "state = excluded.state, detail = excluded.detail, "
                "ticket_id = excluded.ticket_id, first_seen = excluded.first_seen",
                (
                    finding.key,
                    str(finding.kind),
                    finding.subject,
                    finding.detail,
                    ticket_id,
                    now,
                    OPEN,
                ),
            )
            opened += 1
        self._conn.commit()
        return opened


def list_open(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Q5 대기열. **방치 비용이 높다** — 틀린 내용이 계속 노출된다 (§8.2)."""
    return list(
        conn.execute(
            "SELECT * FROM lint_finding WHERE state = ? ORDER BY first_seen", (OPEN,)
        )
    )


def resolve(conn: sqlite3.Connection, key: str) -> None:
    """사람이 처리했다. 티켓도 함께 닫는다."""
    now = _now().isoformat()
    row = conn.execute(
        "SELECT ticket_id, kind FROM lint_finding WHERE key = ? AND state = ?", (key, OPEN)
    ).fetchone()
    if row is None:
        return
    conn.execute(
        "UPDATE lint_finding SET state = ?, resolved_at = ? WHERE key = ?", (RESOLVED, now, key)
    )
    conn.commit()
    conn.execute(
        "INSERT INTO ticket_resolution "
        "(ticket_id, generalized_question, answer, grounding, invalidation) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(ticket_id) DO NOTHING",
        (row["ticket_id"], "Lint 소견을 어떻게 처리하는가", f"처리함: {row['kind']}", "[]", "{}"),
    )
    conn.commit()
    ticket_domain.transition(conn, row["ticket_id"], ticket_domain.State.CLOSED)
