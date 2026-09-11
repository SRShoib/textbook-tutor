# Textbook Tutor Chatbot

MSc thesis, CSE699, Daffodil International University.

A hallucination-aware, retrieval-augmented tutoring chatbot with grade-level
adaptive explanations for Bangladeshi school textbooks.

## Start here

1. `CLAUDE.md` — every architectural decision. Claude Code reads this automatically.
2. `PROMPTS.md` — the session-by-session script for building with Claude Code.
3. `docs/project-guidelines.md` — the long-form plan, evaluation design, thesis structure.
4. `NOTES.md` — lab notebook, append after every session.

## Before the first session

- Put the NCTB Class 5 English for Today PDF in `data/textbook/`
- Put downloaded teacher videos in `data/style_guide/videos/`
- Put `class5_shikkhok_sohayika_2026.pdf` in `data/style_guide/teacher_guide/`
- `git init`, then `claude`, then `/model opusplan`

## Phase 0 (run in a normal terminal, parallel to Phases 1–2)

```
pip install openai-whisper textstat
python tools/style_guide/transcribe.py --media-dir data/style_guide/videos --out data/style_guide/transcripts
python tools/style_guide/measure_style.py --transcripts data/style_guide/transcripts --md
```

Requires ffmpeg: `winget install ffmpeg` (Windows) or `sudo apt install ffmpeg` (Ubuntu).
