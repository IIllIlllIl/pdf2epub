# pdf2epub

[中文](README.md) | [English](README.en.md)

Convert scanned Chinese magazine PDFs into mobile-friendly EPUB files, cleaned Markdown, and optional narrated WAV audio.

## Architecture

- **OCR**: `ocrmypdf + tesseract` using the `tesseract` OCR backend.
- **Text cleanup**: Claude CLI by default, or Codex CLI with `--agent codex`.
- **EPUB generation**: `pandoc`.
- **Audio generation**: `fish-mlx`, loaded once through a persistent `mlx-speech` worker in a separate `tts-mlx` conda environment.
- **Runtime**: local conda by default, Docker optionally.

## Directory Layout

```text
pdf2epub/
  input/               # PDFs to process
    archived_pdf/      # Successfully processed input PDFs
  output/
    epub/              # Latest EPUB outputs
    archived_epub/     # Replaced EPUB outputs
    ocr_pdf/           # OCR PDFs; cleaned after successful full runs
    sidecar_txt/       # OCR text, reusable with --skip-ocr
    clean_md/          # Cleaned Markdown, kept by default
    audio/             # Latest narrated WAV audio
    archived_audio/    # Replaced WAV outputs
    voice_gallery.html # Local voice selection page
    logs/run/          # PDF/EPUB run logs
    logs/audio/        # Audio generation logs
  voices/              # Local voice bank; only README.md is meant for git
  src/
    pdf2epub.py        # Main CLI
    clean_prompt.md    # LLM cleanup prompt
    epub.css           # EPUB stylesheet
    Dockerfile         # Optional Docker image
  run.sh               # Optional Docker wrapper
```

## Quick Start

### 1. Create Environments

```bash
conda create -y -n pdf2epub python=3.11 pandoc tesseract ocrmypdf -c conda-forge
conda run -n pdf2epub python -m pip install fpdf2 pi-heif
npm install -g @anthropic-ai/claude-code
```

Install Codex CLI separately if you want `--agent codex`: `npm install -g @openai/codex`.

For Markdown-to-audio generation, create a separate MLX Speech environment:

```bash
conda create -y -n tts-mlx python=3.13
conda run -n tts-mlx python -m pip install mlx-speech
```

Optional Docker build:

```bash
colima start  # macOS
docker build -f src/Dockerfile -t pdf2epub .
```

### 2. Authenticate LLM CLI

Claude CLI is the default cleanup agent. Make sure one of:

- `ANTHROPIC_API_KEY` is set, or
- `ANTHROPIC_AUTH_TOKEN` is set.

For Codex CLI, use `--agent codex` and make sure either:

- `codex login` has been completed locally, or
- `OPENAI_API_KEY` is set.

### 3. Check Tools

```bash
conda run -n pdf2epub python src/pdf2epub.py --check-tools
```

Expected tools include `claude`, `ocrmypdf`, `tesseract`, and `pandoc`.

### 4. Common Commands

```bash
# Process one PDF into EPUB.
conda run -n pdf2epub python src/pdf2epub.py --input input/example.pdf

# Process all PDFs in input/.
conda run -n pdf2epub python src/pdf2epub.py --all

# Generate Markdown only; useful before audio tests.
conda run -n pdf2epub python src/pdf2epub.py --all --md-only

# Reuse existing OCR sidecar text.
conda run -n pdf2epub python src/pdf2epub.py --all --skip-ocr

# Switch OCR language. Default is Simplified Chinese + English.
conda run -n pdf2epub python src/pdf2epub.py --all --lang chi_sim+eng

# Use Codex instead of Claude.
conda run -n pdf2epub python src/pdf2epub.py --all --agent codex

# Generate audio after PDF -> EPUB succeeds.
conda run -n pdf2epub python src/pdf2epub.py --input input/example.pdf --audio

# Generate audio from existing Markdown.
conda run -n pdf2epub python src/pdf2epub.py --audio-only --md-input output/clean_md/example.md

# List local voices and build the local voice selection page.
conda run -n pdf2epub python src/pdf2epub.py --list-voices
conda run -n pdf2epub python src/pdf2epub.py --voice-gallery

# Import a public Fish Audio sample for internal evaluation.
conda run -n pdf2epub python src/pdf2epub.py --import-fish-voice 0f08cacd3e354471a4b94dd00b4cc4a3
```

## CLI Options

| Option | Description |
|------|------|
| `--input <pdf>` | Process one PDF. |
| `--all` | Process all top-level PDFs in `input/`. |
| `--md-input <md>` | Generate audio from Markdown. Repeat it to process multiple files in one audio batch. |
| `--all-md` | Generate audio from all Markdown files in `output/clean_md/`. |
| `--skip-ocr` | Reuse existing sidecar OCR text. |
| `--md-only` | Run OCR and LLM cleanup only; skip EPUB, audio, and input archiving. |
| `--audio` | Generate audio after PDF -> EPUB succeeds. |
| `--audio-only` | Generate audio from Markdown only. `output/audio/` keeps only this run's successful outputs. If a same-stem PDF exists in `input/`, archive it after successful audio generation. |
| `--keep-md` / `--no-keep-md` | Keep intermediate Markdown. Default: keep. |
| `--lang <lang>` | Tesseract OCR language. Default: `chi_sim+eng`. Use `eng` for English, `chi_tra+eng` for Traditional Chinese if installed. |
| `--ocr-engine <tesseract>` | OCR backend. Currently only `tesseract`. |
| `--agent <codex\|claude>` | LLM cleanup agent. Default: `claude`. |
| `--tts-engine <fish-mlx>` | TTS engine. Currently only `fish-mlx`. |
| `--tts-conda-env <env>` | TTS conda environment. Default: `tts-mlx`. |
| `--tts-model <model>` | `mlx-speech` model name. Default: `fish-s2-pro`. |
| `--tts-chunk-chars <n>` | Max characters per TTS chunk. Default: `120`, a conservative value tested on this local MLX setup. |
| `--tts-voice <voice_id>` | Use `voices/<voice_id>/reference.*` and `reference.txt` for local voice cloning. |
| `--tts-reference-audio <wav>` | One-off reference audio for Fish S2 Pro cloning. |
| `--tts-reference-text <text>` | Transcript for `--tts-reference-audio`. Must be provided together. |
| `--tts-max-new-tokens <n>` | Max generated tokens per TTS chunk. Default: `512`. |
| `--tts-max-runtime-hours <hours>` | Maximum TTS batch runtime. Default: `5` hours. On expiry, stop the worker, release resources, and keep completed parts so the same command can resume. |
| `--audio-format <wav>` | Audio format. Currently only `wav`. |
| `--keep-audio-parts` | Keep per-chunk WAV files after merging. |
| `--list-voices` | List local voices under `voices/`. |
| `--voice-gallery` | Generate `output/voice_gallery.html`. |
| `--import-fish-voice <id\|url>` | Import public Fish Audio sample audio/text into `voices/` for internal evaluation. |
| `--overwrite-voice` | Allow `--import-fish-voice` to replace an existing voice directory. |
| `--check-tools` | Check tool availability. |

## Processing Flow

1. **OCR**: `ocrmypdf` runs Tesseract and writes an OCR PDF plus sidecar text. If `unpaper` exists, OCR cleanup is enabled; otherwise cleanup is skipped to avoid hard failure.
2. **LLM cleanup**: sidecar text is grouped by pages and cleaned by the selected CLI agent into Markdown. Default: `claude -p --output-format json`; with `--agent codex` uses `codex exec`.
3. **Markdown-only**: with `--md-only`, stop here and keep the input PDF in place.
4. **EPUB**: `pandoc` converts Markdown into EPUB.
5. **Audio**: `--audio` or `--audio-only` starts one persistent TTS worker and reuses the loaded Fish S2 Pro model across chunks/files. Markdown is normalized for TTS before synthesis, including abnormal whitespace/punctuation cleanup, heading/paragraph pause tags, and more readable number phrasing. The default runtime limit is 5 hours; on expiry the worker stops and completed parts remain available for a validated resume with the same command. If a same-name WAV already exists, it is archived to `output/archived_audio/` before the new WAV is placed in `output/audio/`.
6. **Archive stale EPUBs**: `output/epub/` keeps only the latest successful batch; older EPUBs move to `output/archived_epub/`.
7. **Archive stale WAVs**: audio runs keep only the latest successful batch in `output/audio/`; older WAVs move to `output/archived_audio/`.
8. **Archive inputs**: successful full PDF runs move processed PDFs to `input/archived_pdf/`. Successful `--audio-only` runs also archive a same-stem PDF if it exists in `input/`.

## Local Voice Selection

Each local candidate voice uses this layout:

```text
voices/<voice_id>/
  metadata.json
  reference.wav
  reference.txt
  sample.wav
```

- `reference.wav` and `reference.txt` are used for actual TTS generation.
- `sample.wav` is used by the voice gallery for manual review.
- `metadata.json` stores source, license, language, gender, style, and notes.

Generate the gallery:

```bash
conda run -n pdf2epub python src/pdf2epub.py --voice-gallery
```

Open `output/voice_gallery.html`, choose a `voice_id`, then run:

```bash
conda run -n pdf2epub python src/pdf2epub.py --audio-only --md-input output/clean_md/example.md --tts-voice <voice_id>
```

Fish Audio public model samples can be imported for internal evaluation:

```bash
conda run -n pdf2epub python src/pdf2epub.py --import-fish-voice <model_id_or_url>
```

Imported Fish voices are marked `license: unknown - confirm permission before production use`. Public preview access does not imply long-term reuse rights.

## Git Ignore Policy

The repository intentionally ignores local input data and generated outputs:

- `input/`: source PDFs and archived PDFs.
- `output/`: OCR PDFs, sidecar text, Markdown, EPUB, audio, galleries, and logs.
- `voices/*`: local voice samples and imported third-party audio.
- `!voices/README.md`: keep only the voice bank documentation.

This keeps large files, private PDFs, generated audio, logs, and third-party voice samples out of git.

## Validation Checklist

- `output/sidecar_txt/*.txt` can be reused with `--skip-ocr`.
- `output/clean_md/*.md` is retained by default.
- `output/epub/*.epub` opens correctly.
- `output/audio/*.wav` plays correctly when audio is generated.
- `input/archived_pdf/` contains successfully processed input PDFs.
- `output/archived_audio/` contains replaced WAV outputs.
- Run logs include token usage and stage outputs.

## Known Limits

- OCR mistakes may remain.
- Complex multi-column layouts may still be ordered imperfectly.
- LLM cleanup consumes Codex/Claude tokens.
- `input/` PDF filename stems are assumed to be unique.
- `output/epub/` represents the latest successful batch, not a permanent archive of all outputs.
- `output/audio/` represents the latest successful audio batch; previous WAV outputs are archived to `output/archived_audio/`.
