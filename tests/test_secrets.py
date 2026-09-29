import sys

import pytest

from switcher.secrets import protect, reveal


def test_roundtrip():
    stored = protect("sk-ant-api03-секрет")
    assert "sk-ant" not in stored
    assert reveal(stored) == "sk-ant-api03-секрет"


def test_empty_and_hand_written_values():
    assert protect("") == ""
    assert reveal("") == ""
    assert reveal("sk-ant-typed-by-hand") == "sk-ant-typed-by-hand"
    assert reveal("dpapi:not-base64!!") == ""


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_windows_uses_dpapi():
    assert protect("sk-ant-x").startswith("dpapi:")
