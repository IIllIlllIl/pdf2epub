#!/usr/bin/env python3
"""PDF to EPUB converter with Claude CLI-based text cleaning.

Usage:
    docker build -f src/Dockerfile -t pdf2epub .
    docker run --rm -v "$PWD":/workspace pdf2epub --check-tools
    docker run --rm -v "$PWD":/workspace pdf2epub --input input/raw_pdf/xxx.pdf
    docker run --rm -v "$PWD":/workspace pdf2epub --all
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Paths
ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT / "input" / "raw_pdf"
OCR_DIR = ROOT / "output" / "ocr_pdf"
SIDECAR_DIR = ROOT / "output" / "sidecar_txt"
MD_DIR = ROOT / "output" / "clean_md"
OUTPUT_DIR = ROOT / "output" / "epub"
LOG_DIR = ROOT / "output" / "logs" / "run"
CSS_PATH = ROOT / "src" / "epub.css"
PROMPT_PATH = ROOT / "src" / "clean_prompt.md"

# Settings
DEFAULT_LANG = "chi_sim+eng"
TOOL_NAMES = ["python3", "claude", "ocrmypdf", "tesseract", "pandoc"]
CONTAINER_MARKER = Path("/.dockerenv")
CLEAN_CHUNK_MAX_CHARS = 8000


@dataclass
class StageResult:
    status: str
    output: str | None = None
    error: str | None = None
    exit_code: int | None = None
    command: list[str] | None = None
    reused: bool = False
    stats: dict[str, int] | None = None

    def to_dict(self) -> dict:
        data = {
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "exit_code": self.exit_code,
            "command": self.command,
            "reused": self.reused,
            "stats": self.stats or {},
        }
        return {k: v for k, v in data.items() if v not in (None, False, {})}


@dataclass
class BookResult:
    input_file: str
    base_name: str
    started_at: str
    finished_at: str | None = None
    status: str = "pending"
    stages: dict[str, StageResult] | None = None

    def to_dict(self) -> dict:
        return {
            "input_file": self.input_file,
            "base_name": self.base_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "stages": {k: v.to_dict() for k, v in (self.stages or {}).items()},
        }


def ensure_directories() -> None:
    for path in (INPUT_DIR, OCR_DIR, SIDECAR_DIR, MD_DIR, OUTPUT_DIR, LOG_DIR, CSS_PATH.parent):
        path.mkdir(parents=True, exist_ok=True)

    legacy_chunk_dirs = [
        ROOT / "work" / "clean_md_chunks",
        ROOT / "output" / "clean_md_chunks",
    ]
    for legacy_chunk_dir in legacy_chunk_dirs:
        if legacy_chunk_dir.exists():
            shutil.rmtree(legacy_chunk_dir)


def detect_tools() -> dict[str, str | None]:
    return {name: shutil.which(name) for name in TOOL_NAMES}


def run_command(
    command: list[str],
    step: str,
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            input=input_text,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{step}: command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"{step}: exit {exc.returncode}: {msg}") from exc


def load_prompt_template() -> str:
    if not PROMPT_PATH.exists():
        raise RuntimeError(f"Prompt template not found: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def extract_token_usage(result: dict) -> tuple[int, int]:
    usage = result.get("usage")
    if isinstance(usage, dict):
        return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))

    model_usage = result.get("modelUsage")
    if not isinstance(model_usage, dict):
        return 0, 0

    input_tokens = 0
    output_tokens = 0
    for item in model_usage.values():
        if not isinstance(item, dict):
            continue
        input_tokens += int(item.get("inputTokens", 0))
        output_tokens += int(item.get("outputTokens", 0))
    return input_tokens, output_tokens


def parse_claude_json_output(stdout: str) -> dict:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"llm_clean: invalid JSON from claude: {exc}") from exc


def extract_claude_error(stdout: str, stderr: str) -> str:
    parsed_stdout = stdout.strip()
    if parsed_stdout:
        try:
            result = parse_claude_json_output(parsed_stdout)
        except RuntimeError:
            pass
        else:
            message = str(result.get("result") or result.get("message") or "").strip()
            if message:
                return f"llm_clean: {message}"

    fallback = (stderr or stdout).strip()
    return f"llm_clean: {fallback}" if fallback else "llm_clean: claude command failed"


def call_claude_cli(text: str, prompt_template: str) -> tuple[str, dict[str, int]]:
    full_prompt = f"{prompt_template}\n\n# 输入文本\n\n{text}"
    command = ["claude", "-p", "--output-format", "json"]

    env = dict(os.environ)
    env.pop("CLAUDECODE", None)

    try:
        completed = run_command(command, "llm_clean", env=env, input_text=full_prompt)
    except RuntimeError as exc:
        cause = exc.__cause__
        if isinstance(cause, subprocess.CalledProcessError):
            raise RuntimeError(extract_claude_error(cause.stdout or "", cause.stderr or "")) from exc
        raise RuntimeError(str(exc)) from exc

    stdout = completed.stdout.strip()
    if not stdout:
        raise RuntimeError("llm_clean: empty stdout from claude")

    result = parse_claude_json_output(stdout)

    if result.get("is_error"):
        error_text = result.get("result") or result.get("message") or "Claude CLI returned an error"
        raise RuntimeError(f"llm_clean: {error_text}")

    cleaned_text = str(result.get("result", "")).strip()
    input_tokens, output_tokens = extract_token_usage(result)
    stats = {
        "input_chars": len(text),
        "output_chars": len(cleaned_text),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    return cleaned_text, stats


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


def normalize_chunk_markdown(cleaned: str, title: str, keep_title: bool) -> str:
    normalized = cleaned.strip()
    title_line = f"# {title}"

    if keep_title:
        if normalized.startswith("# "):
            return normalized
        return f"{title_line}\n\n{normalized}" if normalized else title_line

    lines = normalized.splitlines()
    if lines and lines[0].strip() == title_line:
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


def stage_ocr(input_pdf: Path, ocr_pdf: Path, sidecar_txt: Path, language: str, skip_ocr: bool) -> StageResult:
    if skip_ocr and sidecar_txt.exists() and sidecar_txt.stat().st_size > 0:
        reused_output = ocr_pdf if ocr_pdf.exists() else sidecar_txt
        return StageResult(
            status="skipped",
            output=str(reused_output),
            reused=True,
            stats={"sidecar_bytes": sidecar_txt.stat().st_size},
        )

    command = [
        "ocrmypdf", "-l", language,
        "--rotate-pages", "--deskew", "--clean",
        "--sidecar", str(sidecar_txt),
        str(input_pdf), str(ocr_pdf),
    ]

    try:
        run_command(command, "ocr")
    except RuntimeError as exc:
        code = exc.__cause__.returncode if isinstance(exc.__cause__, subprocess.CalledProcessError) else None
        return StageResult(status="failed", error=str(exc), exit_code=code, command=command)

    stats = {
        "ocr_pdf_bytes": ocr_pdf.stat().st_size if ocr_pdf.exists() else 0,
        "sidecar_bytes": sidecar_txt.stat().st_size if sidecar_txt.exists() else 0,
    }
    return StageResult(status="ok", output=str(ocr_pdf), command=command, stats=stats)


def stage_clean_llm(sidecar_txt: Path, markdown_path: Path, title: str) -> StageResult:
    if not sidecar_txt.exists():
        return StageResult(status="failed", error=f"Missing sidecar: {sidecar_txt}")

    raw_text = sidecar_txt.read_text(encoding="utf-8", errors="ignore")
    if not raw_text.strip():
        return StageResult(status="failed", error=f"Empty sidecar: {sidecar_txt}")

    pages = split_sidecar_pages(raw_text)
    if not pages:
        return StageResult(status="failed", error=f"No non-empty pages found in sidecar: {sidecar_txt}")

    chunks = group_pages_for_cleaning(pages, CLEAN_CHUNK_MAX_CHARS)

    try:
        prompt_template = load_prompt_template()
    except RuntimeError as exc:
        return StageResult(status="failed", error=str(exc))

    normalized_chunks: list[str] = []
    aggregated_stats = {
        "input_chars": 0,
        "output_chars": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }

    for index, chunk in enumerate(chunks, start=1):
        try:
            cleaned_chunk, chunk_stats = call_claude_cli(chunk, prompt_template)
        except RuntimeError as exc:
            return StageResult(status="failed", error=f"llm_clean: chunk {index}/{len(chunks)} failed: {exc}")

        aggregated_stats["input_chars"] += chunk_stats.get("input_chars", 0)
        aggregated_stats["input_tokens"] += chunk_stats.get("input_tokens", 0)
        aggregated_stats["output_tokens"] += chunk_stats.get("output_tokens", 0)

        if not cleaned_chunk:
            continue

        normalized_chunk = normalize_chunk_markdown(cleaned_chunk, title, keep_title=not normalized_chunks)
        if normalized_chunk:
            normalized_chunks.append(normalized_chunk)

    cleaned = "\n\n".join(normalized_chunks).strip()
    if not cleaned:
        return StageResult(status="failed", error="llm_clean: empty merged markdown")

    markdown_path.write_text(cleaned, encoding="utf-8")

    aggregated_stats["output_chars"] = len(cleaned)
    aggregated_stats["output_paragraphs"] = len([p for p in cleaned.split("\n\n") if p.strip()])
    return StageResult(status="ok", output=str(markdown_path), stats=aggregated_stats)


def stage_epub(markdown_path: Path, epub_path: Path, title: str) -> StageResult:
    if not markdown_path.exists():
        return StageResult(status="failed", error=f"Missing markdown: {markdown_path}")
    if not CSS_PATH.exists():
        return StageResult(status="failed", error=f"Missing CSS: {CSS_PATH}")

    command = [
        "pandoc", str(markdown_path),
        "--from", "markdown", "--to", "epub3",
        "--css", str(CSS_PATH),
        "--toc",
        "--metadata", f"title={title}",
        "-o", str(epub_path),
    ]

    try:
        run_command(command, "epub")
    except RuntimeError as exc:
        code = exc.__cause__.returncode if isinstance(exc.__cause__, subprocess.CalledProcessError) else None
        return StageResult(status="failed", error=str(exc), exit_code=code, command=command)

    return StageResult(
        status="ok",
        output=str(epub_path),
        command=command,
        stats={"epub_bytes": epub_path.stat().st_size if epub_path.exists() else 0},
    )


def cleanup_success_outputs(ocr_pdf: Path, markdown_path: Path) -> None:
    for path in (ocr_pdf, markdown_path):
        if path.exists():
            path.unlink()


def process_book(input_pdf: Path, language: str, skip_ocr: bool) -> BookResult:
    started_at = datetime.now().isoformat(timespec="seconds")
    base_name = input_pdf.stem
    result = BookResult(
        input_file=str(input_pdf),
        base_name=base_name,
        started_at=started_at,
        stages={},
    )

    ocr_pdf = OCR_DIR / f"{base_name}.ocr.pdf"
    sidecar_txt = SIDECAR_DIR / f"{base_name}.txt"
    markdown_path = MD_DIR / f"{base_name}.md"
    epub_path = OUTPUT_DIR / f"{base_name}.epub"

    ocr_result = stage_ocr(input_pdf, ocr_pdf, sidecar_txt, language, skip_ocr)
    result.stages["ocr"] = ocr_result
    if ocr_result.status == "failed":
        result.status = "failed"
        result.finished_at = datetime.now().isoformat(timespec="seconds")
        return result

    clean_result = stage_clean_llm(sidecar_txt, markdown_path, base_name)
    result.stages["llm_clean"] = clean_result
    if clean_result.status == "failed":
        result.status = "failed"
        result.finished_at = datetime.now().isoformat(timespec="seconds")
        return result

    epub_result = stage_epub(markdown_path, epub_path, base_name)
    result.stages["epub"] = epub_result
    result.status = "ok" if epub_result.status == "ok" else "failed"
    if result.status == "ok":
        cleanup_success_outputs(ocr_pdf, markdown_path)
    result.finished_at = datetime.now().isoformat(timespec="seconds")
    return result


def iter_input_pdfs(single_input: str | None, process_all: bool):
    if single_input:
        candidate = Path(single_input).expanduser()
        if not candidate.is_absolute():
            candidate = (ROOT / candidate).resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"PDF not found: {candidate}")
        yield candidate
        return

    if process_all:
        yield from sorted(INPUT_DIR.glob("*.pdf"))
        return

    raise ValueError("Use --input <pdf> or --all")


def print_tool_status() -> int:
    detected = detect_tools()
    missing = [name for name, path in detected.items() if not path]
    print(f"container_env: {'yes' if CONTAINER_MARKER.exists() else 'no'}")
    for name, path in detected.items():
        print(f"{name}: {path or 'MISSING'}")
    return 1 if missing else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OCR scanned PDFs and convert to EPUB with Claude CLI cleaning.",
        epilog=(
            "Usage:\n"
            "  docker build -f src/Dockerfile -t pdf2epub .\n"
            "  docker run --rm -v \"$PWD\":/workspace pdf2epub --check-tools\n"
            "  docker run --rm -v \"$PWD\":/workspace pdf2epub --all\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--input", help="Process a single PDF file.")
    parser.add_argument("--all", action="store_true", help="Process all PDFs in input/raw_pdf/.")
    parser.add_argument("--skip-ocr", action="store_true", help="Reuse existing sidecar text.")
    parser.add_argument("--lang", default=DEFAULT_LANG, help=f"OCR language. Default: {DEFAULT_LANG}")
    parser.add_argument("--check-tools", action="store_true", help="Check tool availability.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_directories()

    if args.check_tools:
        return print_tool_status()

    try:
        inputs = list(iter_input_pdfs(args.input, args.all))
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not inputs:
        print(f"No PDF files found in {INPUT_DIR}", file=sys.stderr)
        return 2

    started_at = datetime.now().isoformat(timespec="seconds")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "language": args.lang,
        "skip_ocr": args.skip_ocr,
        "inputs": [str(p) for p in inputs],
        "books": [],
    }

    had_failure = False
    for input_pdf in inputs:
        book_result = process_book(input_pdf, args.lang, args.skip_ocr)
        summary["books"].append(book_result.to_dict())
        if book_result.status != "ok":
            had_failure = True

        status = book_result.status.upper()
        print(f"[{status}] {input_pdf.name}")
        for stage_name, stage in (book_result.stages or {}).items():
            detail = stage.output or stage.error or ""
            print(f"  - {stage_name}: {stage.status} {detail}".rstrip())

    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    summary_path = LOG_DIR / f"{run_id}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary log: {summary_path}")
    return 1 if had_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
