"""
What: turns the NCTB Class 5 English for Today PDF into embedded, lesson-tagged
      chunks stored in the `chunks` table.

Why this file looks the way it does:

1. Broken font. One embedded font in this specific PDF has a corrupt ToUnicode
   CMap: every character extracted through it comes out exactly 29 lower in
   ASCII (`chr(ord(original) - 29)`), including spaces, which survive as \x03.
   A handful of ligature glyphs (fi, fl, ff, ffi) and curly quotes map to fixed
   substitute codepoints instead of a plain shift. This was confirmed by
   sweeping every codepoint in all 118 pages and checking each one against its
   surrounding words (see NOTES.md, Phase 1 session). It is NOT a scanned page
   (PyMuPDF extracts real, selectable text) and Tesseract would not fix it, so
   `repair_span()` below is a deterministic character remap instead of OCR.
   Page 5 (the Bangla preface) uses a different, unrelated legacy Bangla font
   encoding and is simply skipped — it is front matter, not a lesson, and
   decoding a legacy Bangla font is out of scope until Bangla textbook support
   (CLAUDE.md explicitly defers that).
   If NCTB ever reissues this PDF with a fixed font, repair_span() becomes a
   no-op (every span will already read correctly) and can be deleted.

2. No "Lesson" level. This book has 20 Units and no separate lesson numbering.
   Each unit is divided into numbered stages (1.1, 2.1, 3.1, 3.2, 4.1, ...),
   which the book's own Bangla preface describes as pedagogical stages
   (teacher-supported / peer-supported / language focus / independent
   practice). We treat one stage as one "lesson": lesson_no is the stage's
   position within its unit, lesson_title is the unit's title.

Pipeline: extract_pages -> parse_contents (+ find_page_offset) ->
          unit_page_ranges -> unit_text (per unit) -> split_into_stages ->
          classify_stage -> chunk_words -> prepend header -> embed_texts ->
          ingest_book stores everything and updates books.status.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from app.models.chunk import ChunkType
from app.pipeline.embeddings import embed_texts

# --- 1. broken-font repair --------------------------------------------------

# Substitute codepoints the broken font uses instead of a plain -29 shift,
# each confirmed against >=2 independent words in the book. Verified examples
# (garbled -> real):
#   "VFLHQFH\x03¿FWLRQ" -> "science fiction"      (¿ = fi)
#   "EHDXWLIXO\x03ÀRZHUV" -> "beautiful flowers"  (À = fl)
#   "EX൵DOR" -> "buffalo"                          (൵ = ff)
#   "3RVW\x032൶FH" -> "Post Office"                (൶ = ffi)
#   "µJDQM¶" -> "'ganj'"                           (µ = left quote, ¶ = right quote)
#   "SKUDVH«RU" -> "phrase...or" (dot-leader/ellipsis substitute inside a
#   broken-font run; lower confidence, only 6 occurrences, all in filler
#   punctuation rather than lesson content)
_LIGATURE_MAP = {
    "¿": "fi",
    "À": "fl",
    "൵": "ff",
    "൶": "ffi",
    "µ": "‘",
    "¶": "’",
    "«": "…",
}

_SHIFT = 29


def _shift_char(c: str) -> str:
    if c == " ":
        # A handful of bold headings encode double word-spacing: one shifted
        # space (\x03, handled below) plus one already-correct literal space
        # sitting right next to it in the same span. A real space is never
        # itself corrupted — shifting it would turn it into "=" (0x20+29).
        return c
    if c in _LIGATURE_MAP:
        return _LIGATURE_MAP[c]
    o = ord(c)
    shifted = o + _SHIFT
    if 0x20 <= shifted <= 0x7E:
        return chr(shifted)
    # Unknown/out-of-band character inside a flagged span: leave it alone
    # rather than guess. looks_english() will flag the surrounding text for
    # manual review if this leaves a line looking wrong.
    return c


def repair_span(text: str) -> str:
    """Repair one PyMuPDF text span if it came from the broken font.

    Trigger: the span contains a control character (<0x20, excluding tab/
    newline). Real text in this book never contains one; a shifted space
    (0x20 - 29 = 0x03) always does for any span longer than one word. A
    span that is already correct (e.g. genuine ALL-CAPS text like
    "BIRTH-day" in the stressed-syllable exercises) never has a control
    character, so it is never touched.
    """
    if not any(ord(c) < 0x20 and c not in "\t\n" for c in text):
        return text
    return "".join(_shift_char(c) for c in text)


_COMMON_ENGLISH_WORDS = frozenset(
    """
    the a an is are was were be been being and or but if then so because
    to of in on at for with without by from up down out about into over
    under again further this that these those i you he she it we they
    me him her us them my your his its our their what which who whom
    have has had do does did not no yes can could will would shall should
    may might must let us go went come came see saw look read write
    say said tell told ask asked make made take took give gave get got
    know knew think thought like as very just also all any some more
    most other such only own same than too where when why how there
    here now today tomorrow yesterday school class book student teacher
    boy girl name day time water good bad happy new old
    """.split()
)


def looks_english(text: str) -> float:
    """Common-word hit ratio, used to flag chunks worth a manual look."""
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if not words:
        return 0.0
    hits = sum(1 for w in words if w in _COMMON_ENGLISH_WORDS)
    return hits / len(words)


# --- 2. page extraction -----------------------------------------------------


@dataclass
class PageText:
    page_no: int  # 1-indexed PDF page number
    text: str  # spans repaired, joined by line


def extract_pages(doc: fitz.Document) -> list[PageText]:
    pages = []
    for i in range(doc.page_count):
        page = doc[i]
        raw = page.get_text("dict")
        lines_out = []
        for block in raw["blocks"]:
            if block.get("type") != 0:  # skip images
                continue
            for line in block["lines"]:
                line_text = "".join(repair_span(span["text"]) for span in line["spans"])
                if line_text.strip():
                    lines_out.append(line_text)
        pages.append(PageText(page_no=i + 1, text="\n".join(lines_out)))
    return pages


# --- 3. table of contents -> unit page ranges -------------------------------


@dataclass
class UnitEntry:
    unit_no: int
    title: str
    printed_start_page: int


def parse_contents(pages: list[PageText]) -> list[UnitEntry]:
    """Read the Contents page into (unit_no, title, printed_start_page)."""
    toc_page = next((p for p in pages if p.text.strip().startswith("Contents")), None)
    if toc_page is None:
        raise ValueError("Could not find the Contents page (expected a page starting 'Contents')")

    lines = [line.strip() for line in toc_page.text.splitlines() if line.strip()]
    units: list[UnitEntry] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^Unit\s+(\d+)$", lines[i])
        if m:
            unit_no = int(m.group(1))
            title = lines[i + 1]
            j = i + 2
            while j < len(lines) and not lines[j].isdigit():
                j += 1
            if j >= len(lines):
                raise ValueError(f"Contents page: no page number found for Unit {unit_no}")
            units.append(UnitEntry(unit_no=unit_no, title=title, printed_start_page=int(lines[j])))
            i = j + 1
        else:
            i += 1

    if not units:
        raise ValueError("Contents page parsed but no units were found")
    return units


_FOOTER_UNIT_RE = re.compile(r"(\d+)\nUnit\n(\d+)")


def find_page_offset(pages: list[PageText], units: list[UnitEntry]) -> int:
    """PDF page number = printed page number + offset. Derived from the book's
    own running footer ("{printed page}\\nUnit\\n{unit no}"), not hardcoded,
    so a different print run of the same book (different front-matter length)
    still works."""
    first = units[0]
    for page in pages:
        m = _FOOTER_UNIT_RE.search(page.text)
        if m and int(m.group(1)) == first.printed_start_page and int(m.group(2)) == first.unit_no:
            return page.page_no - first.printed_start_page
    raise ValueError(
        f"Could not find the footer for Unit {first.unit_no} (printed page "
        f"{first.printed_start_page}) to determine the PDF/printed page offset."
    )


@dataclass
class UnitRange:
    unit_no: int
    title: str
    pdf_start: int  # 1-indexed, inclusive
    pdf_end: int  # 1-indexed, inclusive


def unit_page_ranges(units: list[UnitEntry], offset: int, total_pages: int) -> list[UnitRange]:
    ranges = []
    for idx, u in enumerate(units):
        pdf_start = u.printed_start_page + offset
        if idx + 1 < len(units):
            pdf_end = units[idx + 1].printed_start_page + offset - 1
        else:
            pdf_end = total_pages
        ranges.append(UnitRange(unit_no=u.unit_no, title=u.title, pdf_start=pdf_start, pdf_end=pdf_end))
    return ranges


def verify_unit_ranges(pages: list[PageText], ranges: list[UnitRange]) -> None:
    """Fail loudly if any page's own footer disagrees with the unit range we
    assigned it to — catches a Contents-parsing or offset bug immediately
    instead of silently mis-attributing chunks to the wrong lesson."""
    page_by_no = {p.page_no: p for p in pages}
    for rng in ranges:
        for page_no in range(rng.pdf_start, rng.pdf_end + 1):
            page = page_by_no.get(page_no)
            if page is None:
                continue
            m = _FOOTER_UNIT_RE.search(page.text)
            if m and int(m.group(2)) != rng.unit_no:
                raise ValueError(
                    f"Page {page_no}: footer says Unit {m.group(2)}, but it was "
                    f"assigned to Unit {rng.unit_no}'s page range. The page offset "
                    "or Contents parsing is wrong."
                )


# --- 4. per-unit text, stage splitting, classification ----------------------

def strip_page_furniture(text: str, unit_title: str | None = None) -> str:
    """Remove the '2026' watermark and the running header/footer: a literal
    "English for Today" line, a bare repeat of the unit's title, and a "Unit"
    line paired with the unit-number line next to it. Most pages put these at
    the end, in page-num/Unit/unit-num/title/"English for Today" order, but a
    few pages with unusual layouts (e.g. a picture-grid page) reorder or split
    them, so this walks line by line and drops the furniture lines wherever
    they land rather than anchoring one regex to the end of the page."""
    lines = text.split("\n")
    if lines and lines[0] == "2026":
        lines = lines[1:]

    cleaned = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line == "English for Today" or (unit_title is not None and line == unit_title):
            i += 1
            continue
        if line == "Unit" and i + 1 < len(lines) and lines[i + 1].strip().isdigit():
            i += 2
            continue
        if line.strip().isdigit() and i + 1 < len(lines) and lines[i + 1] == "Unit":
            i += 1
            continue
        cleaned.append(line)
        i += 1
    return "\n".join(cleaned).strip()


def unit_text(pages: list[PageText], rng: UnitRange) -> str:
    parts = []
    for p in pages:
        if rng.pdf_start <= p.page_no <= rng.pdf_end:
            cleaned = strip_page_furniture(p.text, unit_title=rng.title).strip()
            if cleaned:
                parts.append(cleaned)
    return "\n".join(parts)


# Matches a top-level activity marker like "1.1", "3.2" at the start of a
# line, but not a sub-item like "3.1.1" (negative lookahead on a further
# ".digit") — sub-items stay embedded in their parent stage's text.
_STAGE_RE = re.compile(r"^\s*(\d+)\.(\d+)(?!\.\d)\s", re.MULTILINE)


@dataclass
class Stage:
    unit_no: int
    # 1-based position within the unit. Usually the unit's title is the only
    # thing before the first numbered activity, and unit_text() already strips
    # title-repeat lines, so stage 1 is normally the first "N.N" activity
    # itself rather than a separate intro passage.
    stage_no: int
    text: str


def split_into_stages(unit_no: int, text: str) -> list[Stage]:
    matches = list(_STAGE_RE.finditer(text))
    stages: list[Stage] = []
    stage_no = 1

    intro_end = matches[0].start() if matches else len(text)
    intro = text[:intro_end].strip()
    if intro:
        stages.append(Stage(unit_no=unit_no, stage_no=stage_no, text=intro))
        stage_no += 1

    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.start() : end].strip()
        if body:
            stages.append(Stage(unit_no=unit_no, stage_no=stage_no, text=body))
            stage_no += 1

    return stages


_VOCAB_MARKERS = ("new words", "word meaning", "vocabulary")


def classify_stage(stage: Stage) -> ChunkType:
    """passage = the unit's intro reading/dialogue, and Language Focus stages
    (explanatory prose — the most reusable material for a tutor). exercise =
    everything else (fill-in-the-blank, matching, writing tasks). vocabulary =
    only if an explicit word-list marker is found; confirmed against every
    unit in the book, this edition has none, so it goes unused here.

    The marker check only looks at the stage's heading (its first line), not
    its full body: ordinary prose can legitimately contain a marker phrase
    (e.g. a "Language Focus: Prefix and Suffix" stage saying "...to make new
    words, prefixes or suffixes are added...") without being a vocabulary
    list, so matching the whole body produced a false positive during Phase 1
    validation.
    """
    heading = stage.text.split("\n", 1)[0].lower()
    if any(marker in heading for marker in _VOCAB_MARKERS):
        return ChunkType.VOCABULARY
    if stage.stage_no == 1 or "language focus" in heading:
        return ChunkType.PASSAGE
    return ChunkType.EXERCISE


# --- 5. word-count chunking --------------------------------------------------


def chunk_words(text: str, max_words: int = 600, overlap: int = 50) -> list[str]:
    # .split()/" ".join() also normalizes whitespace, which matters here: a
    # few bold headings double-space words (see _shift_char's space guard),
    # and this collapses that back to single spaces for every chunk.
    words = text.split()
    if not words:
        return []
    if len(words) <= max_words:
        return [" ".join(words)]

    chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - overlap
    return chunks


def chunk_header(unit_no: int, lesson_no: int, lesson_title: str) -> str:
    return f"Unit {unit_no}, Lesson {lesson_no}: {lesson_title}"


# --- 6. chunk drafts (embedding itself lives in pipeline/embeddings.py) -----


@dataclass
class ChunkDraft:
    """A chunk before embedding: everything build_chunks() can determine from
    the PDF alone, without calling the embedding model."""

    unit: int
    lesson_no: int
    lesson_id: str
    lesson_title: str
    page: int
    type: ChunkType
    text: str


@dataclass
class EmbeddedChunk:
    unit: int
    lesson_no: int
    lesson_id: str
    lesson_title: str
    page: int
    type: ChunkType
    text: str
    embedding: list[float]
    sparse: dict[str, float]


# --- 7. orchestration --------------------------------------------------


def build_chunks(pdf_path: Path) -> list[EmbeddedChunk]:
    """Pure (no DB) pipeline: PDF path in, embedded chunks out. Used by
    ingest_book() and directly by tests/a notebook without touching FastAPI."""
    doc = fitz.open(pdf_path)
    pages = extract_pages(doc)
    total_pages = doc.page_count
    doc.close()

    units = parse_contents(pages)
    offset = find_page_offset(pages, units)
    ranges = unit_page_ranges(units, offset, total_pages)
    verify_unit_ranges(pages, ranges)

    drafts: list[ChunkDraft] = []
    for rng in ranges:
        text = unit_text(pages, rng)
        for stage in split_into_stages(rng.unit_no, text):
            chunk_type = classify_stage(stage)
            lesson_id = f"u{rng.unit_no}-s{stage.stage_no}"
            header = chunk_header(rng.unit_no, stage.stage_no, rng.title)
            for piece in chunk_words(stage.text):
                drafts.append(
                    ChunkDraft(
                        unit=rng.unit_no,
                        lesson_no=stage.stage_no,
                        lesson_id=lesson_id,
                        lesson_title=rng.title,
                        page=rng.pdf_start,
                        type=chunk_type,
                        text=f"{header}\n{piece}",
                    )
                )

    if not drafts:
        raise ValueError("No chunks produced — check Contents parsing and stage splitting")

    dense, sparse = embed_texts([d.text for d in drafts])
    return [
        EmbeddedChunk(**vars(draft), embedding=dense_vec, sparse=sparse_weights)
        for draft, dense_vec, sparse_weights in zip(drafts, dense, sparse)
    ]


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def ingest_book(book_id: uuid.UUID, pdf_path: Path) -> None:
    """The only DB-touching function in this module. Builds chunks, stores
    them, and updates books.status/chunk_count — or marks the book failed."""
    import asyncio

    from app.core.db import AsyncSessionLocal
    from app.models.book import Book, BookStatus
    from app.models.chunk import Chunk

    async with AsyncSessionLocal() as session:
        book = await session.get(Book, book_id)
        if book is None:
            return
        try:
            # build_chunks() is entirely synchronous (PyMuPDF, regex, and the
            # embedding model's blocking .encode() call). FastAPI runs this
            # background task on the same event loop as request handling, so
            # without to_thread() this would freeze every other request —
            # including GET /books/{id} polling for status — until ingestion
            # finished.
            chunks = await asyncio.to_thread(build_chunks, pdf_path)
            for c in chunks:
                session.add(
                    Chunk(
                        book_id=book_id,
                        lesson_id=c.lesson_id,
                        unit=c.unit,
                        lesson_no=c.lesson_no,
                        lesson_title=c.lesson_title,
                        page=c.page,
                        type=c.type,
                        text=c.text,
                        embedding=c.embedding,
                        sparse=c.sparse,
                    )
                )
            book.chunk_count = len(chunks)
            book.status = BookStatus.READY
            await session.commit()
        except Exception:
            await session.rollback()
            book = await session.get(Book, book_id)
            if book is not None:
                book.status = BookStatus.FAILED
                await session.commit()
            raise
