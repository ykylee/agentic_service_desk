"""운영 명령.

    sync-harness  pi 하네스 설정 생성 (ADR-009). 배포 시 `.env` 를 바꾸고 이것을
                  돌리면 **애플리케이션과 pi 가 함께** 움직인다
    verify-edit   지식베이스의 사람 편집 세 조건 검사 (FR-54). **지식 저장소의
                  `pre-commit` 훅이 부른다** — 사람이 직접 칠 일은 드물다
    migrate       운영 DB 스키마 이행 (ADR-010). **사람이 명시적으로 돌린다** —
                  이행은 되돌리기 어려운 행위라 조용히 일어나면 안 되고, 온라인·배치
                  두 프로세스가 자동으로 올리면 서로 경쟁한다
"""

from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

from agentic_service_desk.config import load_settings
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.llm.harness import render_models_json, write_models_json
from agentic_service_desk.llm.policy import RemoteEndpointRejected
from agentic_service_desk.operations import migrations
from agentic_service_desk.operations.schema import connect


def _migrate(cfg, *, dry_run: bool) -> int:  # noqa: ANN001
    """스키마를 올린다. **무엇이 올라가는지 먼저 보여준다.**

    이행은 되돌리기 어려우므로, 화면에 이름이 보이지 않는 것이 적용되어서는 안 된다.
    """
    if not cfg.operations_db.exists():
        print(f"운영 DB 가 아직 없다: {cfg.operations_db}")
        print("앱이나 워커를 한 번 띄우면 현재 스키마로 만들어진다.")
        return 0

    conn = connect(cfg.operations_db)
    try:
        try:
            todo = migrations.pending(conn)
            version = migrations.current_version(conn)
        except migrations.SchemaProblem as exc:
            print(f"거부: {exc}", file=sys.stderr)
            return 1
        if version is None:
            print(
                "거부: 스키마 버전을 알 수 없다 — 이행 경로(ADR-010) 도입 이전의 "
                "DB 다. 짐작해서 번호를 찍으면 그 뒤의 모든 이행이 어긋나므로 받지 "
                "않는다. 개발 중이라면 파일을 지우고 다시 만든다.",
                file=sys.stderr,
            )
            return 1

        required = migrations.schema_version()
        if not todo:
            print(f"스키마 v{version} — 올릴 것이 없다 (코드 요구 v{required}).")
            problems = migrations.verify(conn)
            if problems:
                print("\n다만 선언과 다르다:", file=sys.stderr)
                for problem in problems:
                    print(f"  {problem}", file=sys.stderr)
                return 1
            return 0

        for migration in todo:
            print(f"{migration.version:03d} {migration.name}")
        if dry_run:
            print(f"\n{len(todo)}건 대기 (v{version} → v{required}). 적용하지 않았다.")
            return 0

        report = migrations.apply(conn)
        if not report.ok:
            print("\n되돌렸다 — 이행 결과가 선언과 다르다:", file=sys.stderr)
            for problem in report.differences:
                print(f"  {problem}", file=sys.stderr)
            print(
                "\n`schema.py` 의 SCHEMA_SQL 과 마이그레이션이 어긋났다. "
                "둘을 맞춘 뒤 다시 돌린다.",
                file=sys.stderr,
            )
            return 1
        print(f"\n스키마 v{report.to_version}. 앱을 다시 띄운다.")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="asd", description="Agentic Service Desk 운영 명령")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync-harness", help="pi 하네스 제공자 설정을 .env 에서 생성한다")
    p.add_argument("--dry-run", action="store_true", help="쓰지 않고 내용만 보여준다")

    v = sub.add_parser(
        "verify-edit", help="지식 편집의 세 조건을 검사한다 (pre-commit 훅이 부른다)"
    )
    v.add_argument("--root", help="지식 저장소 경로. 없으면 설정값을 쓴다")
    v.add_argument(
        "--message-file", required=True, help="커밋 메시지 파일. commit-msg 훅이 넘긴다"
    )

    m = sub.add_parser("migrate", help="운영 DB 스키마를 코드가 요구하는 버전으로 올린다")
    m.add_argument(
        "--dry-run", action="store_true", help="적용하지 않고 대기 목록만 보여준다"
    )

    args = parser.parse_args(argv)
    cfg = load_settings()

    if args.command == "migrate":
        return _migrate(cfg, dry_run=args.dry_run)

    if args.command == "verify-edit":
        repo = KnowledgeRepository(Path(args.root) if args.root else cfg.knowledge_dir)
        message = repo.read_commit_message(Path(args.message_file))
        problems = repo.verify_staged_edits(message)
        if not problems:
            return 0
        print("커밋을 멈춘다 — 사람 편집의 세 조건 (FR-54, §8.5.3)\n", file=sys.stderr)
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            "\n작업 트리의 수정은 그대로 있다. 빠진 것을 채워 다시 커밋한다.",
            file=sys.stderr,
        )
        return 1

    if args.command == "sync-harness":
        try:
            payload = render_models_json(cfg)
        except RemoteEndpointRejected as exc:
            print(f"거부: {exc}", file=sys.stderr)
            print(
                "\npi 는 애플리케이션의 NFR-1 정책을 지나지 않는다. "
                "설정을 만드는 이 시점이 유일한 검문소다 (ADR-009).",
                file=sys.stderr,
            )
            return 1
        except ValueError as exc:
            print(f"설정 부족: {exc}", file=sys.stderr)
            return 1

        if args.dry_run:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        target = write_models_json(cfg)
        print(f"생성했다: {target}")
        print(f"  baseUrl : {cfg.llm_base_url}")
        print(f"  model   : {cfg.llm_model}")
        print("  apiKey  : $ASD_LLM_API_KEY (파일에 키를 박지 않는다)")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
