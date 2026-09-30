"""Independent regressions for PR #25 numeric sentence-end review."""

import pytest

from srtp_voice.streaming import SentenceChunker


@pytest.mark.parametrize("natural", [False, True])
def test_number_sentence_end_with_known_lookahead_splits_immediately(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)
    chunks = chunker.feed("结果是42. 下一句。", now=0)
    assert [chunk.text for chunk in chunks] == ["结果是42."]
    preview = chunker.peek_pending_hard_boundary()
    assert preview is not None and preview.text == " 下一句。"
    chunks += chunker.finish(now=0.1)
    assert "".join(chunk.text for chunk in chunks) == "结果是42. 下一句。"


@pytest.mark.parametrize("natural", [False, True])
def test_number_sentence_end_is_reconsidered_at_next_token(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)
    assert chunker.feed("结果是42.", now=0) == []
    # An unseen next character must not trigger irreversible TTS preview.
    assert chunker.peek_pending_hard_boundary() is None
    chunks = chunker.feed("下一句。", now=0.1)
    assert [chunk.text for chunk in chunks] == ["结果是42."]
    chunks += chunker.finish(now=0.2)
    assert [chunk.text for chunk in chunks] == ["结果是42.", "下一句。"]


@pytest.mark.parametrize("natural", [False, True])
def test_numeric_point_with_next_digit_stays_decimal_and_version(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)
    chunks = chunker.feed("数值为3.", now=0)
    assert chunks == [] and chunker.peek_pending_hard_boundary() is None
    chunks += chunker.feed("14和版本1.", now=0.1)
    assert chunks == [] and chunker.peek_pending_hard_boundary() is None
    chunks += chunker.feed("23都保留。", now=0.2)
    chunks += chunker.finish(now=0.3)
    assert [chunk.text for chunk in chunks] == ["数值为3.14和版本1.23都保留。"]


@pytest.mark.parametrize("natural", [False, True])
def test_ordinary_hard_boundary_keeps_whitespace_split(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)
    chunks = chunker.feed("第一句。 下一句。", now=0)
    assert [chunk.text for chunk in chunks] == ["第一句。"]
    chunks += chunker.finish(now=0.1)
    assert [chunk.text for chunk in chunks] == ["第一句。", " 下一句。"]


@pytest.mark.parametrize("natural", [False, True])
def test_decimal_lookahead_before_timeout_is_not_flushed_as_sentence(natural):
    chunker = SentenceChunker(
        prefer_sentence_boundaries=natural, max_wait_seconds=0.5
    )
    assert chunker.feed("数值是3.", now=0) == []
    chunks = chunker.feed("14。", now=0.6)
    chunks += chunker.finish(now=0.7)
    assert [chunk.text for chunk in chunks] == ["数值是3.14。"]
