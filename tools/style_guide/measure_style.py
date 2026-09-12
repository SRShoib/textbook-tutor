"""
Measure the style of transcribed teacher explanations.

Reads every .txt in a transcripts folder and reports the numbers you need to
fill Section 2 of style_guide.md — so those thresholds come from measurement,
not from guessing. Also prints the vocabulary and Bangla-usage picture.

Install:
    pip install textstat

Usage:
    python tools/style_guide/measure_style.py --transcripts data/style_guide/transcripts
    python tools/style_guide/measure_style.py --transcripts data/style_guide/transcripts --md >> data/style_guide/style_guide.md

Output is per-file plus a combined summary. The --md flag prints a markdown
table you can paste straight into the style guide.

NOTE: raw Whisper transcripts contain filler, repetition and recognition
errors. Numbers here are indicative. Before quoting them in the thesis, clean
a sample of the transcripts by hand and re-run to see how much the numbers move.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path


# Bangla unicode block
BANGLA_RE = re.compile(r"[\u0980-\u09FF]")
# Kept identical to backend/app/pipeline/style_check.py's _SENT_SPLIT_RE --
# test_style_check.py asserts the two never drift apart. Splits after
# terminal punctuation, and also after terminal punctuation immediately
# followed by a closing quote mark (a quoted question inside a longer
# sentence, e.g. '"...?" The answer is').
SENT_SPLIT_RE = re.compile(r'(?<=[.!?।])\s+|(?<=[.!?।]["”’\'])\s+')
WORD_RE = re.compile(r"[A-Za-z\u0980-\u09FF']+")

# very rough English stopword list, enough to surface content words
STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "and", "or",
    "but", "if", "of", "to", "in", "on", "at", "for", "with", "so", "that",
    "this", "these", "those", "it", "its", "we", "you", "your", "i", "he",
    "she", "they", "them", "his", "her", "our", "will", "can", "do", "does",
    "did", "not", "no", "yes", "here", "there", "now", "then", "what", "who",
    "how", "why", "when", "where", "as", "by", "from", "have", "has", "had",
}


@dataclass
class FileStats:
    name: str
    words: int
    sentences: int
    mean_sentence_words: float
    median_sentence_words: float
    p95_sentence_words: float
    max_sentence_words: int
    bangla_word_ratio: float
    punct_per_100w: float
    fk_grade: float | None


def strip_header(text: str) -> str:
    """Drop the '# source:' comment block the transcriber writes."""
    lines = [l for l in text.splitlines() if not l.strip().startswith("#")]
    return "\n".join(lines).strip()


def sentences(text: str) -> list[str]:
    parts = [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip()]
    return parts


def words(text: str) -> list[str]:
    return WORD_RE.findall(text)


def is_bangla(word: str) -> bool:
    return bool(BANGLA_RE.search(word))


def strip_bangla_keep_punct(text: str) -> str:
    """Remove Bangla characters but KEEP punctuation and spacing.

    Critical: textstat needs sentence-ending punctuation to count sentences.
    Rebuilding the text from a word regex destroys every full stop and makes
    the Flesch-Kincaid score meaningless (it sees one giant sentence).
    """
    return re.sub(r"[\u0980-\u09FF]+", " ", text)


def punctuation_density(text: str) -> float:
    """Sentence-ending marks per 100 words. Whisper output is often sparse."""
    n_words = len(words(text))
    if not n_words:
        return 0.0
    n_marks = len(re.findall(r"[.!?।]", text))
    return round(100 * n_marks / n_words, 1)


def fk_grade(text: str) -> float | None:
    """Flesch-Kincaid on the English portion, punctuation preserved."""
    english = strip_bangla_keep_punct(text)
    if len(english.split()) < 30:
        return None
    try:
        import textstat
    except ImportError:
        return None
    try:
        score = round(textstat.flesch_kincaid_grade(english), 2)
    except Exception:
        return None
    # Sanity check. Valid range is roughly -3 to 20. Anything outside that
    # means the text lacks sentence punctuation and the score is meaningless.
    if score < -5 or score > 25:
        return None
    return score


def analyse(path: Path) -> FileStats | None:
    raw = strip_header(path.read_text(encoding="utf-8", errors="replace"))
    if not raw:
        return None

    sents = sentences(raw)
    if not sents:
        return None

    lens = [len(words(s)) for s in sents]
    lens = [n for n in lens if n > 0]
    if not lens:
        return None

    all_words = words(raw)
    bangla = sum(1 for w in all_words if is_bangla(w))

    lens_sorted = sorted(lens)
    p95_idx = max(0, int(len(lens_sorted) * 0.95) - 1)

    return FileStats(
        name=path.name,
        words=len(all_words),
        sentences=len(sents),
        mean_sentence_words=round(statistics.mean(lens), 1),
        median_sentence_words=round(statistics.median(lens), 1),
        p95_sentence_words=float(lens_sorted[p95_idx]),
        max_sentence_words=max(lens),
        bangla_word_ratio=round(bangla / len(all_words), 3) if all_words else 0.0,
        punct_per_100w=punctuation_density(raw),
        fk_grade=fk_grade(raw),
    )


def combined(paths: list[Path]) -> dict:
    all_lens: list[int] = []
    all_words_list: list[str] = []
    texts: list[str] = []

    for p in paths:
        raw = strip_header(p.read_text(encoding="utf-8", errors="replace"))
        texts.append(raw)
        for s in sentences(raw):
            n = len(words(s))
            if n > 0:
                all_lens.append(n)
        all_words_list.extend(words(raw))

    if not all_lens:
        return {}

    lens_sorted = sorted(all_lens)
    p95 = lens_sorted[max(0, int(len(lens_sorted) * 0.95) - 1)]
    bangla_words = [w for w in all_words_list if is_bangla(w)]

    content = [w.lower() for w in all_words_list
               if not is_bangla(w) and w.lower() not in STOP and len(w) > 2]

    return {
        "files": len(paths),
        "total_words": len(all_words_list),
        "total_sentences": len(all_lens),
        "mean_sentence_words": round(statistics.mean(all_lens), 1),
        "median_sentence_words": round(statistics.median(all_lens), 1),
        "p95_sentence_words": p95,
        "max_sentence_words": max(all_lens),
        "bangla_word_ratio": round(len(bangla_words) / len(all_words_list), 3),
        "punct_per_100w": punctuation_density(" ".join(texts)),
        "fk_grade": fk_grade(" ".join(texts)),
        "top_bangla_words": Counter(bangla_words).most_common(20),
        "top_content_words": Counter(content).most_common(25),
    }


def print_md(c: dict) -> None:
    print("\n<!-- generated by measure_style.py - paste into style_guide.md section 2 -->")
    print("\n| Rule | Value | How measured |")
    print("|------|-------|--------------|")
    print(f"| Max sentence length | {c['p95_sentence_words']} words | 95th percentile of {c['total_sentences']} transcript sentences |")
    print(f"| Mean sentence length | {c['mean_sentence_words']} words | mean over {c['files']} transcripts |")
    fk = c["fk_grade"]
    print(f"| Target Flesch-Kincaid | {fk if fk is not None else 'n/a'} | textstat on English portion |")
    print(f"| Bangla word ratio | {c['bangla_word_ratio']:.1%} | Bangla words / all words |")
    print(f"| Corpus size | {c['total_words']} words | {c['files']} transcripts |")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcripts", required=True, type=Path)
    ap.add_argument("--md", action="store_true",
                    help="print a markdown table for style_guide.md")
    ap.add_argument("--json", type=Path, help="also write raw stats to this path")
    args = ap.parse_args()

    if not args.transcripts.exists():
        print(f"folder not found: {args.transcripts}")
        return 1

    paths = sorted(p for p in args.transcripts.glob("*.txt"))
    if not paths:
        print(f"no .txt transcripts in {args.transcripts}")
        print("run transcribe.py first")
        return 1

    print(f"{len(paths)} transcript(s)\n")

    per_file = []
    print(f"{'file':<24} {'words':>7} {'sents':>6} {'mean':>6} {'p95':>5} {'max':>5} {'bn%':>6} {'pct':>5} {'FK':>6}")
    print("-" * 80)
    for p in paths:
        st = analyse(p)
        if st is None:
            print(f"{p.name:<28} {'(empty)':>7}")
            continue
        per_file.append(st)
        fk = f"{st.fk_grade}" if st.fk_grade is not None else "-"
        short = st.name[:24]
        print(f"{short:<24} {st.words:>7} {st.sentences:>6} "
              f"{st.mean_sentence_words:>6} {st.p95_sentence_words:>5.0f} "
              f"{st.max_sentence_words:>5} {st.bangla_word_ratio:>5.1%} "
              f"{st.punct_per_100w:>5.1f} {fk:>6}")

    c = combined(paths)
    if not c:
        print("\nnothing measurable")
        return 1

    print("\n" + "=" * 76)
    print("COMBINED")
    print(f"  words                {c['total_words']}")
    print(f"  sentences            {c['total_sentences']}")
    print(f"  mean sentence        {c['mean_sentence_words']} words")
    print(f"  median sentence      {c['median_sentence_words']} words")
    print(f"  95th percentile      {c['p95_sentence_words']} words   <- use as max sentence rule")
    print(f"  longest sentence     {c['max_sentence_words']} words")
    print(f"  Bangla word ratio    {c['bangla_word_ratio']:.1%}")
    print(f"  punctuation          {c['punct_per_100w']} sentence marks per 100 words")
    fkv = c['fk_grade']
    print(f"  Flesch-Kincaid       {fkv if fkv is not None else 'n/a — see warning below'}")

    warn = []
    if c["punct_per_100w"] < 8:
        warn.append(
            "Punctuation is sparse (<8 marks per 100 words). Whisper did not segment\n"
            "    sentences reliably, so sentence-length stats and FK are NOT trustworthy.\n"
            "    Hand-punctuate 2-3 transcripts and re-run before quoting any of this.")
    if c["max_sentence_words"] > 60:
        warn.append(
            f"Longest 'sentence' is {c['max_sentence_words']} words — that is a missing full stop,\n"
            "    not a real sentence. Same fix: hand-punctuate a sample.")
    if fkv is None:
        warn.append(
            "FK could not be computed reliably (out of valid range). Almost always\n"
            "    caused by missing sentence punctuation.")
    if c["bangla_word_ratio"] == 0.0:
        warn.append(
            "Zero Bangla detected. Either these lessons really are conducted in English,\n"
            "    or Whisper romanised the Bangla. READ a transcript before concluding either.\n"
            "    This changes what your style guide should say about language switching.")
    if warn:
        print("\n  WARNINGS")
        for w in warn:
            print(f"  ! {w}")

    if c["top_bangla_words"]:
        print("\n  most used Bangla words (where the teacher switches language):")
        for w, n in c["top_bangla_words"][:12]:
            print(f"    {n:>4}  {w}")

    print("\n  most used content words (vocabulary the teacher relies on):")
    for w, n in c["top_content_words"][:15]:
        print(f"    {n:>4}  {w}")

    if args.md:
        print_md(c)

    if args.json:
        payload = {"per_file": [asdict(s) for s in per_file], "combined": c}
        args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        print(f"\nraw stats -> {args.json}")

    print("\nreminder: these come from RAW transcripts. Clean a sample by hand")
    print("and re-run before quoting the numbers in the thesis.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())