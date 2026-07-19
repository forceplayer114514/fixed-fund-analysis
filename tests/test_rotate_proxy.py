"""tools/rotate_proxy.py 的解析/分派逻辑测试 (mock 掉 controller API 请求)。"""
from unittest.mock import MagicMock

import pytest

from tools import rotate_proxy


def _mock_resp(json_data, status_code: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data
    r.raise_for_status = MagicMock()
    if status_code >= 400:
        import requests
        r.raise_for_status.side_effect = requests.HTTPError(f"{status_code}")
    return r


_PROXIES_PAYLOAD = {
    "proxies": {
        "PROXY": {"type": "Selector", "now": "hk-01", "all": ["hk-01", "jp-01", "us-01"]},
        "hk-01": {"type": "Shadowsocks", "all": []},
        "DIRECT": {"type": "Direct", "all": []},
    }
}


class TestListGroups:
    def test_only_switchable_groups_kept(self, monkeypatch):
        monkeypatch.setattr(
            rotate_proxy.requests, "get", MagicMock(return_value=_mock_resp(_PROXIES_PAYLOAD)),
        )
        groups = rotate_proxy.list_groups()
        assert groups == {"PROXY": ["hk-01", "jp-01", "us-01"]}


class TestCurrentNode:
    def test_returns_now_field(self, monkeypatch):
        monkeypatch.setattr(
            rotate_proxy.requests, "get",
            MagicMock(return_value=_mock_resp({"now": "hk-01", "all": ["hk-01", "jp-01"]})),
        )
        assert rotate_proxy.current_node("PROXY") == "hk-01"


class TestRotate:
    def test_picks_a_different_node_and_puts(self, monkeypatch):
        get_mock = MagicMock(
            return_value=_mock_resp({"now": "hk-01", "all": ["hk-01", "jp-01", "us-01"]})
        )
        put_mock = MagicMock(return_value=_mock_resp({}))
        monkeypatch.setattr(rotate_proxy.requests, "get", get_mock)
        monkeypatch.setattr(rotate_proxy.requests, "put", put_mock)
        new_node = rotate_proxy.rotate("PROXY")
        assert new_node in ("jp-01", "us-01")
        assert put_mock.call_args[1]["json"]["name"] == new_node

    def test_no_other_node_raises(self, monkeypatch):
        monkeypatch.setattr(
            rotate_proxy.requests, "get",
            MagicMock(return_value=_mock_resp({"now": "hk-01", "all": ["hk-01"]})),
        )
        with pytest.raises(RuntimeError):
            rotate_proxy.rotate("PROXY")


class TestMainCli:
    def test_list_groups_flag(self, monkeypatch, capsys):
        monkeypatch.setattr(
            rotate_proxy.requests, "get", MagicMock(return_value=_mock_resp(_PROXIES_PAYLOAD)),
        )
        rc = rotate_proxy.main(["--list-groups"])
        assert rc == 0
        assert "PROXY" in capsys.readouterr().out

    def test_connection_error_returns_nonzero(self, monkeypatch, capsys):
        import requests as real_requests
        monkeypatch.setattr(
            rotate_proxy.requests, "get",
            MagicMock(side_effect=real_requests.RequestException("refused")),
        )
        rc = rotate_proxy.main(["--current", "PROXY"])
        assert rc == 1
        assert "连不上控制面板" in capsys.readouterr().err

    def test_no_flags_prints_help_returns_nonzero(self):
        rc = rotate_proxy.main([])
        assert rc == 1
