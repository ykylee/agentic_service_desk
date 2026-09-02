"""대시보드 인증 (WBS-5.2.2).

기록된 위험은 인증과 CSRF **둘**을 함께 말한다. 한 장치(`SameSite=Strict` 세션
쿠키)가 둘을 덮는지가 이 파일이 세는 것이다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.web import auth
from agentic_service_desk.web.app import create_app

PASSWORD = "열려라참깨"


def _settings(tmp_path, **over) -> Settings:  # noqa: ANN001, ANN003
    base = dict(
        _env_file=None,
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
    )
    return Settings(**(base | over))  # type: ignore[arg-type]


def _client(tmp_path, **over) -> TestClient:  # noqa: ANN001, ANN003
    return TestClient(create_app(_settings(tmp_path, **over)))


class TestToken:
    """쿠키에 무엇이 실리는가."""

    def test_암호는_쿠키에_실리지_않는다(self) -> None:
        # 쿠키를 훔쳐도 암호는 나오지 않아야 한다.
        assert PASSWORD not in auth.issue(PASSWORD)

    def test_발급한_토큰은_유효하다(self) -> None:
        assert auth.valid(auth.issue(PASSWORD), PASSWORD, ttl_hours=12)

    def test_다른_암호로는_통하지_않는다(self) -> None:
        # 암호를 바꾸면 살아 있는 세션이 전부 죽는다 — 의도한 부수 효과다.
        assert not auth.valid(auth.issue(PASSWORD), "다른암호", ttl_hours=12)

    def test_기간이_지나면_죽는다(self) -> None:
        old = auth.issue(PASSWORD, now=0)
        assert not auth.valid(old, PASSWORD, ttl_hours=12, now=13 * 3600)

    def test_발급시각을_고쳐도_만료를_미룰_수_없다(self) -> None:
        # 발급 시각이 **서명 안에** 있다. 시각만 바꾸면 서명이 어긋난다.
        token = auth.issue(PASSWORD, now=0)
        _, _, signature = token.partition(".")
        forged = f"{99 * 3600}.{signature}"
        assert not auth.valid(forged, PASSWORD, ttl_hours=12, now=99 * 3600)

    def test_모양이_아니면_거절한다(self) -> None:
        for junk in ("", None, "점이없다", ".", "1.", ".sig"):
            assert not auth.valid(junk, PASSWORD, ttl_hours=12)


class TestDisabled:
    """암호를 선언하지 않으면 종전대로 돈다 — 루프백 개발 구성을 깨지 않는다."""

    def test_인증을_켜지_않으면_그냥_들어간다(self, tmp_path) -> None:
        client = _client(tmp_path)
        assert client.get("/").status_code == 200

    def test_나간다_버튼이_보이지_않는다(self, tmp_path) -> None:
        # 나갈 곳이 없는데 버튼만 있으면 눌러도 아무 일이 없다.
        assert "/logout" not in _client(tmp_path).get("/").text


class TestEnabled:
    """암호를 선언하면 화면 전체가 막힌다."""

    def test_인증_없이는_화면을_못_연다(self, tmp_path) -> None:
        client = _client(tmp_path, web_password=PASSWORD)
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")

    def test_health_는_열려_있다(self, tmp_path) -> None:
        # 기동 확인이 암호를 들고 다니게 하지 않는다.
        client = _client(tmp_path, web_password=PASSWORD)
        assert client.get("/health").status_code == 200

    def test_판정_요청은_리다이렉트가_아니라_401_이다(self, tmp_path) -> None:
        # 로그인 화면으로 보내면 브라우저가 그것을 성공으로 읽어, 누른 사람은
        # **처리된 줄 안다.**
        client = _client(tmp_path, web_password=PASSWORD)
        response = client.post("/queues/Q4/x/resolve", data={"resolution": "y"},
                               follow_redirects=False)
        assert response.status_code == 401

    def test_맞는_암호로_들어가면_화면이_열린다(self, tmp_path) -> None:
        client = _client(tmp_path, web_password=PASSWORD)
        assert client.post("/login", data={"password": PASSWORD, "next": "/"},
                           follow_redirects=False).status_code == 303
        assert client.get("/").status_code == 200

    def test_틀린_암호는_들여보내지_않는다(self, tmp_path) -> None:
        client = _client(tmp_path, web_password=PASSWORD)
        response = client.post("/login", data={"password": "틀림"}, follow_redirects=False)
        assert response.status_code == 401
        assert auth.COOKIE not in response.cookies

    def test_나가면_다시_막힌다(self, tmp_path) -> None:
        client = _client(tmp_path, web_password=PASSWORD)
        client.post("/login", data={"password": PASSWORD})
        client.post("/logout")
        assert client.get("/", follow_redirects=False).status_code == 303


class TestCsrf:
    """다른 사이트의 form 이 우리 버튼을 대신 누르지 못한다."""

    def test_세션_쿠키는_samesite_strict_다(self, tmp_path) -> None:
        # 이 한 줄이 CSRF 를 막는 자리다 — 다른 사이트에서 시작된 요청에는
        # 브라우저가 이 쿠키를 싣지 않는다.
        client = _client(tmp_path, web_password=PASSWORD)
        response = client.post("/login", data={"password": PASSWORD},
                               follow_redirects=False)
        cookie = response.headers["set-cookie"]
        assert "samesite=strict" in cookie.lower()
        assert "httponly" in cookie.lower()


class TestNextTarget:
    """로그인 뒤에 어디로 보내는가."""

    def test_원래_가려던_화면으로_보낸다(self, tmp_path) -> None:
        client = _client(tmp_path, web_password=PASSWORD)
        response = client.post("/login", data={"password": PASSWORD, "next": "/queues/Q4"},
                               follow_redirects=False)
        assert response.headers["location"] == "/queues/Q4"

    def test_남의_주소로는_보내지_않는다(self, tmp_path) -> None:
        client = _client(tmp_path, web_password=PASSWORD)
        for hostile in ("https://evil.example/x", "//evil.example/x"):
            response = client.post("/login", data={"password": PASSWORD, "next": hostile},
                                   follow_redirects=False)
            assert response.headers["location"] == "/"
