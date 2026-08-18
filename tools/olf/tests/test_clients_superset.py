import pytest

from olf.clients.superset import SupersetClient


class _FakeResponse:
    def __init__(self, json_body: dict, status_code: int = 200) -> None:
        self._json_body = json_body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._json_body


def test_dashboards_logs_in_then_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse({"access_token": "tok-123"})

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers})
        return _FakeResponse({"result": [{"slug": "a", "dashboard_title": "A"}]})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", fake_get)

    client = SupersetClient("http://superset:8088")
    dashboards = client.dashboards()

    assert dashboards == [{"slug": "a", "dashboard_title": "A"}]
    assert calls[0]["url"] == "http://superset:8088/api/v1/security/login"
    assert calls[0]["json"] == {"username": "admin", "password": "admin", "provider": "db", "refresh": True}
    assert calls[1]["url"] == "http://superset:8088/api/v1/dashboard/"
    assert calls[1]["headers"]["Authorization"] == "Bearer tok-123"


def test_login_retries_until_it_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return _FakeResponse({}, status_code=500)
        return _FakeResponse({"access_token": "tok-456"})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    client = SupersetClient("http://superset:8088")
    token = client._login()

    assert token == "tok-456"
    assert attempts["n"] == 3
