"""Natural sentence-boundary tests: decimals, abbreviations and lossless limits."""

from __future__ import annotations

from srtp_voice.streaming import SentenceChunker


def _feed_all(text: str, **kwargs) -> tuple[list[str], str]:
    chunker = SentenceChunker(**kwargs)
    chunks: list[str] = []
    for character in text:
        chunks.extend(chunk.text for chunk in chunker.feed(character, now=1.0))
    chunks.extend(chunk.text for chunk in chunker.finish(now=2.0))
    return chunks, chunker.buffered_text


def test_natural_mode_keeps_decimals_and_versions_intact() -> None:
    chunks, remaining = _feed_all(
        "圆周率约 3.14，版本 v1.2 已经发布。", prefer_sentence_boundaries=True
    )

    assert remaining == ""
    joined = "".join(chunks)
    assert "3.14" in joined and "v1.2" in joined
    # The decimal and the version each stay inside a single chunk, and no chunk
    # is a bare terminator produced by splitting the number apart.
    assert sum("3.14" in chunk for chunk in chunks) == 1
    assert sum("v1.2" in chunk for chunk in chunks) == 1
    assert not any(chunk.strip() in {"3.", ".14", "v1.", "3", "14"} for chunk in chunks)
    assert chunks[-1].endswith("。")


def test_natural_mode_keeps_english_words_and_abbreviations() -> None:
    chunks, remaining = _feed_all(
        "见 Dr. Smith 写的 e.g. 示例，token 计数不要拆。", prefer_sentence_boundaries=True
    )

    joined = "".join(chunks)
    assert remaining == ""
    assert "Dr. Smith" in joined
    assert "e.g." in joined
    assert all("toke" not in chunk or chunk.endswith("token") or "token" in chunk for chunk in chunks)


def test_natural_mode_does_not_split_on_enumeration_commas() -> None:
    text = "准备苹果、香蕉，然后带上水。"
    chunks, remaining = _feed_all(
        text, prefer_sentence_boundaries=True, min_chars=2, max_chars=80
    )

    assert remaining == ""
    assert "".join(chunks) == text
    assert len(chunks) == 1


def test_natural_mode_does_not_split_at_a_list_introducing_colon() -> None:
    text = "推荐三道菜：凉拌黄瓜、番茄炒蛋和冬瓜汤，都很清爽。"
    chunks, remaining = _feed_all(
        text, prefer_sentence_boundaries=True, min_chars=2, max_chars=80
    )

    assert remaining == ""
    assert "".join(chunks) == text
    assert len(chunks) == 1, f"a list must stay one sentence, got {chunks}"


def test_legacy_mode_keeps_comma_break_behaviour() -> None:
    text = "甲，乙。"
    legacy, _ = _feed_all(text, prefer_sentence_boundaries=False, min_chars=1, max_chars=80)
    natural, _ = _feed_all(text, prefer_sentence_boundaries=True, min_chars=1, max_chars=80)

    assert len(legacy) >= 2
    assert "".join(legacy) == text
    assert "".join(natural) == text


def test_max_chars_fallback_prefers_complete_clauses_not_enumeration_marks() -> None:
    text = "第一段内容。第二段内容，第三段内容尾巴"
    chunks, remaining = _feed_all(
        text, prefer_sentence_boundaries=True, min_chars=1, max_chars=8
    )

    assert remaining == ""
    assert "".join(chunks) == text
    assert all(chunk.strip() for chunk in chunks)
    # Normal feeds retain commas; at the hard length bound a complete clause
    # is preferable to splitting inside a phrase. A list separator is not one.
    assert chunks == ["第一段内容。", "第二段内容，", "第三段内容尾巴"]
    assert max(map(len, chunks)) <= 8
    assert not any(chunk.endswith("、") for chunk in chunks)


def test_hard_max_without_boundary_is_still_lossless() -> None:
    text = "abcdefghijklmnop" * 3
    chunks, remaining = _feed_all(
        text, prefer_sentence_boundaries=True, min_chars=1, max_chars=10
    )

    assert remaining == ""
    assert "".join(chunks) == text
    assert max(len(chunk) for chunk in chunks) <= 10


def test_flush_due_respects_wait_window_in_natural_mode() -> None:
    chunker = SentenceChunker(
        prefer_sentence_boundaries=True, min_chars=2, max_chars=80, max_wait_seconds=0.5
    )
    assert chunker.feed("等待边界", now=1.0) == []
    flushed = chunker.flush_due(now=1.6)
    assert [chunk.text for chunk in flushed] == ["等待边界"]


def test_preview_does_not_treat_pending_decimal_as_sentence_end() -> None:
    chunker = SentenceChunker(prefer_sentence_boundaries=True, min_chars=1, max_chars=80)
    chunker.feed("价格是 3.", now=1.0)

    assert chunker.peek_pending_hard_boundary() is None

    chunker.feed("5 元。", now=1.1)
    preview = chunker.peek_pending_hard_boundary()
    assert preview is not None and preview.text == "价格是 3.5 元。"
