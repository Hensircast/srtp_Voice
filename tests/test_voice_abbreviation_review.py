"""Codex-owned, lossless streaming regressions for PR #25 abbreviation review.

Public text and explicit clocks only: these prove chunk availability, not
real-model latency or subjective voice quality.
"""

import pytest

from srtp_voice.streaming import SentenceChunker


@pytest.mark.parametrize("natural", [False, True])
@pytest.mark.parametrize(
    "first,following",
    [("Use etc.", " Next step."), ("答案是A.", " 下一题。")],
)
def test_known_sentence_end_is_available_before_following_sentence_finishes(
    natural, first, following
):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)
    chunks = chunker.feed(first + following[:-1], now=0)
    assert [chunk.text for chunk in chunks] == [first]
    assert chunker.buffered_text == following[:-1]
    chunks += chunker.feed(following[-1], now=0.1)
    chunks += chunker.finish(now=0.2)
    assert "".join(chunk.text for chunk in chunks) == first + following
    assert [chunk.sequence_id for chunk in chunks] == list(range(len(chunks)))


@pytest.mark.parametrize("natural", [False, True])
@pytest.mark.parametrize(
    "first,following",
    [("Use etc.", " Next step."), ("答案是A.", " 下一题。")],
)
def test_token_edge_sentence_end_is_reconsidered_before_timeout(
    natural, first, following
):
    chunker = SentenceChunker(
        prefer_sentence_boundaries=natural, max_wait_seconds=0.5
    )
    assert chunker.feed(first, now=0) == []
    assert chunker.peek_pending_hard_boundary() is None
    chunks = chunker.feed(following[:-1], now=0.1)
    assert [chunk.text for chunk in chunks] == [first]
    chunks += chunker.feed(following[-1], now=0.2)
    chunks += chunker.finish(now=0.3)
    assert "".join(chunk.text for chunk in chunks) == first + following


@pytest.mark.parametrize("natural", [False, True])
@pytest.mark.parametrize(
    "first,following",
    [("Use etc.", "Next step."), ("答案是A.", "下一题。")],
)
def test_space_in_its_own_token_keeps_boundary_reconsiderable(
    natural, first, following
):
    chunker = SentenceChunker(
        prefer_sentence_boundaries=natural, max_wait_seconds=0.5
    )
    chunks = chunker.feed(first, now=0)
    chunks += chunker.feed(" ", now=0.1)
    chunks += chunker.feed(following[:-1], now=0.2)
    assert chunks and chunks[0].text == first
    chunks += chunker.feed(following[-1], now=0.3)
    chunks += chunker.finish(now=0.4)
    assert "".join(chunk.text for chunk in chunks) == first + " " + following


@pytest.mark.parametrize("natural", [False, True])
@pytest.mark.parametrize(
    "parts",
    [
        ["Ask Dr.", " Smith about it。"],
        ["请咨询Dr.", " Smith 写的 e.", "g.", " 是举例。"],
        ["Ask A.", " Smith about it。"],
        ["Visit U.", "S.", " offices。"],
        ["Use fruit etc.", " and drink water。"],
    ],
)
def test_real_continuations_stay_intact_even_with_slow_next_token(natural, parts):
    chunker = SentenceChunker(
        prefer_sentence_boundaries=natural, max_wait_seconds=0.5
    )
    chunks = []
    for ordinal, part in enumerate(parts):
        chunks += chunker.feed(part, now=ordinal * 0.6)
    chunks += chunker.finish(now=len(parts) * 0.6)
    # Known lookahead is incorporated before a deadline can cut an abbreviation.
    assert [chunk.text for chunk in chunks] == ["".join(parts)]


@pytest.mark.parametrize("natural", [False, True])
@pytest.mark.parametrize("text", ["Use etc.", "答案是A.", "Ask Dr."])
def test_finish_preserves_unresolved_abbreviation_and_resets_candidate(natural, text):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)
    chunks = chunker.feed(text, now=0)
    chunks += chunker.finish(now=0.1)
    assert [chunk.text for chunk in chunks] == [text]
    assert chunker.buffered_text == ""
    chunks = chunker.feed("新句。", now=0.2)
    chunks += chunker.finish(now=0.3)
    assert [chunk.text for chunk in chunks] == ["新句。"]


@pytest.mark.parametrize("natural", [False, True])
def test_legacy_comma_contract_is_not_changed_by_abbreviation_protection(natural):
    text = "请咨询Dr. Smith，例如 e.g. 是举例。"
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)
    chunks = chunker.feed(text, now=0) + chunker.finish(now=0.1)
    expected = [text] if natural else ["请咨询Dr. Smith，", "例如 e.g. 是举例。"]
    assert [chunk.text for chunk in chunks] == expected


@pytest.mark.parametrize("natural", [False, True])
def test_period_at_buffer_zero_remains_a_normal_hard_boundary(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)
    chunks = chunker.feed(".下一句。", now=0) + chunker.finish(now=0.1)
    assert [chunk.text for chunk in chunks] == [".", "下一句。"]


@pytest.mark.parametrize("natural", [False, True])
def test_plain_text_is_buffered_and_chinese_hard_punctuation_still_works(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural, max_chars=80)
    assert chunker.feed("正常文本没有终止符", now=0) == []
    assert chunker.buffered_text == "正常文本没有终止符"
    chunks = chunker.feed("。下一句！", now=0.1) + chunker.finish(now=0.2)
    assert [chunk.text for chunk in chunks] == ["正常文本没有终止符。", "下一句！"]


@pytest.mark.parametrize("natural", [False, True])
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Use etc. Next step.", ["Use etc.", " Next step."]),
        ("答案是A. 下一题。", ["答案是A.", " 下一题。"]),
        ("Use etc.\" Next step.", ["Use etc.\"", " Next step."]),
        ("Visit U.S. offices。", ["Visit U.S. offices。"]),
        ("Ask Dr. Smith about it。", ["Ask Dr. Smith about it。"]),
        ("请咨询Dr. Smith 写的 e.g. 示例。", ["请咨询Dr. Smith 写的 e.g. 示例。"]),
    ],
)
def test_whole_character_and_every_two_token_partition_are_equivalent(
    natural, text, expected
):
    partitions = [[text], list(text)]
    partitions += [[text[:cut], text[cut:]] for cut in range(1, len(text))]
    for parts in partitions:
        chunker = SentenceChunker(prefer_sentence_boundaries=natural)
        chunks = []
        for part in parts:
            chunks += chunker.feed(part, now=0)
        chunks += chunker.finish(now=0.1)
        assert [chunk.text for chunk in chunks] == expected, parts
        assert "".join(chunk.text for chunk in chunks) == text


@pytest.mark.parametrize("natural", [False, True])
def test_blank_lookahead_does_not_trigger_timeout_or_false_preview(natural):
    chunker = SentenceChunker(
        prefer_sentence_boundaries=natural, max_wait_seconds=0.5
    )
    assert chunker.feed("Use etc.", now=0) == []
    assert chunker.feed(" ", now=0.1) == []
    assert chunker.flush_due(now=0.6) == []
    assert chunker.peek_pending_hard_boundary() is None
    chunks = chunker.feed("Next step.", now=0.7) + chunker.finish(now=0.8)
    assert [chunk.text for chunk in chunks] == ["Use etc.", " Next step."]
