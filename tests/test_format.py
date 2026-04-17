"""Tests for format_sig_figs and format_usd utilities."""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools", "trader"))

from trader import format_sig_figs, format_usd


def test_format_sig_figs_zero():
    assert format_sig_figs(0) == "0.000"


def test_format_sig_figs_small():
    # 4 sig figs: 0.001234 -> 0.001234 (magnitude -3, decimals 6)
    assert format_sig_figs(0.001234) == "0.001234"


def test_format_sig_figs_large():
    # 4 sig figs: 12345 -> 12350, 1234.5 -> 1235
    result = format_sig_figs(1234.5)
    assert "123" in result and len(result) <= 5
    result2 = format_sig_figs(12345)
    assert "123" in result2


def test_format_sig_figs_negative():
    assert format_sig_figs(-0.001) == "-0.001000"


def test_format_sig_figs_invalid():
    assert format_sig_figs("not a number") == "not a number"
    assert format_sig_figs(None) is None


def test_format_usd_positive():
    result = format_usd(300000)
    assert result == "$300,000.00" or "300" in result and "," in result


def test_format_usd_small():
    result = format_usd(0.01)
    assert "$" in result and "0.01" in result


def test_format_usd_invalid():
    assert format_usd("x") == "x"
    assert format_usd(None) is None
