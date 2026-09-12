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

---

## 2026-09-11 — Phase 2: bare pipeline (retrieve -> stage 1) + eval runner

- built:
  - backend/app/pipeline/embeddings.py — bge-m3 singleton extracted out of
    ingest.py so retrieve.py can share it without importing the PDF parser
  - backend/app/pipeline/llm.py — call_llm(), the one function every LLM call
    in the project goes through; disk cache keyed on
    (prompt_version, model, provider, system, prompt, temperature,
    max_tokens); get_stats()/reset_stats() for cost visibility. Same OpenAI
    SDK client hits either OpenAI or Ollama's OpenAI-compatible /v1 endpoint
    depending on `provider`.
  - backend/prompts/v1_stage1.txt + backend/app/pipeline/generate.py —
    stage 1 factual answer, grade kept out of the prompt entirely on purpose
  - backend/app/pipeline/retrieve.py — hybrid_search(): brute-force dense
    (cosine) + sparse (lexical dict dot product) scoring over the whole book
    in Python (pgvector 0.3.6 has no sparse type, so this can't move into
    SQL), reciprocal rank fusion (k=60), parent-lesson expansion
  - backend/app/pipeline/graph.py — two-node LangGraph (retrieve -> generate)
    with route_after_retrieve() as the off-book conditional edge;
    run_pipeline() is the one entry point the API and eval runner both call
  - backend/app/core/auth.py — get_current_user_id()/get_or_create_dev_user(),
    ALLOW_ANONYMOUS branch only (real JWT is Phase 5)
  - backend/app/api/sessions.py — POST /sessions (minimal, pulled forward
    from Phase 5) and POST /sessions/{id}/messages, registered in main.py
  - backend/app/schemas/{session,message}.py
  - backend/eval/metrics.py — retrieval_hit_rate(), JSONL read/write,
    ragas_faithfulness() with CachedRagasLLM routing RAGAS's own LLM calls
    through call_llm() so an eval re-run hits the cache
  - backend/eval/runner.py — CLI: questions.jsonl + book_id + config_version
    in, pipeline runs, messages table + timestamped JSONL out, prints status
    breakdown / hit rate / faithfulness / cache stats / wall time
  - data/question_set/questions.jsonl — 5 real dev-split questions with
    expected_lesson_id, drawn by hand from the actual ingested book (not
    invented) — scaffolding for the real 150-question set in Phase 6
  - 73 tests total (52 new), all pure functions per CLAUDE.md's "test
    pipeline/, skip route tests" — LLM/DB calls monkeypatched out everywhere

- decided (judgment calls, flagged during planning and confirmed unchanged
  during implementation):
  - Off-book gate compares the single highest dense cosine similarity in the
    whole book, not the RRF-fused score — RRF's score has no 0-1 meaning
    (tops out ~0.033), OFFBOOK_SCORE_THRESHOLD=0.35 only means something on
    a similarity scale.
  - Scoring is brute-force Python over all ~135 chunks, not a pgvector index
    — documented as the point to revisit if the corpus grows past a few
    thousand chunks.
  - messages.sources is a JSON list of citation objects, not a dict — the
    Phase 1 model.py type hint (dict | None) was written before this shape
    was decided; updated to (list | None) rather than wrapping a list in an
    artificial dict key.
  - graph.py uses the real MessageStatus enum (not raw strings) for
    GraphState/PipelineResult.status, so a typo can't silently reach the DB.
  - Pulled POST /sessions (create-only) forward from Phase 5 with the user's
    sign-off — otherwise nothing could create a session for /messages to
    attach to. List/rename/soft-delete still wait for Phase 5.

- numbers:
  - Real retrieval sanity check against the live ingested book (135 chunks):
    "What kind of books does Rina like to read?" -> top hit u1-s2 (the
    actual lesson with the answer), best_score 0.587. "Who is the president
    of France?" -> best_score 0.338, just under the 0.35 threshold -> would
    refuse. The threshold is untuned; this is a first sanity check, not a
    calibration.
  - Full off-book round trip verified through the real FastAPI app + real
    Postgres (TestClient, no OpenAI key needed since that branch never calls
    an LLM): POST /sessions -> POST /messages -> 201, status
    refused_off_book, sources populated, verification/readability null,
    config_version "v1" logged, message rows correct in the DB. Cleaned up
    the test session/messages afterward.

- bugs found and fixed during this session:
  - generate_node was calling generate_stage1() (a blocking call_llm()
    underneath) directly inside an async LangGraph node — same class of bug
    as Phase 1's ingest_book/build_chunks issue. Fixed with
    asyncio.to_thread(), before it ever shipped.
  - ragas==0.4.3 unconditionally imports
    langchain_community.chat_models.vertexai at import time; that module no
    longer exists in current langchain-community (Vertex AI support split
    into a separate, uninstalled package). Confirmed there's no clean pin
    fix: an old-enough langchain-community to keep that module forces
    langchain-core back below what langgraph==1.2.11 requires (tried it,
    pip flagged 6 conflicts). Fixed with a tiny stub module registered in
    sys.modules before `import ragas`, documented in eval/metrics.py's
    docstring. Also discovered ragas 0.4.x replaced the LangChain-wrapper
    Faithfulness API I'd planned around with an `instructor`-style
    structured-output contract (generate(prompt, response_model) ->
    pydantic model) — turned out to need no extra prompt engineering, since
    ragas's own PydanticPrompt already embeds full JSON-schema instructions
    and few-shot examples into the prompt string it hands the LLM (verified
    by rendering one directly); the adapter just forwards to call_llm() and
    validates the JSON that comes back.

- known issue, not fixed (out of this module's scope): the installed
  fastapi is 0.141.1 / starlette 1.6.0, not the 0.115.6 pinned in
  requirements.txt since Phase 1 — real drift between the pin and what's
  actually running. Everything still works (verified via TestClient +
  openapi.json), but a fresh `pip install -r requirements.txt` today would
  install a different, older FastAPI. Worth a deliberate pin refresh at some
  point; didn't touch it unasked since it's Phase-1-scoped, not Phase 2.

- not yet run: the in-book "answered" path, the cache-hit-on-repeat check,
  and eval/runner.py itself all need a real OPENAI_API_KEY + OPENAI_MODEL in
  .env (no .env exists yet in this repo). Didn't fabricate one or ask for
  the key in chat — see the wrap-up message for exact commands to run
  locally once .env is filled in.

- next: fill in .env and run the three checks above; then Phase 3 — style
  guide loaded, stage 2 (grade voice), style_check.py, rewrite.py, session
  history.
