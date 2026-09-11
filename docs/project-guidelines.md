> **Note:** this document was written before several decisions changed (Streamlit → Next.js, Chroma → pgvector, teacher interviews dropped, password accounts added). Where it conflicts with `CLAUDE.md`, `CLAUDE.md` is correct.

# Project Guidelines: Hallucination-Aware Textbook Tutor Chatbot

**Working title:** A Hallucination-Aware Retrieval-Augmented Tutoring Chatbot with Grade-Level Adaptive Explanations for Bangladeshi School Textbooks

**Course:** CSE699, MSc in CSE, Daffodil International University
**Student:** Sazzatul Yeakin (252-25-022)

---

## 1. What the project is

A chatbot where a user uploads a school textbook (starting with NCTB Class 5 *English for Today*). The bot first asks which class the student reads in. After that, the student asks any question from the book and the bot answers:

1. **Only from the uploaded book.** If the answer is not in the book, the bot says so instead of inventing one.
2. **In the voice of a Bangladeshi teacher for that class.** Simple words, short sentences, daily-life examples, a Bangla hint for hard English words, a small check-question at the end.
3. **After checking itself.** Every answer is verified against the textbook passages before it is shown.

### Research contributions

| # | Contribution | Why it matters |
|---|---|---|
| 1 | Textbook-grounded RAG with explicit refusal | A child cannot detect a wrong answer; grounding is safety-critical |
| 2 | Grade-level adaptive generation, modelled on real Bangladeshi teaching | Most RAG tutors ignore the learner's level entirely |
| 3 | Verification module (NLI-based) for hallucination control | The "hallucination-aware" part of the thesis |
| 4 | A test set of Class 5 English questions with reference answers | Reusable benchmark; none exists for Bangladeshi textbooks |
| 5 | A documented "Bangladeshi Class 5 teacher style guide" | Built from official DPE materials, defensible and reusable |

---

## 2. Fix the proposal first (Week 0–1)

The submitted Phase I proposal describes a *structured information extraction* system (transcripts → JSON fields, evaluated with P/R/F1). The actual project is a *question-answering tutor*. Correct the document before it is approved.

### What to change

| Section | Change |
|---|---|
| Title | Use the working title above |
| Abstract | Replace "structured information extraction from educational documents" with "textbook-grounded, grade-adaptive question answering" |
| Problem statement | Hallucination is worse for children than for office staff (they cannot verify). Existing RAG tutors do not adapt to grade level or verify answers. |
| Objectives | Replace schema-enforcement and P/R/F1 objectives with: grounding, grade adaptation, verification, benchmark, teacher evaluation |
| Literature review | Add: RAG evaluation (RAGAS), educational chatbots / ITS, text simplification and readability control, hallucination detection via NLI, LLMs in Bangla/low-resource education |
| Expected outcomes | Working chatbot, test set, style guide, ablation results, teacher evaluation |
| References | Delete [4], [6], [9] (wrong papers at those arXiv IDs). Fix [5] venue (ICLR 2023) and [3] arXiv ID (2202.03629). Regenerate every entry from Google Scholar. Fix "Refferences". |

### What stays

RAG as the grounding technique and hallucination mitigation as the core research problem are unchanged. Only the application and evaluation change.

---

## 3. Architecture

### Offline pipeline (runs once per book)

```
Upload PDF → Extract text → Split into lesson chunks → Embed → Store in vector DB
```

### Online pipeline (runs per question)

```
Student question + grade
      ↓
Retrieve top-k lesson chunks (hybrid: BM25 + vector)
      ↓
Off-book pre-check: if best retrieval score < threshold → "Not in your textbook"
      ↓
Stage 1 — Extract factual answer from chunks only
      ↓
Stage 2 — Rewrite in Class-N teacher voice (style guide + few-shot examples)
      ↓
Verify: split answer into sentences, NLI-check each against chunks
      ↓
Supported → show answer      Not supported → regenerate once, else refuse
```

### Why two generation stages

Stage 1 can be checked for correctness. Stage 2 can be checked for simplicity. Keeping them separate means the "make it simple" step can never introduce new facts, and each stage can be evaluated and fixed independently.

---

## 4. Technology stack

| Component | Choice | Reason |
|---|---|---|
| Orchestration | LangGraph | Each pipeline box = one node; verify decision = conditional edge |
| PDF text extraction | PyMuPDF; Tesseract (`ben` model) if scanned | Free, handles Bangla later |
| Chunking | By lesson heading, then 300–500 words with 50-word overlap | Keeps lesson context; metadata: unit, lesson title, page |
| Embeddings | `BAAI/bge-m3` | Same model for English and Bangla |
| Vector store | ChromaDB (one collection per book) | Local, no server |
| Retrieval | Hybrid BM25 + dense, reciprocal rank fusion | Textbook questions reuse lesson wording |
| LLM (main) | Gemini Flash / GPT-4o-mini / Claude Haiku | Cheap, good quality; free tier enough for dev |
| LLM (ablation) | Qwen 2.5 7B or Llama 3.1 8B via Ollama | Shows the method works on open models |
| Verifier | `cross-encoder/nli-deberta-v3-base` | Sentence-level entailment vs. retrieved chunks |
| Readability | `textstat` (Flesch-Kincaid) | Grade-level score for English answers |
| Evaluation | RAGAS | Faithfulness, answer relevance, context precision |
| UI | Streamlit | Upload + class selection screen, then chat |
| Code hygiene | Git, prompts in versioned files, one config per experiment | Reproducibility |

Keep the LLM call behind one function so swapping models for ablation is a one-line change.

---

## 5. Data sources (all free, all Bangladeshi)

| Source | Where | Use for |
|---|---|---|
| NCTB *English for Today* Class 5 (Bangla and English version) | nctb.gov.bd → textbook page | The book the bot answers from. Download the official copy, cite the edition. |
| DPE **Teacher's Guide – English for Today** (English); *Shikkhok Sohayika* (Bangla) | dpe.portal.gov.bd (PDF) | Style guide rules; lesson-by-lesson expected questions and answers |
| **Ghore Bose Shikhi** video classes (Sangsad TV / DPE) | Official YouTube channel; Class 5 English episodes | Transcribe teacher explanations → few-shot examples in real teacher voice |
| Commercial Class 5 English guide books | Widely shared PDFs | Question bank for the test set (not for style — exam answers are stiff) |
| 2–3 real primary teachers | Local schools | Style guide review, few-shot sanity check, final answer rating |

---

## 6. Building the "Bangladeshi Class 5 teacher" style

This is the novel part. A prompt alone will produce a generic Western "simple" style. Do this instead.

### 6.1 Write the style guide (Week 1–2)

1. Read the DPE Teacher's Guide for 5–6 lessons. Note how topics are introduced, what questions are asked, how answers are phrased.
2. Transcribe 5–10 Ghore Bose Shikhi Class 5 English episodes. Extract (topic → teacher explanation) pairs.
3. Sit with 2–3 teachers for an hour each. Ask them to explain 10 topics as they would in class. Record and transcribe.
4. Write the rules down as a document with examples. Typical patterns to capture:
   - Echo the textbook's own sentence first, then explain
   - Sentences under ~12 words
   - Bangla word in brackets for a hard English word
   - Examples from daily life: rickshaw, tiffin, Eid, cricket, rice field, school bag
   - Warm teacher voice ("Look, …", "See, …", "Now tell me, …")
   - End with one small question to check understanding

Put this style guide in the thesis as an appendix and a contribution.

### 6.2 Encode it in the system

1. **Rules in the Stage 2 prompt**, as concrete constraints (length, vocabulary, example, closing question, Bangla hint).
2. **Few-shot examples** — 3–5 real (question, textbook passage, teacher explanation) triples from the transcripts. This is the strongest lever.
3. **Grade as a template variable**, so the same prompt works for Class 3–8 later.

### 6.3 Check automatically (before showing any answer)

| Check | Threshold (tune on dev set) |
|---|---|
| Average sentence length | < 12 words; no sentence > 18 |
| Vocabulary coverage | ≥ 90% of words appear in the textbook or a common-words list |
| Flesch-Kincaid grade | 4–6 |
| LLM grade-judge | Second call: "Suitable for Class 5 in Bangladesh? yes/no + reason" |

Fail → regenerate with the judge's reason added to the prompt (max 2 retries).

### 6.4 Check with humans

Blind rating by 3–5 teachers (see §8). Optionally, read 5 answers to real Class 5 students and ask them to explain it back.

### Example of the target

Question: *What is a noun?*

Generic LLM: "A noun is a word that functions as the name of a specific object or set of objects, such as living creatures, places, actions, qualities, states of existence, or ideas."

Target: "Look, a noun is a naming word. It is the name of a person, a place, or a thing. Your name is a noun. Dhaka is a noun. Rice is a noun. Your school bag is a noun. Anything you can name is a noun. In Bangla we say বিশেষ্য. Now tell me, is 'mango' a noun?"

---

## 7. Step-by-step build plan

| Week | Task | Deliverable |
|---|---|---|
| 0–1 | Rewrite proposal (§2). Download textbook, teacher's guide, videos. | Corrected proposal submitted |
| 1–2 | Style guide (§6.1). Ingestion pipeline: extract, chunk, embed, store. Inspect chunks by eye. | Style guide v1; Chroma index of the book |
| 2–3 | Plain RAG baseline end to end. **Build the evaluation script now**, before the system is good. | Baseline number on dev set |
| 3–4 | Two-stage generation with grade prompt and few-shot examples. Automatic style checks. | Grade-adaptive answers |
| 4–6 | Verifier: NLI per sentence, off-book pre-check, regenerate/refuse logic. Log per-sentence scores. | Full pipeline |
| 6 | LangGraph state (book, grade, history). Streamlit UI. | Working demo |
| 6–8 | Test set (§8.1). Teacher review of questions. Freeze it. | 150-question test set |
| 8–10 | Run all configurations. RAGAS, readability, refusal accuracy. Teacher blind rating. Error analysis. | Results tables |
| 10–14 | Write thesis. Publish code + test set on GitHub. | Thesis draft, repo |
| 14–16 | Supervisor feedback, revisions, defense slides. | Final submission |

Scope rule: one book (Class 5 English) and one main LLM. Add a Bangla textbook only if ahead of schedule at week 8 — Bangla OCR and Bangla readability scoring are each a week of work.

---

## 8. Evaluation

### 8.1 Test set

- ~120 answerable questions spread across all units; mix of factual ("What is the name of the boy in the story?") and explanatory ("What does 'polite' mean?").
- ~30 off-book questions that sound like school questions but are not answered in the book (to test refusal).
- For each: question, source lesson, reference answer, question type.
- One teacher reviews the set. Split: dev (30%) for tuning, test (70%) touched only once at the end.

### 8.2 Configurations (ablation)

| Config | Retrieval | Grade prompt | Verifier |
|---|---|---|---|
| A. Plain LLM | – | – | – |
| B. RAG baseline | ✓ | – | – |
| C. RAG + grade adaptation | ✓ | ✓ | – |
| D. Full system | ✓ | ✓ | ✓ |
| D-open. Full system, open LLM | ✓ | ✓ | ✓ |

### 8.3 Metrics

| Dimension | Metric |
|---|---|
| Correctness / grounding | RAGAS faithfulness, answer relevance, context precision |
| Hallucination | % of answer sentences not entailed by retrieved chunks (from verifier logs) |
| Refusal | Accuracy on the 30 off-book questions; false refusals on answerable ones |
| Simplicity | Flesch-Kincaid grade, mean sentence length, vocabulary coverage |
| Teacher judgement | 3–5 teachers, blind, 40–50 answers from configs B and D, two 1–5 scales: "Correct according to the book?" and "Would you explain it this way?" Report inter-rater agreement. |

### 8.4 Error analysis

Categorise 20–30 failures: wrong chunk retrieved, right chunk but wrong answer, correct but too hard, correct but not "Bangladeshi", wrongly refused, wrongly answered off-book. This table is one of the most useful parts of the results chapter.

---

## 9. Thesis structure

1. Introduction — problem, why children make hallucination high-stakes, contributions
2. Related Work — RAG, hallucination detection, educational chatbots, readability control, LLMs in Bangla education
3. System Design — architecture diagram (§3), each module
4. The Bangladeshi Teacher Style Guide — sources, rules, examples (§6)
5. Dataset — book, test set construction, teacher review
6. Experiments — configurations, metrics, setup
7. Results and Discussion — ablation table, teacher ratings, error analysis
8. Limitations and Future Work — one book, one language, small teacher panel, Bangla extension
9. Conclusion
Appendices — style guide, prompts (versioned), sample answers, test set link

---

## 10. Risks and how to handle them

| Risk | Mitigation |
|---|---|
| NCTB PDF is scanned, text extraction is poor | Try PyMuPDF first; fall back to Tesseract; hand-fix the worst pages — the book is short |
| API cost / quota | Cache all LLM calls to disk; use free tier for dev; run final eval once |
| Teachers hard to recruit | Start asking in week 1; 3 is enough; DIU education contacts or nearby primary schools |
| Verifier too strict (refuses correct answers) | Tune threshold on dev set; report false-refusal rate honestly |
| Style still sounds generic | More few-shot examples from transcripts beats more rules |
| Scope creep (Bangla, more classes, fancy UI) | Hard rule: one book, one LLM, Streamlit, until week 8 |

---

## 11. Practical rules

- Build the evaluation script before the system is good.
- Every prompt lives in its own file with a version number; every experiment records which version it used.
- Cache every LLM response keyed on (prompt version, model, input).
- Never tune on the test split.
- Commit early, commit often; the repo is a deliverable.
