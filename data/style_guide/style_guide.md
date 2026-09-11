# Bangladeshi Primary Teacher Explanation Style Guide

**Status:** SKELETON — rules are placeholders until filled from real sources.
**Owner:** Sazzatul Yeakin
**Purpose:** Defines how the chatbot must phrase an answer for a given class level.
Consumed by `pipeline/generate.py` (stage 2) and `pipeline/style_check.py`.

> **Rule for this document:** every rule below must cite evidence — a transcript
> line, a Teacher's Guide page, or a teacher interview. A rule with an empty
> `Evidence:` field is a guess and must not be used in the thesis.

---

## 1. Sources used

Fill this table as you collect. It becomes a table in the thesis Dataset chapter.

| ID | Source | Type | Detail | Collected |
|----|--------|------|--------|-----------|
| TG-1 | DPE Teacher's Guide — English for Today | Official document | pages ___ | ☐ |
| SS-1 | Shikkhok Sohayika (Bangla) Class 5 | Official document | pages ___ | ☐ |
| V-1 | Ghore Bose Shikhi, Class 5 English, ____-__-__ | Video transcript | URL: | ☐ |
| V-2 | | Video transcript | URL: | ☐ |
| V-3 | | Video transcript | URL: | ☐ |
| V-4 | | Video transcript | URL: | ☐ |
| V-5 | | Video transcript | URL: | ☐ |
| T-1 | Teacher interview — ____ school, ___ yrs experience | Interview | date: | ☐ |
| T-2 | Teacher interview | Interview | date: | ☐ |
| T-3 | Teacher interview | Interview | date: | ☐ |

**Target:** 5+ video transcripts, 2 official documents, 3 teacher interviews.

---

## 2. Measurable rules

Measured from 12 Ghore Boshe Shikhi Class 5 English transcripts,
15,027 words, 2,353 sentences. Whisper large-v3, raw output.

| Rule | Value | How measured | Evidence |
|------|-------|--------------|----------|
| Max sentence length | 14 words | 95th percentile of 2,353 transcript sentences | measure_style.py, 2026-09-10 |
| Mean sentence length | 6.4 words | mean over 12 transcripts | measure_style.py, 2026-09-10 |
| Target Flesch-Kincaid | 3.0 – 4.5 | textstat on English portion, corpus FK = 3.37 | measure_style.py, 2026-09-10 |
| Vocabulary coverage | TBD | % of answer words found in textbook wordlist | pending — needs chunks table |
| Answer length | TBD | words per single explanation | pending — needs segmenting by topic |
| Bangla insertion rate | 0.0% detected | Bangla-script words / all words | UNRESOLVED — see §3.4 |

**Note on the FK target:** the corpus scores 3.37, i.e. these teachers explain
*below* the nominal grade level of the textbook. The style check should target
FK 3–4.5 for Class 5, not 4–6. This is a finding, not an assumption.

**Caveat:** raw Whisper output. Punctuation density is 16.7 marks per 100 words
and one file contains a 354-word run with no full stop. Hand-punctuate a sample
and re-run before quoting these in the thesis; record how much they move.


---

## 3. Structural rules

How a teacher builds an explanation, start to finish. Fill each slot with what
you actually observe, plus a quoted example.

### 3.1 Opening move
Placeholder — observed openings:
- ___
- ___

Evidence:

### 3.2 Core explanation
Placeholder — how is the definition delivered? (echoes textbook wording? / restated
simply first? / example before definition?)

Evidence:

### 3.3 Examples
Placeholder — how many examples, and drawn from what? (school life / home / village /
food / festivals / transport)

Observed example domains:
- ___
- ___

Evidence:

### 3.4 Bangla usage
Placeholder — when does the teacher switch to Bangla? (hard nouns only? / whole
sentence repeated? / only the technical term?)

Observed pattern:

Evidence:

### 3.5 Closing move
Placeholder — how does the teacher end? (check question / instruction to practise /
summary repeat)

Evidence:

---

## 4. Tone rules

| Rule | Description | Evidence |
|------|-------------|----------|
| Address form | e.g. how the teacher refers to the student | |
| Repetition | is the key point repeated? how many times? | |
| Encouragement | phrases used when a student is expected to answer | |
| Formality | | |

---

## 5. Forbidden patterns

Things that mark an answer as "not a Bangladeshi teacher". Fill from contrast
between LLM baseline output and transcripts.

- Abstract dictionary definitions with no example
- Examples from foreign contexts (___)
- ___
- ___

---

## 6. Grade profiles

The prompt template takes `grade` as a variable. One row per class level.

| Grade | Max sentence | Target FK | Bangla hints | Answer length | Notes |
|-------|-------------|-----------|--------------|---------------|-------|
| 3 | | | | | |
| 5 | | | | | **primary target** |
| 8 | | | | | |

Only Class 5 needs to be filled for the thesis. The others demonstrate the
template generalises.

---

## 7. Few-shot examples

Stored separately in `fewshot_examples.jsonl` — see that file's header for the
schema and the replacement rule.

---

## 8. Changelog

| Version | Date | Change |
|---------|------|--------|
| v0 | | Skeleton created, all rules placeholder |
