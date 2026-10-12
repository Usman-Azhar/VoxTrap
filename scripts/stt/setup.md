# Speech-to-text (STT) setup: whisper.cpp

This folder documents how we test offline speech-to-text for VoxTrap using
[whisper.cpp](https://github.com/ggml-org/whisper.cpp). Model files, the whisper
binaries and audio clips are **not** stored in this repo (see `.gitignore`).
Download them using the steps below.

## 1. Get whisper.cpp (Windows)

1. Download the Windows release zip from
   https://github.com/ggml-org/whisper.cpp/releases
2. Extract it somewhere **outside** this repo (for example `C:\whisper\Release`).
   It should contain `whisper-cli.exe` and several `.dll` files.

## 2. Download the models

Put every model file in the same folder as `whisper-cli.exe`.

| base | `ggml-base.bin` | `-l ur` | https://huggingface.co/ggerganov/whisper.cpp |
| small-q5_1 | `ggml-small-q5_1.bin` (~181 MB) | `-l ur` | https://huggingface.co/ggerganov/whisper.cpp |
| roman-small | `ggml-model-q4_0.bin` (~139 MB) | `-l auto` | https://huggingface.co/femustafa/voicedictation-models |
| medium-urdu (optional, 1.4 GB) | `ggml-medium-urdu.bin` | `-l ur` | https://huggingface.co/CodeWithAhsan/whisper-medium-urdu-ggml |

Notes:
- Use the multilingual models, not the `.en` ones (English only).
- The Roman Urdu model must run with `-l auto`. Forcing `-l ur` makes it output Urdu script.

## 3. Prepare test audio

Whisper needs 16 kHz mono WAV:

```powershell
ffmpeg -i clip.m4a -ar 16000 -ac 1 -c:a pcm_s16le clip.wav
```

Record 20 to 30 clips: scam-style lines and normal speech. Include some played
through a phone speaker and re-recorded to imitate a call. Keep clips in a local
folder that is not pushed (`.wav` files are git-ignored).

## 4. Run one clip manually

```powershell
.\whisper-cli.exe -m ggml-small-q5_1.bin -f ver1.wav -l ur
.\whisper-cli.exe -m ggml-model-q4_0.bin -f ver1.wav -l auto
```

## 5. Run all models on all clips

From the repo root:

```powershell
.\scripts\stt\run_tests.ps1 -WhisperDir "C:\whisper\Release" -ClipsDir "C:\path\to\clips"
```

Transcripts go to `results/stt/outputs/<model>/<clip>.txt` and timings to
`results/stt/outputs/timings.csv`.

## 6. Score the results

1. Fill in `results/stt/refs.csv` with what is actually said in each clip.
   Use the `reference_roman` column for the Roman Urdu model and `reference_urdu`
   for the Urdu-script models. A row can have just one of them.
2. Optionally edit `results/stt/keywords.txt`.
3. Run:
   ```powershell
   pip install -r scripts/stt/requirements.txt
   python scripts/stt/compare_wer.py
   ```
   This prints a table and saves `results/stt/summary.csv`.

A realtime factor (RTF) near or above 1.0 on a laptop means the model will be
too slow for real-time use on a phone.

## 7. What to commit

Commit: scripts, `results/stt/*.md`, `refs.csv`, `keywords.txt`, `summary.csv`,
transcripts in `results/stt/outputs/`.
Never commit: `.bin` models, `.exe`/`.dll` files, `.wav` audio, the whisper folder.