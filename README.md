# VoxTrap

Android app that listens to calls on-device, converts speech to text, and flags
OTP/payment scam calls. Nothing leaves the phone.

## Part 3: scam detection (Usman)
Rule-based detector for Urdu script and Roman Urdu text (77 rules in `rules/`).
Scores a transcript and returns SAFE, SUSPICIOUS or SCAM.

## Folders
- `rules/`: rules.json (used by code), rules.csv (for reading)
- `scripts/`: test_whisper.py; `scripts/stt/` has the STT test tools
- `results/stt/`: transcripts, timings and score tables
- Not in Git: `data/` (confidential datasets), `whisper/` (models, exe)

## Quick start
python scripts/test_whisper.py --text path/to/transcript.txt
python scripts/test_whisper.py data/audio/clip.wav --model medium
python scripts/stt/score_outputs.py