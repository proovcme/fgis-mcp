import io
import json
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from fgis_mcp.config import Config
from fgis_mcp.network import BASE, Network, SameHostRedirect, SourceError


def test_direct_ignores_proxy_environment(config, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://secret:password@localhost:1")
    monkeypatch.setattr("urllib.request.getproxies", lambda: {"https": "http://localhost:1"})
    net = Network(config)
    assert all(getattr(h, "proxies", {}) == {} for h in net.opener.handlers)
    assert "password" not in repr(config)


def test_html_not_successful_json(config):
    net = Network(config)
    net.opener.open = Mock(return_value=io.BytesIO(b"<html>Challenge</html>"))
    with pytest.raises(SourceError, match="non-JSON"):
        net.get_json("EstimatedPrice/CountrySubjects")


def test_403_is_not_retried_or_called_vpn(config):
    net = Network(config)
    net.opener.open = Mock(side_effect=HTTPError(BASE, 403, "forbidden", {}, None))
    result = net.diagnose()
    assert not result["ok"]
    assert result["error"]["http_status"] == 403
    assert not result["vpn_bypass_verified"]
    assert net.opener.open.call_count == 1


def test_diagnosis_requires_catalogue_schema(config):
    net = Network(config)
    net.opener.open = Mock(return_value=io.BytesIO(json.dumps([{"error": "denied"}]).encode()))
    assert net.diagnose()["error"]["code"] == "SCHEMA_CHANGED"


def test_external_redirect_blocked():
    with pytest.raises(SourceError):
        SameHostRedirect().redirect_request(Request(BASE), None, 302, "", {}, "https://example.com/token")


@pytest.mark.parametrize("host", ["-oProxyCommand=bad", "host;touch /tmp/bad", "user@host $(id)"])
def test_ssh_target_cannot_inject_options(config, host):
    with pytest.raises(ValueError):
        Config(config.root, network="ssh", ssh_host=host)


def test_ssh_command_quotes_query_and_does_not_leak_stderr(config, monkeypatch):
    net = Network(Config(config.root, network="ssh", ssh_host="example-host", attempts=1))
    run = Mock(return_value=Mock(returncode=1, stderr=b"secret:credential", stdout=b""))
    monkeypatch.setattr("subprocess.run", run)
    with pytest.raises(SourceError) as exc:
        net.get_json("FullTextSearch/SearchEstimatedRates", {"search": "'; $(whoami)"})
    assert "credential" not in str(exc.value)
    command = run.call_args.args[0]
    assert isinstance(command, list) and "%24%28whoami%29" in command[-1]


def test_response_size_limit_rejects_instead_of_truncating(config):
    net = Network(Config(config.root, max_response_mib=1, attempts=1))
    net.opener.open = Mock(return_value=io.BytesIO(b"a" * (1024 * 1024 + 1)))
    with pytest.raises(SourceError) as exc:
        net.fetch("public-data")
    assert exc.value.code == "TOO_LARGE"


def test_total_deadline_stops_slow_stream(config, monkeypatch):
    net = Network(config)
    net._pace = lambda: None
    clock = iter([0, 0, 30])
    monkeypatch.setattr("fgis_mcp.network.time.monotonic", lambda: next(clock))
    net.opener.open = Mock(return_value=io.BytesIO(b"a" * 100000))
    with pytest.raises(SourceError) as exc:
        net.fetch("public-data")
    assert exc.value.code == "NETWORK_ERROR"
