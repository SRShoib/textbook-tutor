"""
Transcribe downloaded lesson videos into text, for building the teacher
style guide.

Reads video or audio files from a folder and writes one .txt per file.
No YouTube, no yt-dlp, no network. Download the videos however you like,
drop them in a folder, run this.

Install:
    pip install -U openai-whisper
    # ffmpeg is required (whisper uses it to read video files):
    #   Windows:  winget install ffmpeg
    #   Ubuntu:   sudo apt install ffmpeg

Usage:
    python tools/style_guide/transcribe.py --media-dir data/style_guide/videos --out data/style_guide/transcripts
    python tools/style_guide/transcribe.py --media-dir data/style_guide/videos --out data/style_guide/transcripts --model medium
    python tools/style_guide/transcribe.py --media-dir data/style_guide/videos --out data/style_guide/transcripts --lang bn
    python tools/style_guide/transcribe.py --media-dir data/style_guide/videos --out data/style_guide/transcripts --list

Accepts: mp4 mkv webm mov avi m4a mp3 wav opus flac
Files already transcribed are skipped, so you can add more videos and re-run.

Model sizes:  tiny < base < small < medium < large
    Use 'medium' for these lessons. Bangladeshi classrooms switch between
    Bangla and English constantly and 'small' garbles the switching.
    If medium is too slow: pip install faster-whisper, or use a Colab GPU.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path


MEDIA_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi",
             ".m4a", ".mp3", ".wav", ".opus", ".flac"}


@dataclass
class Result:
    file: str
    stem: str
    status: str            # done | skipped | failed
    language: str | None
    chars: int
    seconds: float
    out_file: str | None
    error: str | None = None


def find_media(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in MEDIA_EXT)


def check_ffmpeg() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None


_MODEL = None


def resolve_device(requested: str) -> tuple[str, bool]:
    """Return (device, fp16). Warns loudly if CUDA was wanted but is unavailable."""
    try:
        import torch
    except ImportError:
        print("ERROR: torch not installed (openai-whisper needs it)")
        return "cpu", False

    cuda_ok = torch.cuda.is_available()

    if cuda_ok:
        name = torch.cuda.get_device_name(0)
        vram = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"GPU: {name}, {vram:.0f} GB VRAM  (torch {torch.__version__})")
    else:
        print(f"GPU: NOT AVAILABLE  (torch {torch.__version__})")
        print("  Whisper will run on CPU. Expect roughly 10-20x slower.")
        print("  If you have an NVIDIA GPU, you almost certainly installed the")
        print("  CPU-only build of torch. Fix it with:")
        print("    pip uninstall -y torch torchaudio")
        print("    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124")
        print()

    if requested == "cuda" and not cuda_ok:
        print("  --device cuda requested but unavailable, falling back to cpu\n")
        return "cpu", False
    if requested == "cpu":
        return "cpu", False
    return ("cuda", True) if cuda_ok else ("cpu", False)


def load_model(name: str, device: str):
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        import whisper
    except ImportError:
        print("ERROR: openai-whisper not installed")
        print("       pip install -U openai-whisper")
        return None
    print(f"loading whisper '{name}' on {device} (first run downloads the weights)")
    _MODEL = whisper.load_model(name, device=device)
    return _MODEL


def transcribe_one(path: Path, out_dir: Path, model, lang: str | None,
                   fp16: bool = False) -> Result:
    out_file = out_dir / f"{path.stem}.txt"
    size_mb = path.stat().st_size / 1e6

    if out_file.exists():
        text = out_file.read_text(encoding="utf-8")
        print(f"  . skipped (already done — delete the .txt to redo)")
        return Result(str(path), path.stem, "skipped", None, len(text), 0.0, str(out_file))

    t0 = time.time()
    try:
        print(f"  . transcribing {size_mb:.0f} MB — this takes a while")
        # language=None lets whisper auto-detect. These lessons mix Bangla and
        # English, so auto-detect usually beats forcing one language.
        res = model.transcribe(str(path), language=lang, verbose=False, fp16=fp16)
    except Exception as e:
        print(f"  ! {type(e).__name__}: {e}")
        return Result(str(path), path.stem, "failed", None, 0,
                      round(time.time() - t0, 1), None, str(e))

    text = (res.get("text") or "").strip()
    detected = res.get("language", "unknown")
    elapsed = round(time.time() - t0, 1)

    if not text:
        print("  ! empty transcript (no speech detected?)")
        return Result(str(path), path.stem, "failed", detected, 0, elapsed, None,
                      "empty transcript")

    header = (f"# source_file: {path.name}\n"
              f"# language: {detected}\n"
              f"# whisper_seconds: {elapsed}\n\n")
    out_file.write_text(header + text, encoding="utf-8")
    print(f"  + {len(text)} chars, language={detected}, {elapsed}s -> {out_file.name}")

    return Result(str(path), path.stem, "done", detected, len(text), elapsed, str(out_file))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Transcribe downloaded lesson videos with Whisper.")
    ap.add_argument("--media-dir", required=True, type=Path,
                    help="folder containing the downloaded videos")
    ap.add_argument("--out", type=Path,
                    help="output folder for .txt transcripts")
    ap.add_argument("--model", default="medium",
                    choices=["tiny", "base", "small", "medium", "large"],
                    help="whisper model size (default: medium)")
    ap.add_argument("--lang", default=None,
                    help="force a language code, e.g. bn or en. "
                         "Default is auto-detect, which suits mixed speech.")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"],
                    help="auto (default) uses GPU when available")
    ap.add_argument("--list", action="store_true",
                    help="just list the files that would be transcribed")
    args = ap.parse_args()

    if not args.media_dir.exists():
        print(f"folder not found: {args.media_dir}")
        return 1

    files = find_media(args.media_dir)
    if not files:
        print(f"no media files in {args.media_dir}")
        print(f"looked for: {' '.join(sorted(MEDIA_EXT))}")
        return 1

    if args.list:
        total = 0.0
        print(f"{len(files)} file(s) in {args.media_dir}:\n")
        for f in files:
            mb = f.stat().st_size / 1e6
            total += mb
            print(f"  {mb:>8.0f} MB  {f.name}")
        print(f"\n  {total:>8.0f} MB  total")
        return 0

    if args.out is None:
        print("--out is required (or use --list)")
        return 1

    if not check_ffmpeg():
        print("WARNING: ffmpeg not found on PATH. Whisper needs it to read video.")
        print("         Windows:  winget install ffmpeg")
        print("         Ubuntu:   sudo apt install ffmpeg")
        print()

    args.out.mkdir(parents=True, exist_ok=True)

    device, fp16 = resolve_device(args.device)
    model = load_model(args.model, device)
    if model is None:
        return 1

    print(f"\n{len(files)} file(s) to process\n")
    results = []
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name}")
        results.append(transcribe_one(f, args.out, model, args.lang, fp16))

    manifest = args.out / "manifest.json"
    manifest.write_text(
        json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    done = sum(1 for r in results if r.status == "done")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = sum(1 for r in results if r.status == "failed")

    print(f"\n{'=' * 60}")
    print(f"done {done}   skipped {skipped}   failed {failed}")
    print(f"transcripts: {args.out}")
    print(f"manifest:    {manifest}")
    if failed:
        print("\nfailures are listed in the manifest with an error field")
    if done or skipped:
        print(f"\nnext:  python tools/style_guide/measure_style.py --transcripts {args.out} --md")
    return 0


if __name__ == "__main__":
    sys.exit(main())