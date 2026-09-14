"""
What: the two-stage generation CLAUDE.md specifies. Stage 1 (Phase 2) is a
      plain factual answer grounded only in the retrieved+expanded lesson
      text, via prompts/v1_stage1.txt. Stage 2 (Phase 3) rewrites that
      answer in a Class-N Bangladeshi teacher voice, via prompts/v1_stage2.txt.

Why two stages, and why grade never appears in stage 1: stage 1 is checked
      for correctness, stage 2 rewrites it in a Class-N teacher voice and
      must never introduce a fact stage 1 didn't produce. Keeping the grade
      variable out of v1_stage1.txt entirely is what makes that "stage 2
      added no new facts" check meaningful — stage 1's output cannot already
      be grade-flavoured.

Why stage 2 never receives context_chunks: it only sees the question and
      the stage-1 answer. If it structurally cannot read the book, it
      structurally cannot add a book fact stage 1 didn't already produce —
      that is what makes the "no new facts" constraint enforceable rather
      than just a prompt instruction hoping the model complies.

Why few-shot examples carry only (question, teacher_answer), not the
      textbook_passage they were sourced from: one example's teacher_answer
      names a character absent from its own passage (the video and the
      currently-ingested book are different editions — see style_guide.md
      note under Sources). Showing the passage next to that answer would
      model exactly the fact-invention stage 2 is forbidden from doing, so
      the passage field is dropped before the pair reaches the prompt.

Why Bangla is restricted to a single question restatement here: the style
      guide's own §3.4 finding is Bangla usage that scales with lesson
      difficulty, and the strongest evidence is grade 5 comprehension
      questions being restated in Bangla script before an English answer.
      A full bilingual answer is a bigger claim than the evidence supports
      today, so v1_stage2.txt asks for at most one restatement, in Bangla
      script (never the transcripts' Whisper-romanised Latin spelling).

Why heavier bilingual echoing is a separate opt-in prompt (v2_stage2.txt,
      selected via settings.bangla_mode="echo") rather than a rewrite of the
      paragraph above: making every explanation sentence carry a Bangla echo
      is only evidenced by style_guide.md §3.4 for phonics/pronunciation
      lessons, not the general case — promoting it to the default would
      outrun the transcript evidence, which is exactly what the style-guide
      process (CLAUDE.md) exists to prevent. It also isn't just a prompt
      change: verify.py's NLI model is English-only and cannot score a
      Bangla sentence, so echo-mode Bangla sentences are skipped from
      verification and logged, never fact-checked (an accepted limitation —
      see verify.py's module docstring); style_check.py's numeric checks
      must exclude them the same way scaffolding is already excluded, or a
      long Bangla echo would trip the 14-word sentence cap the "light"
      numbers were measured against. stage2_prompt_version() resolves which
      template loads; the two prompt files sit in separate llm.py cache
      namespaces, so flipping BANGLA_MODE never touches the other mode's
      cached calls, and every Phase 6 A/B/C/D result — measured under the
      untouched "light" default — stays reproducible.

Why load_style_rules() can drop section 3.1 (opening move) per call: §3.1
      documents how a teacher opens a whole LESSON — greeting, then
      (usually) a song, then naming the unit/lesson/page — once, at the
      start of a video that runs many questions long. Applying that
      wholesale to every chatbot turn made stage 2 replay the greeting and
      "let's sing a song" on follow-up questions mid-conversation, which is
      wrong on its face (caught live: asking "give me more examples" right
      after a first question still opened with a song). graph.py now passes
      `is_first_turn`; only the first turn of a session gets the greeting +
      lesson-framing instruction, and a song is forbidden outright, on every
      turn, regardless — this project has no audio, so §3.1's song finding
      has nothing to render into. The evidence itself is left untouched in
      style_guide.md; this is a scoping decision made in the application
      layer, not a correction to the research finding.

Why render_stage2_prompt() takes source_citation as a plain string, not a
      Chunk (Phase 6 fix, found via dev-split error analysis): the opening-
      move rule above tells stage 2 to name the unit/lesson/page, but stage
      2 has no context_chunks to read one from — so, unobserved until a
      120-question run surfaced it, the model was inventing a citation
      outright, and always the same two fabricated ones ("Unit 3, Lesson 2,
      page 45" / "Unit 5, Lesson 2, page 45") regardless of the real source.
      Passing the true citation as a formatted string (built in graph.py
      from retrieval.top_chunks[0], the single best-matching chunk) is not a
      book fact stage 1 must first produce — it's retrieval metadata the
      system already has independent of any LLM call, the same status as
      the sources array a message stores. Stage 2 still cannot read the
      book's content; it can now just correctly name where the content it
      was already given came from.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.pipeline.llm import LLMResult, call_llm

STAGE1_PROMPT_VERSION = "v1_stage1"
STAGE2_PROMPT_VERSION = "v1_stage2"
STAGE2_ECHO_PROMPT_VERSION = "v2_stage2"
PLAIN_LLM_PROMPT_VERSION = "v1_plain_llm"

# backend/app/pipeline/generate.py -> parents[2] is backend/, where
# prompts/ lives (matches the repo layout in CLAUDE.md).
_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

# backend/app/pipeline/generate.py -> parents[3] is the repo root (same
# depth as llm.py's _REPO_ROOT).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_STYLE_GUIDE_PATH = _REPO_ROOT / "data" / "style_guide" / "style_guide.md"
_FEWSHOT_PATH = _REPO_ROOT / "data" / "style_guide" / "fewshot_examples.jsonl"


def load_prompt(version: str) -> str:
    return (_PROMPTS_DIR / f"{version}.txt").read_text(encoding="utf-8")


def format_context(chunks: list[Chunk]) -> str:
    """Numbers each chunk for the model to tell separate passages apart.
    Every chunk's stored text already starts with its own
    'Unit N, Lesson M: Title' header (see ingest.py's chunk_header()), so
    this only needs to join them, not re-derive any structure."""
    return "\n\n".join(f"[{i}] {chunk.text}" for i, chunk in enumerate(chunks, start=1))


def render_stage1_prompt(question: str, context: str) -> str:
    template = load_prompt(STAGE1_PROMPT_VERSION)
    return template.format(question=question, context=context)


@dataclass(frozen=True)
class Stage1Result:
    answer: str
    llm: LLMResult


def generate_stage1(question: str, context_chunks: list[Chunk], *, provider: str = "openai") -> Stage1Result:
    context = format_context(context_chunks)
    prompt = render_stage1_prompt(question, context)
    result = call_llm(prompt, prompt_version=STAGE1_PROMPT_VERSION, provider=provider)
    return Stage1Result(answer=result.text, llm=result)


# --- config A: plain LLM, no book, no grade voice, no verifier -----------
# Phase 6's ablation baseline (project-guidelines.md 8.2, config "A. Plain
# LLM") — the question goes straight to the LLM with no retrieved context
# and no teacher-voice framing, so the results table has something to show
# the other three contributions improve on.


@dataclass(frozen=True)
class PlainLLMResult:
    answer: str
    llm: LLMResult


def render_plain_llm_prompt(question: str) -> str:
    template = load_prompt(PLAIN_LLM_PROMPT_VERSION)
    return template.format(question=question)


def generate_plain_llm(question: str, *, provider: str = "openai") -> PlainLLMResult:
    prompt = render_plain_llm_prompt(question)
    result = call_llm(prompt, prompt_version=PLAIN_LLM_PROMPT_VERSION, provider=provider)
    return PlainLLMResult(answer=result.text, llm=result)


# --- stage 2: grade-voice rewrite --------------------------------------


def stage2_prompt_version() -> str:
    """Which stage-2 template to load, per settings.bangla_mode. "light"
    (default) resolves to STAGE2_PROMPT_VERSION — byte-identical to every
    prompt used for the Phase 6 A/B/C/D results, so those numbers stay valid
    for any build that hasn't opted in. "echo" resolves to
    STAGE2_ECHO_PROMPT_VERSION (v2_stage2.txt); see the module docstring for
    why this is a separate file rather than a rewrite of v1's Bangla note."""
    settings = get_settings()
    return STAGE2_ECHO_PROMPT_VERSION if settings.bangla_mode == "echo" else STAGE2_PROMPT_VERSION


_TOP_SECTION_RE = re.compile(r"^## (\d+)\..*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*-\s")


def _top_sections(text: str) -> dict[int, str]:
    """Split style_guide.md by its '## N. Title' headings (not '### N.M').
    Returns each section's raw text, trailing '---' rule stripped."""
    matches = list(_TOP_SECTION_RE.finditer(text))
    sections: dict[int, str] = {}
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].rstrip()
        body = re.sub(r"\n-{3,}\s*$", "", body).strip()
        sections[int(m.group(1))] = body
    return sections


def _strip_evidence_quotes(section3_text: str) -> str:
    """Section 3's rules are prose; every rule is followed by a '-' bulleted
    block of quoted transcript/Teacher's Guide excerpts (labelled 'Evidence:'
    in 3.1/3.2/3.3/3.5, unlabelled in 3.4). Those quotes are provenance for
    the thesis, not instructions for the model, and are ~40% of the section's
    bytes — this drops the label and every bullet (plus its wrapped
    continuation lines) while keeping every prose rule statement intact."""
    out: list[str] = []
    in_bullet = False
    for line in section3_text.splitlines():
        stripped = line.strip()
        if stripped == "Evidence:":
            in_bullet = True
            continue
        if _BULLET_RE.match(line):
            in_bullet = True
            continue
        if in_bullet:
            if stripped == "":
                in_bullet = False
                out.append(line)
                continue
            if stripped.startswith("#") or stripped.startswith("**"):
                in_bullet = False
                # falls through to the normal append below
            else:
                continue  # wrapped continuation of the bullet above
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _drop_subsection(section_text: str, number: str) -> str:
    """Remove one '### N.M ...' block (heading through its content, up to
    the next '### ' heading or end of text)."""
    pattern = re.compile(rf"### {re.escape(number)} .*?(?=\n### |\Z)", re.DOTALL)
    return pattern.sub("", section_text).strip()


def load_style_rules(*, include_opening_move: bool = True) -> str:
    """Sections 3 (structural), 4 (tone) and 5 (forbidden patterns) of
    style_guide.md, evidence quotes stripped from section 3. Sections 1
    (sources), 2 (numeric thresholds — those go through config.py, not the
    prompt), 6 (grade profiles — loaded separately per grade), 7 and 8 are
    documentation for the thesis, not model instructions.

    include_opening_move=False drops section 3.1 (greeting + song + lesson
    framing). That rule describes how a teacher opens a whole LESSON, once —
    applying it to every chatbot turn made stage 2 replay "let's sing a
    song" on follow-up questions like "give me more examples" mid-
    conversation. render_stage2_prompt() passes this through as
    `not is_first_turn`; the rule's evidence stays untouched in
    style_guide.md itself — this is an application-layer scoping decision,
    not a correction to the research finding."""
    text = _STYLE_GUIDE_PATH.read_text(encoding="utf-8")
    sections = _top_sections(text)

    section3 = sections[3]
    # Drop section 3's intro paragraph ("Fill each slot with what you
    # actually observe...") — that sentence instructs whoever edits this
    # document, not the model answering a student.
    first_sub = section3.find("\n### ")
    if first_sub != -1:
        section3 = section3[first_sub + 1 :]
    section3 = _strip_evidence_quotes(section3)
    if not include_opening_move:
        section3 = _drop_subsection(section3, "3.1")

    return "\n\n".join([section3, sections[4], sections[5]])


def load_grade_profile(grade: int) -> str:
    """The '## 6. Grade profiles' table row for this grade, rendered as
    'Column: value' pairs, empty cells skipped. Grades 3 and 8 have no
    filled cells yet (only Grade 5 is the primary target), so they return
    "" and the caller falls back to the general rules from load_style_rules()."""
    text = _STYLE_GUIDE_PATH.read_text(encoding="utf-8")
    sections = _top_sections(text)
    columns: list[str] | None = None
    for line in sections[6].splitlines():
        row = line.strip()
        if not row.startswith("|"):
            continue
        cells = [c.strip() for c in row.strip("|").split("|")]
        if columns is None:
            if cells and cells[0].lower() == "grade":
                columns = cells
            continue
        if set(row) <= {"|", "-", " "}:
            continue  # the '|---|---|' separator row
        if cells and cells[0] == str(grade):
            pairs = [
                f"{col}: {val}"
                for col, val in zip(columns, cells)
                if val and col.lower() != "grade"
            ]
            return "; ".join(pairs)
    return ""


@dataclass(frozen=True)
class FewshotExample:
    question: str
    teacher_answer: str
    grade: int
    provenance: str


def load_fewshot(grade: int) -> list[FewshotExample]:
    """Real (question, teacher_answer) pairs from fewshot_examples.jsonl.
    Deliberately drops textbook_passage — see the module docstring for why.
    Refuses 'synthetic' rows when EVAL_MODE=1 (CLAUDE.md: prompts.md rule),
    and raises rather than silently running few-shot-free if that empties
    the list, so an eval run can't quietly lose its few-shot examples.
    Falls back to all remaining rows if none match `grade` exactly."""
    settings = get_settings()
    rows: list[FewshotExample] = []
    with _FEWSHOT_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if settings.eval_mode and row["provenance"] == "synthetic":
                continue
            rows.append(
                FewshotExample(
                    question=row["question"],
                    teacher_answer=row["teacher_answer"],
                    grade=row["grade"],
                    provenance=row["provenance"],
                )
            )
    if not rows:
        raise ValueError(
            "No few-shot examples available after filtering "
            f"({_FEWSHOT_PATH}, EVAL_MODE={settings.eval_mode}) — "
            "stage 2 must not run voiceless."
        )
    grade_rows = [r for r in rows if r.grade == grade]
    return grade_rows or rows


def format_fewshot(examples: list[FewshotExample]) -> str:
    return "\n\n".join(f"Question: {ex.question}\nTeacher: {ex.teacher_answer}" for ex in examples)


_FIRST_TURN_NOTE_TEMPLATE = (
    "This is the first message in this conversation. Following the opening-"
    "move rule below, begin with a brief one-time greeting and name the "
    "unit, lesson and page this comes from — it is {source_citation}; use "
    "exactly that, do not guess or invent a different one — but skip the "
    "song step described there entirely; that does not apply to a text chat."
)
_FOLLOW_UP_TURN_NOTE = (
    "This is a follow-up in an ongoing conversation, not the start of a "
    "lesson. Do not repeat the greeting and do not name the unit, lesson or "
    "page again — go straight into answering."
)
# Fallback only: route_after_retrieve guarantees at least one scored chunk
# whenever stage 2 runs (the off-book gate has already passed), so this
# should never actually reach the model — it exists so a malformed caller
# fails soft with a vague instruction instead of a KeyError.
_DEFAULT_SOURCE_CITATION = "the source lesson"


def render_stage2_prompt(
    question: str,
    stage1_answer: str,
    *,
    grade: int,
    is_first_turn: bool = True,
    feedback: str | None = None,
    source_citation: str = _DEFAULT_SOURCE_CITATION,
) -> str:
    template = load_prompt(stage2_prompt_version())
    grade_profile = load_grade_profile(grade)
    if not grade_profile:
        grade_profile = (
            "No specific numeric profile recorded yet for this grade — "
            "follow the general rules above."
        )
    feedback_block = ""
    if feedback:
        feedback_block = (
            f"\nYour previous attempt did not pass the style check: {feedback}\n"
            "Rewrite the answer to fix this, without changing any fact.\n"
        )
    turn_note = (
        _FIRST_TURN_NOTE_TEMPLATE.format(source_citation=source_citation)
        if is_first_turn
        else _FOLLOW_UP_TURN_NOTE
    )
    return template.format(
        grade=grade,
        turn_note=turn_note,
        style_rules=load_style_rules(include_opening_move=is_first_turn),
        grade_profile=grade_profile,
        fewshot=format_fewshot(load_fewshot(grade)),
        feedback=feedback_block,
        stage1_answer=stage1_answer,
        question=question,
    )


@dataclass(frozen=True)
class Stage2Result:
    answer: str
    llm: LLMResult


def generate_stage2(
    question: str,
    stage1_answer: str,
    *,
    grade: int,
    is_first_turn: bool = True,
    provider: str = "openai",
    feedback: str | None = None,
    source_citation: str = _DEFAULT_SOURCE_CITATION,
) -> Stage2Result:
    prompt = render_stage2_prompt(
        question,
        stage1_answer,
        grade=grade,
        is_first_turn=is_first_turn,
        feedback=feedback,
        source_citation=source_citation,
    )
    result = call_llm(prompt, prompt_version=stage2_prompt_version(), provider=provider)
    return Stage2Result(answer=result.text, llm=result)
