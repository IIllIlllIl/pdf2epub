#!/usr/bin/env python3
"""JSONL worker for MLX Speech TTS.

This script runs inside the TTS conda environment. It loads the TTS model once,
then receives one JSON request per line on stdin and writes one JSON response
per line on stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mlx_speech.audio import write_wav
from mlx_speech.tts import load


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent MLX Speech TTS worker.")
    parser.add_argument("--model", required=True, help="mlx-speech model alias, repo, or local path.")
    parser.add_argument("--codec", default=None, help="Optional codec model path or repo.")
    return parser.parse_args()


def write_response(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> int:
    args = parse_args()

    try:
        model = load(args.model, codec_path_or_repo=args.codec)
    except Exception as exc:  # noqa: BLE001 - worker must report startup failures.
        write_response({"type": "ready", "status": "failed", "error": str(exc)})
        return 1

    write_response({"type": "ready", "status": "ok", "model": args.model})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            write_response({"type": "response", "status": "failed", "error": f"invalid JSON: {exc}"})
            continue

        request_id = request.get("id")
        if request.get("type") == "shutdown":
            write_response({"type": "shutdown", "status": "ok", "id": request_id})
            return 0

        try:
            text = str(request["text"])
            output_path = Path(str(request["output"]))

            generate_kwargs = {}
            reference_audio = request.get("reference_audio")
            reference_text = request.get("reference_text")
            if reference_audio or reference_text:
                if not reference_audio or not reference_text:
                    raise ValueError("reference_audio and reference_text must both be provided")
                generate_kwargs["reference_audio"] = str(reference_audio)
                generate_kwargs["reference_text"] = str(reference_text)

            max_new_tokens = request.get("max_new_tokens")
            if max_new_tokens is not None:
                generate_kwargs["max_new_tokens"] = int(max_new_tokens)

            result = model.generate(text, **generate_kwargs)
            write_wav(output_path, result.waveform, sample_rate=result.sample_rate)
        except Exception as exc:  # noqa: BLE001 - return task failures to parent.
            write_response({"type": "response", "status": "failed", "id": request_id, "error": str(exc)})
            continue

        write_response({
            "type": "response",
            "status": "ok",
            "id": request_id,
            "output": str(output_path),
            "sample_rate": result.sample_rate,
        })

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
