"""Signing in with the device flow, saved sign-ins, and which credentials are used."""

import os
import stat
import sys
import time
from unittest.mock import patch

import pytest
import requests
import requests_mock

from physionet import auth
from physionet.api.client import PhysioNetClient
from physionet.cli import main

BASE = "https://physionet.org"
START = {
    "device_code": "device-code",
    "user_code": "WDJB-MJHT",
    "verification_uri": f"{BASE}/oauth/device/",
    "expires_in": 1800,
    "interval": 5,
}
TOKEN = {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 36000,
         "scope": "data:download profile:read", "token_type": "Bearer"}


@pytest.fixture(autouse=True)
def no_waiting():
    with patch.object(auth.time, "sleep"):
        yield


def save(record=None, base_url=BASE):
    record = record or {"access_token": "saved", "refresh_token": "saved-refresh",
                        "expires_at": time.time() + 3600, "username": "rgmark"}
    auth._save_login(base_url, record)
    return record


def token_responses(*responses):
    return [{"json": body, "status_code": status} for status, body in responses]


class TestLogin:
    def test_sign_in_saves_token(self, capsys):
        with requests_mock.Mocker() as m:
            m.post(f"{BASE}/oauth/device-authorization/", json=START)
            m.post(f"{BASE}/oauth/token/", token_responses(
                (400, {"error": "authorization_pending"}),
                (200, TOKEN),
            ))
            m.get(f"{BASE}/oauth/userinfo", json={"username": "rgmark"})
            assert auth.login(open_browser=False) == "rgmark"

            start_request = m.request_history[0]
            assert "client_id=physionet-python-client" in start_request.text
            assert "scope=data%3Adownload+profile%3Aread" in start_request.text
            assert m.request_history[-1].headers["Authorization"] == "Bearer access-1"

        out = capsys.readouterr().out
        assert "https://physionet.org/oauth/device/" in out
        assert "WDJB-MJHT" in out

        record = auth.load_saved_login(BASE)
        assert record["access_token"] == "access-1"
        assert record["refresh_token"] == "refresh-1"
        assert record["username"] == "rgmark"
        assert record["expires_at"] > time.time() + 35000

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
    def test_saved_file_is_private(self):
        save()
        path = auth.credentials_path()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700

    def test_slow_down_increases_the_interval(self):
        with requests_mock.Mocker() as m, patch.object(auth.time, "sleep") as sleep:
            m.post(f"{BASE}/oauth/device-authorization/", json=START)
            m.post(f"{BASE}/oauth/token/", token_responses(
                (400, {"error": "slow_down"}),
                (200, TOKEN),
            ))
            m.get(f"{BASE}/oauth/userinfo", status_code=403)
            assert auth.login(open_browser=False) is None
        assert [call.args[0] for call in sleep.call_args_list] == [5, 10]

    @pytest.mark.parametrize("error, message", [
        ("access_denied", "denied"),
        ("expired_token", "expired"),
        ("invalid_grant", "Sign-in failed"),
    ])
    def test_failed_sign_in(self, error, message):
        with requests_mock.Mocker() as m:
            m.post(f"{BASE}/oauth/device-authorization/", json=START)
            m.post(f"{BASE}/oauth/token/", status_code=400, json={"error": error})
            with pytest.raises(auth.LoginError, match=message):
                auth.login(open_browser=False)
        assert auth.load_saved_login(BASE) is None

    def test_server_refuses_to_start(self):
        with requests_mock.Mocker() as m:
            m.post(f"{BASE}/oauth/device-authorization/", status_code=400,
                   json={"error": "invalid_request", "error_description": "Invalid client_id parameter value."})
            with pytest.raises(auth.LoginError, match="Invalid client_id"):
                auth.login(open_browser=False)

    def test_needs_https(self):
        with pytest.raises(auth.LoginError, match="HTTPS"):
            auth.login("http://example.org", open_browser=False)

    def test_sign_ins_are_kept_per_site(self):
        save({"access_token": "a", "expires_at": time.time() + 3600})
        save({"access_token": "b", "expires_at": time.time() + 3600}, base_url="http://localhost:8000")
        assert auth.load_saved_login(BASE)["access_token"] == "a"
        assert auth.load_saved_login("http://localhost:8000/")["access_token"] == "b"
        assert auth.load_saved_login("https://physionet.org:443")["access_token"] == "a"


class TestLogout:
    def test_revokes_and_deletes(self):
        save()
        with requests_mock.Mocker() as m:
            m.post(f"{BASE}/oauth/revoke-token/")
            assert auth.logout() is True
            revoked = [r.text for r in m.request_history]
        assert any("token=saved-refresh" in body for body in revoked)
        assert auth.load_saved_login(BASE) is None

    def test_deletes_even_when_offline(self):
        save()
        with requests_mock.Mocker() as m:
            m.post(f"{BASE}/oauth/revoke-token/", exc=requests.ConnectionError)
            assert auth.logout() is True
        assert auth.load_saved_login(BASE) is None

    def test_not_signed_in(self):
        assert auth.logout() is False


class TestSavedLoginAuth:
    def test_client_uses_saved_sign_in_for_its_site_only(self):
        save()
        client = PhysioNetClient()
        assert client.auth_method == "login"
        with requests_mock.Mocker() as m:
            m.get(f"{BASE}/files/demo/1.0/a.csv", text="x")
            m.get("https://physionet-open.s3.amazonaws.com/demo/1.0/a.csv", text="x")
            client.session.get(f"{BASE}/files/demo/1.0/a.csv")
            client.session.get("https://physionet-open.s3.amazonaws.com/demo/1.0/a.csv")
            physionet_request, s3_request = m.request_history
        assert physionet_request.headers["Authorization"] == "Bearer saved"
        assert "Authorization" not in s3_request.headers

    def test_not_used_for_plain_http(self):
        save(base_url="http://example.org")
        assert PhysioNetClient(base_url="http://example.org").auth_method is None

    def test_can_be_turned_off(self):
        save()
        assert PhysioNetClient(use_saved_login=False).auth_method is None

    def test_expired_token_is_refreshed_and_saved(self):
        save({"access_token": "old", "refresh_token": "refresh-1", "expires_at": time.time() - 10,
              "username": "rgmark"})
        client = PhysioNetClient()
        with requests_mock.Mocker() as m:
            m.post(f"{BASE}/oauth/token/", json={**TOKEN, "access_token": "new", "refresh_token": "refresh-2"})
            m.get(f"{BASE}/files/demo/1.0/a.csv", text="x")
            client.session.get(f"{BASE}/files/demo/1.0/a.csv")
            refresh_request, file_request = m.request_history
        assert "grant_type=refresh_token" in refresh_request.text
        assert "refresh_token=refresh-1" in refresh_request.text
        assert file_request.headers["Authorization"] == "Bearer new"
        record = auth.load_saved_login(BASE)
        assert (record["access_token"], record["refresh_token"], record["username"]) == ("new", "refresh-2", "rgmark")

    def test_uses_a_token_another_process_refreshed(self):
        save({"access_token": "old", "refresh_token": "refresh-1", "expires_at": time.time() - 10})
        client = PhysioNetClient()
        save({"access_token": "fresh", "refresh_token": "refresh-2", "expires_at": time.time() + 3600})
        with requests_mock.Mocker() as m:
            m.get(f"{BASE}/files/demo/1.0/a.csv", text="x")
            client.session.get(f"{BASE}/files/demo/1.0/a.csv")
        assert m.request_history[0].headers["Authorization"] == "Bearer fresh"

    def test_revoked_sign_in_is_forgotten_and_requests_continue(self, capsys):
        save({"access_token": "old", "refresh_token": "refresh-1", "expires_at": time.time() - 10})
        client = PhysioNetClient()
        with requests_mock.Mocker() as m:
            m.post(f"{BASE}/oauth/token/", status_code=400, json={"error": "invalid_grant"})
            m.get(f"{BASE}/files/demo/1.0/a.csv", text="x")
            client.session.get(f"{BASE}/files/demo/1.0/a.csv")
            client.session.get(f"{BASE}/files/demo/1.0/a.csv")
            file_requests = [r for r in m.request_history if r.method == "GET"]
        assert all("Authorization" not in r.headers for r in file_requests)
        assert auth.load_saved_login(BASE) is None
        assert capsys.readouterr().err.count("physionet login") == 1


class TestCredentialOrder:
    def test_token_argument_first(self, monkeypatch):
        monkeypatch.setenv("PHYSIONET_TOKEN", "env-token")
        assert auth.resolve_credentials("u", "p", "arg-token") == (None, None, "arg-token")

    def test_password_arguments_before_environment(self, monkeypatch):
        monkeypatch.setenv("PHYSIONET_TOKEN", "env-token")
        assert auth.resolve_credentials("u", "p") == ("u", "p", None)

    def test_environment_token_before_environment_password(self, monkeypatch):
        monkeypatch.setenv("PHYSIONET_TOKEN", "env-token")
        monkeypatch.setenv("PHYSIONET_USERNAME", "u")
        monkeypatch.setenv("PHYSIONET_PASSWORD", "p")
        assert auth.resolve_credentials() == (None, None, "env-token")

    def test_environment_password(self, monkeypatch):
        monkeypatch.setenv("PHYSIONET_USERNAME", "u")
        monkeypatch.setenv("PHYSIONET_PASSWORD", "p")
        assert auth.resolve_credentials() == ("u", "p", None)

    def test_token_beats_saved_sign_in(self):
        save()
        client = PhysioNetClient(token="pnpat_abc")
        assert client.auth_method == "token"
        with requests_mock.Mocker() as m:
            m.get(f"{BASE}/files/demo/1.0/a.csv", text="x")
            m.get("https://example.org/elsewhere", text="x")
            client.session.get(f"{BASE}/files/demo/1.0/a.csv")
            client.session.get("https://example.org/elsewhere")
        assert m.request_history[0].headers["Authorization"] == "Bearer pnpat_abc"
        assert "Authorization" not in m.request_history[1].headers

    def test_token_needs_https(self):
        with pytest.raises(ValueError, match="HTTPS"):
            PhysioNetClient(base_url="http://example.org", token="pnpat_abc")


class TestCli:
    def run(self, *argv):
        with patch.object(sys, "argv", ["physionet", *argv]):
            return main()

    def test_login(self, capsys):
        with patch("physionet.auth.login", return_value="rgmark") as login:
            assert self.run("login", "--no-browser") == 0
        login.assert_called_once_with("https://physionet.org", open_browser=False)
        assert "Signed in to https://physionet.org as rgmark" in capsys.readouterr().out

    def test_login_failure(self, capsys):
        with patch("physionet.auth.login", side_effect=auth.LoginError("The sign-in was denied.")):
            assert self.run("login") == 1
        assert "denied" in capsys.readouterr().err

    def test_whoami_and_logout(self, capsys):
        assert self.run("whoami") == 0
        assert "Not signed in" in capsys.readouterr().out

        save()
        assert self.run("whoami") == 0
        assert "as rgmark" in capsys.readouterr().out

        with requests_mock.Mocker() as m:
            m.post(f"{BASE}/oauth/revoke-token/")
            assert self.run("logout") == 0
        assert "Signed out" in capsys.readouterr().out
        assert auth.load_saved_login(BASE) is None
