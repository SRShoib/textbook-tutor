# Lab notebook

Append-only. One dated entry per working session. Never rewrite earlier entries.
This file becomes the Experiments chapter.

Format:
## YYYY-MM-DD — short title
- built:
- decided:
- numbers:
- next:

---

## 2026-09-11 — Phase 1: foundation, ingest.py, POST /books

- built:
  - docker-compose.yml (Postgres 16 + pgvector, one service, no app container)
  - backend/app/core/{config,db,errors}.py — Settings mirrors .env.example in full;
    async engine + get_db(); {"error":{"code","message"}} handler
  - backend/app/models/ — users, books, chunks, sessions, messages exactly per
    CLAUDE.md's schema section; UUID PKs (Python-side uuid4); native Postgres
    ENUMs for every role/status/type column via a shared str_enum() helper
  - backend/alembic/ — async template, hand-written 0001_initial (verified
    against the models with `alembic check` — zero drift)
  - backend/app/main.py — /health (real SELECT 1), /docs, books router
  - backend/app/pipeline/ingest.py — extract → repair broken font → parse
    Contents page → unit page ranges → split into stages → classify → chunk
    → embed (bge-m3, dense+sparse) → store
  - POST /api/v1/books (202 + background task, sha256 dedup) and
    GET /api/v1/books/{id} (status polling)
  - backend/tests/test_ingest.py — 21 tests on the pure functions, several
    built from byte sequences captured directly from the real PDF

- decided:
  - Broken-font repair: the PDF's bold/dialogue text extracts through a font
    with a corrupt ToUnicode CMap — every character is exactly ASCII-29 off,
    plus 6 ligature/quote substitutes (fi, fl, ff, ffi, left/right quote,
    ellipsis). Confirmed by sweeping every codepoint in all 118 pages.
    Chose a deterministic character remap over OCR (user approved) — no new
    dependency, byte-exact, and the trigger (a control character in the span)
    naturally never touches genuinely correct text.
  - Lesson unit: this book has no separate "Lesson" numbering, only 20 Units
    each split into numbered stages (1.1, 2.1, 3.1, ...). User approved
    stage = lesson (not unit = lesson): lesson_no is the stage's position in
    its unit, lesson_title is the unit's title. ~135 lessons vs. ~20 if unit
    had been the lesson.
  - Page offset (PDF page = printed page + 6) is derived from the book's own
    running footer at ingest time, not hardcoded, and every page's footer is
    cross-checked against its assigned unit — would raise loudly on mismatch.

- numbers:
  - 118 PDF pages, 20 units, 135 stages/lessons/chunks total
  - 41 passage, 94 exercise, 0 vocabulary (this edition has no explicit
    word-list section in any of the 20 units — confirmed book-wide, not just
    sampled)
  - 0 chunks needed the 600-word/50-word-overlap split (every stage is
    naturally shorter) — CLAUDE.md's "~300 chunks" estimate was for a
    different granularity; 135 is what this book actually produces
  - embedding: bge-m3, dense dim 1024, sparse term counts ranged ~28-155 per
    chunk in the sample checked
  - live end-to-end run (upload real PDF through POST /books → GET ready):
    chunk_count 135, matches the offline validation exactly

- bugs found and fixed during validation (real PDF, not synthetic tests):
  - A few bold headings double-space words: one shifted space (\x03) plus an
    already-correct literal space in the same span. The repair was blindly
    shifting every character in a flagged span, turning that literal space
    into "=" (0x20+29). Fix: never re-shift a literal space.
  - Footer element order isn't constant — a picture-grid-layout page (Unit 20,
    "Work in groups") puts title/"English for Today" before the page-num/
    Unit/unit-num block instead of after. Rewrote strip_page_furniture() to
    walk line-by-line and drop furniture wherever it lands, instead of one
    end-anchored regex.
  - classify_stage()'s vocabulary-marker check ("new words") false-matched a
    Language Focus stage about word formation ("...to make new words,
    prefixes or suffixes..."). Fixed to check only the stage's heading line.
  - SQLAlchemy's Enum binds a Python enum member's .name ("PROCESSING") by
    default, not .value ("processing") — mismatched the lowercase Postgres
    enum labels from the migration and broke every insert. Fixed via a
    values_callable helper (str_enum) used on all five enum columns.
  - ingest_book() called the fully synchronous build_chunks() (PyMuPDF +
    bge-m3's blocking .encode()) directly inside an async background task,
    freezing FastAPI's event loop — including the GET /books/{id} polling
    endpoint itself — for the whole ~3 minutes of first-run model download.
    Fixed with asyncio.to_thread().

- follow-up verification (same day): re-ran docker compose up / alembic
  upgrade head / upload from a cold check — all idempotent as expected
  (upload hit the sha256 dedup path and returned the existing ready book
  instead of re-ingesting). Eyeballed one lesson per unit across all 20
  units plus the full lesson list for units 1-3. One thing worth knowing:
  Unit 14 ("The Champion Girl") starts its numbered activities at "1.2", not
  "1.1" — checked the raw PDF page directly (page 80), and the book itself
  has no "1.1" there, not even in an image block. Not a pipeline bug.

- next: Phase 2 — pipeline/retrieve.py (hybrid dense+sparse, RRF, parent-lesson
  expansion), pipeline/llm.py (disk-cached call), generate.py stage 1,
  graph.py (2 nodes), eval/runner.py. Read the 20 sample chunks above by eye
  before starting — lesson boundaries looked correct on this pass but this is
  the moment to catch it if not.
