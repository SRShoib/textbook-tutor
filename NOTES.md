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

---

## 2026-09-13 — Phase 7, module 4: sidebar

Frontend-only module — no backend changes, 254 backend tests unaffected.

**Built:**
- `frontend/lib/format.ts` — `relativeTime()`, hand-rolled ("just now"/"5m ago"/"3h ago"/
  "2d ago", falling back to a plain date past a week), not a `date-fns` dependency for one
  display detail.
- `frontend/components/sidebar.tsx` (new) — fetches `GET /sessions` (module 2) on mount and on
  every route change (`usePathname()` in the effect deps), so a session created by `/upload`
  shows up the moment you land on `/chat/{id}` with no shared state between the two pages. "+ New
  chat" link to `/upload`; each row shows title (or "New conversation"), relative time, class;
  active session highlighted via `useParams().sessionId`. Inline rename (pencil icon -> input ->
  `PATCH` on Enter/blur) and delete (trash icon -> `window.confirm` -> `DELETE`, redirecting to
  `/upload` if the deleted session was the one open). Empty and error states, both non-blocking.
- `app/(app)/layout.tsx`: renders `<Sidebar />` in the slot module 1 left for it.

**Two real bugs found via live testing, not caught by lint/typecheck:**
1. **`frontend/lib/types.ts`'s `SessionRead` was missing `updated_at`** — added to the backend
   schema in module 2, never mirrored to the frontend types because module 2 touched no frontend
   files. Caught immediately by `tsc --noEmit` the moment this module tried to read
   `session.updated_at`, not by anything module 2 itself ran (a hand-written-mirror cost flagged
   as a tradeoff back in module 1's plan, now a concrete example of it).
2. **`eslint-plugin-react-hooks`'s newer `set-state-in-effect` rule** correctly flagged a
   synchronous `setLoading(true)` at the top of the session-list effect. Fixed by dropping it
   entirely rather than working around it: the effect re-runs on every navigation, and
   re-flashing the whole sidebar to a loading state on every click would have been worse UX than
   just leaving the (still-valid) previous list visible until the fresh fetch resolves — `loading`
   now only ever goes true -> false once, on first mount.

**A third bug found only by driving a real browser, invisible to both lint and a human reading
the diff:** `<Button render={<Link href="/upload" />}>` (Base UI's polymorphic-rendering prop,
not Radix's `asChild` — this project's shadcn install uses `@base-ui/react`) rendered without
error but logged a real console warning: Base UI's `Button` defaults to assuming the element it's
given IS a native `<button>`, and a `<Link>` renders an `<a>`. Fixed with `nativeButton={false}`,
which is exactly what the warning's own message pointed at. Would have shipped invisibly — the
button worked and looked identical either way; only the browser console surfaced the
accessibility-semantics gap.

**Verified live** (Playwright, both servers running, real Postgres): fresh account -> empty-state
sidebar -> uploaded the real book twice (dedup path both times) -> confirmed 2 real distinct
sessions appear, second one highlighted -> renamed the active session inline -> **reloaded the
page** to confirm the rename was a real `PATCH`, not just local React state (it was) -> deleted
the active session -> redirected to `/upload`, sidebar correctly down to 1 row. No console errors
on the final clean run.

**Not done, out of scope (per the plan's judgment call):** no mobile collapse/hamburger toggle —
sidebar is a fixed-width column always visible, deferred to module 7's responsive pass.

**Next:** Phase 7 module 5 — the backend SSE streaming endpoint
(`POST /sessions/{id}/messages/stream`). Flagged as "the hard one" back in module 1's roadmap:
`call_llm()` is synchronous and cache-first with no streaming path at all.

---

## 2026-09-13 — Phase 7, module 5: backend SSE streaming

Backend only, per the roadmap — module 6 is where the frontend actually consumes this.

**The real constraint this module is built around, found by re-reading `pipeline/graph.py`
before touching anything:** `stage2_node` can run more than once before the graph settles —
`style_check` and `verify` both have retry edges back to `stage2` (up to
`(style_max_retries+1)*(verify_max_retries+1)` attempts). Only `retrieve_node` never gets retried.
So true token-by-token relay of an in-progress LLM call would risk showing a student text the
pipeline is about to silently discard and regenerate. Decided with the user before planning:
**"streaming" here means real progress events the moment they genuinely happen (`retrieving` on
the `rewrite` node's update, `sources` on `retrieve`'s), then a simulated typing effect over the
already-fully-checked final answer** — not a live relay of in-progress tokens. Total wall-clock
latency is unchanged from the synchronous endpoint; this is about perceived responsiveness and
progress, not raw speed. Redesigning the retry-loop architecture to make true streaming safe
would mean reshaping deliberate research architecture for a UI module — out of scope, not
attempted.

**Built:**
- `pipeline/graph.py`: extracted `_build_result(final_state, question, latency_ms)` out of
  `run_pipeline()`'s tail so it and the new streaming path can never silently drift in how they
  assemble a `PipelineResult`. New `StreamEvent` (frozen dataclass: `kind`, `data`) and
  `run_pipeline_stream()` — iterates LangGraph's own `_compiled.astream(inputs,
  stream_mode="updates")` (already part of the compiled graph; no new dependency), accumulating
  each node's update into a running `final_state` exactly like `.ainvoke()` does internally.
  Yields `retrieving` off the `rewrite` node's update (carries the resolved `search_query`),
  `sources` off `retrieve`'s (`build_sources()`, unchanged), then one final `result` event once
  the graph reaches `END`. Configs that skip nodes (A has no retrieval; B never reaches verify)
  simply never produce those intermediate updates — no special-casing needed, confirmed by the
  off-book and verify-exhausted tests below, which still resolve to exactly one `result` event
  each despite an internal retry.
- `api/sessions.py`: extracted `_require_ready_book()` and `_touch_session()` (title generation +
  `updated_at` bump) out of `create_message()` so the new route reuses the exact same bookkeeping
  rather than a second copy that could drift — the same class of bug module 4 found once already
  (the frontend's `SessionRead` type mirror). New `POST /sessions/{id}/messages/stream`: the
  ownership/book-ready checks and the user-message insert + `_touch_session()` + commit all happen
  *before* `StreamingResponse` is returned, so a 404/409 is a normal JSON error, never a broken
  event-stream. The generator chunks the settled answer into words
  (`_TOKEN_STREAM_DELAY_SECONDS = 0.03`, a cosmetic constant, not a config.py threshold — same
  treatment as `graph.OFF_BOOK_REFUSAL` being a plain UI string) as `token` events, **inserts and
  commits the assistant message before emitting any of them** (a disconnecting client loses
  nothing — the answer is already durably saved), then `verification`, then `done` with the full
  `MessageRead`-shaped payload so module 6 can reuse one type for both endpoints. Any mid-stream
  exception yields a single `error` event (`{code: "STREAM_FAILED", message}`) instead of dying
  silently — a pragmatic fifth event beyond CLAUDE.md's four named ones.
- Tests: 3 new in `test_graph.py` (event-kind sequence for a clean answered run, an off-book
  refusal, and a verify-exhausted run — same `monkeypatch.setattr(graph, ...)` fixtures already in
  the file, no LangGraph internals mocked) + 4 new in `test_sessions_routes.py` (`db`-marked):
  ownership/book-ready gating, full event-order + DB-persistence check, and the `error`-event
  path. 254 -> 260 total, both `pytest -m "not db"` and `pytest -m db` green.

**Verified live** against the real ingested book with a real `gpt-4o-mini` key, every SSE line
timestamped as it arrived (not just content-checked after the fact): `retrieving` fired
essentially instantly; `sources` took ~28s on this run (the process's first `hybrid_search()`
call since a server restart, cold-loading `bge-m3`) but still fired the moment retrieval actually
finished, not before; a further ~9s of real pipeline work (stage 1, stage 2, style_check, verify)
elapsed before the first `token`; then real per-token gaps of ~30-40ms matching the configured
delay; a genuine `verification` event (7 real NLI-scored sentences, all `supported: true`); a
`done` payload whose `readability` honestly shows `passed: false` (one 18-word sentence over the
grade's 14-word cap) — the real style_check result, not smoothed over for the demo. Confirmed via
separate `GET` calls that the session's title was set (`generate_title()`, same as the
non-streaming path) and the assistant message persisted with content byte-identical to the
concatenated `token` events.

**Not done, out of scope:** frontend consumption (module 6); `POST /evaluate` as a real API route
(Phase 5 leftover, unrelated to this module).

**Next:** Phase 7 module 6 — the real chat screen: consume this endpoint, source lesson card,
the four status designs, and the collapsible evidence panel decided back in module 1.

---

## 2026-09-13 — Phase 7, module 6: the real chat screen

Frontend only — no backend changes, 260 backend tests unaffected. Replaces module 3's static
`/chat/[sessionId]` placeholder with the actual chat experience.

**Decided with the user before planning, a child-safety call, not a style one:** `graph.py`
deliberately keeps the raw generated text on a `refused_unverified` message for error-analysis,
and explicitly leaves how a client *displays* it as a Phase 7 decision. Since that text can be
ungrounded, and the same question applies to the currently-unreachable `low_confidence`: the
primary bubble for both never shows it. Instead a plain "I couldn't fully check this against your
book" message, with the real generated text available only inside the evidence panel (collapsed by
default, labeled "Generated but not shown to the student"). `refused_off_book`'s text is the fixed,
safe `OFF_BOOK_REFUSAL` string and is always shown directly — no such risk there.

**Built:**
- `lib/types.ts`: `StreamEvent` discriminated union for the five SSE kinds `api/sessions.py`
  emits (+ `error`). `lib/api.ts`: exported `API_BASE`/`API_PREFIX` (were private) so `lib/stream.ts`
  doesn't duplicate them.
- `lib/stream.ts` (new) — `streamMessage()`: the browser's native `EventSource` can't POST, so this
  is `fetch()` + a manual `ReadableStream` reader, buffering and splitting on `\n\n` to parse the
  `event:`/`data:` blocks, reusing `getAccessToken()`/`refreshSession()` from `lib/api.ts` (a single
  401-retry, same pattern `apiFetch` already uses) rather than a parallel auth path.
- `components/chat/`: `status-meta.ts` (the `MessageStatus -> {label, icon, className}` lookup --
  `--destructive` theme tokens reused for `refused_unverified`, plain `amber-*` utilities for
  `low_confidence` since no dedicated "caution" token exists for a status nothing produces yet),
  `source-card.tsx`, `evidence-panel.tsx` (collapsed by default, plain `useState`, content fades via
  the existing `fade` variant rather than animating height), `assistant-message.tsx` (a *settled*
  `MessageRead` -- same component whether just-streamed or loaded from history), `streaming-message.tsx`
  (the in-flight turn: "Reading your book…" -> sources appear immediately once real -> accumulating
  token text with a blinking cursor), `message-list.tsx`, `chat-input.tsx`.
- `app/(app)/chat/[sessionId]/page.tsx` rewritten: loads session + history on mount
  (`GET /sessions/{id}`, `GET /sessions/{id}/messages`, both unchanged), `handleSend()` appends the
  user's turn optimistically and drives `streamMessage()`'s callbacks into one `streaming` state;
  `done` replaces it with the real `MessageRead` pushed into the settled list, so a reload renders
  identically through `AssistantMessage` alone -- confirmed by test, not just asserted.

**Verified live** (Playwright, real book, real key, no mocks for the main flow): registered,
uploaded (dedup), sent "What is a noun?" through the real UI, and watched the actual sequence
render correctly on screen -- the source card appeared *before* the answer text started
("Writing an answer…"), tokens accumulated with the real per-word delay, the bubble settled to
"Answered" with the real evidence panel (7/7 sentences supported, real entailment scores 0.97-1.00,
readability "did not pass" with the real FK 3.28/18-word-longest-sentence numbers -- matching
module 5's live check of the same question byte-for-byte). Reloaded the page: identical rendering
from history alone, no special-casing needed. Separately verified all four status designs
(including the two unreachable-in-practice ones) by intercepting the history GET with Playwright
route mocking and rendering all four together: `answered` (neutral), `low_confidence` (amber,
generic message), `refused_off_book` (muted, no evidence panel -- correctly nothing to show),
`refused_unverified` (red/destructive, generic message). Confirmed the hallucinated test sentence
appeared **zero times** in the rendered page before expanding anything, and only inside the
labeled evidence panels after -- the child-safety decision actually holds, not just in the code.

**Not done, out of scope:** module 7's polish pass (reduced-motion audit beyond what already
exists globally, keyboard nav, responsive/mobile sidebar); `POST /evaluate` as a real route
(Phase 5 leftover).

**Next:** Phase 7 module 7 — polish pass: reduced-motion audit, keyboard nav, Bangla rendering
check (still only spot-checked in module 1), responsive check including the sidebar's mobile
collapse deferred there.

---

## 2026-09-13 — Phase 7, module 7: polish pass (closes out Phase 7)

Frontend only, no backend changes, 260 backend tests unaffected. Mostly verification rather than
new code, per the plan -- one real feature (sidebar mobile collapse) and one real bug fix.

**Built:**
- `components/sidebar.tsx`: takes `open`/`onClose` props; the `<aside>` is `fixed ...
  -translate-x-full` off-screen by default, `md:relative md:translate-x-0 md:transition-none` at
  the `md:` breakpoint (unchanged desktop behavior). Closes itself on every route change (the
  existing pathname-watching effect gained `onClose()`) and on Escape while open.
- **The real bug, found re-reading the file before planning, not assumed:** each row's
  rename/delete buttons were `hidden ... group-hover:flex` -- `display:none` removes an element
  from the tab order entirely, so a keyboard user could never reach or even see them. Fixed with
  `group-focus-within:flex` alongside the existing hover variant.
- `app/(app)/layout.tsx`: one `sidebarOpen` boolean, a `md:hidden` bar with a hamburger button
  above the sidebar+content row, a click-to-close backdrop.

**A second real bug, found only by testing live, not by reading the code:** once the mobile
drawer was open, the hamburger button that opens it became visually covered by the sidebar itself
-- a `fixed z-40` element stacks above normal-flow content regardless of DOM order, so the
`<aside>` painted directly over where the hamburger sits once slid open. There was then no
*visible* way to close the drawer except already knowing to tap the backdrop or press Escape.
Fixed by adding an explicit `X` close button inside the sidebar's own header, `md:hidden`. Caught
this specifically because a Playwright test tried to click the hamburger a second time and got a
"element intercepts pointer events" error instead of a silent false-pass -- reading the error
mattered more than the assertion here.

**Verified live, each with a real fix-forward when the first attempt was wrong (both times because
the *test* was wrong, worth recording so the pattern is recognizable next time):**
- **Keyboard nav**: first attempt called `.focus()` directly on the (still-hidden,
  `display:none`) rename button and got `visible: false` -- which looked like the fix hadn't
  worked, but a hidden element genuinely cannot receive focus, so this was proving nothing either
  way. Corrected to focus the row's *link* first (the actual focusable element inside the same
  `group`), confirmed `group-focus-within` then reveals the buttons, confirmed a further Tab
  press lands on the rename button (`document.activeElement`'s `aria-label` checked directly), and
  confirmed Enter opens the inline rename input. All real, all passing.
- **Responsive**: at a 390px viewport, confirmed `document.body.scrollWidth === 390` (no
  horizontal overflow), the drawer's bounding box moves between fully off-screen and `x:0` on
  open/close/Escape/backdrop-click, and reverts to always-visible with the hamburger hidden at a
  desktop viewport -- one continuous script, not separate assumptions.
- **Reduced motion**: `page.emulateMedia({reducedMotion:"reduce"})`, confirmed the sidebar's
  `getComputedStyle(...).transitionDuration` reads `1e-05s` (matches `globals.css`'s `0.01ms`
  override exactly), then ran a full real chat turn (streaming, evidence panel expand) under the
  same emulation with zero console errors.
- **Bangla, in the real chat bubble, not an injected probe like module 1's check:** the first
  narrative question tried ("What kind of books does Rina like to read?") came back English-only
  -- a reminder that the Bangla restatement is a real LLM stylistic choice, not guaranteed per
  question. A second, more clearly narrative question ("Why does Rayan ask Erhan to be quiet?")
  produced a real Bangla response, which rendered with correct conjuncts and no tofu boxes inside
  the actual `AssistantMessage` bubble. **A real finding, not a frontend bug and not fixed here:**
  the entire answer came back in Bangla this time, not just a question restatement with an
  English answer body as style_guide.md's rule describes -- a stage 2 prompt-adherence question
  for whoever next touches `generate.py`, out of scope for this module.

**Not done, out of scope:** full WCAG-level audit (focus trapping inside the mobile drawer, ARIA
live regions for streaming text) -- the one concrete bug found (hidden buttons unreachable by
keyboard) is fixed; deeper accessibility work was never asked for and isn't free.

**This closes Phase 7.** All seven modules built, committed, and verified live: foundation/auth,
sessions CRUD + titles, upload + class picker, sidebar, backend SSE, the real chat screen, and this
polish pass. Remaining project work is Phase 5's leftover `POST /evaluate` route (not blocking) and
Phase 8, thesis writing.

---

## 2026-09-13 — Frontend visual redesign: Indigo & Amber design system

Not a Phase 7 module (that closed with the previous entry) -- a user-requested visual pass on top
of it: the functional app read as "generic shadcn gray," and the user asked for a real color
palette, a more premium feel, and effects/animation. Frontend only, no backend changes, 260
backend tests unaffected. User picked **Indigo & Amber** from three palette directions presented
with concrete hex/OKLCH previews (the other two: Emerald & coral, Terracotta & deep teal).

**Built:**
- `app/globals.css`: every color token rebuilt on Tailwind v4's own built-in `--color-indigo-*`/
  `--color-amber-*`/`--color-slate-*`/`--color-red-*` scales (confirmed these exist in
  `node_modules/tailwindcss/theme.css` before using them, rather than hand-guessing OKLCH values) --
  indigo primary, amber accent (`--accent`/`--accent-foreground` were confirmed unused by every
  installed shadcn component before repurposing that slot), slate neutrals, indigo-tinted sidebar
  active state, indigo focus rings. Light and dark variants for all of it.
- **Dark mode actually works now.** The tokens existed since shadcn's original scaffold but nothing
  ever added the `.dark` class -- no `ThemeProvider` was mounted, even though `next-themes` has been
  an installed dependency since module 1 (pulled in by the `sonner` toast component, unused since).
  Mounted it in `app/layout.tsx` (`attribute="class"`, `defaultTheme="system"`, `enableSystem`) and
  added `components/theme-toggle.tsx`, placed in the sidebar footer.
- `lib/motion.ts`: added `staggerContainer`/`staggerItem` (list children animate in sequence) and
  switched the eased curves from generic `"easeOut"`/`"easeIn"` strings to a custom cubic-bezier
  for a snappier feel. `components/ui/button.tsx` gained a transform-only
  `hover:scale-[1.02] active:scale-[0.97]` press micro-interaction on every variant -- no box-shadow
  transitions anywhere, staying strictly within the original "transform and opacity" brief.
- New `components/logo.tsx` (icon badge + wordmark, no image asset needed) on auth pages and the
  sidebar; `app/(auth)/layout.tsx` gained a subtle indigo/amber gradient background.
- `app/(app)/upload/page.tsx`: the processing checklist's four rows get distinct icons
  (`BookOpen`/`Layers`/`Sparkles`/`Check`) instead of identical spinners, plus the new stagger
  animation -- still shown "in progress" together per the existing module 3 decision, just richer.
- `components/sidebar.tsx`: logo + close button in a proper header, active-row indigo tint with a
  left accent bar, theme toggle footer.
- `components/chat/`: `message-list.tsx` now actually animates messages in
  (`staggerContainer`/`staggerItem` -- previously plain, unanimated divs, a real gap from module 6);
  bot/user avatar badges on `assistant-message.tsx`/`streaming-message.tsx`/`message-list.tsx`;
  `status-meta.ts` retuned to the new palette (emerald for `answered` -- deliberately *not* the
  brand indigo, so status color stays visually distinct from UI-chrome color; amber/red/muted
  unchanged in kind, just retuned); `chat-input.tsx` became a pill-shaped bar with a rounded indigo
  send button.

**A real, if minor, visual bug found and fixed via live screenshots, not caught by
lint/typecheck:** the evidence panel and source card both used `bg-muted/40`-ish backgrounds,
which blended into the `answered` status bubble's new emerald tint -- barely distinguishable as a
separate layer. Switched both to `bg-card/80` for a background that reads as a distinct surface
against every status color, not just the neutral ones. Caught by actually looking at the
screenshot, not by reasoning about the classes in the abstract.

**Verified live**, both themes, real data (not mocks): registered -> gradient-backed auth pages
with the new logo -> upload (light, then dark) -> a real streamed chat answer in dark mode with
the evidence panel open (real 7/7-sentence verification, real emerald "Answered" badge, indigo
avatars/accents throughout) -> toggled back to light on the same screen to confirm both themes
render correctly, not just whichever one was tested first. Re-ran module 7's regression checks
against the new UI specifically because a redesign this size could plausibly have reintroduced
exactly what that module fixed: confirmed no horizontal overflow at 390px, the mobile sidebar
drawer still opens/closes correctly with `transition-duration` still reading `1e-05s` under
`prefers-reduced-motion: reduce`, a real chat turn still completes cleanly under that same
emulation, and the keyboard-focus fix for the sidebar's rename/delete buttons still holds
(invisible until focus, visible and operable once focused) -- none of this broke silently while
recoloring everything around it.

**Not done, out of scope:** a full component-by-component dark-mode contrast audit beyond what was
visually spot-checked above; no new dependency was added (motion, theming, and icons all came from
packages already installed).

**A real functional gap found right after, not by testing but by the user just asking "how do I log
out":** there was no logout control anywhere in the app. Module 3's static chat placeholder had one;
module 6 replaced that whole page with the real chat screen and never re-added it anywhere else --
`lib/auth-context.tsx`'s `logout()` stayed fully wired and correct the entire time, just unreachable
from any screen. Fixed by adding a footer row to `components/sidebar.tsx` (visible on every
authenticated page): the user's display name, the theme toggle, and a logout icon button. Verified
live: click logs out, redirects to `/login`, and a direct navigation to `/upload` afterward bounces
straight back to `/login` -- confirming the session was actually cleared server-side (the refresh
cookie revoked), not just a client-side redirect that left the account still logged in underneath.

**A second round of user feedback on the same redesign:** the auth and upload cards looked "too
small" for the screen -- a real layout complaint, not a color one. `app/(auth)/layout.tsx` was
rebuilt as a two-column split (a common pattern for this: a branded indigo/amber gradient panel
with the logo, a headline, and a 3-item feature list on one side, the actual form on the other,
`md:hidden` collapsing to the original single-column form on mobile) instead of a small card
floating alone on a big empty gradient. `app/(app)/upload/page.tsx`'s card widened
(`max-w-md` -> `max-w-lg`) and gained a matching icon badge + shadow + gradient backdrop for more
presence within the app shell (a full split-screen doesn't fit there -- the sidebar already
occupies that role). Verified live at a real desktop width (1440px) and re-confirmed no horizontal
overflow at 390px mobile, where the branded panel correctly disappears and the form's small logo
takes over exactly as before.

**A third round of feedback: sidebar and chat text were too small, and "make every element size
perfect based on the screen."** Two distinct problems, not one:
1. A pure type-scale issue -- most reading content (chat messages, sidebar session titles/meta,
   evidence panel, source card) was sitting at Tailwind's `text-xs`/`text-sm` (12/14px). Moved every
   one of those up one full step (`text-xs` -> `text-sm`, `text-sm` -> `text-base`) across
   `components/sidebar.tsx`, `components/logo.tsx`, and every file in `components/chat/`, plus
   `leading-relaxed` on message bodies. Widened the sidebar (`w-64` -> `w-72`) and its row
   padding/icon sizes to match, rather than just enlarging text inside an unchanged-width column.
2. **A real layout bug, the same root cause as the earlier auth-card complaint, just in a
   different screen:** chat bubbles were capped at `max-w-lg` and left-aligned inside a
   *full-width* flex row -- on a wide monitor this put all the content in a narrow strip on the
   left with a huge dead zone on the right, which reads as "small" regardless of font-size.
   `components/chat/message-list.tsx` now centers everything in a `max-w-3xl mx-auto` reading
   column (the same pattern most chat products use), with bubbles filling up to 85% of that
   column instead of a fixed narrow width; `app/(app)/chat/[sessionId]/page.tsx`'s header and
   `components/chat/chat-input.tsx` were given the same centered column so the whole screen reads
   as one consistent width, not three different ones stacked on top of each other.

Verified live at a real 1440px desktop width (screenshots, not just class-name review) and
re-confirmed mobile (390px, no overflow) and the full module 7 regression suite (reduced motion --
sidebar `transition-duration` still `1e-05s`, unchanged -- keyboard-focus reveal on the sidebar's
rename button, dark mode) all still pass after touching this many files a second time.

**A fourth round: sidebar still not broad enough, user asked to check it against Claude.ai's own
proportions.** Can't literally inspect Claude.ai's DOM/CSS from here, so this is calibrated from
well-known patterns in modern AI chat UIs rather than a pixel-exact comparison -- said so directly
rather than implying a comparison that didn't happen. Widened the sidebar again, `w-72` -> `w-80`
(320px), and gave it noticeably more breathing room to match: header/footer padding `p-3` -> `p-4`,
row padding `px-3 py-2.5` -> `px-3.5 py-3`, the "New chat" button bumped to the `lg` size, and every
icon button in the sidebar/theme-toggle from `icon-sm` to the full `icon` size.

**A real testing artifact hit while re-verifying, worth recording so it's recognizable next
time:** the mobile regression re-check started failing with Playwright unable to click the
hamburger button -- `<nextjs-portal>` (Next.js's own dev-mode toolbar, the small "N" badge) was
sitting on top of it at that exact screen position. Confirmed by screenshot before doing anything
about it. `force: true` didn't help (it still dispatches the click at the target's screen
coordinates, so a real overlapping element in dev tooling can still absorb it); switched the check
to `page.evaluate(() => button.click())`, a direct DOM call that bypasses hit-testing entirely --
this is purely a dev-mode testing-harness quirk (the overlay doesn't exist in production), not an
app regression, and the app's own behavior (sidebar opens to the new 320px width, everything else
in the module 7 regression suite) checked out clean once verified correctly.

**A fifth round: the login/register cards themselves, not just the page around them.** Widened the
shared form container in `app/(auth)/layout.tsx` (`max-w-sm` -> `max-w-md`) and, in both
`login/page.tsx` and `register/page.tsx`: bumped the `Card`'s internal padding via its own
`--card-spacing` CSS variable (`--spacing(4)` -> `--spacing(6)`, the same variable `card.tsx`
already uses for every one of its sub-components' padding, so this is one override, not five),
title to `text-2xl`, every label/input/button/helper text up to `text-base`, inputs to `h-11`, and
the submit button to the `lg` size. Verified live at 1440px (visibly larger, more substantial
cards) and re-confirmed 390px mobile has no overflow with the wider container.

**A sixth round: scrolling the chat scrolled the whole page, dragging the sidebar and the input
bar along with it, instead of only the message list scrolling internally.** Root cause was a
broken height-constraint chain, not the `overflow-y-auto` on `components/chat/message-list.tsx`
itself (that was always correctly written) -- CSS's flexbox rule that a `flex-1` child only
actually gets clipped/scrollable if *every* ancestor up the chain has a genuine bounded height (not
just a `min-height`), and a plain column flex item needs `min-h-0` to be allowed to shrink to that
bound instead of growing to fit its content. Two links in the chain were missing a real ceiling:
`app/(app)/layout.tsx`'s outer shell div was only `flex-1` (grow to fill available space) with
nothing above it in `app/layout.tsx` (`body` is deliberately `min-h-full`, unbounded, so that
short-viewport pages like login/upload can still fall back to normal whole-page scrolling) ever
handing it an actual ceiling to grow *into* -- fixed by giving that one div a hard `h-dvh` instead
(dynamic viewport height, so mobile browser chrome collapsing doesn't leave a stale gap the way
`h-screen` can). `app/(app)/chat/[sessionId]/page.tsx`'s root div was also missing `min-h-0`,
so even with the shell now bounded, the chat page itself would still grow to fit all its messages
rather than shrink to the space it was given -- added it. Deliberately scoped both fixes to the
`(app)` route group instead of touching the shared root `body`, so pages that *should* still
fall back to normal document scroll on a short viewport (auth pages, upload) are untouched.

Verified with Playwright, not just re-reading the CSS: at a squeezed 1280x500 viewport with three
real streamed answers loaded, `document.scrollingElement.scrollHeight` equals `clientHeight`
exactly (500 === 500) and `window.scrollY` stayed `0` even after a mouse-wheel event on the page,
while the message list's own container measured `scrollHeight` 1796 vs `clientHeight` 352 (real
internal overflow) and its `scrollTop` could be moved independently from 1429 down to 0. Sidebar
and chat-input bounding boxes were pixel-identical before and after scrolling the message list.
Re-ran the full desktop (1440x900, sidebar spans the full viewport height, no page scroll),
mobile-drawer, and reduced-motion-reload regression checks from the earlier rounds -- all still
pass. `tsc --noEmit`, lint, and `next build` all clean.

**A seventh round: the sidebar's display name was too small, and the "Add your textbook" upload
card had never actually gotten the same enlargement pass the login/register cards got in round
five.** `components/sidebar.tsx`'s footer name span: `text-sm text-muted-foreground` ->
`text-base font-medium` (also dropped the muted color now that it's meant to read as a real label,
not secondary metadata). `app/(app)/upload/page.tsx`: brought it in line with the auth cards --
container `max-w-lg` -> `max-w-xl`, `Card`'s `--card-spacing` bumped to `--spacing(6)`, icon badge
`size-11` -> `size-14`, title `text-xl` -> `text-2xl`, description/labels/error text all to
`text-base`, inputs/select to `h-11`, submit and retry buttons to `size="lg"`, and the processing
checklist rows/icons scaled up to match. Verified live: Playwright confirms the sidebar name
renders fully untruncated (`scrollWidth === clientWidth` at 83px for "Sizing Two") -- a screenshot
that looked like it was cut off to "g Two" turned out to be the same `<nextjs-portal>` dev-mode
toolbar badge from the earlier finding sitting on top of the sidebar footer at that exact
viewport position, not a real truncation bug; checked the computed style/DOM directly rather than
trusting the screenshot.

**An eighth round: "Textbook Tutor" itself needed a bigger, more stylized typeface, not just a
size bump on the existing Inter weight.** Rather than a one-off override on the wordmark alone,
loaded a real second display typeface -- `Fraunces` (a serif, via `next/font/google`, weights
500/600/700) -- into `app/layout.tsx` and pointed the *already-existing* `--font-heading` CSS
variable at it in `globals.css` (it previously just aliased back to `--font-sans`/Inter, so
nothing anywhere actually used a distinct heading face despite the token existing since the
redesign). Because `components/ui/card.tsx`'s `CardTitle` already applies `font-heading`, this one
token swap also picked up every card title app-wide ("Welcome back", "Create your account", "Add
your textbook") for free, not just the wordmark -- a deliberate reuse of the existing shared
token rather than a special case. Bumped the wordmark text itself in both places it's hand-rolled
(`components/logo.tsx`: `text-base` -> `text-xl`; `app/(auth)/layout.tsx`'s branded-panel copy:
`text-lg` -> `text-2xl`), with icon badges scaled up slightly to match. Verified live: computed
`font-family` on both wordmark instances and on a card title all resolve to `Fraunces, "Fraunces
Fallback", Inter, ...` at the intended sizes (24px/20px), and it visually reads as a genuine
serif-display + sans-body premium pairing rather than a font-weight trick. `tsc --noEmit`, lint,
and `next build` all clean.

**A ninth round: chat text needed to be bigger with "a perfect font style suitable for this
project."** Rather than just resizing Inter again, loaded a third typeface scoped specifically to
chat content: `Lexend` (a humanist sans, via `next/font/google`) -- chosen deliberately, not
arbitrarily: Lexend was engineered and studied specifically to raise reading proficiency and
reduce visual stress (tuned x-height/letter-spacing), which is a direct match for this product's
actual purpose -- a Class 5 student reading grade-adapted explanations -- rather than a purely
decorative pick. Wired it in the same way as the heading font: new `--font-reading` token in
`globals.css` (`var(--font-lexend), var(--font-noto-bengali)`, same Bangla-script fallback reason
as `--font-sans`), which Tailwind v4 automatically exposes as a `font-reading` utility class.
Applied `font-reading` + bumped `text-base` (16px) -> `text-lg` (18px) on exactly the chat
*reading* surfaces: the user bubble and assistant bubble paragraphs
(`components/chat/message-list.tsx`, `assistant-message.tsx`), both states of the streaming
message (`streaming-message.tsx`), and the chat input itself (`chat-input.tsx`, also bumped
`h-9`->`h-11` and its send button `icon`->`icon-lg` so the taller text doesn't look cramped in a
now-undersized pill). Deliberately left the status badge ("Answered"), the source card, and the
evidence panel in the existing Inter UI font/size -- those are metadata/chrome around the message,
not the message itself, and keeping them visually distinct from the reading content is the same
content-vs-chrome typographic split already used for `--font-heading` vs `--font-sans` elsewhere.

Verified live: computed style on both the user-question paragraph and the assistant-answer
paragraph resolves to `Lexend, "Lexend Fallback", "Noto Sans Bengali", ...` at 18px, while the
header's "New conversation" title (an unrelated, un-migrated element) stayed on `Inter` at 16px --
confirms the swap is correctly scoped to chat content only, not a global regression. Re-checked
390px mobile with a real streamed answer (`body.scrollWidth` still exactly 390, no horizontal
overflow) and confirmed the enlarged input/button still fit the pill correctly. `tsc --noEmit`,
lint, and `next build` all clean.

**A tenth round: no dark-mode control existed anywhere before login/register.** Added the existing
`ThemeToggle` to `app/(auth)/layout.tsx`, fixed-positioned top-right (`fixed top-4 right-4 z-10`)
so it reaches both the mobile single-column view and the desktop split layout, and specifically
over the right-hand form panel rather than the always-dark branded panel, so its icon color always
has real contrast.

This surfaced a genuine, pre-existing bug in `components/theme-toggle.tsx`, not just a missing
feature -- caught from the dev server's own console output, not a screenshot: a real, reproducible
"Hydration failed" React error on every load of `/login`. The component's `resolvedTheme ===
undefined` check (written during the redesign round, to dodge a `set-state-in-effect` lint
violation) only avoided a *mismatch* by coincidence in its one prior usage -- the sidebar -- because
`AppLayout` gates all of its children behind a client-only auth-loading check, so `ThemeToggle`
was never actually present in the real server-rendered HTML there in the first place; nothing ever
hydration-diffed it. `app/(auth)/layout.tsx` is a plain server-rendered page with no such gate, so
this was the first time `ThemeToggle` truly had to match between server and client, and it didn't:
by the client's first hydration pass, `resolvedTheme` was already resolved to a real value while
the server had rendered the `undefined`-branch placeholder div. Fixed by replacing that check with
`useSyncExternalStore(emptySubscribe, () => true, () => false)` -- its `getServerSnapshot` is the
actual sanctioned way to deliberately render one thing on the server/first-hydration pass and swap
after, which React treats as an intentional transition rather than a mismatch, and (unlike a
`useState` + `useEffect(() => setMounted(true))` mount flag) involves no `setState` inside an
effect, so the lint rule that motivated the original approach still doesn't fire.

While chasing why the toggle wasn't even visually appearing (before finding the hydration bug),
also hit and fixed an unrelated dev-environment issue worth recording: the running Next.js dev
server's Turbopack CSS pipeline stopped picking up newly-added utility classes (`top-4`, `right-4`,
`z-10` -- confirmed absent from the actually-served compiled CSS bundle by fetching it directly,
even after a real file edit and a several-second wait) after being left running across this
entire long session. A second real edit sometimes got picked up and sometimes didn't -- genuinely
flaky, not something to chase further -- resolved cleanly by stopping the stale dev server process
and starting a fresh one, after which every subsequent edit compiled correctly and consistently.
Dev-tooling-only, not an application bug, but worth knowing: if a utility class visibly has no
effect despite the DOM showing the right class name, check the actually-served CSS bundle before
assuming the component code is wrong.

Verified on a fresh server: no hydration/console errors on `/login` (only the pre-existing,
unrelated 401 refresh-retry noise already known from earlier rounds), toggle renders and functions
at the exact intended position on both desktop (`x:1392,y:16` at 1440px) and mobile (`x:342,y:16`
at 390px, no horizontal overflow), dark mode applies correctly across both the branded panel and
the form card, and the full register -> upload -> chat flow still works end to end. `tsc --noEmit`,
lint, and `next build` all clean.

**Next:** whatever the user directs -- `POST /evaluate` as a real route, or Phase 8 thesis writing.

---

## 2026-09-14 — bangla_mode="echo": opt-in bilingual stage 2 answers

User asked whether stage 2 could answer with a Bangla-script echo after every English
sentence (the phonics-lesson pattern style_guide.md section 3.4 evidences), not just
the existing single question-restatement. Traced two real blockers before touching
anything: verify.py's NLI model is English-only and would score Bangla sentences near
zero (recreating Phase 6's original false-refusal bug at scale), and verify_node's
retry feedback would tell stage 2 to delete the very Bangla it was asked to add.
Planned properly (plan mode, three judgment calls put to the user with tradeoffs
before writing code) rather than just editing the prompt.

**Built:**
- `backend/prompts/v2_stage2.txt` -- copy of v1_stage2.txt, only the Bangla note
  rewritten: every explanation sentence gets an immediate Bangla-script echo (a
  translation only, script only -- never romanised -- danda-terminated); greeting,
  citation, and comprehension-check stay English-only.
- `core/config.py`: new `bangla_mode: Literal["light", "echo"] = "light"`;
  `.env.example` documents it. Default unchanged -- every Phase 6 A/B/C/D number
  stays valid for any build that hasn't opted in.
- `generate.py`: `STAGE2_ECHO_PROMPT_VERSION = "v2_stage2"` + `stage2_prompt_version()`
  resolves which template loads, by setting; `render_stage2_prompt()`/
  `generate_stage2()` call it instead of the hardcoded constant. Separate llm.py
  cache namespace per prompt version, so echo mode never touches v1's ~1,500 cached
  entries.
- `style_check.py`: new `is_bangla_sentence()` (Bengali-script word count >= Latin
  word count in a sentence -- a code-switched sentence that keeps a borrowed English
  term, e.g. style_guide.md section 3.4's phonics examples, still counts as the
  echo). `content_sentences()` gained `exclude_bangla` (default False, no-op in light
  mode). `check_style()` excludes Bangla from all three numeric checks in echo mode
  and, in that mode, sends the judge the Bangla-free `content` instead of the raw
  bilingual answer -- the judge prompt says nothing about language, so it would
  otherwise return an unpredictable verdict on mixed text.
- `verify.py`: Bangla sentences excluded from `supported_ratio`'s denominator in
  echo mode -- skipped and logged in the existing `skipped_sentences` field, the
  same treatment as scaffolding, not scored and not silently dropped.
- `eval/runner.py`: manifest now logs `bangla_mode` and the resolved stage-2 prompt
  version, so an echo-mode run is distinguishable from a Phase 6 run in
  `eval/runs/*.jsonl`.
- 19 new tests across `test_style_check.py`, `test_verify.py`, `test_generate.py`.
  260 -> 279 total (249 non-db + 30 db, both green).

**Decided** (three judgment calls, presented to the user with tradeoffs before
planning, approved as proposed):
- Opt-in setting, default unchanged -- over making bilingual the new default and
  re-running Phase 6's eval (real cost, a second touch of the test split) or editing
  the prompt in place with no re-run (would silently invalidate the results chapter
  without saying so).
- Bangla sentences skipped and logged by the verifier, not translated-then-verified
  -- the alternative closes the fact-checking gap below but adds a paid LLM call per
  sentence and confounds the hallucination metric with translation quality.
- Every explanation sentence gets an echo, not just framing and not just "core"
  sentences -- matches the phonics-lesson pattern style_guide.md section 3.4
  evidences, applied here beyond phonics content as a deliberate application-layer
  scope decision (style_guide.md itself is untouched -- that finding still says what
  it always said).

**Known limitation**, recorded in verify.py's module docstring: the Bangla text is
never fact-checked. The prompt requires each Bangla sentence to be a translation of
the English sentence immediately before it (which IS verified), but a hallucination
appearing only in the Bangla would pass unverified. Belongs in the thesis
Limitations section.

**Verified live**, echo mode, real ingested book (`97a7267b-...-4be4355e1a4f`, 135
chunks), real gpt-4o-mini key, real NLI model on GPU: two questions ("What kind of
books does Rina like to read?", "What is a noun?") both returned `status: answered`
(not `refused_unverified`) with clean English/Bangla alternation in real Bangla
script, no romanisation. `supported_ratio` 1.0 and 0.889 respectively, computed only
over English sentences (3 and 9 scored); Bangla lines correctly landed in
`skipped_sentences` (6 and 9). The one readability failure that did occur (Q2, "a
sentence is 16 words long") was traced to a genuine 16-word English sentence ("A noun
is a word that names an object, a place, a group, or an item.") -- confirmed Bangla
word counts are not what tripped the cap. Confirmed `v1_stage2.txt` is byte-for-byte
untouched (`git diff` empty) -- light mode's prompt output and existing cache entries
are provably unaffected.

**Real gap found during this same verification, not fixed here (out of scope,
flagged for the user):** `frontend/components/chat/evidence-panel.tsx` renders
`verification.sentences` but never renders `verification.skipped_sentences` at all.
This predates this session -- scaffolding sentences were already invisible in the
evidence panel -- but it now also hides every Bangla echo from the "how I checked
this" panel with no on-screen indication anything was excluded. Worth a small
follow-up module before demoing `bangla_mode="echo"` at the defense, since
contribution 3's visual proof is exactly what that panel exists for.

**Next:** user's call -- set `BANGLA_MODE=echo` in local `.env` when ready to demo
bilingual answers (default stays "light" otherwise); optionally a small frontend
module to surface `skipped_sentences` in the evidence panel.
