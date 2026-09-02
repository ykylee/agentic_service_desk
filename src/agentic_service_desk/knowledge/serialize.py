"""지식 항목 ↔ 파일 (FR-5, FR-55).

`KnowledgeItem` 을 OKF frontmatter 를 가진 마크다운으로 쓰고 다시 읽는다.
**한 파일이 한 개념이다** (ADR-003).

읽기가 쓰기보다 까다롭다. 파일은 **사람이 고칠 수 있고**(D37), 고친 파일도 다시
읽혀야 하기 때문이다. 그래서 파싱은 관대하되 **불변식은 양보하지 않는다** —
출처가 없거나 무효화 조건이 없는 파일은 지식 항목으로 인정하지 않는다.
그것을 통과시키면 D3(출처는 1급 시민)가 파일 왕복 한 번으로 무너진다.
"""

from __future__ import annotations

import os

import re
from pathlib import Path

import yaml

from agentic_service_desk.knowledge.layout import TMP_SUFFIX
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


class MalformedItem(ValueError):
    """지식 항목으로 읽을 수 없는 파일이다."""


def to_markdown(item: KnowledgeItem) -> str:
    """항목을 파일 내용으로 만든다.

    `sort_keys=False` 로 쓴다 — 키 순서가 매번 달라지면 **git diff 가 의미 없는 변경으로
    가득 차** 1 커밋 = 1 ingest 의 이력이 읽히지 않는다.
    `allow_unicode` 도 마찬가지다: 한국어 제목이 이스케이프되면 사람이 못 읽는다.
    """
    front: dict = {
        "type": item.okf_type,
        "id": item.id,
        "title": item.title,
        "provenance": [_provenance_to_dict(p) for p in item.provenance],
        "invalidation": _invalidation_to_dict(item.invalidation),
        "stale": item.stale,
        "edited_by_human": item.edited_by_human,
    }
    dumped = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).rstrip("\n")
    return f"---\n{dumped}\n---\n\n{item.body.rstrip()}\n"


def from_markdown(text: str) -> KnowledgeItem:
    """파일 내용을 항목으로 읽는다. 불변식을 어기면 거부한다."""
    match = _FRONTMATTER.match(text)
    if not match:
        raise MalformedItem("frontmatter 가 없다")
    try:
        front = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise MalformedItem(f"frontmatter 를 읽을 수 없다: {exc}") from exc
    if not isinstance(front, dict):
        raise MalformedItem("frontmatter 가 매핑이 아니다")

    raw_provenance = front.get("provenance") or []
    if not raw_provenance:
        raise MalformedItem("출처가 없다 — 지식 항목이 아니다 (D3, FR-4)")
    if not front.get("invalidation"):
        raise MalformedItem("무효화 조건이 없다 (§6.5.3)")

    try:
        provenance = [_provenance_from_dict(p) for p in raw_provenance]
        invalidation = _invalidation_from_dict(front["invalidation"])
    except (TypeError, ValueError, KeyError) as exc:
        raise MalformedItem(f"frontmatter 값이 형식을 어긴다: {exc}") from exc

    item = KnowledgeItem(
        title=front.get("title") or "",
        body=match.group(2).strip(),
        provenance=provenance,
        invalidation=invalidation,
        stale=bool(front.get("stale", False)),
        edited_by_human=bool(front.get("edited_by_human", False)),
        okf_type=front.get("type", "knowledge"),
    )
    if front.get("id"):
        # **파일에 있는 id 를 그대로 쓴다.** 새로 만들면 답변 이력의 근거 링크가
        # 끊긴다 (ADR-002) — 이 대입이 그 링크를 지키는 지점이다.
        item.id = str(front["id"])
    return item


def read_item(path: Path) -> KnowledgeItem:
    return from_markdown(path.read_text(encoding="utf-8"))


def write_item(path: Path, item: KnowledgeItem) -> None:
    """항목을 **원자적으로** 쓴다 (WBS-5.6.1).

    같은 파일을 읽는 쪽이 늘 있다 — 질의는 `scan()` 으로 작업 트리를 매번 통째로
    읽고, ingest 는 지식을 짓는 동안 계속 쓴다. `write_text` 는 자리에 바로 쓰므로
    그 사이에 읽으면 **쓰다 만 내용**이 잡혀 `MalformedItem` 이 되고, 그것이
    `broken_files` 로 세어져 없는 고장이 대기열에 오른다.

    같은 디렉터리에 쓰고 `os.replace` 로 바꿔 끼운다 — 같은 파일 시스템이라야
    원자적이므로 임시 파일을 옆에 둔다. 읽는 쪽은 **옛 내용이나 새 내용 중
    하나**를 보고 그 중간은 보지 않는다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    tmp.write_text(to_markdown(item), encoding="utf-8")
    os.replace(tmp, path)


def slugify(title: str) -> str:
    """제목에서 파일 이름을 만든다.

    **식별자가 아니라 사람이 읽기 위한 것이다** — 식별은 불변 `id` 가 한다(ADR-002).
    그래서 한글을 로마자로 옮기지 않고 그대로 둔다. 옮기면 읽기 어려워지기만 한다.
    """
    slug = re.sub(r"[^\w가-힣-]+", "-", title.strip(), flags=re.UNICODE)
    slug = re.sub(r"-{2,}", "-", slug).strip("-").lower()
    return slug[:80] or "untitled"


# --- frontmatter 값 변환 --------------------------------------------------


def _provenance_to_dict(p: Provenance) -> dict:
    out: dict = {}
    if p.commit:
        out["commit"] = p.commit
    if p.path:
        out["path"] = p.path
    if p.qna:
        out["qna"] = p.qna
    return out


def _provenance_from_dict(raw: object) -> Provenance:
    if not isinstance(raw, dict):
        raise TypeError("출처 항목이 매핑이 아니다")
    return Provenance(
        commit=raw.get("commit"), path=raw.get("path"), qna=raw.get("qna")
    )


def _invalidation_to_dict(inv: Invalidation) -> dict:
    out: dict = {"kind": str(inv.kind)}
    if inv.refs:
        out["refs"] = list(inv.refs)
    if inv.period_days:
        out["period_days"] = inv.period_days
    return out


def _invalidation_from_dict(raw: object) -> Invalidation:
    if not isinstance(raw, dict):
        raise TypeError("무효화 조건이 매핑이 아니다")
    return Invalidation(
        kind=InvalidationKind(raw["kind"]),
        refs=tuple(raw.get("refs") or ()),
        period_days=raw.get("period_days"),
    )
