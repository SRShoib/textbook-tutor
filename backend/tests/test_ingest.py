"""Tests for the pure (no DB, no embedding model) functions in pipeline/ingest.py.
CLAUDE.md: test pipeline/ functions, skip route tests."""

from app.models.chunk import ChunkType
from app.pipeline.ingest import (
    Stage,
    UnitEntry,
    chunk_words,
    classify_stage,
    looks_english,
    parse_contents,
    repair_span,
    split_into_stages,
    strip_page_furniture,
)


class FakePage:
    """Minimal stand-in for pipeline.ingest.PageText, since parse_contents
    only reads .text."""

    def __init__(self, text: str):
        self.text = text


# --- repair_span -------------------------------------------------------

def test_repair_span_real_captured_sequence():
    # Captured verbatim from the NCTB Class 5 English for Today PDF, page 7:
    # PyMuPDF's raw (broken-font) extraction of "b) What do you do in the
    # library?" — every character, including the shifted spaces (\x03), is
    # exactly 29 lower in ASCII than the real character.
    garbled = "\x03 :KDW\x03GR\x03\\RX\x03GR\x03LQ\x03WKH\x03OLEUDU\\\""
    assert repair_span(garbled) == "  What do you do in the library?"


def test_repair_span_ligatures():
    # "science fiction", "beautiful flowers", "goat, sheep, buffalo",
    # "Post Office" — each ligature confirmed against >=2 independent
    # occurrences in the book. Real spans always carry these ligatures
    # alongside other words in the same font run (see the isolated-span
    # limitation test below), so the fixtures include that real context
    # rather than testing the ligature character in artificial isolation.
    assert repair_span("VFLHQFH\x03¿FWLRQ") == "science fiction"
    assert repair_span("EHDXWLIXO\x03ÀRZHUV") == "beautiful flowers"
    assert repair_span("JRDW\x0f\x03VKHHS\x0f\x03EX൵DOR") == "goat, sheep, buffalo"
    assert repair_span("3RVW\x032൶FH") == "Post Office"


def test_repair_span_known_limitation_isolated_ligature_word():
    # A span containing *only* a ligature word with no space/punctuation has
    # no control character to trigger repair, so it is left unrepaired. This
    # is a documented, accepted gap (see ingest.py's module docstring) rather
    # than a bug: in the real PDF this only ever happens when a ligature word
    # is bolded completely alone, which is rare and would be caught by eye
    # when reviewing sample chunks.
    assert repair_span("EX൵DOR") == "EX൵DOR"


def test_repair_span_leaves_correct_text_untouched():
    # No control character present anywhere in these -> never touched, even
    # though they are genuine ALL-CAPS text from the stressed-syllable
    # exercises (a plain +29 shift would otherwise mangle them).
    assert repair_span("BIRTH-day") == "BIRTH-day"
    assert repair_span("Rina: Hello!") == "Rina: Hello!"


def test_repair_span_never_reshifts_a_literal_space():
    # Some bold headings double-space words: one shifted space (\x03) plus an
    # already-correct literal space sitting right next to it in the same
    # span. Reshifting that literal space would turn it into "=" (0x20+29).
    garbled = "5HDG\x03 WKH"
    assert repair_span(garbled) == "Read  the"
    assert "=" not in repair_span(garbled)


# --- looks_english -------------------------------------------------------

def test_looks_english_scores_real_text_higher_than_garbled():
    real = "What do you do in the library"
    garbled = ":KDW GR \\RX GR LQ WKH OLEUDU\\"
    assert looks_english(real) > looks_english(garbled)


def test_looks_english_empty_string_is_zero():
    assert looks_english("") == 0.0


# --- parse_contents -------------------------------------------------------

def test_parse_contents_reads_unit_entries():
    toc_text = (
        "Contents\n"
        "Unit 1 \nAt the Library \n \n \n1\n"
        "Unit 2 \nOur School Garden \n \n \n7\n"
        "Unit 3 \nBe Quiet, Please  \n \n \n12\n"
    )
    units = parse_contents([FakePage(toc_text)])
    assert units == [
        UnitEntry(unit_no=1, title="At the Library", printed_start_page=1),
        UnitEntry(unit_no=2, title="Our School Garden", printed_start_page=7),
        UnitEntry(unit_no=3, title="Be Quiet, Please", printed_start_page=12),
    ]


def test_parse_contents_raises_without_a_contents_page():
    import pytest

    with pytest.raises(ValueError):
        parse_contents([FakePage("Some other page\nwith no contents heading")])


# --- strip_page_furniture -------------------------------------------------------

def test_strip_page_furniture_removes_watermark_and_footer():
    page = "2026\nSome lesson text here.\n2\nUnit\n1\nAt the Library\nEnglish for Today"
    assert strip_page_furniture(page, unit_title="At the Library") == "Some lesson text here."


def test_strip_page_furniture_handles_reordered_footer():
    # A picture-grid-layout page puts title/"English for Today" before the
    # page-num/Unit/unit-num block instead of after it.
    page = "2026\na\nb\nc\nEnglish for Today\nWriting a Story\n110\nUnit\n20"
    assert strip_page_furniture(page, unit_title="Writing a Story") == "a\nb\nc"


# --- split_into_stages / classify_stage -------------------------------------------------------

def test_split_into_stages_splits_on_top_level_markers_only():
    text = (
        "1.1 Look at the pictures.\n"
        "1.1.1 Clap your hands.\n"
        "2.1 Pairwork. Ask and answer.\n"
    )
    stages = split_into_stages(unit_no=1, text=text)
    assert len(stages) == 2
    assert stages[0].stage_no == 1
    assert "1.1.1 Clap your hands." in stages[0].text  # sub-item stays with its parent
    assert stages[1].text.startswith("2.1 Pairwork")


def test_split_into_stages_no_markers_is_one_stage():
    stages = split_into_stages(unit_no=1, text="Just some plain text.")
    assert len(stages) == 1
    assert stages[0].stage_no == 1


def test_classify_stage_language_focus_is_passage():
    stage = Stage(unit_no=1, stage_no=2, text="3.1 Language Focus: Common Noun\nLook at the words.")
    assert classify_stage(stage) == ChunkType.PASSAGE


def test_classify_stage_vocab_marker_only_checked_in_heading():
    # Regression: "new words" appearing in the *body* of a Language Focus
    # stage about word formation must not be misclassified as vocabulary.
    stage = Stage(
        unit_no=13,
        stage_no=5,
        text="3.1 Language Focus: Prefix and Suffix\nTo make new words, prefixes or suffixes are added.",
    )
    assert classify_stage(stage) == ChunkType.PASSAGE


def test_classify_stage_vocab_marker_in_heading_is_vocabulary():
    stage = Stage(unit_no=1, stage_no=2, text="New Words\ncat, dog, bird")
    assert classify_stage(stage) == ChunkType.VOCABULARY


def test_classify_stage_default_is_exercise():
    stage = Stage(unit_no=1, stage_no=2, text="2.1 Fill in the blanks.")
    assert classify_stage(stage) == ChunkType.EXERCISE


# --- chunk_words -------------------------------------------------------

def test_chunk_words_single_chunk_when_under_limit():
    text = " ".join(f"word{i}" for i in range(10))
    chunks = chunk_words(text, max_words=600, overlap=50)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_words_empty_text_is_no_chunks():
    assert chunk_words("", max_words=600, overlap=50) == []
    assert chunk_words("   ", max_words=600, overlap=50) == []


def test_chunk_words_splits_and_overlaps():
    words = [f"w{i}" for i in range(150)]
    text = " ".join(words)
    chunks = chunk_words(text, max_words=100, overlap=20)
    assert len(chunks) == 2
    first_words = chunks[0].split()
    second_words = chunks[1].split()
    assert len(first_words) == 100
    assert first_words[-20:] == second_words[:20]  # the overlap region matches
    assert second_words[-1] == "w149"  # the split reaches the end of the text


def test_chunk_words_normalizes_internal_whitespace():
    # A doubled space (see the "never reshift a literal space" case above)
    # should collapse to one space, not survive into the stored chunk.
    text = "Read  the  names  given"
    assert chunk_words(text, max_words=600, overlap=50) == ["Read the names given"]
