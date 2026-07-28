from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

from srtp_voice.audio_io import NoSpeechDetectedError
from srtp_voice.config import AppConfig
from srtp_voice.types import EmotionResult, StrategyResult


def _run_vad_main(
    monkeypatch,
    tmp_path: Path,
    *,
    continuous: bool,
    record_actions: list[str],
    asr_texts: list[str],
):
    import main as main_module

    cfg = AppConfig(
        output_dir=tmp_path,
        memory_file=tmp_path / "memory.json",
        state_file=tmp_path / "emotion.json",
        ser_backend="sensevoice",
        llm_backend="mock",
        tts_backend="mock",
    )
    args = argparse.Namespace(
        mode="vad",
        audio="",
        text="",
        record_seconds=0.1,
        no_play=True,
        continuous=continuous,
    )
    counters = {
        "ser_init": 0,
        "ser_warmup": 0,
        "ser_predict": 0,
        "asr_init": 0,
        "asr_transcribe": 0,
        "generator_init": 0,
        "generate": 0,
        "tts_init": 0,
        "synthesize": 0,
        "smoother_init": 0,
        "memory_init": 0,
        "record": 0,
        "ser_results": [],
        "llm_emotions": [],
        "memory_states": [],
        "serial_packets": [],
    }
    remaining_actions = list(record_actions)
    remaining_texts = list(asr_texts)

    class FakeSER:
        backend_name = "sensevoice"

        def __init__(self, received_cfg):
            assert received_cfg is cfg
            counters["ser_init"] += 1

        def warmup(self):
            counters["ser_warmup"] += 1

        def predict(self, path):
            counters["ser_predict"] += 1
            result = EmotionResult(
                label="neutral",
                intensity=0.4,
                confidence=0.7,
                features={"fusion_evidence_strength": 0.7},
            )
            counters["ser_results"].append(result)
            return result

    class FakeASR:
        def __init__(self, received_cfg):
            assert received_cfg is cfg
            counters["asr_init"] += 1

        def transcribe(self, path):
            counters["asr_transcribe"] += 1
            return remaining_texts.pop(0)

    class FakeGenerator:
        def __init__(self, received_cfg):
            assert received_cfg is cfg
            counters["generator_init"] += 1

        def generate(self, user_text, emotion, history):
            counters["generate"] += 1
            counters["llm_emotions"].append(emotion)
            return StrategyResult(reply_text=f"reply: {user_text}", action={})

    class FakeTTS:
        def __init__(self, received_cfg):
            assert received_cfg is cfg
            counters["tts_init"] += 1

        def synthesize(self, text, path):
            counters["synthesize"] += 1
            Path(path).write_bytes(b"fake reply wav")

    class Smoothed:
        label = "neutral"
        valence = 0.0
        arousal = 0.0
        dominance = 0.0

        def to_dict(self):
            return {"label": self.label}

    class FakeSmoother:
        def __init__(self, path, alpha, **kwargs):
            counters["smoother_init"] += 1

        def update(self, emotion):
            return Smoothed()

    class FakeMemory:
        def __init__(self, path, max_turns):
            counters["memory_init"] += 1

        def load(self):
            return []

        def append(self, state):
            counters["memory_states"].append(state)
            return None

    def fake_record(path, cfg):
        counters["record"] += 1
        action = remaining_actions.pop(0)
        if action == "no_speech":
            raise NoSpeechDetectedError("no speech")
        if action == "interrupt":
            raise KeyboardInterrupt
        Path(path).write_bytes(b"fake user wav")

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module.AppConfig, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setattr(main_module, "SpeechEmotionRecognizer", FakeSER)
    monkeypatch.setattr(main_module, "ASRAdapter", FakeASR)
    monkeypatch.setattr(main_module, "StrategyGenerator", FakeGenerator)
    monkeypatch.setattr(main_module, "TTSAdapter", FakeTTS)
    monkeypatch.setattr(main_module, "EmotionStateSmoother", FakeSmoother)
    monkeypatch.setattr(main_module, "JsonMemory", FakeMemory)
    monkeypatch.setattr(main_module, "record_until_silence", fake_record)
    monkeypatch.setattr(main_module, "build_energy_lip_sync", lambda path: {"frames": []})
    monkeypatch.setattr(main_module, "build_serial_packet", lambda action, lip_sync: {"ok": True})
    monkeypatch.setattr(
        main_module,
        "save_serial_packet",
        lambda packet, path: counters["serial_packets"].append(packet),
    )

    main_module.main()

    state = json.loads((tmp_path / "last_state.json").read_text(encoding="utf-8"))
    return counters, state


def test_parse_args_continuous_defaults_false(monkeypatch) -> None:
    import main

    monkeypatch.setattr(sys, "argv", ["main.py"])
    assert main.parse_args().continuous is False


@pytest.mark.parametrize("mode", ["console", "file"])
def test_continuous_rejects_non_recording_modes(monkeypatch, mode) -> None:
    import main

    args = argparse.Namespace(
        mode=mode,
        audio="input.wav",
        text="",
        record_seconds=1.0,
        no_play=True,
        continuous=True,
    )
    monkeypatch.setattr(main, "parse_args", lambda: args)

    with pytest.raises(ValueError, match="仅支持 --mode vad 或 --mode mic"):
        main.main()


def test_default_vad_runs_one_turn(monkeypatch, tmp_path, capsys) -> None:
    counters, state = _run_vad_main(
        monkeypatch,
        tmp_path,
        continuous=False,
        record_actions=["speech"],
        asr_texts=["one turn"],
    )

    assert counters["record"] == 1
    assert counters["ser_predict"] == 1
    assert counters["generate"] == 1
    assert counters["llm_emotions"][0] is counters["ser_results"][0]
    assert counters["memory_states"][0].emotion is counters["ser_results"][0]
    assert json.loads(json.dumps(counters["memory_states"][0].to_dict()))
    assert json.loads(json.dumps(counters["serial_packets"][0]))
    action = json.loads((tmp_path / "last_action.json").read_text(encoding="utf-8"))
    assert set(action) == {"reply_text", "action"}
    assert action["action"]["emotion_state"]["label"] == "neutral"
    assert state["stage"] == "Idle"
    output = capsys.readouterr().out
    assert "[TURN 1] 开始监听" in output
    assert "[TURN 2]" not in output
    assert "instant/fused_emotion=neutral" in output
    assert "smoothed_emotion_state=label=neutral" in output


def test_continuous_vad_reuses_runtime_until_keyboard_interrupt(
    monkeypatch, tmp_path, capsys
) -> None:
    counters, state = _run_vad_main(
        monkeypatch,
        tmp_path,
        continuous=True,
        record_actions=["speech", "speech", "interrupt"],
        asr_texts=["turn one", "turn two"],
    )

    assert counters["ser_init"] == 1
    assert counters["ser_warmup"] == 1
    assert counters["ser_predict"] == 2
    assert counters["asr_init"] == 1
    assert counters["asr_transcribe"] == 2
    assert counters["generator_init"] == 1
    assert counters["generate"] == 2
    assert counters["tts_init"] == 1
    assert counters["synthesize"] == 2
    assert counters["smoother_init"] == 1
    assert counters["memory_init"] == 1
    assert state["stage"] == "Idle"
    output = capsys.readouterr().out
    assert "[TURN 1] 开始监听" in output
    assert "[TURN 2] 开始监听" in output
    assert "已收到 Ctrl+C，状态机已回到 Idle，程序正常退出" in output


def test_continuous_no_speech_continues_next_turn(monkeypatch, tmp_path, capsys) -> None:
    counters, state = _run_vad_main(
        monkeypatch,
        tmp_path,
        continuous=True,
        record_actions=["no_speech", "speech", "interrupt"],
        asr_texts=["recognized"],
    )

    assert counters["record"] == 3
    assert counters["ser_predict"] == 1
    assert counters["asr_transcribe"] == 1
    assert counters["generate"] == 1
    assert state["stage"] == "Idle"
    assert "未检测到有效语音，本轮结束，继续监听" in capsys.readouterr().out


def test_continuous_empty_asr_continues_next_turn(monkeypatch, tmp_path, capsys) -> None:
    counters, state = _run_vad_main(
        monkeypatch,
        tmp_path,
        continuous=True,
        record_actions=["speech", "speech", "interrupt"],
        asr_texts=["", "recognized"],
    )

    assert counters["ser_predict"] == 2
    assert counters["asr_transcribe"] == 2
    assert counters["generate"] == 1
    assert counters["synthesize"] == 1
    assert state["stage"] == "Idle"
    assert "ASR 未返回有效文本，本轮结束，继续监听" in capsys.readouterr().out
