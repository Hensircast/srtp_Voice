"""Last late-review probes: no external paths, processes or private input."""
import argparse
import json
from pathlib import PurePosixPath

import pytest

from tools import workbench_latency as latency
from tools import workbench_tasks as tasks


@pytest.mark.parametrize("value", [10 ** 400, -(10 ** 400)])
def test_oversized_latency_is_a_safe_validation_error(value):
    with pytest.raises(latency.BaselineError):
        latency._require_non_negative(value, "latency")


def test_cli_oversized_source_returns_two_without_output_or_value_echo(tmp_path, monkeypatch, capsys):
    source = tmp_path / "metrics.json"
    source.write_text(json.dumps({"summary": {"turn_total_ms": {"count": 1, "p50": 10 ** 400}}}), encoding="utf-8")
    monkeypatch.setattr(latency, "resolve_project_path_or_escape", lambda value: (source, False))
    writes = []
    monkeypatch.setattr(latency, "write_baseline", lambda *args: writes.append(args))
    result = latency._handle_baseline(argparse.Namespace(metrics="metrics.json", output="outputs/result.json",
        compare=None, recording_metadata=None, measurement="real", label="probe"))
    assert result == 2
    assert not writes
    output = capsys.readouterr().out
    assert "Traceback" not in output
    assert str(10 ** 400) not in output


@pytest.mark.parametrize("value", [r"C:\Users\fake-private-person\metrics.json",
    r"\\fake-host\share\metrics.json", r"models/C:\Users\fake-private-person\metrics.json",
    "C:private/metrics.json", r"\private\metrics.json"])
def test_foreign_task_paths_rejected_before_any_resolution_on_posix(monkeypatch, value):
    # Use POSIX syntax even on Windows, so this exercises the foreign-path
    # branch rather than passing only because the host detects C: as absolute.
    monkeypatch.setattr(tasks, "Path", PurePosixPath)
    class NeverResolve:
        def __truediv__(self, other):
            raise AssertionError("argument must be rejected before probing/logging")
    with pytest.raises(tasks.TaskError) as error:
        tasks._check_project_path(value, NeverResolve())
    assert "fake-private-person" not in str(error.value)
    assert "fake-host" not in str(error.value)
