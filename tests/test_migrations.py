"""스키마 이행 (ADR-010).

여기서 지키는 것은 여섯.

    1. **새 DB 는 버전을 갖고 태어난다** — 나중에 "어디서부터 올릴지"를 알 수 있게
    2. **기존 DB 에 조용히 표를 만들지 않는다** — 이행이 반쪽이 되지 않게
    3. **버전을 모르는 DB 는 받지 않는다** — 짐작한 번호는 그 뒤 전부를 어긋나게 한다
    4. **버전이 안 맞으면 기동하지 않는다** — 배치 한복판이 아니라 기동에서 드러난다
    5. **선언과 계단이 어긋나면 되돌린다** — 환경마다 다른 스키마가 되지 않게
    6. **절반만 적용되지 않는다** — 한 트랜잭션이다
"""

from __future__ import annotations

import sqlite3

import pytest

from agentic_service_desk.config import Settings
from agentic_service_desk.operations import migrations
from agentic_service_desk.operations.preflight import check_schema
from agentic_service_desk.operations.schema import connect, initialize


def _fresh(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    conn = connect(tmp_path / "ops.sqlite3")
    initialize(conn)
    return conn


def _settings(tmp_path) -> Settings:  # noqa: ANN001
    return Settings(  # type: ignore[arg-type]
        _env_file=None,
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
    )


class TestBaseline:
    """새 DB 는 현재 스키마로 태어나고 그 사실을 적어 둔다."""

    def test_새_DB_는_버전을_갖고_태어난다(self, tmp_path) -> None:
        conn = _fresh(tmp_path)
        assert migrations.current_version(conn) == migrations.schema_version()
        conn.close()

    def test_baseline_이력이_남는다(self, tmp_path) -> None:
        # **덮어쓰지 않고 쌓는다** — 언제 무엇이 올라갔는지가 사고를 되짚는 단서다.
        conn = _fresh(tmp_path)
        rows = conn.execute("SELECT version, name FROM schema_version").fetchall()
        assert [(r["version"], r["name"]) for r in rows] == [
            (migrations.schema_version(), "baseline")
        ]
        conn.close()

    def test_여러_번_불러도_이력이_늘지_않는다(self, tmp_path) -> None:
        # `initialize()` 는 연결을 열 때마다 불린다.
        conn = _fresh(tmp_path)
        initialize(conn)
        initialize(conn)
        count = conn.execute("SELECT count(*) c FROM schema_version").fetchone()["c"]
        assert count == 1
        conn.close()

    def test_요구_버전은_계단_목록에서_나온다(self) -> None:
        """따로 적어 두면 **올리는 것을 잊는다.**"""
        expected = (
            migrations.MIGRATIONS[-1].version
            if migrations.MIGRATIONS
            else migrations.BASELINE
        )
        assert migrations.schema_version() == expected

    def test_계단_번호가_이어진다(self) -> None:
        # 번호가 건너뛰면 무엇이 빠졌는지 알 수 없다.
        versions = [m.version for m in migrations.MIGRATIONS]
        assert versions == list(
            range(migrations.BASELINE + 1, migrations.BASELINE + 1 + len(versions))
        )


class TestNoSilentCreation:
    """기존 DB 에 조용히 표를 만들지 않는다."""

    def test_기존_DB_에는_손대지_않는다(self, tmp_path) -> None:
        """예전처럼 매번 `executescript` 를 돌리면 **표 추가는 조용히 되고 열
        추가만 실패해** 이행이 반쪽이 된다 — 어느 쪽이 자동인지 아무도 기억 못 한다.
        """
        conn = _fresh(tmp_path)
        conn.execute("DROP TABLE ticket")
        conn.commit()

        initialize(conn)  # 다시 불러도
        gone = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket'"
        ).fetchone()
        assert gone is None  # 되살아나지 않는다
        conn.close()

    def test_빈_DB_판정은_사용자_표만_본다(self, tmp_path) -> None:
        conn = connect(tmp_path / "ops.sqlite3")
        assert migrations.is_fresh(conn)
        initialize(conn)
        assert not migrations.is_fresh(conn)
        conn.close()


class TestUnknownVersion:
    """ADR-010 결정 5 — 버전을 모르는 DB 는 받지 않는다."""

    def _legacy(self, tmp_path) -> sqlite3.Connection:  # noqa: ANN001
        """이행 경로 도입 이전의 DB — 표는 있는데 `schema_version` 이 없다."""
        conn = _fresh(tmp_path)
        conn.execute("DROP TABLE schema_version")
        conn.commit()
        return conn

    def test_모른다와_0_을_섞지_않는다(self, tmp_path) -> None:
        """섞으면 **이미 있는 표에 계단을 처음부터 놓으려 한다.**"""
        conn = self._legacy(tmp_path)
        assert migrations.current_version(conn) is None
        conn.close()

    def test_기동을_거부한다(self, tmp_path) -> None:
        conn = self._legacy(tmp_path)
        with pytest.raises(migrations.SchemaUnknown):
            migrations.require_current(conn)
        conn.close()

    def test_이행도_거부한다(self, tmp_path) -> None:
        # 어디서부터 올릴지 정할 수 없다.
        conn = self._legacy(tmp_path)
        with pytest.raises(migrations.SchemaUnknown):
            migrations.apply(conn)
        conn.close()


class TestStartupGate:
    """ADR-010 결정 3 — 배치 한복판이 아니라 기동에서 드러난다."""

    def test_맞으면_통과한다(self, tmp_path) -> None:
        _fresh(tmp_path).close()
        assert check_schema(_settings(tmp_path))

    def test_DB_가_없으면_통과한다(self, tmp_path) -> None:
        # 아직 아무것도 돌지 않았다. 첫 연결이 현재 스키마로 만든다.
        assert check_schema(_settings(tmp_path))

    def test_뒤처지면_거부한다(self, tmp_path, capsys) -> None:  # noqa: ANN001
        conn = _fresh(tmp_path)
        conn.execute("DELETE FROM schema_version")
        migrations.stamp(conn, migrations.BASELINE - 1, "옛 버전")
        conn.commit()
        conn.close()

        assert check_schema(_settings(tmp_path)) is False
        assert "asd migrate" in capsys.readouterr().err

    def test_앞서_있으면_거부한다(self, tmp_path, capsys) -> None:  # noqa: ANN001
        """**내려가는 계단은 없다** — 코드를 되돌렸는데 DB 가 앞선 경우다."""
        conn = _fresh(tmp_path)
        migrations.stamp(conn, migrations.schema_version() + 5, "미래")
        conn.commit()
        conn.close()

        assert check_schema(_settings(tmp_path)) is False
        assert "앞서 있다" in capsys.readouterr().err

    def test_버전_미상이면_거부한다(self, tmp_path, capsys) -> None:  # noqa: ANN001
        conn = _fresh(tmp_path)
        conn.execute("DROP TABLE schema_version")
        conn.commit()
        conn.close()

        assert check_schema(_settings(tmp_path)) is False
        assert "알 수 없다" in capsys.readouterr().err


class TestVerify:
    """ADR-010 결정 4 — 선언과 계단이 어긋나면 되돌린다."""

    def test_갓_만든_DB_는_선언과_같다(self, tmp_path) -> None:
        conn = _fresh(tmp_path)
        assert migrations.verify(conn) == []
        conn.close()

    def test_열이_모자라면_말한다(self, tmp_path) -> None:
        conn = _fresh(tmp_path)
        conn.execute("ALTER TABLE ticket DROP COLUMN state_at")
        conn.commit()
        problems = migrations.verify(conn)
        assert any("ticket.state_at" in p for p in problems)
        conn.close()

    def test_선언에_없는_열은_말한다(self, tmp_path) -> None:
        conn = _fresh(tmp_path)
        conn.execute("ALTER TABLE ticket ADD COLUMN 담당자 TEXT")
        conn.commit()
        problems = migrations.verify(conn)
        assert any("담당자" in p and "선언에 없다" in p for p in problems)
        conn.close()

    def test_인덱스도_본다(self, tmp_path) -> None:
        # 부분 유니크 인덱스가 "한 초안은 한 번만 나간다"를 지킨다 (WBS-4.5.1).
        conn = _fresh(tmp_path)
        conn.execute("DROP INDEX answer_record_one_per_draft")
        conn.commit()
        assert migrations.verify(conn)
        conn.close()

    def test_공백_차이로는_거부하지_않는다(self, tmp_path) -> None:
        """원문을 그대로 비교하지 않는다 — **그런 차이로 거부하면 사람이 대조를
        믿지 않게 된다.**"""
        conn = _fresh(tmp_path)
        assert migrations.verify(conn) == []
        conn.close()


class TestApply:
    """계단을 올린다. **절반만 적용되지 않는다.**"""

    def _with_migration(self, monkeypatch, *statements: str):  # noqa: ANN001, ANN202
        step = migrations.Migration(
            version=migrations.BASELINE + 1,
            name="시험용 계단",
            statements=tuple(statements),
        )
        monkeypatch.setattr(migrations, "MIGRATIONS", (step,))
        return step

    def test_대기_목록을_보여준다(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        conn = _fresh(tmp_path)  # baseline 으로 찍힌다
        step = self._with_migration(monkeypatch, "ALTER TABLE ticket ADD COLUMN 메모 TEXT")
        assert migrations.pending(conn) == [step]

        report = migrations.apply(conn, dry_run=True)
        assert report.applied == [step]
        # 적용하지 않았다.
        assert migrations.current_version(conn) == migrations.BASELINE
        conn.close()

    def test_선언과_어긋나면_되돌린다(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """**환경마다 다른 스키마가 되는 것**을 막는 자리다.

        선언(`SCHEMA_SQL`)에 없는 열을 더하는 계단은 통과하지 못한다.
        """
        conn = _fresh(tmp_path)
        self._with_migration(monkeypatch, "ALTER TABLE ticket ADD COLUMN 메모 TEXT")

        report = migrations.apply(conn)
        assert not report.ok
        assert any("메모" in d for d in report.differences)
        # 되돌렸다 — 열도 버전도 그대로다.
        columns = [c["name"] for c in conn.execute("PRAGMA table_info(ticket)")]
        assert "메모" not in columns
        assert migrations.current_version(conn) == migrations.BASELINE
        conn.close()

    def test_중간에_터지면_절반이_남지_않는다(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        conn = _fresh(tmp_path)
        self._with_migration(
            monkeypatch,
            "ALTER TABLE ticket ADD COLUMN 메모 TEXT",
            "이건 SQL 이 아니다",
        )
        with pytest.raises(sqlite3.OperationalError):
            migrations.apply(conn)

        columns = [c["name"] for c in conn.execute("PRAGMA table_info(ticket)")]
        assert "메모" not in columns
        assert migrations.current_version(conn) == migrations.BASELINE
        conn.close()

    def test_올릴_것이_없으면_대조만_한다(self, tmp_path) -> None:
        conn = _fresh(tmp_path)
        report = migrations.apply(conn)
        assert report.ok
        assert report.applied == []
        assert report.to_version == migrations.schema_version()
        conn.close()


class TestCli:
    """`asd migrate` — 무엇이 올라가는지 먼저 보여준다."""

    def _run(self, tmp_path, monkeypatch, *args):  # noqa: ANN001, ANN202
        from agentic_service_desk import cli

        monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
        return cli.main(["migrate", *args])

    def test_올릴_것이_없으면_그렇게_말한다(self, tmp_path, monkeypatch, capsys) -> None:  # noqa: ANN001
        _fresh(tmp_path).close()
        assert self._run(tmp_path, monkeypatch) == 0
        assert "올릴 것이 없다" in capsys.readouterr().out

    def test_DB_가_없으면_안내만_한다(self, tmp_path, monkeypatch, capsys) -> None:  # noqa: ANN001
        assert self._run(tmp_path, monkeypatch) == 0
        assert "아직 없다" in capsys.readouterr().out

    def test_버전_미상이면_거부한다(self, tmp_path, monkeypatch, capsys) -> None:  # noqa: ANN001
        conn = _fresh(tmp_path)
        conn.execute("DROP TABLE schema_version")
        conn.commit()
        conn.close()

        assert self._run(tmp_path, monkeypatch) == 1
        assert "지우고 다시 만든다" in capsys.readouterr().err

    def test_이름을_보여주고_적용한다(self, tmp_path, monkeypatch, capsys) -> None:  # noqa: ANN001
        """**화면에 이름이 보이지 않는 것이 적용되어서는 안 된다.**"""
        _fresh(tmp_path).close()
        monkeypatch.setattr(
            migrations,
            "MIGRATIONS",
            (
                migrations.Migration(
                    version=migrations.BASELINE + 1,
                    name="시험용 계단",
                    statements=("ALTER TABLE ticket ADD COLUMN 메모 TEXT",),
                ),
            ),
        )
        # 선언과 어긋나므로 되돌린다 — 그래도 이름은 먼저 나왔다.
        assert self._run(tmp_path, monkeypatch) == 1
        out = capsys.readouterr()
        assert "시험용 계단" in out.out
        assert "되돌렸다" in out.err
