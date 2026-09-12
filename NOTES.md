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

---

## 2026-09-12 — 30-question baseline eval + Phase 0 style guide completed

- built:
  - data/question_set/questions.jsonl rewritten: 30 questions (25 in-book
    across all 20 units, 5 off-book), schema changed to
    id/question/lesson_id/reference_answer/type/split per user request;
    eval/runner.py updated to read `lesson_id` (was `expected_lesson_id`) and
    pass `reference_answer`/`type` through into the output JSONL
  - Ran `eval/runner.py --config baseline` for real against the live book and
    a real OpenAI key (gpt-4o-mini): 87 LLM calls, 0 cache hits (first run),
    94.6k prompt + 5.8k completion tokens, 77.3s wall time
  - Filled in Phase 0's style guide for real: read all 12 Ghore Boshe Shikhi
    transcripts and the Teacher's Guide PDF (pp. 6-8, 13-14, 144 — rendered to
    PNG and read visually, since the PDF has no text layer, likely
    Illustrator-outlined text, same broken-font family of problem as Phase
    1's textbook PDF but worse: zero extractable text, zero embedded raster
    images). Filled style_guide.md §1, §3 (all 5 structural-rule subsections),
    §4, §5, §6 (grade 5 row), §8; replaced all 5 synthetic rows in
    fewshot_examples.jsonl with real ones.

- decided:
  - Held out 4 of 12 transcripts (V-1 `JV5LTEnfpIc`, V-5 `Ip7CyD1jdT8`, V-11
    `l476VH3iBNo`, V-12 `bBESclfJN8o`), untouched, as the human reference for
    Phase 6 eval, per CLAUDE.md. Caught and fixed a real mistake here during
    drafting: my first pass at style_guide.md cited several of these
    held-out transcripts directly as evidence for the rules, which would
    have quietly invalidated the whole point of holding them out (comparing
    the bot against something its own rules weren't shaped by). Re-sourced
    every affected citation from the remaining 8 transcripts before writing
    the final version, and swapped one holdout pick (V-4, the only phonics
    lesson in the batch and needed as evidence) for V-12 (a third recording
    of the same "Shoykot's family" lesson already covered by two other
    non-held-out transcripts, so losing it costs nothing).
  - Teacher interviews, which the style_guide.md skeleton originally
    targeted (3 interviews), are dropped entirely — CLAUDE.md's Phase 0
    section restricts sources to exactly three types and explicitly excludes
    interviews. None were conducted; the requirement itself was stale, not
    unfinished.
  - Removed the "0.0% Bangla insertion rate" measurement from being treated
    as a finding — it's a measurement artifact. Whisper transliterated all
    Bangla speech into Latin letters (romanized), so a Bangla-Unicode-script
    detector reads ~0% even though Bangla is extensively present. Replaced
    with a qualitative pattern instead (§3.4): Bangla usage scales with
    content abstractness — light (question/instruction restatement only) in
    narrative lessons, heavy (near sentence-for-sentence paraphrase) in the
    one phonics lesson sampled.

- numbers:
  - Baseline eval (30 questions, gpt-4o-mini, threshold 0.35): 29 answered /
    1 refused_off_book. Retrieval hit rate 0.96 (24/25 labelled questions).
    RAGAS faithfulness 0.90 (n=29, but see caveat below).
  - Real finding, not yet acted on: only 1 of 5 off-book questions correctly
    triggered refused_off_book — the other 4 scored just above the 0.35
    off-book threshold on dense similarity to *some* chunk despite being
    pure trivia. Stage 1 itself behaved correctly in all 4 (answered
    honestly that the book doesn't cover it, no hallucination), but the
    message `status` still reads "answered", which is the wrong label for
    what happened. This is exactly the gap Phase 4's verifier should close;
    until then `status=answered` is not proof the book supported the answer.
  - Related caveat: the 0.90 faithfulness average is partly inflated by
    those same 4 near-miss off-book rows — a response that mostly declines
    to answer makes few checkable claims, so it scores as trivially
    faithful. Don't read 0.90 as "90% of answers are good"; it conflates
    genuine faithfulness with evasive non-answers.
  - Style guide: 8 of 12 transcripts (15,027 words total across all 12)
    supplied every citation in the final style_guide.md; 4 held out clean.

- follow-up same day: checked the actual PROMPTS.md Phase 0 script against
  what was done and found two real gaps — the Teacher's Guide instruction
  says "for 5 lessons," and only 1.5 had been read (Unit 1 in full, plus one
  page of Unit 14). Closed this by reading the Introduction + Review of
  prior knowledge sections of 4 more lessons across the book (Units 5, 8, 12,
  17). Result: the fixed greeting is now confirmed identical across 6 units
  spanning nearly the whole book, not 2 — strong evidence, not a
  coincidence. Also found a refinement worth keeping: "Review of the prior
  knowledge" is usually topic-*anticipation* for the lesson about to start,
  not a recap of the previous one (only Units 1 and 14 tie it to earlier
  content). And a stronger, session-script-level citation for the
  English-usage rule turned up in Unit 12: "[Encourage Ss to respond in
  English.]" All folded into style_guide.md as v1.1.
  The second gap — PROMPTS.md has the user picking the 5 few-shot examples
  from candidates and hand-fixing their transcription errors before they go
  in — was named but not closed; that checkpoint is deliberately the user's,
  not mine, and is still open.

- git: repo was git-init'd and Phase 1 + Phase 2 are now real commits
  (`755f03f`, `6d95601` on a `phase-2-pipeline` branch off `main`) — gave the
  user branch/push/merge commands rather than running git myself, per
  CLAUDE.md.

- next: merge phase-2-pipeline into main (user's call on timing); use the
  eval findings above to motivate Phase 4's verifier scope; then Phase 3
  proper — stage 2 (grade voice) built from this style guide, style_check.py,
  rewrite.py, session history. The off-book threshold (0.35) is still
  untuned — Phase 6 tunes it on the dev split, not before.

---

## 2026-09-12 — Phase 3 step A: generate.py stage 2 (grade voice)

- built:
  - `backend/prompts/v1_stage2.txt` — new prompt, placeholders `{grade}`,
    `{style_rules}`, `{grade_profile}`, `{fewshot}`, `{feedback}`,
    `{stage1_answer}`, `{question}`; plain `str.format`, no literal braces.
  - `generate.py`: `load_style_rules()` slices style_guide.md sections 3/4/5
    by their `## N.` headings and strips section 3's `Evidence:` quote
    blocks (and unlabelled ones in §3.4) — keeps every prose rule, drops the
    quoted transcript excerpts, which are provenance for the thesis, not
    instructions for the model. `load_grade_profile(grade)` reads the §6
    table row, empty for grades 3/8 (only 5 is filled). `load_fewshot(grade)`
    reads `fewshot_examples.jsonl`, drops `provenance: synthetic` rows when
    `EVAL_MODE=1`, raises if that empties the list, falls back to all rows
    when none match the requested grade. `render_stage2_prompt()` and
    `generate_stage2()` wire it together; `STAGE2_PROMPT_VERSION = "v1_stage2"`
    gets its own `.llm_cache/` subfolder automatically.
  - 15 new tests in `test_generate.py` (19 total in that file, 86 total in
    the suite, all green).

- decided:
  - Stage 2 receives only `(question, stage1_answer)`, never
    `context_chunks` — a structural guarantee, not just a prompt
    instruction, that it cannot introduce a book fact stage 1 didn't
    already produce.
  - Few-shot examples are rendered as `(question, teacher_answer)` only —
    `textbook_passage` is dropped. Checked one row concretely:
    `fs_v1_comprehension`'s `teacher_answer` names "Andy Smith", who is not
    in that row's own `textbook_passage` (the Ghore Boshe Shikhi videos use
    an older textbook edition than the ingested book — style_guide.md §1
    already flags this edition mismatch). Showing the passage next to that
    answer would model exactly the fact-invention stage 2 is forbidden from
    doing.
  - Bangla in stage 2 output: one question restatement in Bangla **script**
    allowed for narrative/comprehension questions (style_guide.md §3.4's
    strongest finding), answer body stays English, and the prompt explicitly
    forbids the transcripts' Whisper-romanised Latin spelling. Chose this
    over "English only" (leaves the strongest style-guide finding
    unencoded) and over "scale Bangla by question type" (needs stage 2 to
    classify question type — another moving part to defend, for a
    difficulty-scaling claim §3.4 itself flags as based on only 12
    transcripts, all narrative lessons being fairly easy reads).
  - Section 3's intro sentence ("Fill each slot with what you actually
    observe...") is dropped from the loaded rules — it instructs whoever
    edits style_guide.md, not the model answering a student. Confirmed by
    test.

- numbers:
  - `load_style_rules()` output: 7,305 characters after stripping (sections
    3+4+5), evidence-quote-free, confirmed no leaked source IDs
    (`PBxbCgjFyQ8` etc.) or Bangla-romanised quotes.
  - Grade 5 profile string includes the measured `14 words` sentence cap and
    `3.0–4.5` FK target from §2; grades 3 and 8 correctly return `""`.
  - A rendered stage-2 prompt (grade 5, no feedback) is ~9,500 characters —
    worth watching against context/cost budgets once real eval runs start
    (5 few-shot examples + the full §3-5 rule text on every call).

- open item carried over, not closed here: PROMPTS.md's Phase 0 checkpoint
  — the user personally picking/hand-fixing the 5 few-shot rows — is still
  outstanding. Stage 2 is now built on top of the current 5 rows; flagging
  again since this is the first module that actually consumes them.

- next: Phase 3 step B — `style_check.py` (sentence length, vocab coverage,
  Flesch-Kincaid, LLM judge, retry loop), per the approved 3-step plan.

---

## 2026-09-12 — Phase 3 step B: style_check.py

- built:
  - `backend/app/pipeline/style_check.py` — `check_style()` runs, in order:
    sentence-length (gate on max, report mean), vocabulary coverage against
    the book's own `chunks.text` (header line dropped) unioned with the 5
    real few-shot `teacher_answer`s, Flesch-Kincaid on the Bangla-stripped
    text, and an LLM judge returning `{suitable, reason}` JSON — the judge
    only runs if the three numeric checks already passed. Word/sentence
    splitting is a documented copy of `tools/style_guide/measure_style.py`'s
    regexes (not an import — `tools/` isn't an importable package under
    `backend/`), checked against the original by a test so the two can't
    silently drift apart.
  - `backend/prompts/v1_style_judge.txt` — new prompt, doubled `{{`/`}}` in
    its JSON example so `str.format` doesn't choke on the literal braces.
  - `call_llm()` in `llm.py` gained an optional `model` override (already in
    the cache key, so cache correctness is unchanged) — the judge passes
    `settings.openai_judge_model` through it so an answer is never graded
    by the model that wrote it. Falls back to the main model if unset.
  - Config: `style_max_sentence_words=14`, `style_fk_min=3.0`,
    `style_fk_max=4.5`, `style_vocab_coverage_min=0.9` — a test asserts
    these still match style_guide.md section 2's measured numbers.
  - `textstat` pinned to `0.7.13` in requirements.txt — matched to the
    version already installed ad hoc (per README.md) when
    `measure_style.py` produced section 2's numbers; I initially pinned
    0.7.4 without checking and corrected it before committing, since a
    different textstat version can score FK differently and would have
    silently invalidated the measured 3.0-4.5 target.
  - 20 new tests in `test_style_check.py`, 2 more in `test_llm.py` for the
    `model` override (108 total in the suite, all green).

- decided:
  - The judge runs only after the numeric checks pass — a failing sentence
    length or vocab check is free and already reason enough to regenerate,
    so gating the paid judge call behind them roughly halves judge calls
    across a 150-question x 5-config eval.
  - A still-failing report after retries does not block the answer or
    change its status (confirmed with the user before planning this step:
    "answered + failed report", not a new/repurposed status) — the report
    is stored either way so error analysis can count it.

- numbers: sanity-checked by hand, not just by test — grade-5 config values
  (14 words, FK 3.0-4.5) match style_guide.md section 2 verbatim; a fixture
  sentence pair correctly reported max=7/mean=6.5 words; Bangla-script text
  is stripped from vocab/FK scoring while Whisper-romanised Bangla (Latin
  letters) is deliberately NOT stripped — same artifact style_guide.md
  documents, and exactly why stage 2's prompt requires Bangla script only.

- next: Phase 3 step C — `rewrite.py`, graph rewiring, session history.

---

## 2026-09-12 — Phase 3 step C: rewrite.py + graph rewiring + verified live

- built:
  - `backend/app/pipeline/rewrite.py` — `needs_rewrite()` (pure heuristic,
    biased toward rewriting: empty history never rewrites, otherwise
    triggers on <5 words, a pronoun/deictic, or a continuation opener like
    "give me more"/"what about"), `load_history()` (last
    `history_max_messages=8` messages, its own DB session, same shape as
    `retrieve.hybrid_search`), `rewrite_question()`.
  - `backend/prompts/v1_rewrite.txt` — new prompt.
  - `graph.py` rewired end to end:
    `rewrite -> retrieve -> gate -> stage1 -> stage2 -> style_check`, with a
    conditional edge back to `stage2` (feeding `report.failures` back as
    `feedback`) until it passes or `style_max_retries` is exhausted. Fixed a
    genuine off-by-one while writing the tests: `style_max_retries=2` now
    correctly means 2 retries *after* the first attempt (3 total), not 2
    attempts total — caught because a test's own comment ("1 initial + 2
    retries = 3") didn't match what the code did.
  - `api/sessions.py` now passes `session_id` into `run_pipeline` and stores
    `asdict(result.style)` into the previously-always-null
    `messages.readability` column (no migration needed — it already
    existed, nullable, unused). The rewritten standalone query is not a DB
    column (schema is fixed) — it rides only on `PipelineResult`, the POST
    `/messages` response (`MessageRead.search_query`, not from the ORM row),
    and each eval JSONL record, per the option discussed with the user
    before planning this step.
  - `eval/runner.py` + `eval/metrics.py`: manifest's `prompt_versions` now
    includes stage2/rewrite/style_judge, `thresholds` includes all four
    style numbers; `metrics.style_summary()` reports pass rate, mean FK,
    mean max-sentence-words, mean vocab coverage over answered rows — the
    project guidelines' "Simplicity" results row.
  - 10 new tests in `test_rewrite.py`; `test_graph.py` rewritten (13 tests,
    was 6) to mock the full chain end to end, including the retry loop and
    exhaustion path; 3 new tests in `test_eval_metrics.py` for
    `style_summary`. 128 total in the suite, all green.

- decided: (bundled with step A's Bangla/status/storage decisions, made with
  the user before planning — see that entry above)

- verified live, per the user's own request — not just unit tests: started
  the API against the already-ingested Class 5 book
  (`97a7267b-...-4be4355e1a4f`, 135 chunks) with `ALLOW_ANONYMOUS=true`,
  created a session, and asked two real questions through
  `POST /sessions/{id}/messages`:
  - Q1 "What is a noun?" (40.8s, 3 stage-2 attempts) -> answered in a
    teacher voice with a real textbook example (school garden vocabulary,
    Unit 2), but style_check still failed after exhausting retries: one
    15-word sentence over the 14-word cap. Status stayed `answered` with
    the failing report attached, exactly as designed.
  - Q2 "give me more examples" (9.4s) -> rewrite.py correctly resolved it to
    the standalone query "Can you provide additional examples of common
    nouns?" using Q1's history; retrieval pulled the same noun-vocabulary
    lesson; style_check again failed (30-word sentence, FK 6.17) and again
    exhausted retries without blocking the answer.
  - Real finding, not yet acted on: across both live turns, style_check
    never once passed cleanly within 3 attempts — stage 2 keeps writing one
    sentence over the 14-word cap even after being told the exact reason on
    retry. The judge never got to run either time (`judge_ran: false`),
    since the numeric checks never cleared. Worth watching once the eval
    set runs at scale — if this is the norm rather than the exception, 14
    words (a raw, unpunctuated Whisper p95) or the retry feedback wording
    may need revisiting before Phase 6 tunes anything formally.

- next: Phase 6's eval config should include a style-focused run once the
  test set exists, to see whether the live-run pattern above (retries
  exhausting without a clean pass) is typical or a fluke of these two
  questions. Phase 4 (verify.py) is next in build order regardless.

---

## 2026-09-12 — Phase 3 fix: stage 2 was re-opening every turn with a song

- found: the user caught this reading the step-C live-verification output —
  Q2 ("give me more examples", a follow-up in the same session) still
  opened with "Let's sing a little song together before we start our
  lesson," identical to Q1's opening. Root cause: `load_style_rules()`
  (step A) pulled in style_guide.md §3.1 "Opening move" unconditionally on
  every stage-2 call. §3.1 documents how a teacher opens a whole LESSON
  VIDEO once — greeting, then usually a song, then naming class/unit/
  lesson/page — evidenced from 12 transcripts + 6 confirmed identical
  Teacher's Guide scripts. Applying that per chatbot turn was a scoping
  mistake made in step A: correct evidence, wrong unit of application.

- decided (with the user, before touching code — this was a judgment call
  with three real options: drop §3.1 entirely, keep it every turn but
  greeting-only, or scope it to first-turn-only): first-turn-only, greeting
  + lesson framing (class/unit/lesson/page), no song ever, on any turn.
  Implementation:
  - `generate.load_style_rules()` gained `include_opening_move: bool` —
    drops the `### 3.1` block (evidence in style_guide.md is left
    untouched; this is an application-layer scoping decision, documented in
    generate.py's module docstring, not a correction to the finding).
  - `render_stage2_prompt()` / `generate_stage2()` gained `is_first_turn`
    (default `True`), selecting one of two `{turn_note}` strings and
    whether `include_opening_move` is set. A standing "never include a
    song, on any turn" instruction was added to v1_stage2.txt itself,
    unconditionally — belt-and-suspenders since the song is forbidden
    regardless of turn, not just omitted via the dropped rule text.
  - `graph.py`: `rewrite_node` now also returns `is_first_turn` — `True`
    when `session_id` is `None` (eval runner: every question is
    independent, so treated as a first turn) or when `load_history()`
    returned nothing (a session's actual first message); `False` whenever
    real prior history exists. `stage2_node` threads it through.
  - 3 new tests in `test_generate.py` targeting `load_style_rules`
    directly, 4 more on `render_stage2_prompt`/`generate_stage2`, plus 2 new
    end-to-end `test_graph.py` cases asserting `is_first_turn` is `False`
    for a real follow-up and `True` for a session's first message. 135
    total in the suite, all green (was 128).

- verified live again after the fix, same book/session pattern as before:
  Q1 no longer forces a greeting (the instruction is a soft "begin with a
  brief one-time greeting," and the model chose to skip straight to the
  answer this run — acceptable, since the hard requirement was only "never
  a song," not "always greet"); Q2 confirmed clean — no greeting, no
  lesson-framing, no song, just the answer. The pre-existing style_check
  finding from the prior entry still holds unchanged (both turns still
  exceed the 14-word sentence cap and exhaust retries) — unrelated to this
  fix, still flagged as a Phase 6 follow-up, not addressed here.

- next: same as previous entry — Phase 4 (verify.py) per build order.
  Nothing in this session is committed yet; proposed commit messages for
  steps A/B/C (plus this fix, likely folded into step C's commit since it
  was found while verifying step C) are pending the user's decision on
  whether to keep them separate or squash into one Phase 3 commit.

---

## 2026-09-12 — Phase 4: verify.py (NLI-based hallucination gate)

- built:
  - `backend/app/pipeline/verify.py` — `verify_answer()` splits the final
    (stage-2) answer into sentences (reusing `style_check.sentences()`, so
    the two checks never disagree on sentence boundaries), builds every
    (chunk, sentence) pair against `context_chunks` (the same post-expansion
    lesson text stage 1 read), scores them all in one batched
    `CrossEncoder.predict(..., apply_softmax=True)` call, and keeps the max
    entailment probability per sentence. `get_nli_model()` is a lazy
    singleton for `cross-encoder/nli-deberta-v3-base`, same shape as
    `embeddings.get_embedding_model()`. `VerificationReport` (passed,
    supported_ratio, per-sentence `SentenceVerification`) is the JSON stored
    in `messages.verification`.
  - `graph.py`: new `verify_node` runs after `style_check`, with a
    conditional edge — supported → answered, partial → regenerate stage 2
    (feeding back exactly which sentence(s) the book doesn't support) up to
    `verify_max_retries` times, then `refused_unverified`. The retry rejoins
    the existing `stage2 -> style_check` loop rather than a separate bypass
    path, so a verify-triggered rewrite also gets re-style-checked before
    it's shown. Refactored `stage2_node` to read `state["retry_feedback"]`
    instead of deriving it from `state["style"]` inline — both
    `style_check_node` and `verify_node` now write that field every time
    they run (`None` on a pass), so a retry always carries the *current*
    loop's failure reason, never a stale one from the other check.
  - `config.py`: new `verify_max_retries=1` (thresholds
    `verify_entailment_threshold=0.5`/`verify_supported_ratio=0.8` already
    existed from earlier setup, unused until now).
  - `requirements.txt`: `sentence-transformers==6.0.1` pinned explicitly —
    was already present transitively via `FlagEmbedding`, but `verify.py`
    now imports it directly.
  - `api/sessions.py` / `schemas/message.py`: `messages.verification` is no
    longer hardcoded `None` — populated from `asdict(result.verification)`.
  - 18 new tests: `test_verify.py` (11, NLI model monkeypatched via
    `get_nli_model` — never loads the real DeBERTa model) and 7 new/rewritten
    cases in `test_graph.py` covering `route_after_verify` and full
    supported / verify-retry-then-pass / verify-retries-exhausted paths
    end to end. All pre-existing end-to-end `test_graph.py` cases now also
    monkeypatch `verify_answer` (the graph reaches it unconditionally after
    style_check). 164 total in the suite, all green.

- decided (four judgment calls, presented to the user before implementing,
  approved as proposed):
  - NLI premise is the whole chunk text, not chunk text re-split into
    sentences — matches CLAUDE.md's "against retrieved chunks" wording and
    stays simple for a ~135-chunk book. Known limitation, not handled: a
    chunk over nli-deberta-v3-base's ~512-token window can be silently
    truncated by the tokenizer; documented in verify.py's module docstring
    rather than worked around.
  - Verify's retry goes back through `stage2 -> style_check -> verify`
    (full loop), not a bare `stage2 -> verify` bypass — a regenerated
    answer that fixes an unsupported sentence could just as easily come out
    too long or too hard for the grade, so it should be re-style-checked
    before it's shown. Costs more possible LLM calls in the worst case
    (bounded at `(style_max_retries+1) * (verify_max_retries+1)` stage 2
    calls: 3*2=6 today), traded for not skipping a real quality gate.
  - `refused_unverified` keeps the actual (ungrounded) generated answer text
    on the message rather than a canned refusal string — unlike the
    off-book gate, this fires after generation, and the hallucinated text
    itself is exactly what Phase 6's error analysis needs to see. Only
    `status` marks it as refused.
  - `verify_max_retries` is a new config field (parity with
    `style_max_retries`) rather than hardcoding "regenerate once" as a
    constant, so eval configs can vary it without a code change.

- numbers: `sentence-transformers` (6.0.1) and `transformers` (5.17.0) were
  already installed transitively via `FlagEmbedding`, confirmed by checking
  the project's `.venv` directly — no new install needed, just an explicit
  pin. The real `cross-encoder/nli-deberta-v3-base` weights are not yet
  downloaded/exercised (all 18 new tests monkeypatch the model); first real
  run against the live book is still pending, same as Phase 3's live
  verification step.

- not yet done: no live end-to-end run against the real ingested book with
  a real NLI model download — all verification so far is at the unit/mocked
  level. Also haven't sanity-checked the assumed
  `['contradiction', 'entailment', 'neutral']` label order for
  `cross-encoder/nli-deberta-v3-base` against the actual downloaded model
  (asserted from the model family's documented convention, not derived from
  the model config) — worth a one-off manual check the first time this runs
  for real, before trusting eval numbers built on it.

- next: run this live against the real book/session (same pattern as
  Phase 3's live check) to (a) confirm the label-order assumption above and
  (b) see whether the two real hallucination-control questions from the
  Phase 2 baseline eval (the 4 off-book questions that scored just above
  0.35 and got "answered" instead of refused) now correctly get caught by
  verify.py. Then Phase 5: SSE, sessions CRUD, JWT auth, `POST /evaluate`.

---

## 2026-09-12 — Phase 4 live check: verify.py over-rejects genuinely correct answers

- ran (ad hoc, per the user's request — not a committed script): 10
  answerable questions (q01-q10) + all 5 off-book questions (q26-q30) from
  `data/question_set/questions.jsonl` through the real `run_pipeline()`
  against the live book (`97a7267b-...-4be4355e1a4f`, gpt-4o-mini,
  `cross-encoder/nli-deberta-v3-base` downloaded for real this time, on
  GPU). Docker Postgres had to be started fresh this session
  (`docker compose up -d` + `alembic upgrade head`, which was a no-op — same
  data volume, book still `ready`/135 chunks from Phase 1).

- numbers:
  - Status breakdown: 1 `answered` (q01), 13 `refused_unverified`, 1
    `refused_off_book` (q29, the one off-book question that actually cleared
    the 0.35 gate threshold in the wrong direction, i.e. scored below it).
  - This includes **9 of the 10 genuinely answerable, in-book questions**
    (q02-q10) — not just the off-book ones. That's the headline problem:
    verify.py is rejecting correct answers, not just catching hallucinations.
  - The other 4 off-book questions (q26-q28, q30) — the exact ones flagged
    in the 2026-09-12 baseline-eval entry as scoring just above 0.35 and
    wrongly getting `answered` — now correctly end up `refused_unverified`.
    That part worked as intended: verify.py is a working second net for
    off-book leakage even when the retrieval-similarity gate under-fires.

- root cause, confirmed by direct diagnostic (not guessed): first confirmed
  the label order via `model.config.id2label` on the real downloaded model
  — `{0: contradiction, 1: entailment, 2: neutral}`, exactly the assumed
  order, so that part of the plan was right. The actual bug is the
  "whole chunk as premise" judgment call from the approved Phase 4 plan
  (flagged then only as a *token-truncation* risk, which turned out to be
  the wrong risk to worry about). Direct test: feeding the model a short
  **two-sentence** premise built from chunk u2-s1 ("Our school has a lovely
  garden. We use a set of gardening tools like spades, rakes, and
  sickles.") against the hypothesis "They use tools like spades, rakes, and
  sickles to clean the school garden." — a near word-for-word match to a
  sentence actually inside the premise — scored
  `[contradiction=0.002, entailment=0.005, neutral=0.993]`. Compare to the
  model performing exactly as expected on a classic single-sentence SNLI
  pair ("A man is eating a pizza." -> "A man is eating food.":
  `entailment=0.987`). `cross-encoder/nli-deberta-v3-base` is trained on
  single-sentence-premise/single-sentence-hypothesis pairs (SNLI/MultiNLI);
  handed a multi-sentence *paragraph* as the premise, it systematically
  classifies a hypothesis that matches only one sentence within that
  paragraph as "neutral," not "entailment" — even at ~180 words, nowhere
  near the ~512-token truncation limit originally worried about. This is a
  paragraph-vs-sentence out-of-distribution effect, not an edge case; it
  reproduced on the very first non-trivial multi-sentence chunk tried.
  q01 only "passed" (0.8 ratio) because 4 of its 5 sentences are near-verbatim
  restatements of a *short* answer ("Rina likes to read science fiction
  books" — the whole relevant chunk content in one sentence), which is
  exactly the in-distribution case the model handles well; its only failing
  sentence was unrelated teacher patter ("I will repeat that."), correctly
  flagged.

- not decided yet — flagged to the user, code unchanged pending their call:
  the fix is almost certainly to make the premise sentence-level (split each
  context chunk into sentences too, score every hypothesis sentence against
  every premise sentence, keep the max) rather than whole-chunk, which is
  much closer to the model's actual training distribution and was
  considered and set aside in the original plan specifically to avoid the
  n_sentences x n_premise_sentences blowup — that tradeoff needs revisiting
  now that "keeps the whole plan simple" has a concrete, large false-refusal
  cost attached to it. Threshold-tuning alone will not fix this: neutral is
  eating the entailment mass, so no verify_entailment_threshold value fixes
  a systematically-misclassified label.

- next: get the user's decision on the premise-granularity fix above before
  touching verify.py again. Whatever is decided, re-run this same
  10-answerable/5-off-book check afterward as the regression check.

---

## 2026-09-12 — Phase 4 fix: sentence-split NLI premises

- decided: user chose the sentence-split-premise fix from the options
  presented in the entry above (over swapping the NLI model, doing nothing,
  or lowering verify_supported_ratio — the last was already known not to
  work, since neutral eating the entailment mass isn't a threshold problem).

- built:
  - `verify.py`: new `_premise_sentences(chunks)` — splits each chunk's body
    (header line dropped, same as `style_check.book_vocabulary()`) into
    sentences via the same `style_check.sentences()` splitter used for the
    answer side, returning `(lesson_id, sentence)` pairs. `verify_sentences()`
    now scores every answer sentence against every *premise sentence* across
    all chunks (not against whole chunks), still one batched `predict()`
    call. `best_lesson_id` now comes from whichever premise sentence won,
    not whichever chunk won — same field, finer-grained source.
  - Module docstring rewritten to record the live finding as the reason for
    the change (concrete scores: the two-sentence-premise test from the
    diagnostic above, and the SNLI single-sentence pair it was compared
    against) rather than just asserting the new design.
  - 2 new tests in `test_verify.py`: `_premise_sentences` header-stripping +
    splitting, and the exact q02 shape (a fact buried in a multi-sentence
    chunk must still score as entailed) as a regression test for this exact
    bug. All 6 pre-existing `test_verify.py` tests passed unchanged with no
    edits — their fixture chunks all happened to already be single-sentence,
    so they exercised the new code path trivially; the 2 new tests are what
    actually cover multi-sentence chunks. 148 total in the suite, all green
    (was 146; +2, not +18, since this is an edit to Phase 4's existing
    module, not new Phase 5 work).

- numbers (same 15-question re-run, same book/session pattern, LLM answers
  served from cache so no new OpenAI spend — only the NLI scoring re-ran):
  status went from 1 `answered` / 13 `refused_unverified` / 1
  `refused_off_book` to 4 `answered` (q01, q02, q04, q26) / 9
  `refused_unverified` / 1 `refused_off_book`, i.e. still not "everything
  correct is answered," but a real improvement, not a fluke: q02
  specifically (the diagnostic case above) went from
  `entailment=0.002/supported=False` to `entailment=0.66/supported=True` on
  its exact factual sentence, sourced correctly to chunk u2-s1.

- new finding surfaced by this same re-run, NOT introduced by today's fix
  (carried over from the Phase 2 baseline-eval entry's "answered honestly
  that the book doesn't cover it" observation, now visible again from a
  different angle): q26 ("What is the capital of Japan?", off-book) now also
  shows `status=answered`. Its stage-1/stage-2 answer is actually honest —
  "The passages do not contain any information about the capital of Japan"
  — so no hallucinated fact reaches the student, but the self-referential
  refusal sentence itself scored `entailment=0.977` and `0.961` against two
  unrelated chunks (u6-s2, u7-s2), which is a second, narrower NLI quirk:
  a sentence *about the book's own coverage* ("the passages do not contain
  X") apparently reads as generically entailable from almost any chunk,
  independent of X. This is a different failure mode from the
  whole-chunk-premise bug fixed above (that one under-scored true positives;
  this one over-scores a specific sentence *shape*) and is not fixed here —
  flagged for the user to decide on, same as the premise-granularity call
  was, rather than acted on unilaterally.

- decided: user chose to leave the q26-shaped finding for Phase 6's error
  analysis at scale rather than act on it now — verify.py is unchanged for
  this. Revisit then with real eval-set volume behind it, not one question.

- next: Phase 5 — SSE, sessions CRUD, JWT auth, `POST /evaluate`.
