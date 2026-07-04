from pathlib import Path


def test_env_example_has_no_bom() -> None:
    data = Path(".env.example").read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert data.startswith(b"SAMPLE_RATE=16000")


def test_env_example_first_line() -> None:
    first_line = Path(".env.example").read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "SAMPLE_RATE=16000"
