# PROMPTS.md — Claude Code session script

Copy-paste prompts, one session at a time. Do not skip ahead.

## Setup, once

```
cd textbook-tutor
git init
claude
/model opusplan
```

`opusplan` uses Opus only while you are in **plan mode** (press Shift+Tab until
the footer says "plan mode"). Outside plan mode it uses Sonnet. So the rhythm
for every phase is:

1. Shift+Tab into plan mode → paste the phase prompt → Opus writes a plan
2. Read the plan. Push back if anything contradicts CLAUDE.md.
3. Approve → Claude Code drops to execution mode → Sonnet implements
4. Read the code. Ask questions until you could explain it yourself.
5. Say "update NOTES.md and give me a commit message" — then you commit

If a phase is too big to plan in one go, plan one module at a time.

---

## Session 0 — Orientation

Not in plan mode. Just:

```
Read CLAUDE.md and docs/project-guidelines.md fully. Then tell me in 10 lines what
we are building, what the fixed stack is, and what Phase 1 delivers. Do not write
any code.
```

If its summary is wrong, fix CLAUDE.md before going further.

---

## Session 1 — Foundation

Before this session, put the NCTB PDF in `data/textbook/`.

Plan mode:

```
We are starting Phase 1 from CLAUDE.md. Plan the foundation: docker-compose with
Postgres + pgvector, Alembic with the five tables from the schema section, a
FastAPI skeleton with /health and /docs, and config via environment variables.
Then plan pipeline/ingest.py exactly as the chunking rules describe, and
POST /api/v1/books as a 202 background task with status polling. Show me the
plan before writing anything.
```

After it runs, verify by hand:

```
Start docker-compose, run the migrations, upload data/textbook/*.pdf through
/docs, and show me 20 chunks from the database with their unit, lesson,
type and the first 100 characters of text.
```

Read those 20 chunks yourself. If lesson boundaries are wrong, fix now.

---

## Session 2 — Bare pipeline + evaluation harness

Plan mode:

```
Phase 2. Plan pipeline/retrieve.py (hybrid dense+sparse from bge-m3, reciprocal
rank fusion, expand to parent lessons), pipeline/llm.py (single provider-agnostic
call with disk cache keyed on prompt_version+model+input hash),
pipeline/generate.py stage 1 only with prompts/v1_stage1.txt, and graph.py with
two nodes. Wire POST /sessions/{id}/messages. Then plan eval/runner.py: takes a
questions.jsonl and a config name, runs every question through pipeline/, writes
results to JSONL and to the messages table with config_version, and prints
retrieval hit rate and RAGAS faithfulness. Show the plan first.
```

Then:

```
Write 30 rough test questions into data/question_set/questions.jsonl with
fields id, question, lesson_id, reference_answer, type, split. Run
eval/runner.py --config baseline and show me the table.
```

That number is your baseline. Write it in NOTES.md.

---

## Session 3 — Grade voice

Before this session, `data/style_guide/style_guide.md` §2 and §3 must be filled
and `fewshot_examples.jsonl` must have real examples. See Phase 0 below.

Plan mode:

```
Phase 3. Plan generate.py stage 2 with prompts/v1_stage2.txt: grade as a
template variable, rules loaded from data/style_guide/style_guide.md, few-shot
examples loaded from fewshot_examples.jsonl, refuse synthetic rows when
EVAL_MODE=1, and a hard constraint that stage 2 may only rephrase stage 1 and
never add facts. Then style_check.py: sentence length, vocab coverage against a
word list built from the chunks table, Flesch-Kincaid on English only, an LLM
judge returning JSON, retry max 2 with the judge reason fed back. Then
rewrite.py for follow-up questions with a heuristic to skip standalone ones,
and session history of the last 6-8 turns. Show the plan first.
```

Verify:

```
Ask "What is a noun?" then "give me more examples" through the API. Show me
both answers, the standalone query that rewrite.py produced, and the style_check
report for each.
```

---

## Session 4 — Verifier

Plan mode:

```
Phase 4. Plan pipeline/verify.py: split the answer into sentences, run
cross-encoder/nli-deberta-v3-base against retrieved chunks, keep max entailment
per sentence. Add the off-book gate before generation. Add the conditional edge
in graph.py: supported → answered, partial → regenerate once → else
refused_unverified. Store the verification JSON on every message. Make the
threshold a config value, not a constant. Show the plan first.
```

Verify:

```
Run 10 answerable and 5 off-book questions. Show me the status of each and the
per-sentence entailment scores for two of them.
```

Tune the threshold on the dev split only. Write the chosen value and why in NOTES.md.

---

## Session 5 — API completeness

Plan mode:

```
Phase 5. Plan the SSE streaming endpoint with typed events, sessions list /
rename / soft-delete, JWT auth exactly as CLAUDE.md specifies with
ALLOW_ANONYMOUS for dev, background title generation, POST /api/v1/evaluate
calling the same runner, and pytest for the pipeline/ functions. Show the plan
first.
```

---

## Session 6 — Test set and experiments

Not code-heavy. Mostly you.

```
Help me expand questions.jsonl to 150: ~120 answerable across all units, ~30
off-book that sound like school questions. Keep the split field. Then run all
five configurations from the guidelines through /evaluate and produce one
results table with RAGAS faithfulness, hallucination rate, refusal accuracy,
false refusal rate, and readability per config.
```

Then error analysis:

```
Pull 30 failures from the messages table. Categorise each as: wrong chunk,
right chunk wrong answer, correct but too hard, correct but not Bangladeshi
in style, wrongly refused, wrongly answered off-book. Give me the table.
```

Freeze the test split. Do not touch the pipeline after this.

---

## Session 7 — Frontend

Only after Session 6 results are written down.

Plan mode:

```
Phase 7. Plan the Next.js frontend per CLAUDE.md: App Router, TypeScript,
Tailwind, shadcn/ui, Framer Motion, Noto Sans Bengali. Screens: register/login,
upload with staged progress, class picker, chat with SSE streaming, a source
lesson card under each answer, distinct designs for the four answer statuses,
and a sidebar from GET /sessions. Keep animations to transform and opacity and
respect prefers-reduced-motion. Show the plan first.
```

---

## Phase 0 — Style guide (runs alongside Sessions 1–2)

Transcription is a long job. Run it in a normal terminal, not in Claude Code:

```
pip install openai-whisper textstat
python tools/style_guide/transcribe.py --media-dir data/style_guide/videos --out data/style_guide/transcripts
python tools/style_guide/measure_style.py --transcripts data/style_guide/transcripts --md
```

Then in Claude Code, read the teacher's guide first — it is the highest-authority
source and it shapes what you look for in the transcripts:

```
Read data/style_guide/teacher_guide/class5_shikkhok_sohayika_2026.pdf. It is the
NCTB Class 5 teacher's guide for English for Today, written in Bangla. For 5
lessons, extract: how the teacher is told to introduce the topic, what questions
to ask the class, what answers are expected, and anywhere it instructs using
Bangla versus English. Give page numbers for everything. Do not edit the style
guide yet.
```

Then, still not in plan mode:

```
Read every file in data/style_guide/transcripts/. These are Bangladeshi teachers
teaching Class 5 English. Propose the structural patterns for section 3 of
data/style_guide/style_guide.md: opening move, example placement, when Bangla
appears, closing move. For every pattern give a quoted line with the filename.
Never invent a quote. Do not edit the file yet — show me the proposals and I
will decide which go in.
```

You decide. Then:

```
Write the patterns I approved into section 3 with their evidence. Paste the
measure_style.py table into section 2. Then find 5 passages where a teacher
explains one thing clearly and show them to me as candidate few-shot examples.
```

You pick the 5. Hand-fix their transcription errors. Then:

```
Write the 5 I chose into fewshot_examples.jsonl with provenance real and
source_id. Delete every synthetic row.
```

Set aside 3-4 transcripts as held-out evaluation references. Note which in
style_guide.md section 1.

---

## Every session ends with

```
Append a dated entry to NOTES.md: what was built, what was decided, any number
that changed. Then list the files you changed and propose a commit message. Do not
run git — I commit myself.
```

---

## When to leave opusplan

Stuck on a hard bug during execution: `/model opus`, fix it, `/model opusplan`.
Long mechanical work like writing 100 test questions: `/model sonnet` is fine.
