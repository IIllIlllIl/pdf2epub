#!/usr/bin/env python3
"""Test each sidecar chunk against Claude to identify sensitive-content rejections."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIDECAR_PATH = ROOT / "output" / "sidecar_txt" / "中国新闻周刊2026年第18期.txt"
PROMPT_PATH = ROOT / "src" / "clean_prompt.md"
CLEAN_CHUNK_MAX_CHARS = 8000


def split_sidecar_pages(raw_text: str) -> list[str]:
    return [page.strip() for page in raw_text.split("\f") if page.strip()]


def group_pages_for_cleaning(pages: list[str], max_chunk_chars: int) -> list[str]:
    chunks: list[str] = []
    current_pages: list[str] = []
    current_length = 0

    for page in pages:
        page_length = len(page)
        separator_length = 2 if current_pages else 0
        next_length = current_length + separator_length + page_length

        if current_pages and next_length > max_chunk_chars:
            chunks.append("\n\n".join(current_pages))
            current_pages = []
            current_length = 0

        if page_length > max_chunk_chars:
            if current_pages:
                chunks.append("\n\n".join(current_pages))
                current_pages = []
                current_length = 0
            chunks.append(page)
            continue

        current_pages.append(page)
        current_length += (2 if len(current_pages) > 1 else 0) + page_length

    if current_pages:
        chunks.append("\n\n".join(current_pages))

    return chunks


def test_chunk_with_claude(chunk: str, prompt_template: str) -> tuple[str, str | None]:
    """Returns (status, error_or_empty). status is 'ok' or 'rejected'."""
    full_prompt = f"{prompt_template}\n\n# 输入文本\n\n{chunk}"
    command = ["claude", "-p", "--output-format", "json"]
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)

    try:
        result = subprocess.run(
            command,
            input=full_prompt,
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        stdout = exc.stdout or ""
        if "high risk" in stderr or "high risk" in stdout:
            return "rejected", "high risk"
        return "rejected", (stderr or stdout).strip()[:200]
    except FileNotFoundError:
        return "rejected", "claude command not found"

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "ok", None

    if parsed.get("is_error"):
        msg = str(parsed.get("result") or parsed.get("message") or "").strip()
        if "high risk" in msg:
            return "rejected", "high risk"
        return "rejected", msg[:200]

    cleaned = str(parsed.get("result", "")).strip()
    return ("ok", None) if cleaned else ("rejected", "empty output")


def main() -> int:
    if not SIDECAR_PATH.exists():
        print(f"Sidecar not found: {SIDECAR_PATH}", file=sys.stderr)
        return 1

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    raw_text = SIDECAR_PATH.read_text(encoding="utf-8", errors="ignore")
    pages = split_sidecar_pages(raw_text)
    chunks = group_pages_for_cleaning(pages, CLEAN_CHUNK_MAX_CHARS)

    print(f"Total pages: {len(pages)}")
    print(f"Total chunks: {len(chunks)}")
    print()

    rejected_indices: list[int] = []
    for index, chunk in enumerate(chunks, start=1):
        print(f"Testing chunk {index}/{len(chunks)} ({len(chunk)} chars) ... ", end="", flush=True)
        status, error = test_chunk_with_claude(chunk, prompt_template)
        if status == "ok":
            print("OK")
        else:
            print(f"REJECTED: {error}")
            rejected_indices.append(index)

    print()
    print(f"Rejected chunks ({len(rejected_indices)}/{len(chunks)}): {rejected_indices}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
