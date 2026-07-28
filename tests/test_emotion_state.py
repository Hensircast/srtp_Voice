from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from srtp_voice.config import AppConfig
from srtp_voice.emotion_state import (
    EMOTION_TO_VAD,
    NEUTRAL_VAD_NORM_THRESHOLD,
    EmotionStateSmoother,
    EmotionStateTracker,
)
from srtp_voice.types import EmotionResult


def _emotion(
    label: str,
    *,
    intensity: float = 1.0,
    confidence: float = 1.0,
) -> EmotionResult:
    return EmotionResult(label, intensity, confidence, {})


def _write_state(
    path: Path,
    *,
    label: str,
    vad: tuple[float, float, float] | None = None,
    updated_at: float = 0.0,
) -> None:
    valence, arousal, dominance = vad or EMOTION_TO_VAD[label]
    path.write_text(
        json.dumps(
            {
                "valence": valence,
                "arousal": arousal,
                "dominance": dominance,
                "label": label,
                "intensity": 1.0,
                "confidence": 1.0,
                "updated_at": updated_at,
            }
        ),
        encoding="utf-8",
    )


def test_loads_legacy_vad_label_state(tmp_path) -> None:
    path = tmp_path / "emotion.json"
    path.write_text(
        json.dumps(
            {
                "valence": 0.4,
                "arousal": "0.2",
                "dominance": -0.1,
                "label": "happy",
            }
        ),
        encoding="utf-8",
    )

    tracker = EmotionStateSmoother(path, alpha=0.35)

    assert tracker.state.valence == 0.4
    assert tracker.state.arousal == 0.2
    assert tracker.state.dominance == -0.1
    assert tracker.state.label == "happy"
    assert tracker.state.intensity == 0.0
    assert tracker.state.confidence == 0.0
    assert tracker.state.updated_at is None


@pytest.mark.parametrize("content", ["", "{bad json", "[]", '"text"'])
def test_corrupt_or_non_object_state_recovers_to_neutral(tmp_path, content) -> None:
    path = tmp_path / "emotion.json"
    path.write_text(content, encoding="utf-8")

    state = EmotionStateTracker(path).state

    assert state.label == "neutral"
    assert (state.valence, state.arousal, state.dominance) == (0.0, 0.0, 0.0)


def test_nonfinite_and_unknown_fields_are_sanitized(tmp_path) -> None:
    path = tmp_path / "emotion.json"
    path.write_text(
        """
        {
          "valence": NaN,
          "arousal": "Infinity",
          "dominance": -9,
          "label": "not-supported",
          "intensity": 4,
          "confidence": -2,
          "updated_at": "not-a-time",
          "future_field": {"ignored": true}
        }
        """,
        encoding="utf-8",
    )

    state = EmotionStateTracker(path).state

    assert state.valence == 0.0
    assert state.arousal == 0.0
    assert state.dominance == -1.0
    assert state.label == "neutral"
    assert state.intensity == 1.0
    assert state.confidence == 0.0
    assert state.updated_at is None
    assert all(
        math.isfinite(value)
        for value in (state.valence, state.arousal, state.dominance)
    )


def test_smoothing_uses_alpha_confidence_and_intensity(tmp_path) -> None:
    tracker = EmotionStateTracker(
        tmp_path / "emotion.json",
        alpha=0.5,
        max_step=2.0,
    )

    state = tracker.update(_emotion("happy"), now=10.0)

    assert state.valence == pytest.approx(0.35)
    assert state.arousal == pytest.approx(0.225)
    assert state.dominance == pytest.approx(0.175)
    assert state.intensity == pytest.approx(0.5)
    assert state.confidence == pytest.approx(0.5)
    assert state.label == "happy"


def test_confidence_and_intensity_independently_reduce_update_size(tmp_path) -> None:
    high = EmotionStateTracker(
        tmp_path / "high.json",
        alpha=1.0,
        max_step=2.0,
    ).update(
        _emotion("happy", intensity=1.0, confidence=1.0),
        now=1.0,
    )
    low_confidence = EmotionStateTracker(
        tmp_path / "low-confidence.json",
        alpha=1.0,
        max_step=2.0,
    ).update(
        _emotion("happy", intensity=1.0, confidence=0.25),
        now=1.0,
    )
    low_intensity = EmotionStateTracker(
        tmp_path / "low-intensity.json",
        alpha=1.0,
        max_step=2.0,
    ).update(
        _emotion("happy", intensity=0.25, confidence=1.0),
        now=1.0,
    )

    assert high.valence > low_confidence.valence > 0.0
    assert high.valence > low_intensity.valence > 0.0
    assert low_confidence.valence == pytest.approx(0.175)
    assert low_intensity.valence == pytest.approx(0.0765625)


@pytest.mark.parametrize("anomaly_label", ["unknown", "angry"])
def test_low_confidence_anomaly_does_not_override_stable_state(
    tmp_path,
    anomaly_label,
) -> None:
    path = tmp_path / "emotion.json"
    path.write_text(
        json.dumps(
            {
                "valence": 0.6,
                "arousal": 0.4,
                "dominance": 0.3,
                "label": "happy",
                "intensity": 0.8,
                "confidence": 0.8,
                "updated_at": 100.0,
            }
        ),
        encoding="utf-8",
    )
    tracker = EmotionStateTracker(path, alpha=1.0, max_step=2.0)

    state = tracker.update(
        _emotion(anomaly_label, intensity=1.0, confidence=0.01),
        now=100.0,
    )

    assert abs(state.valence - 0.6) < 0.02
    assert abs(state.arousal - 0.4) < 0.01
    assert abs(state.dominance - 0.3) < 0.01
    assert state.label == "happy"


def test_half_life_decay_moves_state_toward_neutral(tmp_path) -> None:
    path = tmp_path / "emotion.json"
    path.write_text(
        json.dumps(
            {
                "valence": 0.8,
                "arousal": 0.6,
                "dominance": 0.4,
                "label": "happy",
                "intensity": 0.8,
                "confidence": 0.6,
                "updated_at": 0.0,
            }
        ),
        encoding="utf-8",
    )
    tracker = EmotionStateTracker(path, decay_half_life_seconds=10.0)

    once = tracker.decay(now=10.0)
    assert once.valence == pytest.approx(0.4)
    assert once.arousal == pytest.approx(0.3)
    assert once.dominance == pytest.approx(0.2)

    later = tracker.decay(now=110.0)
    assert abs(later.valence) < 0.001
    assert abs(later.arousal) < 0.001
    assert abs(later.dominance) < 0.001
    assert later.label == "neutral"


@pytest.mark.parametrize(
    ("label", "forbidden_label"),
    [
        ("angry", "disgust"),
        ("excited", "surprise"),
    ],
)
def test_half_life_decay_preserves_original_label(
    tmp_path,
    label,
    forbidden_label,
) -> None:
    path = tmp_path / f"{label}.json"
    original = EMOTION_TO_VAD[label]
    _write_state(path, label=label)
    tracker = EmotionStateTracker(path, decay_half_life_seconds=10.0)

    state = tracker.decay(now=10.0)

    assert state.valence == pytest.approx(original[0] / 2.0)
    assert state.arousal == pytest.approx(original[1] / 2.0)
    assert state.dominance == pytest.approx(original[2] / 2.0)
    assert state.label == label
    assert state.label != forbidden_label


@pytest.mark.parametrize("label", ["fear", "sad", "happy"])
def test_decay_does_not_create_another_specific_emotion(tmp_path, label) -> None:
    path = tmp_path / f"{label}.json"
    _write_state(path, label=label)
    tracker = EmotionStateTracker(path, decay_half_life_seconds=10.0)

    state = tracker.decay(now=10.0)

    assert state.label == label


def test_repeated_decay_keeps_label_until_neutral_threshold(tmp_path) -> None:
    path = tmp_path / "angry.json"
    _write_state(path, label="angry")
    tracker = EmotionStateTracker(path, decay_half_life_seconds=10.0)
    previous_magnitude = max(abs(value) for value in EMOTION_TO_VAD["angry"])

    for timestamp in (10.0, 20.0, 30.0):
        state = tracker.decay(now=timestamp)
        magnitude = max(
            abs(state.valence),
            abs(state.arousal),
            abs(state.dominance),
        )
        assert magnitude < previous_magnitude
        assert math.sqrt(
            state.valence**2 + state.arousal**2 + state.dominance**2
        ) > NEUTRAL_VAD_NORM_THRESHOLD
        assert state.label == "angry"
        previous_magnitude = magnitude

    neutral = tracker.decay(now=40.0)
    assert math.sqrt(
        neutral.valence**2 + neutral.arousal**2 + neutral.dominance**2
    ) <= NEUTRAL_VAD_NORM_THRESHOLD
    assert neutral.label == "neutral"


def test_unknown_decay_never_creates_specific_emotion(tmp_path) -> None:
    path = tmp_path / "unknown.json"
    _write_state(
        path,
        label="unknown",
        vad=(-0.6, 0.7, 0.5),
    )
    tracker = EmotionStateTracker(path, decay_half_life_seconds=10.0)

    state = tracker.decay(now=10.0)

    assert state.label == "neutral"
    assert state.label not in {
        "happy",
        "sad",
        "angry",
        "fear",
        "surprise",
        "disgust",
        "tired",
        "excited",
    }


def test_decay_persists_label_and_vad_for_reload(tmp_path) -> None:
    path = tmp_path / "excited.json"
    _write_state(path, label="excited")
    tracker = EmotionStateTracker(path, decay_half_life_seconds=10.0)

    decayed = tracker.decay(now=10.0)
    reloaded = EmotionStateTracker(path, decay_half_life_seconds=10.0).state

    assert reloaded.to_dict() == decayed.to_dict()
    assert reloaded.label == "excited"


def test_update_with_new_evidence_can_change_preserved_label(tmp_path) -> None:
    path = tmp_path / "emotion.json"
    _write_state(path, label="angry")
    tracker = EmotionStateTracker(
        path,
        alpha=1.0,
        decay_half_life_seconds=10.0,
        max_step=2.0,
    )

    decayed = tracker.decay(now=10.0)
    assert decayed.label == "angry"

    updated = tracker.update(_emotion("happy"), now=10.0)
    assert updated.label == "happy"


def test_repeated_decay_uses_previous_decay_timestamp(tmp_path) -> None:
    path = tmp_path / "emotion.json"
    path.write_text(
        json.dumps(
            {
                "valence": 0.8,
                "arousal": 0.6,
                "dominance": 0.4,
                "label": "happy",
                "updated_at": 0.0,
            }
        ),
        encoding="utf-8",
    )
    tracker = EmotionStateTracker(path, decay_half_life_seconds=10.0)

    first = tracker.decay(now=10.0)
    assert first.valence == pytest.approx(0.4)
    assert first.updated_at == 10.0

    second = tracker.decay(now=20.0)
    assert second.valence == pytest.approx(0.2)
    assert second.arousal == pytest.approx(0.15)
    assert second.dominance == pytest.approx(0.1)
    assert second.updated_at == 20.0


def test_single_update_is_limited_by_max_step(tmp_path) -> None:
    tracker = EmotionStateTracker(
        tmp_path / "emotion.json",
        alpha=1.0,
        max_step=0.1,
    )

    state = tracker.update(_emotion("angry"), now=1.0)

    assert state.valence == pytest.approx(-0.1)
    assert state.arousal == pytest.approx(0.1)
    assert state.dominance == pytest.approx(0.1)


def test_write_is_valid_and_can_be_reloaded(tmp_path) -> None:
    path = tmp_path / "nested" / "emotion.json"
    tracker = EmotionStateTracker(path, alpha=0.5)

    written = tracker.update(_emotion("surprise"), now=407.0)
    reloaded = EmotionStateTracker(path, alpha=0.5).state

    assert path.is_file()
    assert reloaded.to_dict() == written.to_dict()
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))
    json.loads(path.read_text(encoding="utf-8"))


def test_repeated_replace_works_with_closed_temp_file(tmp_path) -> None:
    path = tmp_path / "emotion.json"
    tracker = EmotionStateTracker(path)

    tracker.update(_emotion("happy"), now=1.0)
    tracker.update(_emotion("sad"), now=2.0)

    assert json.loads(path.read_text(encoding="utf-8"))["updated_at"] == 2.0


def test_update_rejects_nonfinite_injected_time(tmp_path) -> None:
    tracker = EmotionStateTracker(tmp_path / "emotion.json")

    with pytest.raises(ValueError, match="timestamp"):
        tracker.update(_emotion("neutral"), now=float("nan"))


def test_config_parses_and_bounds_emotion_state_values(monkeypatch) -> None:
    import srtp_voice.config as config_module

    monkeypatch.setattr(config_module, "load_dotenv", None)
    monkeypatch.setenv("EMOTION_SMOOTH_ALPHA", "2")
    monkeypatch.setenv("EMOTION_DECAY_HALF_LIFE_SECONDS", "0")
    monkeypatch.setenv("EMOTION_MAX_STEP", "99")

    cfg = AppConfig.from_env()

    assert cfg.emotion_smooth_alpha == 1.0
    assert cfg.emotion_decay_half_life_seconds == 1.0
    assert cfg.emotion_max_step == 2.0


def test_config_nonfinite_emotion_values_use_defaults(monkeypatch) -> None:
    import srtp_voice.config as config_module

    monkeypatch.setattr(config_module, "load_dotenv", None)
    monkeypatch.setenv("EMOTION_SMOOTH_ALPHA", "NaN")
    monkeypatch.setenv("EMOTION_DECAY_HALF_LIFE_SECONDS", "Infinity")
    monkeypatch.setenv("EMOTION_MAX_STEP", "-Infinity")

    cfg = AppConfig.from_env()

    assert cfg.emotion_smooth_alpha == 0.35
    assert cfg.emotion_decay_half_life_seconds == 120.0
    assert cfg.emotion_max_step == 0.25


def test_config_applies_lower_emotion_state_bounds(monkeypatch) -> None:
    import srtp_voice.config as config_module

    monkeypatch.setattr(config_module, "load_dotenv", None)
    monkeypatch.setenv("EMOTION_SMOOTH_ALPHA", "-1")
    monkeypatch.setenv("EMOTION_DECAY_HALF_LIFE_SECONDS", "-10")
    monkeypatch.setenv("EMOTION_MAX_STEP", "0")

    cfg = AppConfig.from_env()

    assert cfg.emotion_smooth_alpha == 0.0
    assert cfg.emotion_decay_half_life_seconds == 1.0
    assert cfg.emotion_max_step == 0.001
