#!/usr/bin/env python3
"""
VoxTrap - score every STT transcript in results/stt/outputs/ with the rules.

Run from the repo root:
    python scripts/stt/score_outputs.py

Reads : results/stt/outputs/<model>/<clip>.txt   (made by run_tests.ps1)
        results/stt/outputs/timings.csv          (optional)
        results/stt/refs.csv                     (optional: clip, expected, reference)
Writes: results/stt/rules_scores.csv   one row per model x clip
        results/stt/summary.csv        one row per model
"""
import csv, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import test_whisper as tw          # reuses the same rules + normalizer as the live script

OUT_DIR = ROOT / "results" / "stt" / "outputs"
REFS = ROOT / "results" / "stt" / "refs.csv"
DETAIL = ROOT / "results" / "stt" / "rules_scores.csv"
SUMMARY = ROOT / "results" / "stt" / "summary.csv"

def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def wer(ref, hyp):
    r, h = ref.split(), hyp.split()
    if not r:
        return None
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / len(r)

def verdict(expected, level):
    if expected == "SCAM":
        return {"SCAM": "OK", "SUSPICIOUS": "PARTIAL", "SAFE": "MISSED"}[level]
    if expected == "SAFE":
        return "OK" if level == "SAFE" else "FALSE ALARM"
    return ""

def main():
    rules, tmap = tw.load_rules()
    refs = {r["clip"].strip().lower().removesuffix(".wav"): r for r in read_csv(REFS)}
    timing = {(r["clip"].lower().removesuffix(".wav"), r["model"]): r for r in read_csv(OUT_DIR / "timings.csv")}

    rows = []
    for model_dir in sorted(p for p in OUT_DIR.iterdir() if p.is_dir()):
        for txt in sorted(model_dir.glob("*.txt")):
            clip = txt.stem.lower()
            text = txt.read_text(encoding="utf-8-sig", errors="replace").strip()
            score, level, hits = tw.score_text(text, rules, tmap)
            ref = refs.get(clip, {})
            expected = (ref.get("expected") or "").strip().upper()
            reference = (ref.get("reference") or "").strip()
            w = wer(tw.normalize(reference, tmap), tw.normalize(text, tmap)) if reference else None
            t = timing.get((clip, model_dir.name), {})
            rows.append({"model": model_dir.name, "clip": txt.stem, "score": score, "level": level,
                         "expected": expected, "verdict": verdict(expected, level),
                         "rules": " ".join(h[0] for h in hits),
                         "wer": "" if w is None else round(w, 2),
                         "seconds": t.get("seconds", ""), "rtf": t.get("realtime_factor", ""),
                         "transcript": text.replace("\n", " ")})

    if not rows:
        sys.exit(f"No transcripts found in {OUT_DIR}")
    with open(DETAIL, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)

    print(f"\n{'model':<14}{'clip':<8}{'score':>6}  {'level':<11}{'expected':<9}{'verdict':<12}rules")
    for r in rows:
        print(f"{r['model']:<14}{r['clip']:<8}{r['score']:>6}  {r['level']:<11}{r['expected']:<9}{r['verdict']:<12}{r['rules']}")

    summary = []
    for m in sorted({r["model"] for r in rows}):
        mr = [r for r in rows if r["model"] == m]
        judged = [r for r in mr if r["verdict"]]
        rtf = [float(r["rtf"]) for r in mr if r["rtf"] != ""]
        wers = [float(r["wer"]) for r in mr if r["wer"] != ""]
        summary.append({"model": m, "clips": len(mr), "judged": len(judged),
                        "ok": sum(r["verdict"] == "OK" for r in judged),
                        "partial": sum(r["verdict"] == "PARTIAL" for r in judged),
                        "missed": sum(r["verdict"] == "MISSED" for r in judged),
                        "false_alarm": sum(r["verdict"] == "FALSE ALARM" for r in judged),
                        "avg_rtf": round(sum(rtf) / len(rtf), 2) if rtf else "",
                        "avg_wer": round(sum(wers) / len(wers), 2) if wers else ""})
    with open(SUMMARY, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=list(summary[0])); wr.writeheader(); wr.writerows(summary)
    print(f"\n{'model':<14}{'clips':>6}{'ok':>5}{'partial':>9}{'missed':>8}{'false':>7}{'avg_rtf':>9}{'avg_wer':>9}")
    for s in summary:
        print(f"{s['model']:<14}{s['clips']:>6}{s['ok']:>5}{s['partial']:>9}{s['missed']:>8}{s['false_alarm']:>7}{str(s['avg_rtf']):>9}{str(s['avg_wer']):>9}")
    print(f"\nSaved: {DETAIL}\n       {SUMMARY}")

if __name__ == "__main__":
    main()