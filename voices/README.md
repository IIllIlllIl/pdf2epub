# Local Voice Bank

Put locally usable TTS voices here. Each voice uses one directory:

```text
voices/
  voice_id/
    metadata.json
    reference.wav
    reference.txt
    sample.wav
```

Required for generation:

- `reference.wav`: 10-30 seconds of clear single-speaker audio.
- `reference.txt`: exact transcript of `reference.wav`.

Recommended for manual selection:

- `sample.wav`: a short generated preview from the same test paragraph.
- `metadata.json`: source and license information.

Example `metadata.json`:

```json
{
  "id": "aishell3-s0001",
  "title": "AISHELL-3 S0001",
  "language": "zh-CN",
  "gender": "female",
  "style": "calm narration",
  "source": "AISHELL-3",
  "license": "Apache-2.0",
  "tags": ["mandarin", "neutral", "candidate"],
  "description": "Candidate voice for long-form magazine narration.",
  "reference_audio": "reference.wav",
  "sample_audio": "sample.wav"
}
```

Commands:

```bash
conda run -n pdf2epub python src/pdf2epub.py --list-voices
conda run -n pdf2epub python src/pdf2epub.py --voice-gallery
conda run -n pdf2epub python src/pdf2epub.py --audio-only --md-input output/clean_md/example.md --tts-voice aishell3-s0001
```

Fish Audio public model samples can be imported for internal evaluation:

```bash
conda run -n pdf2epub python src/pdf2epub.py --import-fish-voice 0f08cacd3e354471a4b94dd00b4cc4a3
```

Imported Fish voices are marked with `license: unknown`; confirm permission before production use.
