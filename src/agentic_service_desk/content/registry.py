"""콘텐츠 타입 레지스트리 — **다섯 선언으로 타입을 더한다** (FR-42, §7.5).

새 콘텐츠 타입을 추가하는 것은 코드를 새로 짜는 일이 아니라 **선언을 추가하는
일**이다. 그래서 선언이 코드에 있지 않고 **파일에 있다** — 파이썬 상수로 두면
"코드 변경이 필요 없다"(FR-42 검증)를 시험이 실제로 확인할 수 없고, 확인할 수
없는 요구는 지켜지지 않아도 아무도 모른다.

기본 넷(FAQ · 가이드 · 칼럼 · 뉴스레터)은 이 패키지의 `types.toml` 로 함께 온다.
운영자가 더하는 타입은 `ASD_CONTENT_TYPES` 가 가리키는 파일에 선언하며, 그 파일은
기본 넷에 **더해질 뿐 덮지 않는다**.

## 선언은 검사받는다

선언만으로 돌아간다는 것은 **선언이 틀렸을 때 조용히 다르게 도는 것**과 종이 한 장
차이다. 그래서 등록 시점에 거부한다.

- **모르는 열쇠를 무시하지 않는다.** `final_chek = true` 는 오타지만, 무시하면
  발행물의 최종 확인이 **꺼진 채로** 지나간다 — 회수할 수 없는 것을 회수할 수 없게
  내보낸 뒤에야 드러난다
- **성격과 게재처가 어긋나면 거부한다** (§7.3·§7.7). 살아있는 문서를 발행 면에
  내면 갱신이 회차 누적이 되어 **같은 문서가 여러 벌**이 되고, 발행물을 문서 면에
  올리면 **지난 회차를 덮어쓴다** — 회수 불가인 것을 회수한 척하게 된다
- **발행물의 검수를 낮출 수 없다** (§7.3, §5.5.5). 발행물은 W3(게재 후 진실 변화)를
  해소할 수 없으므로 전문 검수 + 발행 직전 최종 확인이 하한이다

## 선언하지 않는 것

**검수자가 누구인가는 타입이 고르지 않는다.** FR-39 가 콘텐츠를 국면과 무관하게
전수 사람 승인으로 못 박았다 — 타입이 고를 수 있게 두면 그 요구는 선언 한 줄로
꺼지고, 답변과 달리 콘텐츠에는 자동 게재 관문 자체가 없다는 사실이 흐려진다.
타입이 고르는 것은 **범위**(변경분이냐 전문이냐)와 **추가 반려 사유**뿐이다.

**언어도 선언하지 않는다.** 콘텐츠는 1차 언어로 쓴다 (FR-43, D55) — 타입마다
다르면 살아있는 문서 하나가 언어별로 갈라진다.
"""

from __future__ import annotations

import enum
import tomllib
from dataclasses import dataclass
from pathlib import Path

#: 콘텐츠 검수자. **타입이 고르지 않는다** (FR-39) — 국면과도 무관하다.
REVIEWER = "human"

BUILTIN_PATH = Path(__file__).parent / "types.toml"


class InvalidDeclaration(ValueError):
    """선언이 성립하지 않는다. **등록 시점에 터진다** — 돌다가 아니라."""


class Input(enum.StrEnum):
    """주 입력 — 무엇을 읽는가 (§7.5-1)."""

    QNA_STATS = "qna_stats"
    KNOWLEDGE = "knowledge"
    BOTH = "both"


class Nature(enum.StrEnum):
    """성격 (§7.3). **타입을 가르는 가장 중요한 축이다.**"""

    LIVING = "living"
    """살아있는 문서 — 항상 최신이어야 한다. 근거가 낡으면 **갱신한다.**"""

    ISSUED = "issued"
    """발행물 — 발행 시점의 스냅샷. **고칠 수 없고 후속 회차로 바로잡는다.**"""


class Place(enum.StrEnum):
    """게재처의 자리 (§7.7). **타입은 넷이어도 자리는 둘이다.**"""

    DOCUMENT = "document"
    PUBLICATION = "publication"

    @property
    def operation(self) -> str:
        """자리가 곧 연산이다 (§7.7.1) — 타입마다 다른 API 가 필요하지 않다."""
        return "upsert" if self is Place.DOCUMENT else "create"


#: 성격이 자리를 정한다 (§7.3 → §7.7). 어긋난 선언은 등록되지 않는다.
PLACE_FOR: dict[Nature, Place] = {
    Nature.LIVING: Place.DOCUMENT,
    Nature.ISSUED: Place.PUBLICATION,
}


class Scope(enum.StrEnum):
    """검수 범위 (§5.5.5)."""

    DIFF = "diff"
    """변경분만. 살아있는 문서는 매 갱신마다 전문을 다시 읽을 수 없다."""

    FULL = "full"
    """전문. **발행물의 하한이다.**"""


@dataclass(frozen=True)
class Trigger:
    """트리거 (§7.5-2). **주기든 임계든 하나는 있어야 한다** — 없으면 돌지 않는다."""

    period_days: int | None = None
    threshold: str | None = None
    """임계의 이름. 무엇이 차면 도는가 — 값의 해석은 그 타입의 제작기가 한다."""

    threshold_value: float | None = None

    @property
    def periodic(self) -> bool:
        return self.period_days is not None


@dataclass(frozen=True)
class Destination:
    """게재처 (§7.5-4)."""

    place: Place
    path: str = ""
    """문서 면의 경로. **타입 안의 구분은 경로로 한다** — 자리를 늘리지 않는다
    (§7.7). 발행 면에는 없다: 회차는 경로를 갖지 않는다."""


@dataclass(frozen=True)
class Review:
    """검수 강도 (§7.5-5). **누가 보는가는 여기 없다** — FR-39 가 이미 정했다."""

    scope: Scope
    final_check: bool = False
    """발행 직전 최종 확인 (§7.3). 발행물의 필수 조건이다."""

    extra_rejections: tuple[str, ...] = ()
    """이 타입에만 붙는 반려 사유. 칼럼의 P6~P8 이 그것이다 (§7.6.4) —
    P1~P5 는 **사실 진술을 전제**하므로 칼럼에 그대로 쓰이지 않는다."""

    reviewer: str = REVIEWER


@dataclass(frozen=True)
class ContentType:
    """타입 하나 — **다섯 선언** (§7.5)."""

    id: str
    title: str
    input: Input
    trigger: Trigger
    nature: Nature
    destination: Destination
    review: Review

    @property
    def living(self) -> bool:
        return self.nature is Nature.LIVING


_TOP_KEYS = frozenset({"title", "input", "trigger", "nature", "destination", "review"})
_TRIGGER_KEYS = frozenset({"period_days", "threshold", "threshold_value"})
_DESTINATION_KEYS = frozenset({"place", "path"})
_REVIEW_KEYS = frozenset({"scope", "final_check", "extra_rejections"})


def _reject_unknown(where: str, given, allowed: frozenset[str]) -> None:  # noqa: ANN001
    """**모르는 열쇠를 무시하지 않는다.**

    오타는 조용히 기본값이 된다 — `final_chek` 하나로 발행물의 최종 확인이 꺼지고,
    그 사실은 회수할 수 없는 글이 나간 뒤에야 드러난다.
    """
    if not isinstance(given, dict):
        raise InvalidDeclaration(f"{where}: 표(table)여야 한다")
    unknown = sorted(set(given) - allowed)
    if unknown:
        raise InvalidDeclaration(
            f"{where}: 모르는 열쇠 {unknown} — 오타라면 조용히 기본값이 된다"
        )


def _enum(where: str, raw, cls):  # noqa: ANN001, ANN202
    try:
        return cls(raw)
    except ValueError:
        allowed = [str(m) for m in cls]
        raise InvalidDeclaration(f"{where}: {raw!r} 는 없다 — {allowed}") from None


def _parse(type_id: str, body: dict) -> ContentType:
    _reject_unknown(type_id, body, _TOP_KEYS)
    missing = sorted(_TOP_KEYS - set(body))
    if missing:
        raise InvalidDeclaration(
            f"{type_id}: {missing} 가 없다 — 다섯을 모두 선언해야 한다 (§7.5)"
        )

    trigger_raw = body["trigger"]
    _reject_unknown(f"{type_id}.trigger", trigger_raw, _TRIGGER_KEYS)
    trigger = Trigger(
        period_days=trigger_raw.get("period_days"),
        threshold=trigger_raw.get("threshold"),
        threshold_value=trigger_raw.get("threshold_value"),
    )
    if trigger.period_days is None and trigger.threshold is None:
        raise InvalidDeclaration(
            f"{type_id}.trigger: 주기도 임계도 없다 — 이 타입은 영영 돌지 않는다"
        )
    if trigger.threshold is not None and trigger.threshold_value is None:
        raise InvalidDeclaration(
            f"{type_id}.trigger: 임계 이름만 있고 값이 없다 — 무엇이 차야 도는지 모른다"
        )

    nature = _enum(f"{type_id}.nature", body["nature"], Nature)

    dest_raw = body["destination"]
    _reject_unknown(f"{type_id}.destination", dest_raw, _DESTINATION_KEYS)
    place = _enum(f"{type_id}.destination.place", dest_raw.get("place"), Place)
    if place is not PLACE_FOR[nature]:
        # 살아있는 문서를 발행 면에 내면 갱신이 회차 누적이 되고, 발행물을 문서
        # 면에 올리면 지난 회차를 덮어쓴다 — 둘 다 조용히 일어난다.
        raise InvalidDeclaration(
            f"{type_id}: 성격 {nature} 의 자리는 {PLACE_FOR[nature]} 다 — "
            f"{place} 로 선언됐다 (§7.3·§7.7)"
        )
    path = dest_raw.get("path", "")
    if place is Place.DOCUMENT and not path:
        raise InvalidDeclaration(f"{type_id}.destination: 문서 면에는 경로가 필요하다")
    if place is Place.PUBLICATION and path:
        raise InvalidDeclaration(
            f"{type_id}.destination: 발행 면에는 경로가 없다 — 회차는 경로를 갖지 않는다"
        )

    review_raw = body["review"]
    _reject_unknown(f"{type_id}.review", review_raw, _REVIEW_KEYS)
    review = Review(
        scope=_enum(f"{type_id}.review.scope", review_raw.get("scope"), Scope),
        final_check=bool(review_raw.get("final_check", False)),
        extra_rejections=tuple(review_raw.get("extra_rejections", ())),
    )
    if nature is Nature.ISSUED and not (review.scope is Scope.FULL and review.final_check):
        # 발행물은 W3 를 해소할 수 없다 (§7.3) — 이미 읽힌 회차는 되돌아오지 않는다.
        raise InvalidDeclaration(
            f"{type_id}.review: 발행물은 전문 검수 + 발행 직전 최종 확인이 하한이다"
        )

    return ContentType(
        id=type_id,
        title=str(body["title"]),
        input=_enum(f"{type_id}.input", body["input"], Input),
        trigger=trigger,
        nature=nature,
        destination=Destination(place=place, path=path),
        review=review,
    )


@dataclass(frozen=True)
class Registry:
    """등록된 타입 전부. **읽기 전용이다** — 선언은 파일에서만 온다."""

    types: dict[str, ContentType]

    def get(self, type_id: str) -> ContentType:
        try:
            return self.types[type_id]
        except KeyError:
            raise InvalidDeclaration(
                f"{type_id!r} 는 등록되지 않았다 — 선언 파일을 확인한다"
            ) from None

    def all(self) -> list[ContentType]:
        return list(self.types.values())

    def living(self) -> list[ContentType]:
        return [t for t in self.types.values() if t.living]

    def issued(self) -> list[ContentType]:
        return [t for t in self.types.values() if not t.living]


def _read(path: Path) -> dict[str, ContentType]:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InvalidDeclaration(f"선언 파일이 없다 — {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise InvalidDeclaration(f"선언 파일을 읽을 수 없다 — {path}: {exc}") from None
    return {tid: _parse(tid, body) for tid, body in raw.items()}


def load(extra: Path | None = None) -> Registry:
    """기본 넷을 읽고, 있으면 운영자의 선언을 **더한다.**

    **같은 id 를 덮지 않는다.** 덮게 두면 기본 타입의 검수 강도가 설정 한 줄로
    낮아지고 — 발행물이 변경분 검수가 되고 — 그 사실이 어디에도 남지 않는다.
    바꿔야 한다면 선언이 아니라 이 파일을 고칠 일이다.
    """
    types = _read(BUILTIN_PATH)
    if extra is not None:
        for tid, declared in _read(extra).items():
            if tid in types:
                raise InvalidDeclaration(
                    f"{tid!r} 는 이미 있다 — 덮어쓰면 검수 강도가 조용히 낮아질 수 있다"
                )
            types[tid] = declared
    return Registry(types=types)
