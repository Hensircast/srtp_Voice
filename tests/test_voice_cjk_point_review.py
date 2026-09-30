"""Independent PR #25 CJK-lookahead sentence-delivery regressions."""

import pytest

from srtp_voice.streaming import SentenceChunker


@pytest.mark.parametrize("natural", [False, True])
@pytest.mark.parametrize("first", ["OK.", "Go.", "Hi.", "Nope."])
def test_cjk_lookahead_after_normal_word_releases_first_sentence(natural, first):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)
    chunks = chunker.feed(first + "下一句", now=0)
    assert [chunk.text for chunk in chunks] == [first]
    chunks += chunker.feed("。", now=0.1) + chunker.finish(now=0.2)
    assert [chunk.text for chunk in chunks] == [first, "下一句。"]


@pytest.mark.parametrize("natural", [False, True])
def test_short_word_cjk_boundary_is_independent_of_token_partition(natural):
    source = "OK.下一句。"
    for parts in ([source], list(source), ["OK.", "下一句。"]):
        chunker = SentenceChunker(prefer_sentence_boundaries=natural)
        chunks = []
        for part in parts:
            chunks += chunker.feed(part, now=0)
        chunks += chunker.finish(now=0.1)
        assert [chunk.text for chunk in chunks] == ["OK.", "下一句。"]
        assert "".join(chunk.text for chunk in chunks) == source


@pytest.mark.parametrize("natural", [False, True])
def test_ascii_short_word_continuation_rule_is_unchanged(natural):
    chunker = SentenceChunker(prefer_sentence_boundaries=natural)
    chunks = chunker.feed("Read ab.cd here。", now=0) + chunker.finish(now=0.1)
    assert [chunk.text for chunk in chunks] == ["Read ab.cd here。"]
