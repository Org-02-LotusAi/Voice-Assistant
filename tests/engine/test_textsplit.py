import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
from textsplit import MAX_CHUNK, split_for_streaming  # noqa: E402


def test_two_sentences_split():
    assert split_for_streaming("Hello there, how are you today? I am doing well, thank you.") == [
        "Hello there, how are you today?",
        "I am doing well, thank you.",
    ]


def test_long_first_sentence_cut_at_first_clause():
    text = "When you have a moment, I would love to walk you through how our service works and what to expect."
    chunks = split_for_streaming(text)
    assert chunks == ["When you have a moment,", "I would love to walk you through how our service works and what to expect."]


def test_long_sentence_without_usable_clause_stays_whole():
    text = "Well, I think that the best way to handle this situation is to take a careful look at the details first."
    assert split_for_streaming(text) == [text]


def test_tiny_pieces_merged_not_clipped():
    chunks = split_for_streaming("Hi. Yes. That works for me, thanks a lot.")
    assert all(len(c) >= 10 for c in chunks)
    assert " ".join(chunks) == "Hi. Yes. That works for me, thanks a lot."


def test_very_long_text_respects_max_chunk_and_loses_nothing():
    text = " ".join(["word"] * 400)
    chunks = split_for_streaming(text)
    assert len(chunks) > 1 and all(len(c) <= MAX_CHUNK for c in chunks)
    assert " ".join(chunks) == text


def test_empty_and_whitespace():
    assert split_for_streaming("   \n  ") == []
