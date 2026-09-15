"""
What: one Settings object, read once, used everywhere.
Why: CLAUDE.md requires every model name and threshold to come from config, never
     be hardcoded, so an eval run can log exactly what it used. A single
     pydantic-settings class reading backend/../.env is the simplest way to get
     that with validation for free.
Alternative considered: reading os.environ directly in each module. Rejected —
     that scatters parsing/defaults across the codebase and gives no validation.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repo root (one level above backend/), matching .env.example.
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # --- OpenAI ---
    openai_api_key: str | None = None
    openai_model: str | None = None
    openai_judge_model: str | None = None

    # --- local models ---
    embedding_model: str = "BAAI/bge-m3"
    nli_model: str = "cross-encoder/nli-deberta-v3-base"
    device: str = "cuda"

    # --- ablation config only ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    # --- database ---
    database_url: str = "postgresql+asyncpg://tutor:tutor@localhost:5432/tutor"

    # --- pipeline ---
    retrieval_top_k: int = 5
    offbook_score_threshold: float = 0.35
    verify_entailment_threshold: float = 0.5
    # Tuned down from 0.8 on the dev split (Phase 6 error analysis, never
    # touched test): even after excluding scaffolding sentences and fixing
    # the sentence splitter, ~49% of remaining false refusals sat at
    # supported_ratio exactly 0.0 (no threshold recovers these — genuine
    # NLI cross-encoder strictness on paraphrase: tense changes, pronoun to
    # noun substitution, true added detail) while the rest cleared 0.33+.
    # At 0.5, eval/metrics.py's hallucination_rate jumps from ~3% to ~24% —
    # NOT confirmed safe by that number alone. A full manual+automated audit
    # of all 72 dev-split answers this let through (cross-checked against
    # questions.jsonl's reference_answer) found zero actual fabrications:
    # every "unsupported" sentence was the SAME fact restated in a noisier
    # wrapper next to a cleanly-scoring restatement of it (style_guide.md's
    # "heavy repetition" pattern, §3.2/§3.3), which is exactly what inflates
    # this metric — it counts per sentence, not per distinct claim. See
    # NOTES.md for the audit. hallucination_rate itself may need redefining
    # (dedupe near-identical sentences before scoring) before quoting 24% in
    # the thesis as if it meant 1-in-4 answers contains a fabricated fact.
    verify_supported_ratio: float = 0.5
    verify_max_retries: int = 1
    style_max_retries: int = 2
    # Measured in data/style_guide/style_guide.md section 2 (12 Ghore Boshe
    # Shikhi transcripts). Live here, not parsed from that file's prose
    # table, so an eval manifest can log the exact number used — a test
    # asserts these still match what section 2 claims.
    style_max_sentence_words: int = 14
    style_fk_min: float = 3.0
    style_fk_max: float = 4.5
    style_vocab_coverage_min: float = 0.9
    history_max_messages: int = 8
    config_version: str = "v1"
    # "light" (default) is today's measured behaviour, unchanged since Phase 6:
    # at most one Bangla-script restatement of the student's question, answer
    # body stays English (v1_stage2.txt). "echo" switches stage 2 to
    # v2_stage2.txt, which asks for a Bangla-script echo after every
    # explanation sentence — the sentence-for-sentence pattern style_guide.md
    # section 3.4 evidences for phonics/pronunciation lessons specifically,
    # applied here to every answer. Kept opt-in, not the new default: that
    # evidence doesn't cover the general case, and every Phase 6 A/B/C/D
    # number was measured under "light" — see generate.py's module docstring.
    bangla_mode: Literal["light", "echo"] = "light"

    # --- caching ---
    llm_cache_dir: str = ".llm_cache"
    llm_cache_enabled: bool = True

    # --- auth ---
    jwt_secret: str = "change-me"
    access_token_minutes: int = 30
    refresh_token_days: int = 30
    # False in local dev (plain http). Set true behind TLS in any real deployment.
    cookie_secure: bool = False
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 300
    allow_anonymous: bool = True

    # --- password reset ---
    # Short-lived on purpose -- a reset link is a sensitive one-time action,
    # not something that should stay usable as long as an access token.
    password_reset_token_minutes: int = 30
    # "console" (default) prints the reset link instead of emailing it --
    # this project has no email-sending infrastructure and no third-party
    # email service is wired in on purpose (children's email addresses,
    # minimal-data stance). "smtp" sends a real email via stdlib smtplib
    # once real SMTP settings below are filled in. See core/email.py.
    email_backend: Literal["console", "smtp"] = "console"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str = "no-reply@textbook-tutor.local"
    smtp_use_tls: bool = True

    # --- frontend (Phase 7) ---
    # The Next.js dev origin allowed to send credentialed (cookie-bearing)
    # requests. Must be an explicit origin, never "*" -- CORSMiddleware
    # rejects allow_credentials=True paired with a wildcard, and a wildcard
    # would defeat the refresh cookie's httpOnly/SameSite protection anyway.
    frontend_origin: str = "http://localhost:3000"

    # --- eval ---
    eval_mode: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
