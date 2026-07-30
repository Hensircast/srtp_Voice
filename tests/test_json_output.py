from __future__ import annotations

import json
import sys
import types

import pytest

from srtp_voice.serial_out import save_serial_packet, send_serial_packet
from srtp_voice.utils import save_json


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_persisted_json_rejects_nonfinite_numbers(tmp_path, value) -> None:
    general_path = tmp_path / "state.json"
    serial_path = tmp_path / "serial.json"

    with pytest.raises(ValueError, match="JSON compliant"):
        save_json(general_path, {"value": value})
    with pytest.raises(ValueError, match="JSON compliant"):
        save_serial_packet({"value": value}, serial_path)

    assert not general_path.exists()
    assert not serial_path.exists()


def test_serial_payload_rejects_nonfinite_numbers_before_write(
    monkeypatch,
    capsys,
) -> None:
    writes = []

    class FakeSerialConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def write(self, data):
            writes.append(data)

    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = lambda port, baudrate, timeout: FakeSerialConnection()
    monkeypatch.setitem(sys.modules, "serial", fake_serial)

    result = send_serial_packet({"value": float("nan")}, "COM3")

    assert result is False
    assert writes == []
    assert "ValueError" in capsys.readouterr().out


def test_persisted_json_remains_standard_json(tmp_path) -> None:
    path = tmp_path / "state.json"

    save_json(path, {"value": 0.5})

    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 0.5}
