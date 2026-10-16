#!/usr/bin/env python3
"""
VoxTrap - automatic pipeline:  microphone -> Whisper -> rules -> scam verdict (in the terminal)

Setup once:   pip install sounddevice numpy

Run from the repo root:
  python scripts/live_pipeline.py                      # record one message (Enter to start / stop), then check it
  python scripts/live_pipeline.py --live               # continuous: listens in chunks, rolling 30-second window
  python scripts/live_pipeline.py --live --model small-q5_1 --chunk 6
  python scripts/live_pipeline.py --list-mics          # show microphones, then use --device N

Audio is deleted right after it is transcribed (add --keep to save it in data/audio/).
"""
import argparse, collections, datetime, os, queue, subprocess, sys, threading, time, wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_whisper as tw          # same rules, normalizer, and Whisper finder as the other scripts

SR = 16000
TMP_DIR = tw.ROOT / "data" / "audio" / "_live_tmp"

USE_COLOR = sys.stdout.isatty()
if USE_COLOR and os.name == "nt":
    os.system("")                  # enables ANSI colours in Windows terminals
COL = {"red": "\033[1;31m", "yellow": "\033[1;33m", "green": "\033[1;32m", "dim": "\033[2m", "end": "\033[0m"}
def paint(s, c): return f"{COL[c]}{s}{COL['end']}" if USE_COLOR else s

CONCEPT_LABEL = {
    "SENSITIVE_INFO": "asks for sensitive info", "PAYMENT_DEMAND": "demands money",
    "IMPERSONATION": "claims to be bank/authority", "THREAT": "threat / account blocking",
    "URGENCY": "pressure / urgency", "LURE": "prize / offer lure", "PRETEXT": "verification pretext",
    "LINK_REMOTE": "link / app request", "SECRECY": "asks you to keep it secret",
    "FAMILY_EMERGENCY": "family-emergency money request", "PROTECTIVE": "protective warning (lowers score)",
}

# ---------------- audio ----------------
def need_audio_libs():
    try:
        import numpy as np, sounddevice as sd
        return np, sd
    except ImportError:
        sys.exit("Missing packages. Run:  pip install sounddevice numpy")

def write_wav(path, audio):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(audio.astype("int16").tobytes())

def record_once(np, sd, device):
    blocks = []
    def cb(indata, frames, t, status): blocks.append(indata.copy())
    input(paint("\nPress Enter to START recording... ", "dim"))
    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", device=device, callback=cb):
        input(paint("* RECORDING - speak now. Press Enter to STOP... ", "red"))
    return np.concatenate(blocks)[:, 0] if blocks else np.zeros(0, dtype="int16")

# ---------------- whisper ----------------
def transcribe(exe, model_path, wav, lang, prompt, keep):
    base = wav.with_suffix("")
    cmd = [str(exe), "-m", str(model_path), "-f", str(wav), "-l", lang, "-nt", "-otxt", "-of", str(base)]
    if prompt:
        cmd += ["--prompt", prompt]
    t0 = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    secs = time.time() - t0
    txt = Path(str(base) + ".txt")
    if res.returncode != 0 or not txt.exists():
        raise RuntimeError("Whisper failed:\n" + res.stderr[-500:])
    text = txt.read_text(encoding="utf-8", errors="replace").strip()
    if not keep:
        for p in (wav, txt):
            try: p.unlink()
            except OSError: pass
    return text, secs

def looks_bad(text):
    """Whisper sometimes loops one phrase or invents text on noise. Skip those."""
    toks = text.split()
    return len(toks) == 0 or (len(toks) >= 8 and len(set(toks)) / len(toks) < 0.3)

# ---------------- report ----------------
def flagged(hits):
    return sorted(hits, key=lambda h: -abs(h[2]))

def print_flags(hits, indent="  "):
    for rid, concept, w, snippet in flagged(hits):
        print(f"{indent}{rid:4s} {w:+d}  {CONCEPT_LABEL.get(concept, concept):<34} -> \"{snippet}\"")

def banner(level, score):
    if level == "SCAM":
        return paint(f"*** SCAM CALL DETECTED (score {score}) - do NOT share OTP, PIN, CNIC or card details ***", "red")
    if level == "SUSPICIOUS":
        return paint(f"!! SUSPICIOUS call (score {score}) - be careful, do not share anything !!", "yellow")
    return paint(f"OK - no scam signs found (score {score})", "green")

def report_once(text, secs, score, level, hits):
    print("\n" + "=" * 70)
    print(f"Transcript ({secs:.1f}s): {text}")
    print("-" * 70)
    if hits:
        print("Flagged:")
        print_flags(hits)
    else:
        print("Flagged: nothing")
    print("-" * 70)
    print(banner(level, score))
    print("=" * 70)

# ---------------- modes ----------------
def mode_once(a, np, sd, rules, tmap, exe, model_path):
    audio = record_once(np, sd, a.device)
    if len(audio) < SR // 2:
        sys.exit("Recording too short or empty. Check the microphone (use --list-mics).")
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    wav = TMP_DIR / f"rec_{datetime.datetime.now():%H%M%S}.wav"
    write_wav(wav, audio)
    print(f"Recorded {len(audio) / SR:.1f}s. Transcribing with {model_path.stem}... (first run may take a while)")
    text, secs = transcribe(exe, model_path, wav, a.lang, a.prompt, a.keep)
    if looks_bad(text):
        print("Whisper returned empty or unclear text. Try again, speak closer to the mic.")
        return
    score, level, hits = tw.score_text(text, rules, tmap)
    report_once(text, secs, score, level, hits)

def mode_live(a, np, sd, rules, tmap, exe, model_path):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    work_q, audio_q = queue.Queue(), queue.Queue()
    window = collections.deque(maxlen=max(1, int(a.window // a.chunk)))
    state = {"max": 0, "level": "SAFE", "alerts": 0, "n": 0}

    def worker():
        while True:
            audio = work_q.get()
            if audio is None:
                return
            state["n"] += 1
            now = datetime.datetime.now().strftime("%H:%M:%S")
            rms = float(np.sqrt(np.mean((audio.astype("float32") / 32768.0) ** 2)))
            if rms < a.silence:
                print(paint(f"[{now}] (silence)", "dim")); continue
            wav = TMP_DIR / f"chunk_{state['n']}.wav"
            write_wav(wav, audio)
            try:
                text, secs = transcribe(exe, model_path, wav, a.lang, a.prompt, a.keep)
            except RuntimeError as e:
                print(paint(f"[{now}] {e}", "red")); continue
            if looks_bad(text):
                print(paint(f"[{now}] (unclear audio)", "dim")); continue
            window.append(text)
            score, level, hits = tw.score_text(" ".join(window), rules, tmap)
            print(f"[{now}] ({secs:.1f}s) {text}")
            lag = work_q.qsize()
            if lag >= 2:
                print(paint(f"      note: Whisper is slower than real time ({lag} chunks waiting). Use a smaller model.", "yellow"))
            print(f"      window score {score}: {banner(level, score)}")
            if hits:
                print_flags(hits, indent="        ")
            if level == "SCAM" and state["level"] != "SCAM":
                state["alerts"] += 1
            state["max"] = max(state["max"], score); state["level"] = level

    th = threading.Thread(target=worker, daemon=True); th.start()
    chunk_n, buf, got, total = int(a.chunk * SR), [], 0, 0
    limit = int(a.seconds * SR) if a.seconds else None

    def cb(indata, frames, t, status): audio_q.put(indata[:, 0].copy())
    print(paint(f"Listening (chunk {a.chunk:g}s, window {a.window:g}s, model {model_path.stem}). Press Ctrl+C to stop.\n", "dim"))
    try:
        with sd.InputStream(samplerate=SR, channels=1, dtype="int16", device=a.device, callback=cb):
            while limit is None or total < limit:
                try:
                    blk = audio_q.get(timeout=0.5)
                except queue.Empty:
                    continue
                buf.append(blk); got += len(blk); total += len(blk)
                if got >= chunk_n:
                    work_q.put(np.concatenate(buf)); buf, got = [], 0
    except KeyboardInterrupt:
        print("\nStopping...")
    if got >= SR:
        work_q.put(np.concatenate(buf))
    work_q.put(None); th.join()
    print("\n" + "=" * 70)
    print(f"Session finished. Highest score: {state['max']}   Scam alerts raised: {state['alerts']}   Last level: {state['level']}")
    print("=" * 70)

def main():
    ap = argparse.ArgumentParser(description="VoxTrap: mic -> Whisper -> rules -> scam verdict")
    ap.add_argument("--live", action="store_true", help="continuous listening with a rolling window")
    ap.add_argument("--model", default="medium", help="medium | small-q5_1 | base | large-v3-turbo | ... (name after 'ggml-')")
    ap.add_argument("--lang", default="ur", help="Whisper language: ur (default) or auto")
    ap.add_argument("--chunk", type=float, default=6, help="live: seconds per chunk (default 6)")
    ap.add_argument("--window", type=float, default=30, help="live: seconds of recent speech to score together (default 30)")
    ap.add_argument("--seconds", type=float, default=0, help="live: stop automatically after N seconds")
    ap.add_argument("--silence", type=float, default=0.006, help="live: skip chunks quieter than this (default 0.006)")
    ap.add_argument("--device", help="microphone number or name (see --list-mics)")
    ap.add_argument("--list-mics", action="store_true")
    ap.add_argument("--prompt", help="optional vocabulary hint for Whisper")
    ap.add_argument("--keep", action="store_true", help="keep audio and text files in data/audio/_live_tmp")
    ap.add_argument("--whisper-dir", help="folder with whisper-cli.exe and the models (if auto-detect fails)")
    a = ap.parse_args()

    np, sd = need_audio_libs()
    if a.list_mics:
        print(sd.query_devices()); return
    if a.device is not None and a.device.isdigit():
        a.device = int(a.device)

    rules, tmap = tw.load_rules()
    exe = tw.locate_whisper(a.whisper_dir)
    model_name, model_path = tw.find_models(a.model, exe.parent)[0]
    print(f"Whisper: {exe}\nModel:   {model_path.name}")
    if a.live and model_name in ("medium", "large-v3-turbo", "large-v3", "large"):
        print(paint("Tip: this model is likely slower than real time on a laptop CPU. For live mode try --model small-q5_1.", "yellow"))
    (mode_live if a.live else mode_once)(a, np, sd, rules, tmap, exe, model_path)

if __name__ == "__main__":
    main()