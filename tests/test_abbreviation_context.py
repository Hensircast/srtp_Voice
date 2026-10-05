"""Own regressions for abbreviation context resolution in SentenceChunker.

Public text and explicit clocks only: these assert chunk availability and
lossless reconstruction, not device latency or voice quality.
"""

from __future__ import annotations

import pytest

from srtp_voice.streaming import SentenceChunker


@pytest.mark.parametrize("natural", [False, True])
@pytest.mark.parametrize(
    "first,following",
    [("Use etc.", " Next step."), ("答案是A.", " 下一题。")],
)
def test_first_sentence_is_delivered_once_context_arrives(natural, first, following):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural, max_wait_seconds=0.5)

    # The abbreviation period is undecided while it sits at the token edge.
    assert chunker.feed(first, now=0) == []
    assert chunker.peek_pending_hard_boundary() is None

    # Real context arrives well before the wait deadline.
    chunks = chunker.feed(following, now=0.1)
    assert [chunk.text for chunk in chunks] == [first]
    assert chunker.buffered_text == following
    chunks += chunker.finish(now=0.2)
    assert "".join(chunk.text for chunk in chunks) == first + following
    assert [chunk.sequence_id for chunk in chunks] == list(range(len(chunks)))


@pytest.mark.parametrize("natural", [False, True])
def test_space_only_token_does_not_decide_an_abbreviation(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural, max_wait_seconds=0.5)
    chunker.feed("Use etc.", now=0)
    chunker.feed(" ", now=0.1)
    assert chunker.peek_pending_hard_boundary() is None

    chunks = chunker.feed("Next step.", now=0.2)
    chunks += chunker.finish(now=0.3)
    assert [chunk.text for chunk in chunks] == ["Use etc.", " Next step."]


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
def test_real_continuations_are_never_split(natural, parts):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural, max_wait_seconds=0.5)
    chunks = []
    for ordinal, part in enumerate(parts):
        chunks += chunker.feed(part, now=ordinal * 0.6)
    chunks += chunker.finish(now=len(parts) * 0.6)

    assert [chunk.text for chunk in chunks] == ["".join(parts)]


@pytest.mark.parametrize("natural", [False, True])
def test_lowercase_continuation_after_initial_is_treated_as_a_sentence_end(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural, max_wait_seconds=0.5)

    assert chunker.feed("答案是A.", now=0) == []
    chunks = chunker.feed(" next question.", now=0.1)
    assert [chunk.text for chunk in chunks] == ["答案是A."]
    chunks += chunker.finish(now=0.2)
    assert "".join(chunk.text for chunk in chunks) == "答案是A. next question."


@pytest.mark.parametrize("natural", [False, True])
def test_unresolved_period_is_flushed_by_finish_and_reset(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)

    assert chunker.feed("Use etc.", now=0) == []
    chunks = chunker.finish(now=0.1)
    assert [chunk.text for chunk in chunks] == ["Use etc."]
    assert chunker.buffered_text == ""

    chunks = chunker.feed("新句。", now=0.2) + chunker.finish(now=0.3)
    assert [chunk.text for chunk in chunks] == ["新句。"]


@pytest.mark.parametrize("natural", [False, True])
def test_decimal_and_version_are_unaffected(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural, max_wait_seconds=0.5)

    assert chunker.feed("数值为3.", now=0) == []
    assert chunker.feed("14和版本1.", now=0.1) == []
    assert chunker.feed("23都保留。", now=0.2) == []
    chunks = chunker.finish(now=0.3)
    assert [chunk.text for chunk in chunks] == ["数值为3.14和版本1.23都保留。"]


@pytest.mark.parametrize("natural", [False, True])
def test_digit_sentence_end_still_splits_immediately(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)

    chunks = chunker.feed("结果是42. 下一句。", now=0)
    assert [chunk.text for chunk in chunks] == ["结果是42."]
    chunks += chunker.finish(now=0.1)
    assert "".join(chunk.text for chunk in chunks) == "结果是42. 下一句。"
