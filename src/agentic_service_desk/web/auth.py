"""대시보드 인증 (WBS-5.2.2).

**최종 이용자 인증이 아니다** — 그것은 모 시스템의 책임이고 범위 밖이다(§13).
여기서 지키는 것은 **이 화면 자체**다. 여기 오는 사람은 운영자 하나뿐이지만,
그 하나가 누르는 버튼이 승인·게재·모순 해결·국면 전진이다 — 붙을 수 있는 사람은
곧 누를 수 있는 사람이다.

## 왜 HTTP Basic 이 아닌가

기록된 위험은 인증과 **CSRF 둘을 함께** 말한다. Basic 은 앞의 것만 푼다 —
브라우저가 요청마다 자격을 자동으로 실어 보내므로, 다른 사이트의 form 이
우리 주소로 POST 하면 그것도 인증된 요청이 된다. 화면 전체가 form POST 로
돌아가는 이 대시보드에서 그것은 인증을 넣으나 마나로 만든다.

`SameSite=Strict` 세션 쿠키는 **한 장치로 둘을 덮는다**: 쿠키가 없으면 인증이
아니고, 다른 사이트에서 시작된 요청에는 브라우저가 쿠키를 싣지 않는다.

## 왜 별도 비밀키가 없는가

쿠키를 **암호 자체로 서명한다**. 비밀키를 따로 두면 그것을 어디에 저장할지가
새 문제가 되고(파일이면 백업에 섞이고, 설정이면 값이 둘이 된다), 1인 운영에서
그 대가는 값어치가 없다. 부수 효과가 오히려 바람직하다 — **암호를 바꾸면 살아
있는 세션이 전부 죽는다.**

암호를 쿠키에 싣지는 않는다. 싣는 것은 `발급시각.HMAC(암호, 발급시각)` 이라
쿠키를 훔쳐도 암호는 나오지 않고, 발급시각이 서명 안에 있어 **만료를 뒤로
미룰 수 없다.**
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

COOKIE = "asd_session"

OPEN_PATHS = frozenset({"/health", "/login"})
"""인증 없이 지나가는 경로.

`/health` 는 **살아 있는지만 답한다** — 기동 확인이 인증을 요구하면 배포 점검이
암호를 들고 다녀야 한다. `/login` 은 인증을 얻는 자리라 당연히 열려 있다.
"""


def _sign(password: str, issued: str) -> str:
    return hmac.new(password.encode(), issued.encode(), hashlib.sha256).hexdigest()


def issue(password: str, *, now: float | None = None) -> str:
    """세션 토큰을 만든다. 형태는 `발급시각.서명` 이다."""
    issued = str(int(now if now is not None else time.time()))
    return f"{issued}.{_sign(password, issued)}"


def valid(token: str | None, password: str, *, ttl_hours: int, now: float | None = None) -> bool:
    """이 토큰이 지금 유효한가.

    **서명을 먼저 보고 시각을 나중에 본다.** 순서가 반대면 서명되지 않은 시각을
    믿고 만료를 판정하게 된다.
    """
    if not token or not password:
        return False
    issued, _, signature = token.partition(".")
    if not issued or not signature:
        return False
    if not hmac.compare_digest(signature, _sign(password, issued)):
        return False
    try:
        age = (now if now is not None else time.time()) - int(issued)
    except ValueError:
        return False
    return 0 <= age <= ttl_hours * 3600


def matches(given: str, password: str) -> bool:
    """암호가 맞는가. **상수 시간으로 본다.**"""
    return bool(password) and hmac.compare_digest(given.encode(), password.encode())


def set_cookie(response, token: str, *, ttl_hours: int) -> None:  # noqa: ANN001
    """세션 쿠키를 붙인다.

    `samesite="strict"` 가 CSRF 를 막는 자리다 — 다른 사이트에서 시작된 요청에는
    브라우저가 이 쿠키를 싣지 않으므로, 남의 페이지에 숨은 form 이 우리 화면의
    승인·게재 버튼을 대신 누를 수 없다.

    `secure` 는 켜지 않는다. 이 화면은 사내망·tailnet 위에서 평문 http 로 도는
    것이 전제이고(§8), 켜면 그 구성에서 쿠키가 아예 저장되지 않아 **인증을 켠
    순간 로그인할 수 없게 된다.**
    """
    response.set_cookie(
        COOKIE, token, max_age=ttl_hours * 3600, httponly=True, samesite="strict"
    )


class RequireLogin(BaseHTTPMiddleware):
    """암호가 선언돼 있으면 화면 전체가 인증을 요구한다.

    **선언하지 않으면 아무것도 하지 않는다.** 루프백 개발 구성에 암호를 강제하면
    시험과 로컬 실행이 전부 그것을 들고 다녀야 하고, 그 부담은 결국 암호를 코드에
    적게 만든다. 대신 **인증 없이 밖에 열리는 것**은 기동에서 막는다
    (`preflight.check_live_exposure`) — 강제할 자리가 여기가 아니라 거기다.

    GET 은 로그인 화면으로 보내고 나머지는 **401 로 끊는다.** 판정 요청을 로그인
    화면으로 리다이렉트하면 브라우저가 그것을 성공으로 읽어, 누른 사람은 처리된
    줄 안다.
    """

    def __init__(self, app, *, password: str, ttl_hours: int) -> None:  # noqa: ANN001
        super().__init__(app)
        self._password = password
        self._ttl_hours = ttl_hours

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        path = request.url.path
        if path in OPEN_PATHS or valid(
            request.cookies.get(COOKIE), self._password, ttl_hours=self._ttl_hours
        ):
            return await call_next(request)
        if request.method == "GET":
            return RedirectResponse(f"/login?next={path}", status_code=303)
        return JSONResponse({"detail": "인증이 필요하다"}, status_code=401)
