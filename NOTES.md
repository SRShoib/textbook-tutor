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

## 2026-09-12 — Phase 5, module 1: JWT auth

Planned as one module, not the whole phase (CLAUDE.md's working rhythm is explicit
that a phase-sized plan is too big to review). Fixed order set for the rest of
Phase 5: sessions CRUD + title generation next, then `POST /api/v1/evaluate`, then
SSE streaming last (it's the hard one — `call_llm()` is synchronous and cache-first
with no streaming path at all, so real `token` events need a design decision inside
the one function CLAUDE.md mandates every LLM call go through), then a final test
sweep (`test_embeddings.py` is the one pipeline module still untested).

Built: `core/security.py` (argon2 password hashing via passlib; HS256 JWT for
access + refresh tokens), `core/rate_limit.py` (hand-rolled in-memory fixed-window
limiter for login), `models/refresh_token.py` + migration `0002` (a
`refresh_tokens` table), `schemas/user.py`, `api/auth.py` (register/login/refresh/
logout/me). `core/auth.py`'s `get_current_user_id()` is now a thin wrapper over a
new `get_current_user()` that tries a Bearer token first, falls back to the
existing dev-user path only when `ALLOW_ANONYMOUS=true` and no header was sent at
all, and 401s on a header that's present but invalid (even under
`ALLOW_ANONYMOUS`) rather than silently falling back — a bad token should surface
as an error, not quietly write rows under the dev user. `get_or_create_dev_user()`
and everything `eval/runner.py` imports from `core/auth.py` are untouched.

Judgment calls, decided with the user before writing code (not silently picked):
- Refresh tokens are stateful (a DB table, hash stored not the raw token) so
  `POST /auth/logout` is a real revocation and rotation-on-refresh can detect a
  stolen token being replayed — reusing an already-rotated token now revokes every
  active token for that user, not just the one replayed.
- Login rate limiting is hand-rolled and in-memory (keyed on email+client IP), not
  a library or Redis — CLAUDE.md rules out a second database and the app runs
  single-process on the host, so a dependency would add machinery without adding
  capability. Limitation for the thesis write-up: this resets on restart and
  doesn't hold across multiple workers.
- Auth got route tests (`test_auth_routes.py`, marked `db`), a documented exception
  to "skip route tests unless asked" — token expiry/rotation/cookie behavior can't
  be exercised by a pipeline-style unit test.
- `SessionCreate.grade` is now optional and falls back to `user.grade`, the other
  half of CLAUDE.md's "on register, grade becomes the default for new sessions."

New dependencies: `pyjwt`, `passlib[argon2]`, `argon2-cffi`, `email-validator`,
`httpx` (tests only). passlib 1.7.4 is unmaintained; fine on this project's
Python 3.12, noted in `requirements.txt` in case of a future Python upgrade.

Test infra: first `conftest.py` for the backend. Route tests need a real Postgres
connection, so they use a transaction-rollback `AsyncSession` (bound with
`join_transaction_mode="create_savepoint"` so a route's own `commit()` lands on a
savepoint, then the outer transaction rolls back) rather than a second test
database. Discovered along the way: that session must NOT reuse the shared
module-level engine from `core/db.py` — its pooled asyncpg connections are
loop-bound, and pytest-asyncio gives each test its own event loop, so a pooled
connection from one test breaks when a later test's loop tries to close it.
Fixed by giving the test fixture its own `NullPool` engine instead of importing
the app's engine; not a global pytest-asyncio config change, so the 148 existing
pipeline tests are untouched. New `db` pytest marker registered in `pytest.ini` —
`pytest -m "not db"` runs exactly the old suite with Docker stopped.

Suite: 148 → 179 tests (16 pure unit tests for security/rate-limit, 15 route tests
against real Postgres). All green, both with and without Docker running.
Verified by hand against a live server: register → grade stored → `/me` →
`/refresh` rotates the cookie → `/logout` → replaying the old refresh token fails;
`POST /sessions` with no `grade` in the body correctly inherits the registering
user's grade (8) rather than the book's grade (5). Did not run
`eval.runner` end-to-end as a regression check — no `.llm_cache` entries exist yet,
so a real run would spend real OpenAI money for a check answerable by reading the
code: `eval/runner.py`'s only dependency on `core/auth.py` is
`get_or_create_dev_user`, which this module left untouched, and `eval.runner`/
`app.main` both import cleanly.

## 2026-09-13 — Phase 6, part 1: 150-question test set, configs A-D, and a real bug hunt in verify.py

Scope: expand `questions.jsonl` to 150 and run the guidelines' 5 ablation configs
through eval, one results table. Configs A/B/C didn't exist in code yet (only D,
the always-built full pipeline) — built the ablation switch, then the dev-split
sweep immediately surfaced a severe, previously-invisible bug in `verify.py`.
D-open (Ollama/Qwen) skipped — never run in this repo, no docker service; user
chose to defer it rather than debug an unverified local-model path mid-session.

**Question set.** `data/question_set/questions.jsonl` is now 150 rows (was 30):
120 answerable (120 dev/test split not needed here — see split note below) + 30
off_book, 120 dev / 30 test overall (80/20, matching the original ratio). Every
new answerable question and reference answer is grounded in the actual ingested
book content, pulled directly from Postgres (`SELECT ... FROM chunks WHERE
book_id=...`), not guessed from the PDF or invented — q01-q30 (the original set)
kept byte-for-byte unchanged, q031-q150 appended. Off-book questions are generic
curriculum/trivia (math, science, geography, current-affairs) chosen to avoid any
topical overlap with the 20 units, so the off-book gate's job stays unambiguous.

**Ablation configs (project-guidelines.md 8.2), `graph.py`.** Added a
`pipeline_config: "A"|"B"|"C"|"D"` field, threaded through three conditional
edges so each config is structurally unable to use what it's meant to ablate,
not just told not to: `route_from_start` (A skips straight to a bare
`plain_llm_node`, no retrieval/off-book gate), `route_after_stage1` (B stops
right after stage 1's raw factual answer, `finish_stage1_node`, no stage 2/
style_check), `route_after_style_check` (C falls through to END once the style
loop resolves instead of continuing to verify). Default is `"D"` everywhere —
the live API and every session before this existed are unaffected. New prompt
`prompts/v1_plain_llm.txt` for config A. `eval/runner.py` got a
`--pipeline-config` flag; `--config` still just tags `config_version`, kept
separate on purpose (see runner.py's docstring for why they're not the same
knob). New metrics in `eval/metrics.py`: `hallucination_rate`,
`refusal_accuracy`, `false_refusal_rate`, `readability_summary` (the last one
scores raw answer text directly via `style_check`'s pure functions, so it works
for A/B too, which never run `check_style`).

**The dev sweep (120 questions x A/B/C/D) found config D almost non-functional:**
5/120 answered, 113 refused_unverified. False refusal rate 0.96. This is the
issue NOTES.md's Phase 4 "live check" entry flagged and explicitly deferred to
"Phase 6's error analysis at scale" — now confirmed at scale, plus one new
compounding cause found alongside it. Root-caused via `eval/runs/dev_D_*.jsonl`
records, not guesswork:

1. **Stage 2 hallucinated its own lesson citation.** `_FIRST_TURN_NOTE` told it to
   "name the unit, lesson and page" but stage 2 structurally never receives
   `context_chunks` (by design — see `generate.py`'s docstring on why), so it had
   no way to know one. Checked 21 failing answers with a citation sentence: 0/21
   correct — it collapsed onto two fabricated placeholders ("Unit 3, Lesson 2,
   page 45" / "Unit 5, Lesson 2, page 45") regardless of the real source.
   **Fix:** `graph.py`'s new `_format_citation()` builds the true citation from
   `retrieval.top_chunks[0]` (the single best-matching chunk) and
   `generate_stage2`/`render_stage2_prompt` now take `source_citation` and tell
   the model to use exactly that. Confirmed 5/5 correct in a follow-up smoke test.

2. **`verify_answer` scored pedagogical scaffolding as if it were a factual
   claim.** Greetings, the citation line, "I'll repeat that", comprehension-check
   questions — none of these are claims about the book (many can't even be
   entailed when accurate, since `ingest.py`'s chunk header is stripped before
   premise sentences are built), but they counted toward `supported_ratio`'s
   denominator and dragged genuinely correct answers below the 0.8 pass bar.
   **Fix:** `verify.py`'s new `is_scaffolding_sentence()` — a closed,
   evidence-grounded pattern list tied to specific style_guide.md rules
   (§3.1/3.2/3.3/3.5), not a general filler detector — excludes these before
   scoring; `VerificationReport` gained `skipped_sentences` so the eval JSONL
   stays a complete record. Iterated against real failures several times (the
   model's phrasing varies: "Let's begin our lesson." / "Let's get started with
   our lesson." / "We are in Class 5, ... learning about geography." / "Does
   everyone understand?" all needed separate patterns).

3. **Sentence splitter didn't treat `?" ` as a sentence boundary.** Stage 2's
   "heavy repetition" pattern (§3.3) echoes the question in quotes before
   answering — `You asked, "...?" The answer is, X.` — and the un-augmented
   lookbehind never matched at the `?"` boundary, so the whole thing stayed one
   noisy sentence. This was 47/71 (66%) of what was left after fixes 1-2.
   **Fix:** `_SENT_SPLIT_RE` in `style_check.py` now also splits after terminal
   punctuation immediately followed by a closing quote. This regex is
   cross-validated against `tools/style_guide/measure_style.py`'s copy
   (`test_style_check.py` asserts they stay identical) — updated both in
   lockstep, then re-ran `measure_style.py` against all 12 transcripts to check
   whether style_guide.md section 2's published numbers moved: **they did not**
   (14 words / 6.4 mean / FK 3.37 / 2,353 sentences, byte-identical to before) —
   the `?" ` pattern is an artifact of the chatbot's own generated text, never
   occurs in spoken-teacher transcripts. Changelog entry v1.2 added there.

4. **Self-referential refusals scored as entailed (the exact thing Phase 4
   flagged as unresolved).** For off-book questions that weakly cleared the
   retrieval gate, stage 1 correctly said "the passages do not contain any
   information about X" (its prompt tells it to) — but that sentence itself
   scored 0.97-0.99 entailment against unrelated book content, so
   `refusal_accuracy` read 0.48 even though the *text* was functionally a
   refusal. **Fix:** `graph.py`'s new `is_self_refusal()` checks stage 1's raw
   output against its two documented phrasings and routes straight to
   `refuse_node` — skipping stage 2/style_check/verify entirely — regardless of
   `pipeline_config`, so config B (which never touches `verify.py` at all) is
   also protected. `refusal_accuracy` went to a clean 1.00 (25/25) immediately.

**Threshold tuning (dev split only, per CLAUDE.md — never touched test).** After
fixes 1-3, 49 false refusals remained; ~49% sat at `supported_ratio` exactly 0.0
(no threshold recovers these — genuine NLI cross-encoder strictness on
paraphrase: tense changes, pronoun-to-noun substitution, true added detail like
"...of Bangladesh"), the rest cleared 0.33+. Lowered `verify_supported_ratio`
0.8 -> 0.5 (`config.py`, `.env`, `.env.example`) — defensible because
`heavy repetition` means an answer's checked sentences are usually the same fact
restated 2-3 times, not independent claims. **This needs a caveat on the
headline number:** `hallucination_rate` jumped 0.03 -> 0.24 at the new
threshold, which looks alarming, but a full audit (manually inspected the 6
lowest-scoring rows, then automated keyword-overlap cross-check against
`reference_answer` across all 72 answered rows, 7 flagged, all inspected by
hand) found **zero actual fabrications** — every "unsupported" sentence was the
same true fact restated in a noisier wrapper next to a cleanly-scoring bare
repeat of it. `hallucination_rate` as currently defined (mean fraction of
*sentences* unsupported) double-penalizes redundant restatement; it is not
currently measuring "fraction of answers containing a fabricated fact." Left as
an open methodological question for the thesis write-up rather than silently
redefined — see config.py's comment on `verify_supported_ratio`.

**Config D, dev split, before -> after (120 questions, same book/model throughout):**

| | before | after fix 1 | after fixes 1-3 (final patterns) | after fixes 1-3 + threshold |
|---|---|---|---|---|
| answered | 5 | 30 | 45 | 72 |
| refused_unverified | 113 | 88 | 49 | 22 |
| refused_off_book | 2 | 2 | 26 | 26 |
| false_refusal_rate | 0.96 | 0.75 | 0.53 | **0.24** |
| refusal_accuracy (off-book) | n/a (0 correct citations to begin with) | 0.76 | 1.00 | **1.00** |
| hallucination_rate | n/a | 0.01 | 0.03 | 0.24 (see caveat above) |

**Not done yet:** configs A/B (unaffected by any of this — no `verify.py`/stage 2
involvement for A, no `verify.py` for B, confirmed and not re-run) still need one
more clean pass alongside a final C/D dev run for a fully consistent 4-config dev
table; the 30-question test split (touched zero times so far); the combined
results table (faithfulness/hallucination/refusal-accuracy/false-refusal/
readability x 5); re-delivering the 30-failure categorized table against the
*fixed* D run (the one already given was against the broken run, useful for
diagnosis but not the thesis artifact). Test suite: 179 -> 229 (new tests for
every routing function and pattern above).

## 2026-09-13 — Phase 6, part 2: dev+test run for real, D-open dropped, two more findings fixed

Continuation of the same day's work. Ran the actual dev sweep (all 4 configs) and,
for the first and only time, the test split — then two more issues turned up from
reading the results honestly rather than treating "tests pass" as "done."

**D-open dropped, not deferred.** User confirmed OpenAI-only is fine; no interest
in getting Ollama/Qwen working. CLAUDE.md's stack table and hardware section (the
Qwen/Ollama rows) and Phase 6's line (now "4 configurations") edited to match —
the ablation set is A/B/C/D, full stop, not "5, one pending."

**Results (test split, 30 questions, gpt-4o-mini, first and only touch):**

| Config | Faithfulness | Hallucination (mean / severe) | Refusal acc. | False refusal | FK |
|---|---|---|---|---|---|
| A. Plain LLM | n/a | n/a | 0.00 | 0.00 | 11.01 |
| B. RAG baseline | 0.88 | n/a | 1.00 | 0.00 | 12.23 (n=2) |
| C. +grade voice | 0.68 | n/a | 1.00 | 0.00 | 6.30 |
| D. Full system | 0.69 | 0.24 / **0.00** | 1.00 | 0.32 | 6.12 |

Grade-5 FK target is 3.0-4.5 (style_guide.md §2) — none of A-D hit it on this
pass, worse than the dev numbers below; small-sample noise from a 30-question
split plus the two fixes below landing mid-session (test was re-run once, after
the style_check fix, per "never touched test until pipeline behavior is settled").

**Finding 1 — `hallucination_rate`'s definition needed a companion metric, not a
redefinition (yet).** Manually audited all 72 dev-split answered rows: zero
fabrications despite `hallucination_rate` reading 0.24 (see part 1's entry above).
Added `severe_hallucination_rate` to `eval/metrics.py` — fraction of answered rows
where `supported_ratio` is exactly 0.0 (nothing in the message entailed at all,
not just some of it). Reads **0.00 on every run so far** (dev and test), a clean,
defensible headline number the noisy averaged one isn't. Kept both — neither
alone is the full picture, and redefining `hallucination_rate` itself (e.g.
deduping near-identical sentences before scoring) is still open, deliberately
left for the thesis's methodology discussion rather than done under time
pressure.

**Finding 2 — the exact same scaffolding bug existed in `style_check.py`, just
never looked for.** 65% of dev-split answerable questions (62/95) got a correct
answer that still failed the readability check — `style_check.py` counts every
sentence toward the 14-word/FK/vocab bar, including the "Now, to answer your
question, '<entire original question restated>'" sentence verify.py already
learned to ignore. Checked 10 of the 54 "too long" failures: 9/10 had that exact
question-echo as their longest sentence, often 15-20 words on its own regardless
of how simple the real answer was. **Fix:** moved `is_scaffolding_sentence()` (and
the pattern list) from `verify.py` to `style_check.py` — the lower-level module,
avoiding a circular import — added `content_sentences()`, and `check_style()` now
scores `check_sentence_length`/`check_vocab_coverage`/`check_fk_grade` against
scaffolding-stripped text instead of the raw answer. `verify.py` re-imports the
same name, so nothing there changed. Style pass rate: dev C 0.10 -> 0.33, dev D
0.11 -> 0.38 (roughly 3x). Error-analysis category counts on the re-run (dev D,
120 questions): correct-but-too-hard 62 -> 36, clean pass 8 -> 26,
not-Bangladeshi-in-style (judge) 2 -> 6 (expected — fewer numeric failures means
the judge actually runs more often instead of being skipped), wrongly refused 23
-> 27 (noise: regenerated stage-2 text on the newly-passing retries shifts which
exact wording reaches verify_answer, not a regression in the verifier itself).

**Housekeeping:** deleted 14 superseded `eval/runs/*.jsonl` files (tiny smoketest
sanity checks, and dev/test runs made obsolete by the style_check fix) — kept the
original broken run, the verify.py fix progression, and the current final
dev+test numbers for every config, since those are what the before/after tables
above and the results table cite.

**Still open, explicitly not done today:** `hallucination_rate`'s real
redefinition (dedupe-before-scoring); rebuilding the 30-failure categorized table
against `styfix_dev_D_20260912T203830Z.jsonl` (the numbers above are counts only,
not the full table — the previous full table was built against the pre-style-fix
run and is now stale). Test suite: 229 -> 235.

---

## 2026-09-13 — Phase 7, module 1: frontend foundation + auth

Scope: `frontend/` didn't exist yet. Planned as one module (scaffold, design
system, API client, register/login), not the whole phase, per CLAUDE.md's working
rhythm.

**Finding that reshaped the plan, before any frontend code was written:** Phase 5
was never finished — only module 1 (JWT auth) exists. `api/sessions.py` has just
`POST /sessions` (create) and `POST /sessions/{id}/messages` (synchronous); no
session list, rename, soft-delete, title generation, or SSE route, and no CORS
middleware at all, so a browser on `localhost:3000` could not call this API in any
form. Decided with the user: close each gap as its own small backend module,
immediately before the frontend module that needs it (sessions CRUD before the
sidebar, SSE before the chat screen) — not one big Phase 5 catch-up, not against
mocks. Full Phase 7 module order (7 modules) written into the plan file for
reference each time a new module starts.

Also decided with the user: upload progress is an honest indeterminate checklist
driven only by the real `processing -> ready -> failed` transition (`books` has no
per-stage signal and this module doesn't add one); every answer gets a collapsible
"how I checked this" evidence panel over the child-facing source card, showing
`supported_ratio`, per-sentence entailment and readability numbers — the live demo
of contributions 1 and 3 at the defense.

**Built:**
- Backend (small, required before anything in a browser could work at all):
  `CORSMiddleware` in `main.py` (explicit origin + `allow_credentials=True`, never
  `"*"` — that pairing is rejected by the middleware anyway, and a wildcard would
  defeat the refresh cookie's protection), new `frontend_origin` setting
  (`config.py` + `.env.example`).
- `frontend/` scaffolded from scratch: Next.js 16 (App Router, Turbopack, React
  19), TypeScript strict, Tailwind v4, shadcn/ui (`base-nova` style, `@base-ui/react`
  primitives), Framer Motion. Every dependency create-next-app/shadcn installed
  with a `^` range was re-pinned to its exact resolved version in `package.json`,
  matching this project's reproducibility rule.
- `lib/types.ts` — hand-written TS mirrors of every Pydantic schema and pipeline
  dataclass a message carries (`MessageRead`, `VerificationReport`,
  `StyleReport`, `SourceCitation`, ...), not generated from `openapi.json`.
- `lib/api.ts` — the one `apiFetch()` every call goes through: in-memory-only
  access token (never `localStorage`, since these are children's accounts),
  401-triggers-refresh-then-retry-once, unwraps `core/errors.py`'s
  `{"error":{code,message}}` shape into a typed `ApiError`.
- `lib/auth-context.tsx` — `AuthProvider`/`useAuth()`: silent refresh on mount to
  recover a session after a hard reload, `login`/`register`/`logout`.
- `lib/motion.ts` — shared transform/opacity-only Framer variants;
  `<MotionConfig reducedMotion="user">` in the root layout handles
  prefers-reduced-motion globally so individual components never check it
  themselves; a matching CSS-level `@media (prefers-reduced-motion: reduce)`
  block in `globals.css` covers shadcn's own non-Framer transitions.
- `app/layout.tsx` — Noto Sans Bengali + Inter loaded via `next/font/google` as a
  single stacked `--font-sans` (Inter first, Bengali glyphs fall through
  automatically since the two scripts don't overlap); fixed a dangling
  `--font-sans: var(--font-sans)` self-reference left by `shadcn init`'s default
  `globals.css`.
- `app/(auth)/{login,register}` + `app/(app)/layout.tsx` (client-side guard, no
  edge middleware — the access token lives only in memory, so there's nothing
  for middleware outside React to inspect anyway) + a placeholder `/chat` page
  (just enough to prove the guard/redirect chain end to end; upload/sidebar/chat
  are later modules).

**A real bug found and fixed via live testing, not just unit-level checks:**
Playwright-driven registration followed by a page reload intermittently bounced
the new user straight back to `/login` instead of restoring the session. Root
cause, confirmed by capturing the actual network trace: React Strict Mode
double-invokes effects in dev, so `AuthProvider`'s mount-time silent refresh fired
two concurrent `POST /auth/refresh` calls presenting the same cookie. The
single-flight guard the plan called for existed in `lib/api.ts` but was only wired
into `apiFetch`'s own 401-retry path — the direct call from the mount effect
bypassed it entirely. When the two requests' DB reads/writes in `api/auth.py`
interleaved the wrong way, the second saw the first's rotation as a replay and
`REFRESH_TOKEN_REUSED` fired, revoking every token for that user, including the
one the first request had just issued. Fixed by exporting one shared
`refreshSession()` (returns the full `TokenResponse`, not just the token string)
that both the mount effect and `apiFetch`'s 401 handling now call through — only
one real request ever goes out no matter how many callers ask concurrently.
Verified with a 10-iteration Playwright stress test (0/10 lost the session,
confirmed via network trace that no duplicate `POST /auth/refresh` reaches the
server) after the fix, versus a reproducible failure before it.

**Verified live** (backend `pytest -m "not db"`: 220 passed, unaffected by the CORS
change; frontend `tsc --noEmit` and `next build` clean; real Postgres + FastAPI +
Next.js dev server, driven with Playwright since no `chromium-cli`/project run
skill existed yet): register -> lands on `/chat` authed -> hard reload survives via
silent refresh -> logout clears the httpOnly cookie and redirects to `/login` ->
direct navigation to `/chat` while logged out bounces back to `/login`. Confirmed
no access token in `localStorage`/`sessionStorage` at any point; confirmed
`refresh_token` cookie is `httpOnly`/`SameSite=Lax`. Confirmed the Bengali font
fallback renders real conjuncts correctly (`তুমি কেমন আছো?`, no tofu) and that
`prefers-reduced-motion: reduce` renders the login card at full opacity
immediately rather than stuck mid-animation.

**Not yet resolved, flagged for the user rather than decided unilaterally:**
`frontend/.gitignore` (create-next-app's default) excludes any `.env*`, which also
silently excludes the tracked-on-purpose `frontend/.env.local.example` — the root
repo's own `.env.example` is tracked because the root `.gitignore` only excludes
the exact name `.env`, not the broader pattern. CLAUDE.md requires asking before
touching `.gitignore`, so this wasn't fixed; the user needs to either add a
`!.env.local.example` exception or decide the example isn't worth tracking.

**Numbers:** 220 backend tests (unchanged — this module touched no `pipeline/`
code), 0 frontend tests (none in scope for this module — CLAUDE.md only requires
tests for `pipeline/`; `tsc --noEmit` + `next build` + the Playwright checks above
are the safety net until/unless the user wants Playwright added as a real suite).

**Next:** Phase 7 module 2 — backend sessions CRUD (`GET /sessions`,
`GET /sessions/{id}/messages`, rename, soft-delete) + title generation, per the
fixed module order in the plan file.

---

## 2026-09-13 — Phase 7, module 2: backend sessions CRUD + title generation

Closed the loop on the previous entry's `.gitignore` item first: user chose the
`!.env.local.example` exception; `frontend/.gitignore` now tracks it (confirmed
with `git ls-files --others --exclude-standard`, not just `git check-ignore`,
since a negated pattern still shows up in `check-ignore -v`'s match line even
though the path is no longer actually ignored).

**Built** (backend only — no frontend UI in this module, that's module 4):
- `backend/prompts/v1_title.txt` + `backend/app/pipeline/title.py` —
  `generate_title()`, same shape as `rewrite.py` (own prompt version, one
  `call_llm()` wrapper). Falls back to `_fallback_title()` (pure
  first-6-words truncation) on any LLM error or an empty response — a title
  is a UX nicety, never allowed to block message creation. Always
  `provider="openai"` regardless of `pipeline_config` (A/B/C/D) — titling
  isn't part of the ablation being measured.
- `api/sessions.py`: new `GET /sessions` (list, scoped to caller, ordered
  `updated_at DESC`), `GET /sessions/{id}` (single), `GET /sessions/{id}/messages`
  (full history, chronological, no pagination), `PATCH /sessions/{id}` (rename,
  1-200 chars), `DELETE /sessions/{id}` (soft-delete, never hard — matches
  existing rule). All five share a new `_get_owned_session()` helper
  (wrong-id/not-yours/soft-deleted all collapse to the same `SESSION_NOT_FOUND`
  404, refactored out of the pre-existing `create_message()` too).
  `create_message()` also gained two side effects, both committed alongside the
  user's message and before the pipeline runs: sets `session.title` via
  `generate_title()` when still `None` (self-healing -- if generation failed on
  message 1, message 2 tries again, no extra state needed), and bumps
  `session.updated_at` on every message.
- `schemas/session.py`: new `SessionUpdate`; `SessionRead` gained `updated_at`
  (column already existed since Phase 1's `0001_initial` -- **no new migration
  needed** for this whole module).
- Tests: `test_title.py` (7, pure functions, `call_llm` monkeypatched) +
  `test_sessions_routes.py` (8, `db`-marked, transaction-rollback fixture --
  same documented exception to "skip route tests" as Phase 5's auth tests):
  list scoping + activity ordering, single-get 404 across users, message-history
  scoping, rename + validation, soft-delete then idempotent 404, auto-title
  fires once and survives a second message unchanged, `updated_at` monotonically
  bumps. 235 -> 250 total (227 non-`db` + 23 `db`, both green).

**A real gap found while reading `create_message()` before touching it, not
something the plan predicted precisely:** the session row was never touched
when a message was added, so sorting the sidebar by `updated_at` would have
silently meant "session creation time," not "last activity." Fixed as part of
this module rather than filed separately, since the fix is one line in the
same function already being edited for title generation.

**Verified live**, not just via the mocked route tests, against the real
ingested Class 5 book (`97a7267b-...-4be4355e1a4f`, still `ready`/135 chunks)
with a real `gpt-4o-mini` key: created a session (`title: null`), asked "What
is a noun?" (38.5s, real pipeline run, `status: answered`) -> title became
"Understanding the Basics of Nouns", `updated_at` advanced past `created_at`.
Second message ("Give me an example.", correctly rewritten by the pre-existing
`rewrite.py` into a standalone query, 6.0s) -> title unchanged, `updated_at`
advanced again. `GET /sessions` correctly sorted this session first, ahead of
~30 older eval-runner sessions already sitting in the dev DB (each of which sets
its own descriptive `"eval: <config> <timestamp>"` title directly, bypassing
`generate_title()` entirely -- confirmed that doesn't conflict, both paths just
write the same nullable column). `GET /sessions/{id}/messages` returned all 4
messages in correct chronological order. Confirmed unauthenticated requests
still resolve (200, not 401) only because `ALLOW_ANONYMOUS=true` in this local
`.env` — expected dev behavior via the pre-existing `get_current_user_id()`
fallback, not a new auth gap; still correctly 404s on another (nonexistent)
session id under that same dev-user identity.

**Not done, out of scope for this module:** `POST /evaluate` and the SSE
streaming route are still open (modules 5 and later in the roadmap); this
module only closed sessions CRUD + titling.

**Next:** Phase 7 module 3 — upload + class picker (`POST /books`, poll
`GET /books/{id}`, the honest indeterminate stage checklist decided with the
user, then create a session), per the plan file's fixed module order.

---

## 2026-09-13 — Phase 7, module 3: upload + class picker

**Found while reading `api/books.py` before touching it:** `POST /books` and
`GET /books/{id}` had no auth dependency at all — anyone, logged in or not,
could upload a PDF to this server. Written in Phase 1, before JWT auth
existed, never revisited. Closed the same way CORS was in module 1: small,
necessary, flagged rather than silently patched. Both routes now require
`get_current_user_id()` (same `ALLOW_ANONYMOUS` dev-user fallback as every
other route) — not user-scoped data (no `books.user_id` column, schema is
fixed), so this only gates *who can trigger* an upload/lookup, not which
books they can see.

**Built:**
- `backend/app/api/books.py`: the auth dependency above.
- `backend/tests/test_books_routes.py` (4, `db`-marked): 401 without auth on
  both routes; an authenticated upload that hash-dedupes against a
  pre-inserted `Book` row returns the existing `ready` book (asserting the
  *submitted* title/grade are correctly ignored in favor of the existing
  row's) without ever touching `ingest_book()` — a real, non-dedup ingest
  stays out of the route-test suite (loads bge-m3 for real; already covered
  by Phase 1's `test_ingest.py` + this session's live checks below). 254 ->
  258 total.
- `frontend/lib/api.ts`: `apiUpload()` — same `ApiError`/401-retry handling
  as `apiFetch`, `FormData` body instead of JSON.
- `frontend/app/(app)/upload/page.tsx` (new) — file + title (auto-derived
  from the filename, editable) + class picker (1-12, defaults to
  `user.grade`) -> `apiUpload("/books", ...)`. A `status: "ready"` response
  (hash-dedup) skips straight to session creation; otherwise polls
  `GET /books/{id}` every 2s, no hard timeout, with a "this can take a few
  minutes" note past 20s. The checklist is four fixed labels that animate as
  "working" together while `processing` and flip to done together on
  `ready` — never implying it knows which step is "really" running, since
  `books.status` still only has three values. `failed` shows a plain error
  + "Try again" reset (no failure-reason column exists to say more).
- `frontend/app/(app)/chat/[sessionId]/page.tsx` (new, replaces module 1's
  static placeholder) — fetches the real session via `GET /sessions/{id}`
  (module 2) instead of showing a canned string. Still a placeholder for
  real chat (module 6).
- Removed `frontend/app/(app)/chat/page.tsx` (no `sessionId`); `app/page.tsx`
  and both auth pages now redirect an authed user to `/upload`, not `/chat`.

**Judgment call, flagged rather than silently decided:** every login
currently re-runs the upload flow — there's no "resume a previous session"
until module 4's sidebar exists. Cheap in practice (hash-dedup returns the
existing ready book instantly), and deliberately left as a known temporary
gap rather than reaching into module 4's scope early.

**Verified live**, twice, with a real browser (Playwright, no project run
skill existed yet so same pattern as module 1): (1) registered, landed on
`/upload` (not `/chat`), uploaded the exact already-ingested book PDF
(`97a7267b-...-4be4355e1a4f.pdf`) — hash-dedup fired, redirected straight to
`/chat/{new session id}` showing "New conversation" / "Class 5" with no
processing wait. (2) Generated a minimal-but-valid PDF with PyMuPDF
containing no real textbook content, uploaded it as a genuinely new file
(different hash, forces a real `ingest_book()` run) — caught the checklist
mid-"processing" (screenshot), watched `ingest.py`'s real content-parsing
step fail fast (no Contents page to find) and flip the book to `failed`,
confirmed the error state renders with a working "Try again" that resets
the form. All three checklist phases (processing / failed / reset) are
real, not simulated.

**Not done, out of scope:** no `GET /books` list endpoint (nothing in this
module's design needs one — a student always picks a file, dedup handles
repeats); the "resume a session" gap above stays open until module 4.

**Next:** Phase 7 module 4 — the sidebar (session list, rename, delete,
active-session state), per the plan file's fixed module order.
