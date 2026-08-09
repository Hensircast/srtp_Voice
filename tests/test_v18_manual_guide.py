from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUIDE_PATH = PROJECT_ROOT / "docs" / "development" / "V1.8_REAL_DEVICE_TESTS.md"


def test_v18_manual_guide_covers_required_real_device_checks() -> None:
    text = GUIDE_PATH.read_text(encoding="utf-8")

    required = (
        "Windows PowerShell",
        "--help",
        "--diagnose",
        "compileall -q main.py srtp_voice tests",
        "ollama list",
        "--streaming --no-play",
        "UTF-8 中文复杂问答",
        "time_to_first_token_ms",
        "first_sentence_ms",
        "time_to_first_audio_ms",
        "[ASR partial]",
        "记忆只写 final",
        "Piper 分句合成与播放顺序",
        "Ctrl+C",
        "耳机",
        "连续 10 轮",
        "失败恢复",
        "streaming_events.json",
        "streaming_metrics.json",
        "通过/失败判定",
        "最小日志范围",
        "安全终止",
    )
    assert all(value in text for value in required)
    assert "```bash" not in text


def test_v18_manual_outputs_remain_gitignored() -> None:
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "outputs/*" in gitignore
    assert "*.wav" in gitignore
    assert ".env" in gitignore
