"""Tests for SFOXTrader client."""

from unittest.mock import Mock, patch, MagicMock

from sfox_trader.lib.sfox_client import SFOXTrader


def test_sfoxtrader_init():
    client = SFOXTrader("test_api_key")
    assert client.api_key == "test_api_key"
    assert client.base_url == "https://api.sfox.com/v1"
    assert client.auth == ("test_api_key", "")


@patch("sfox_trader.lib.sfox_client.requests.get")
def test_get_balances(mock_get):
    mock_get.return_value.json.return_value = [{"currency": "usd", "balance": 100}]
    mock_get.return_value.raise_for_status = Mock()

    client = SFOXTrader("test_key")
    result = client.get_balances()

    assert result == [{"currency": "usd", "balance": 100}]
    mock_get.assert_called_once()
    call_args = mock_get.call_args
    assert "auth" in call_args[1]
    assert call_args[1]["auth"] == ("test_key", "")


@patch("sfox_trader.lib.sfox_client.requests.get")
def test_get_currency_pairs(mock_get):
    mock_get.return_value.json.return_value = [{"pair": "btcusd"}]
    mock_get.return_value.raise_for_status = Mock()

    client = SFOXTrader("test_key")
    result = client.get_currency_pairs()

    assert result == [{"pair": "btcusd"}]


@patch("sfox_trader.lib.sfox_client.requests.get")
def test_get_open_orders_empty(mock_get):
    mock_get.return_value.json.return_value = []
    mock_get.return_value.raise_for_status = Mock()

    client = SFOXTrader("test_key")
    result = client.get_open_orders()

    assert result == []


@patch("sfox_trader.lib.sfox_client.requests.get")
def test_get_open_orders_with_pair_filter(mock_get):
    mock_get.return_value.json.return_value = [{"id": 1, "pair": "btcusd"}]
    mock_get.return_value.raise_for_status = Mock()

    client = SFOXTrader("test_key")
    result = client.get_open_orders(currency_pair="btcusd")

    assert result == [{"id": 1, "pair": "btcusd"}]
    call_kwargs = mock_get.call_args[1]
    assert call_kwargs.get("params", {}).get("currency_pair") == "btcusd"
