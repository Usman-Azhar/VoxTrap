#!/usr/bin/env python3
"""
VoxTrap - test Whisper output against the rules.

Usage (run from the repo root):
  python scripts/test_whisper.py --text data/audio/out_medium.txt        # score an existing transcript
  python scripts/test_whisper.py data/audio/Sample.wav                   # run Whisper (medium) + score
  python scripts/test_whisper.py data/audio/Sample.wav --model all       # compare every model found
  python scripts/test_whisper.py data/audio/Sample.wav --model small --prompt "OTP, CNIC, Easypaisa"
"""
import argparse, csv, json, re, shutil, subprocess, sys, time, unicodedata, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_FILE = ROOT / "rules" / "rules.json"
AUDIO_DIR = ROOT / "data" / "audio"
RESULTS_FILE = ROOT / "results" / "whisper_test.csv"

SAFE_BELOW, SCAM_FROM = 2, 4      # provisional: <2 safe, 2-3 suspicious, >=4 scam
POS_CAP, NEG_FLOOR = 4, -4        # max positive points per concept / max protective discount

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------- rules + normalizer (must match the one used to build rules.json) ----------------
URDU_TR = {ord("ي"): "ی", ord("ى"): "ی", ord("ك"): "ک", ord("ه"): "ہ", ord("ۃ"): "ہ",
           ord("ؤ"): "و", ord("أ"): "ا", ord("إ"): "ا"}
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

def load_rules():
    data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    token_map = data["meta"]["normalizer"]["roman_token_map"]
    rules = [dict(r, rx=re.compile(r["regex"])) for r in data["entries"]]
    return rules, token_map

def normalize(text, token_map):
    s = unicodedata.normalize("NFKC", str(text)).lower()
    s = re.sub("[\u064B-\u065F\u0670\u0640\u200c\u200d\u200e\u200f]", "", s)
    s = s.translate(URDU_TR).translate(DIGITS)
    s = re.sub(r"\bo\W{0,2}t\W{0,2}p\b", "otp", s)
    s = re.sub(r"\bc\W{0,2}n\W{0,2}i\W{0,2}c\b", "cnic", s)
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)
    s = re.sub(r"[^\w\s]|_", " ", s)
    return " ".join(token_map.get(t, t) for t in s.split())

def score_text(text, rules, token_map):
    norm = normalize(text, token_map)
    per_concept, hits = {}, []
    for r in rules:
        m = r["rx"].search(norm)
        if m:
            per_concept[r["concept"]] = per_concept.get(r["concept"], 0) + r["weight"]
            hits.append((r["id"], r["concept"], r["weight"], m.group(0)[:45]))
    total = 0
    for concept, v in per_concept.items():
        total += max(v, NEG_FLOOR) if concept == "PROTECTIVE" else min(v, POS_CAP)
    level = "SAFE" if total < SAFE_BELOW else ("SUSPICIOUS" if total < SCAM_FROM else "SCAM")
    return total, level, hits

# ---------------- whisper ----------------
def locate_whisper(custom):
    """Find whisper-cli anywhere inside the repo (or inside --whisper-dir). Works with or without a Release folder."""
    base = Path(custom) if custom else ROOT
    for name in ("whisper-cli.exe", "whisper-cli"):
        for p in base.rglob(name):
            if ".git" not in p.parts:
                return p
    sys.exit(f"whisper-cli.exe not found under {base}.\n"
             f"Put your Whisper folder inside the repo, or run with:  --whisper-dir \"C:\\path\\to\\your\\whisper folder\"")

def find_models(choice, folder):
    found = {p.stem.replace("ggml-", ""): p for p in folder.rglob("ggml-*.bin")}
    if not found:
        found = {p.stem.replace("ggml-", ""): p for p in ROOT.rglob("ggml-*.bin") if ".git" not in p.parts}
    if not found:
        sys.exit(f"No ggml-*.bin model files found near {folder}.")
    if choice == "all":
        order = ["tiny", "base", "small", "medium", "large-v3-turbo"]
        return [(n, found[n]) for n in order if n in found] + [(n, p) for n, p in found.items() if n not in order]
    if choice not in found:
        sys.exit(f"Model '{choice}' not found. Available: {', '.join(found)}")
    return [(choice, found[choice])]

def ensure_16k_mono(wav):
    try:
        with wave.open(str(wav)) as w:
            if w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2:
                return wav
    except Exception:
        pass
    ff = shutil.which("ffmpeg")
    if not ff:
        sys.exit("Audio must be 16 kHz mono WAV and ffmpeg was not found. Install it (winget install ffmpeg) and retry.")
    out = wav.with_name(wav.stem + "_16k.wav")
    res = subprocess.run([ff, "-y", "-i", str(wav), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        sys.exit("ffmpeg could not convert the audio:\n" + res.stderr[-600:])
    return out

def run_whisper(exe, model_name, model_path, wav, prompt):
    out_base = AUDIO_DIR / f"out_{wav.stem.replace('_16k', '')}_{model_name}"
    cmd = [str(exe), "-m", str(model_path), "-f", str(wav), "-l", "ur", "-nt", "-otxt", "-of", str(out_base)]
    if prompt:
        cmd += ["--prompt", prompt]
    t0 = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    secs = time.time() - t0
    txt = Path(str(out_base) + ".txt")
    if res.returncode != 0 or not txt.exists():
        sys.exit(f"Whisper failed ({model_name}):\n{res.stderr[-800:]}")
    return txt.read_text(encoding="utf-8").strip(), secs

# ---------------- output ----------------
def show(label, text, secs, total, level, hits):
    print("=" * 70)
    print(f"{label}" + (f"   ({secs:.1f} sec)" if secs is not None else ""))
    print("-" * 70)
    print(text)
    print("-" * 70)
    print(f"SCORE: {total}   LEVEL: {level}")
    for rid, concept, w, snippet in hits:
        print(f"  {rid:4s} {concept:16s} {w:+d}   matched: {snippet}")
    if not hits:
        print("  (no rule matched)")

def save_row(source, model, secs, total, level, hits, text):
    RESULTS_FILE.parent.mkdir(exist_ok=True)
    new = not RESULTS_FILE.exists()
    with open(RESULTS_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["source", "model", "seconds", "score", "level", "rules_matched", "transcript"])
        w.writerow([source, model, f"{secs:.1f}" if secs is not None else "", total, level,
                    " ".join(h[0] for h in hits), text.replace("\n", " ")])

def main():
    ap = argparse.ArgumentParser(description="Test Whisper output against the VoxTrap rules")
    ap.add_argument("audio", nargs="?", help="WAV file to transcribe with Whisper")
    ap.add_argument("--text", help="score an existing transcript .txt instead of running Whisper")
    ap.add_argument("--model", default="medium", help="tiny | base | small | medium | large-v3-turbo | all")
    ap.add_argument("--prompt", help="optional vocabulary hint for Whisper")
    ap.add_argument("--whisper-dir", help="folder containing whisper-cli.exe and the models (only if auto-detect fails)")
    a = ap.parse_args()
    if not a.audio and not a.text:
        ap.error("give a WAV file or --text file.txt")

    rules, token_map = load_rules()
    if a.text:
        text = Path(a.text).read_text(encoding="utf-8", errors="replace").strip()
        total, level, hits = score_text(text, rules, token_map)
        show(a.text, text, None, total, level, hits)
        save_row(a.text, "(text file)", None, total, level, hits, text)
        return

    audio = Path(a.audio)
    if not audio.exists():
        here = sorted(p.name for p in AUDIO_DIR.glob("*")) if AUDIO_DIR.exists() else []
        sys.exit(f"Audio file not found: {audio.resolve()}\nFiles in {AUDIO_DIR}: {here if here else 'folder is empty or missing'}")
    wav = ensure_16k_mono(audio)
    exe = locate_whisper(a.whisper_dir)
    print(f"Using Whisper: {exe}")
    for name, path in find_models(a.model, exe.parent):
        text, secs = run_whisper(exe, name, path, wav, a.prompt)
        total, level, hits = score_text(text, rules, token_map)
        show(f"{Path(a.audio).name} | model: {name}", text, secs, total, level, hits)
        save_row(Path(a.audio).name, name, secs, total, level, hits, text)
    print(f"\nSaved to {RESULTS_FILE}")

if __name__ == "__main__":
    main()