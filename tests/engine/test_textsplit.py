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


def test_long_sentence_without_clause_is_cut_at_word_boundary():
    text = "Well I think that the best way to handle this situation is to take a careful look at the details first."
    chunks = split_for_streaming(text)
    assert 14 <= len(chunks[0]) <= 41 and len(chunks) == 2
    assert text.startswith(chunks[0]) and " ".join(chunks) == text


def test_first_chunk_is_short_for_long_sentences():
    text = "I would be delighted to explain everything about how our service works, and what you can expect from us."
    chunks = split_for_streaming(text)
    assert len(chunks[0]) <= 45
    assert " ".join(chunks) == text


def test_short_final_piece_joins_previous_but_not_first():
    chunks = split_for_streaming("Hello there, friend. This is a longer middle sentence here. Okay.")
    assert chunks[-1].endswith("Okay.") and len(chunks) == 2
    assert split_for_streaming("Hello there, friend. Okay.") == ["Hello there, friend.", "Okay."]


def test_tiny_pieces_merged_not_clipped():
    chunks = split_for_streaming("Hi. Yes. That works for me, thanks a lot.")
    assert all(len(c) >= 10 for c in chunks)
    assert " ".join(chunks) == "Hi. Yes. That works for me, thanks a lot."


def test_very_long_text_respects_max_chunk_and_loses_nothing():
    text = " ".join(["word"] * 400)
    chunks = split_for_streaming(text)
    assert MAX_CHUNK == 120 and len(chunks) > 1 and all(len(c) <= MAX_CHUNK for c in chunks)
    assert " ".join(chunks) == text


def test_empty_and_whitespace():
    assert split_for_streaming("   \n  ") == []
