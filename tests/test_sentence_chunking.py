from __future__ import annotations

import pytest

from srtp_voice.streaming import SentenceChunker


def _texts(chunks):
    return [chunk.text for chunk in chunks]


def test_sentence_chunker_splits_chinese_and_english_punctuation_losslessly() -> None:
    source = "你好，世界！Are you ok?我很好；继续：结束、下一段。"
    chunker = SentenceChunker(turn_id="turn-1", max_chars=100)

    chunks = chunker.feed(source, now=1.0) + chunker.finish(now=1.1)

    assert _texts(chunks) == [
        "你好，",
        "世界！",
        "Are you ok?",
        "我很好；",
        "继续：",
        "结束、",
        "下一段。",
    ]
    assert "".join(_texts(chunks)) == source
    assert [chunk.sequence_id for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.turn_id == "turn-1" for chunk in chunks)
    assert chunks[-1].is_final is False


def test_sentence_chunker_keeps_closing_quotes_and_brackets_with_punctuation() -> None:
    chunker = SentenceChunker(max_chars=100)
    chunks = chunker.feed("你好！”然后（真的？）好。", now=2.0)

    assert _texts(chunks) == ["你好！”", "然后（真的？）", "好。"]


def test_sentence_chunker_keeps_repeated_terminal_marks_together() -> None:
    source = "真的？！好吧……继续。"
    chunker = SentenceChunker(max_chars=100)

    chunks = chunker.feed(source, now=3.0) + chunker.finish(now=3.1)

    assert _texts(chunks) == ["真的？！", "好吧……", "继续。"]
    assert "".join(_texts(chunks)) == source


def test_sentence_chunker_uses_max_chars_for_text_without_punctuation() -> None:
    source = "abcdefghij"
    chunker = SentenceChunker(max_chars=4)

    chunks = chunker.feed(source, now=4.0) + chunker.finish(now=4.1)

    assert _texts(chunks) == ["abcd", "efgh", "ij"]
    assert "".join(_texts(chunks)) == source


def test_sentence_chunker_flushes_after_max_wait() -> None:
    chunker = SentenceChunker(max_chars=100, max_wait_seconds=0.5)

    assert chunker.feed("尚未完成", now=5.0) == []
    assert chunker.flush_due(now=5.49) == []
    chunks = chunker.flush_due(now=5.5)

    assert _texts(chunks) == ["尚未完成"]
    assert chunks[0].timestamp_ms == 5500
    assert chunker.buffered_text == ""


def test_sentence_chunker_emits_hard_boundary_at_end_of_feed() -> None:
    chunker = SentenceChunker(max_chars=100, max_wait_seconds=10.0)

    chunks = chunker.feed("第一句。", now=5.0)

    assert _texts(chunks) == ["第一句。"]
    assert chunker.buffered_text == ""
    assert chunker.finish(now=5.1) == []


def test_sentence_chunker_coalesces_short_soft_punctuation_fragments() -> None:
    source = "你好，我是语音助手。推荐三道菜：凉拌黄瓜、番茄炒蛋和冬瓜汤，都很清爽。"
    chunker = SentenceChunker(min_chars=12, max_chars=100)

    chunks = chunker.feed(source, now=5.0) + chunker.finish(now=5.1)

    assert _texts(chunks) == [
        "你好，我是语音助手。",
        "推荐三道菜：凉拌黄瓜、番茄炒蛋和冬瓜汤，",
        "都很清爽。",
    ]
    assert "".join(_texts(chunks)) == source


def test_sentence_chunker_does_not_timeout_flush_whitespace_or_tiny_text() -> None:
    chunker = SentenceChunker(
        min_chars=4,
        max_chars=100,
        max_wait_seconds=0.5,
    )

    assert chunker.feed("  \n", now=6.0) == []
    assert chunker.flush_due(now=7.0) == []
    chunks = chunker.finish(now=7.1)

    assert _texts(chunks) == ["  \n"]


def test_sentence_chunker_preserves_every_character_across_token_boundaries() -> None:
    tokens = ["第", "一句。第", "二句（含", "括号）！", "尾巴没有标点"]
    source = "".join(tokens)
    chunker = SentenceChunker(max_chars=7, max_wait_seconds=10.0)
    chunks = []
    for index, token in enumerate(tokens):
        chunks.extend(chunker.feed(token, now=6.0 + index / 10.0))
    chunks.extend(chunker.finish(now=7.0))

    assert "".join(_texts(chunks)) == source
    assert len("".join(_texts(chunks))) == len(source)
    assert [chunk.sequence_id for chunk in chunks] == list(range(len(chunks)))


def test_sentence_chunker_rejects_invalid_limits_and_clock_regression() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        SentenceChunker(max_chars=0)
    with pytest.raises(ValueError, match="min_chars"):
        SentenceChunker(min_chars=0)
    with pytest.raises(ValueError, match="greater than zero"):
        SentenceChunker(max_wait_seconds=0)

    chunker = SentenceChunker()
    chunker.feed("文本", now=10.0)
    with pytest.raises(RuntimeError, match="backwards"):
        chunker.flush_due(now=9.0)
