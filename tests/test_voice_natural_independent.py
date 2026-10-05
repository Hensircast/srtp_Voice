"""Independent lossless and prosodic-boundary checks for natural mode."""

from srtp_voice.streaming import SentenceChunker


def _natural(**kwargs):
    return SentenceChunker(prefer_sentence_boundaries=True, **kwargs)


def test_cross_token_decimal_never_previews_a_false_sentence():
    chunker = _natural(min_chars=1, max_chars=80)
    emitted = chunker.feed("圆周率是3.", now=0)
    assert emitted == []
    assert chunker.peek_pending_hard_boundary() is None
    emitted += chunker.feed("14，温度为26.", now=0.1)
    assert emitted == []
    assert chunker.peek_pending_hard_boundary() is None
    emitted += chunker.feed("5摄氏度。", now=0.2)
    preview = chunker.peek_pending_hard_boundary()
    assert preview is not None
    assert preview.text == "圆周率是3.14，温度为26.5摄氏度。"
    emitted += chunker.finish(now=0.3)
    assert [chunk.text for chunk in emitted] == [preview.text]


def test_short_enumeration_stays_in_one_natural_sentence():
    chunker = _natural(min_chars=3, max_chars=80)
    source = "推荐凉拌黄瓜、番茄炒蛋和冬瓜汤，都很适合夏天。"
    result = []
    for ordinal, part in enumerate([source[:9], source[9:18], source[18:]]):
        result += chunker.feed(part, now=ordinal * 0.1)
    result += chunker.finish(now=0.3)
    assert [chunk.text for chunk in result] == [source]


def test_timeout_uses_completed_words_and_keeps_unfinished_word():
    chunker = _natural(min_chars=3, max_chars=80, max_wait_seconds=0.5)
    result = chunker.feed("Let's make the response natu", now=0)
    result += chunker.feed("ral and clear.", now=0.6)
    result += chunker.finish(now=0.7)
    pieces = [chunk.text for chunk in result]
    assert "".join(pieces) == "Let's make the response natural and clear."
    assert len(pieces) >= 2  # Timeout still makes progress.
    assert all(not piece.endswith("natu") and not piece.startswith("ral") for piece in pieces)


def test_abbreviation_does_not_preview_or_split_into_a_sentence():
    chunker = _natural(min_chars=1, max_chars=80)
    result = chunker.feed("Ask Dr.", now=0)
    assert chunker.peek_pending_hard_boundary() is None
    result += chunker.feed(" Smith about version 1.", now=0.1)
    assert chunker.peek_pending_hard_boundary() is None
    result += chunker.feed("23 tomorrow.", now=0.2)
    result += chunker.finish(now=0.3)
    assert [chunk.text for chunk in result] == ["Ask Dr. Smith about version 1.23 tomorrow."]


def test_long_unpunctuated_text_has_a_hard_memory_bound():
    chunker = _natural(min_chars=3, max_chars=12)
    source = "这是没有标点的中文长句" * 15
    result = chunker.feed(source, now=0)
    result += chunker.finish(now=0.1)
    assert "".join(chunk.text for chunk in result) == source
    assert all(len(chunk.text) <= 12 for chunk in result)
    assert len(result) > 1


def test_timeout_prefers_a_complete_chinese_clause():
    chunker = _natural(min_chars=3, max_chars=80, max_wait_seconds=0.5)
    result = chunker.feed("先烧开水，再加入蔬菜慢慢", now=0)
    result += chunker.flush_due(now=0.6)
    assert [chunk.text for chunk in result] == ["先烧开水，"]
    result += chunker.feed("煮。", now=0.7)
    result += chunker.finish(now=0.8)
    assert [chunk.text for chunk in result] == ["先烧开水，", "再加入蔬菜慢慢煮。"]


def test_abbreviation_with_chinese_prefix_and_cross_token_initial():
    chunker = _natural(min_chars=1, max_chars=80)
    result = chunker.feed("请咨询Dr.", now=0)
    assert chunker.peek_pending_hard_boundary() is None
    result += chunker.feed(" Smith，例如 e.", now=0.1)
    assert chunker.peek_pending_hard_boundary() is None
    result += chunker.feed("g. 是举例。", now=0.2)
    result += chunker.finish(now=0.3)
    assert [chunk.text for chunk in result] == ["请咨询Dr. Smith，例如 e.g. 是举例。"]


def test_natural_boundary_flag_rejects_string_truthiness():
    import pytest
    with pytest.raises(TypeError, match="boolean"):
        SentenceChunker(prefer_sentence_boundaries="false")
