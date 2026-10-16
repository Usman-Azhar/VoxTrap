<#
Runs every model on every .wav clip and saves transcripts + timings.

Usage (PowerShell, from the repo root):
  .\scripts\stt\run_tests.ps1 -WhisperDir "C:\path\to\whisper\Release" -ClipsDir "C:\path\to\clips"

Output:
  results\stt\outputs\<model>\<clip>.txt   transcript for each clip
  results\stt\outputs\timings.csv          time taken per clip and model
#>
param(
    [Parameter(Mandatory = $true)] [string]$WhisperDir,
    [Parameter(Mandatory = $true)] [string]$ClipsDir,
    [string]$OutDir = ""
)

# Default output folder: <repo>\results\stt\outputs (computed here because
# $PSScriptRoot can be empty inside the param block on some PowerShell versions)
if (-not $OutDir) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $OutDir = Join-Path $scriptDir "..\..\results\stt\outputs"
}

# Models to test. Name is used as the output folder name.
# A name containing "roman" is scored against the Roman Urdu reference by compare_wer.py.
$models = @(
    @{ Name = "base";           File = "ggml-base.bin";           Lang = "ur"   },
    @{ Name = "roman-small";    File = "ggml-model-q4_0.bin";     Lang = "auto" },
    @{ Name = "small-q5_1";     File = "ggml-small-q5_1.bin";     Lang = "ur"   },
    @{ Name = "medium";         File = "ggml-medium.bin";         Lang = "ur"   },
    @{ Name = "large-v3-turbo"; File = "ggml-large-v3-turbo.bin"; Lang = "ur"   }
    # @{ Name = "medium-urdu"; File = "ggml-medium-urdu.bin"; Lang = "ur" }
) 

$cli = Join-Path $WhisperDir "whisper-cli.exe"
if (-not (Test-Path $cli)) { Write-Error "whisper-cli.exe not found in $WhisperDir"; exit 1 }

$clips = @(Get-ChildItem -Path $ClipsDir -Filter *.wav)
if ($clips.Count -eq 0) { Write-Error "No .wav files found in $ClipsDir"; exit 1 }

if (-not [System.IO.Path]::IsPathRooted($OutDir)) { $OutDir = Join-Path (Get-Location).Path $OutDir }
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
[System.IO.Directory]::CreateDirectory($OutDir) | Out-Null
if (-not (Test-Path -LiteralPath $OutDir)) { Write-Error "Could not create output folder $OutDir"; exit 1 }
$timingFile = Join-Path $OutDir "timings.csv"
"clip,model,seconds,audio_seconds,realtime_factor" | Set-Content -Encoding UTF8 $timingFile

foreach ($m in $models) {
    $modelPath = Join-Path $WhisperDir $m.File
    if (-not (Test-Path $modelPath)) {
        Write-Warning "Skipping $($m.Name): $($m.File) not found in $WhisperDir"
        continue
    }
    $modelOut = Join-Path $OutDir $m.Name
    [System.IO.Directory]::CreateDirectory($modelOut) | Out-Null

    foreach ($clip in $clips) {
        $prefix = Join-Path $modelOut $clip.BaseName
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        # -nt = no timestamps, -otxt -of = write <prefix>.txt directly (keeps Urdu text as UTF-8)
        & $cli -m $modelPath -f $clip.FullName -l $m.Lang -nt -otxt -of $prefix 2>$null | Out-Null
        $sw.Stop()

        # approx. duration for 16 kHz mono 16-bit WAV (44-byte header)
        $audioSec = [math]::Round(($clip.Length - 44) / 32000, 2)
        $sec = [math]::Round($sw.Elapsed.TotalSeconds, 2)
        $rtf = if ($audioSec -gt 0) { [math]::Round($sec / $audioSec, 2) } else { 0 }

        "$($clip.Name),$($m.Name),$sec,$audioSec,$rtf" | Add-Content -Encoding UTF8 $timingFile
        Write-Host "$($m.Name)  $($clip.Name)  ${sec}s  (realtime factor $rtf)"
    }
}
Write-Host "Done. Transcripts are in $OutDir"