from __future__ import annotations

import json
import sys
import types

import pytest

from srtp_voice.serial_out import send_serial_packet


@pytest.mark.parametrize("port", ["COM3", "/dev/ttyACM0", "/dev/ttyUSB0"])
def test_send_serial_packet_preserves_port_and_writes_utf8_json_line(
    monkeypatch,
    port,
) -> None:
    calls = []
    writes = []

    class FakeSerialConnection:
        def __init__(self, received_port, baudrate, timeout):
            calls.append(
                {
                    "port": received_port,
                    "baudrate": baudrate,
                    "timeout": timeout,
                }
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def write(self, data):
            writes.append(data)

    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = FakeSerialConnection
    monkeypatch.setitem(sys.modules, "serial", fake_serial)
    packet = {
        "type": "robot_head_action",
        "expression": "开心",
        "servo_targets": {"mouth_open": 0.5},
    }

    result = send_serial_packet(packet, port, baudrate=57600)

    assert result is True
    assert calls == [{"port": port, "baudrate": 57600, "timeout": 1}]
    assert len(writes) == 1
    assert isinstance(writes[0], bytes)
    assert writes[0].endswith(b"\n")
    assert "开心".encode("utf-8") in writes[0]
    assert json.loads(writes[0].decode("utf-8")) == packet


def test_send_serial_packet_without_pyserial_returns_false(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "serial", None)

    result = send_serial_packet({"type": "test"}, "COM3")

    assert result is False
    output = capsys.readouterr().out
    assert "pyserial 未安装" in output
    assert "python -m pip install pyserial" in output
    assert "Traceback" not in output


@pytest.mark.parametrize("stage", ["open", "write"])
def test_serial_failure_reports_port_baudrate_and_exception(
    monkeypatch,
    capsys,
    stage,
) -> None:
    class FakeSerialError(OSError):
        pass

    class FailingConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def write(self, data):
            raise FakeSerialError("write failed")

    def fake_serial(port, baudrate, timeout):
        if stage == "open":
            raise FakeSerialError("open failed")
        return FailingConnection()

    fake_module = types.ModuleType("serial")
    fake_module.Serial = fake_serial
    monkeypatch.setitem(sys.modules, "serial", fake_module)

    result = send_serial_packet(
        {"type": "test"},
        "/dev/ttyACM0",
        baudrate=230400,
    )

    assert result is False
    output = capsys.readouterr().out
    assert "port=/dev/ttyACM0" in output
    assert "baudrate=230400" in output
    assert "FakeSerialError" in output
    assert f"{stage} failed" in output
    assert "Traceback" not in output


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(2)])
def test_process_control_exceptions_propagate(monkeypatch, interrupt) -> None:
    def raise_interrupt(port, baudrate, timeout):
        raise interrupt

    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = raise_interrupt
    monkeypatch.setitem(sys.modules, "serial", fake_serial)

    with pytest.raises(type(interrupt)):
        send_serial_packet({"type": "test"}, "/dev/ttyUSB0")
