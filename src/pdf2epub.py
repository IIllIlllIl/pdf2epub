#!/usr/bin/env python3
"""PDF to EPUB converter with CLI agent-based text cleaning.

Usage:
    conda run -n pdf2epub python src/pdf2epub.py --check-tools
    conda run -n pdf2epub python src/pdf2epub.py --input input/xxx.pdf
    conda run -n pdf2epub python src/pdf2epub.py --all
    conda run -n pdf2epub python src/pdf2epub.py --all --md-only
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import wave
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Paths
ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT / "input"
ARCHIVED_INPUT_DIR = ROOT / "input" / "archived_pdf"
OCR_DIR = ROOT / "output" / "ocr_pdf"
SIDECAR_DIR = ROOT / "output" / "sidecar_txt"
MD_DIR = ROOT / "output" / "clean_md"
OUTPUT_DIR = ROOT / "output" / "epub"
AUDIO_DIR = ROOT / "output" / "audio"
AUDIO_PARTS_DIR = AUDIO_DIR / "parts"
VOICES_DIR = ROOT / "voices"
ARCHIVED_EPUB_DIR = ROOT / "output" / "archived_epub"
LOG_DIR = ROOT / "output" / "logs" / "run"
AUDIO_LOG_DIR = ROOT / "output" / "logs" / "audio"
VOICE_GALLERY_PATH = ROOT / "output" / "voice_gallery.html"
CSS_PATH = ROOT / "src" / "epub.css"
PROMPT_PATH = ROOT / "src" / "clean_prompt.md"

# Settings
DEFAULT_LANG = "chi_sim+eng"
DEFAULT_OCR_ENGINE = "tesseract"
OCR_ENGINE_CHOICES = ("tesseract",)
DEFAULT_KEEP_MD = True
DEFAULT_AGENT = "codex"
AGENT_CHOICES = ("codex", "claude")
DEFAULT_TTS_ENGINE = "fish-mlx"
TTS_ENGINE_CHOICES = ("fish-mlx",)
DEFAULT_TTS_CONDA_ENV = "tts-mlx"
DEFAULT_TTS_MODEL = "fish-s2-pro"
DEFAULT_TTS_CHUNK_CHARS = 120
DEFAULT_TTS_MAX_NEW_TOKENS = 512
DEFAULT_AUDIO_FORMAT = "wav"
SUPPORTED_AUDIO_FORMATS = ("wav",)
TTS_WORKER_PATH = ROOT / "src" / "tts_worker.py"
BASE_TOOL_NAMES = ["python3", "pandoc"]
OCR_ENGINE_TOOL_NAMES = {
    "tesseract": ["ocrmypdf", "tesseract"],
}
CONTAINER_MARKER = Path("/.dockerenv")
CLEAN_CHUNK_MAX_CHARS = 8000
FILENAME_TIMESTAMP_SUFFIX_RE = re.compile(r"--\d{8}-\d{6}(?:-\d+)?$")


@dataclass
class StageResult:
    status: str
    output: str | None = None
    error: str | None = None
    exit_code: int | None = None
    command: list[str] | None = None
    reused: bool = False
    stats: dict[str, float | int | str] | None = None

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


@dataclass
class VoiceProfile:
    voice_id: str
    directory: Path
    title: str
    reference_audio: Path | None
    reference_text: str | None
    sample_audio: Path | None
    metadata: dict


def ensure_directories() -> None:
    for path in (
        INPUT_DIR,
        ARCHIVED_INPUT_DIR,
        OCR_DIR,
        SIDECAR_DIR,
        MD_DIR,
        OUTPUT_DIR,
        AUDIO_DIR,
        AUDIO_PARTS_DIR,
        VOICES_DIR,
        ARCHIVED_EPUB_DIR,
        LOG_DIR,
        AUDIO_LOG_DIR,
        CSS_PATH.parent,
    ):
        path.mkdir(parents=True, exist_ok=True)

    legacy_chunk_dirs = [
        ROOT / "work" / "clean_md_chunks",
        ROOT / "output" / "clean_md_chunks",
    ]
    for legacy_chunk_dir in legacy_chunk_dirs:
        if legacy_chunk_dir.exists():
            shutil.rmtree(legacy_chunk_dir)


def tool_names_for_run(ocr_engine: str, agent: str) -> list[str]:
    return [*BASE_TOOL_NAMES, *OCR_ENGINE_TOOL_NAMES.get(ocr_engine, []), agent]


def detect_tools(ocr_engine: str, agent: str) -> dict[str, str | None]:
    return {name: shutil.which(name) for name in tool_names_for_run(ocr_engine, agent)}


def detect_tts_cli(tts_conda_env: str) -> str | None:
    if not shutil.which("conda"):
        return None
    try:
        completed = subprocess.run(
            ["conda", "run", "-n", tts_conda_env, "which", "mlx-speech"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


def resolve_conda_env_python(conda_env: str) -> str:
    try:
        completed = subprocess.run(
            ["conda", "run", "-n", conda_env, "python", "-c", "import sys; print(sys.executable)"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"audio: cannot resolve Python for conda env {conda_env!r}") from exc
    python_path = completed.stdout.strip()
    if not python_path:
        raise RuntimeError(f"audio: empty Python path for conda env {conda_env!r}")
    return python_path


def resolve_relative_path(base_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    return candidate


def read_optional_text(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    return text or None


def display_title_from_stem(stem: str) -> str:
    return FILENAME_TIMESTAMP_SUFFIX_RE.sub("", stem).strip() or stem


def load_voice_profile(voice_dir: Path) -> VoiceProfile:
    metadata_path = voice_dir / "metadata.json"
    metadata: dict = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"voice: invalid metadata JSON: {metadata_path}: {exc}") from exc

    voice_id = str(metadata.get("id") or voice_dir.name).strip()
    if not voice_id:
        raise RuntimeError(f"voice: empty voice id in {voice_dir}")

    title = str(metadata.get("title") or voice_id).strip() or voice_id
    reference_audio = resolve_relative_path(
        voice_dir,
        str(metadata.get("reference_audio")) if metadata.get("reference_audio") else None,
    )
    if reference_audio is None:
        for candidate_name in ("reference.wav", "reference.mp3", "reference.m4a"):
            candidate = voice_dir / candidate_name
            if candidate.exists():
                reference_audio = candidate.resolve()
                break

    reference_text = metadata.get("reference_text")
    if reference_text is not None:
        reference_text = str(reference_text).strip() or None
    if reference_text is None:
        reference_text = read_optional_text(voice_dir / "reference.txt")

    sample_audio = resolve_relative_path(
        voice_dir,
        str(metadata.get("sample_audio")) if metadata.get("sample_audio") else None,
    )
    if sample_audio is None:
        for candidate_name in ("sample.wav", "sample.mp3", "preview.wav", "preview.mp3"):
            candidate = voice_dir / candidate_name
            if candidate.exists():
                sample_audio = candidate.resolve()
                break

    return VoiceProfile(
        voice_id=voice_id,
        directory=voice_dir.resolve(),
        title=title,
        reference_audio=reference_audio,
        reference_text=reference_text,
        sample_audio=sample_audio,
        metadata=metadata,
    )


def iter_voice_profiles() -> list[VoiceProfile]:
    profiles: list[VoiceProfile] = []
    if not VOICES_DIR.exists():
        return profiles
    for voice_dir in sorted(path for path in VOICES_DIR.iterdir() if path.is_dir()):
        profiles.append(load_voice_profile(voice_dir))
    return profiles


def get_voice_profile(voice_id: str) -> VoiceProfile:
    normalized = voice_id.strip()
    if not normalized:
        raise RuntimeError("voice: --tts-voice cannot be empty")
    if "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        raise RuntimeError(f"voice: invalid voice id: {voice_id}")

    voice_dir = (VOICES_DIR / normalized).resolve()
    try:
        voice_dir.relative_to(VOICES_DIR.resolve())
    except ValueError as exc:
        raise RuntimeError(f"voice: invalid voice path for {voice_id}") from exc
    if not voice_dir.exists() or not voice_dir.is_dir():
        raise RuntimeError(f"voice: not found: {normalized} ({voice_dir})")

    profile = load_voice_profile(voice_dir)
    if profile.reference_audio is None or not profile.reference_audio.exists():
        raise RuntimeError(f"voice: missing reference audio for {normalized}")
    if not profile.reference_text:
        raise RuntimeError(f"voice: missing reference text for {normalized}")
    return profile


def resolve_tts_reference(
    *,
    tts_voice: str | None,
    tts_reference_audio: str | None,
    tts_reference_text: str | None,
) -> tuple[str | None, str | None, VoiceProfile | None]:
    if tts_voice and (tts_reference_audio or tts_reference_text):
        raise RuntimeError("voice: use either --tts-voice or --tts-reference-audio/--tts-reference-text, not both")
    if tts_voice:
        profile = get_voice_profile(tts_voice)
        return str(profile.reference_audio), profile.reference_text, profile
    if tts_reference_audio or tts_reference_text:
        if not tts_reference_audio or not tts_reference_text:
            raise RuntimeError("voice: --tts-reference-audio and --tts-reference-text must both be provided")
        reference_audio = Path(tts_reference_audio).expanduser()
        if not reference_audio.is_absolute():
            reference_audio = (ROOT / reference_audio).resolve()
        if not reference_audio.exists():
            raise RuntimeError(f"voice: reference audio not found: {reference_audio}")
        return str(reference_audio), tts_reference_text, None
    return None, None, None


def http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"http: GET {url} failed with {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"http: GET {url} failed: {exc.reason}") from exc


def http_get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    payload = http_get(url, headers=headers)
    try:
        data = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"http: invalid JSON from {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"http: expected JSON object from {url}")
    return data


def extension_from_url(url: str, default: str = ".mp3") -> str:
    path = urllib.parse.urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".wav", ".mp3", ".m4a", ".opus"}:
        return suffix
    return default


def import_fish_voice(model_id: str, *, overwrite: bool = False) -> Path:
    normalized_id = model_id.strip().rstrip("/").split("/")[-1]
    if not re.fullmatch(r"[0-9a-fA-F]{32}", normalized_id):
        raise RuntimeError(f"fish voice: invalid model id: {model_id}")

    model_url = f"https://api.fish.audio/model/{normalized_id}"
    model = http_get_json(model_url)
    samples = model.get("samples")
    if not isinstance(samples, list) or not samples:
        raise RuntimeError(f"fish voice: no public samples found for {normalized_id}")

    selected_sample = None
    for sample in samples:
        if isinstance(sample, dict) and sample.get("audio") and sample.get("text"):
            selected_sample = sample
            break
    if selected_sample is None:
        raise RuntimeError(f"fish voice: no sample with both audio and text for {normalized_id}")

    voice_id = f"fish-{normalized_id}"
    voice_dir = VOICES_DIR / voice_id
    if voice_dir.exists() and any(voice_dir.iterdir()) and not overwrite:
        raise RuntimeError(f"fish voice: {voice_dir} already exists; pass --overwrite-voice to replace files")
    voice_dir.mkdir(parents=True, exist_ok=True)

    audio_url = str(selected_sample["audio"])
    audio_ext = extension_from_url(audio_url)
    reference_name = f"reference{audio_ext}"
    sample_name = f"sample{audio_ext}"
    reference_path = voice_dir / reference_name
    sample_path = voice_dir / sample_name

    audio_bytes = http_get(audio_url)
    reference_path.write_bytes(audio_bytes)
    sample_path.write_bytes(audio_bytes)
    reference_text = str(selected_sample["text"]).strip()
    (voice_dir / "reference.txt").write_text(reference_text, encoding="utf-8")

    author = model.get("author") if isinstance(model.get("author"), dict) else {}
    metadata = {
        "id": voice_id,
        "fish_model_id": normalized_id,
        "title": model.get("title") or voice_id,
        "description": model.get("description") or "",
        "language": ",".join(model.get("languages") or []),
        "style": ", ".join(model.get("tags") or []),
        "source": "fish.audio",
        "source_url": f"https://fish.audio/zh-CN/m/{normalized_id}/",
        "source_api": model_url,
        "license": "unknown - confirm permission before production use",
        "author": author.get("nickname") or "",
        "fish_author_id": author.get("_id") or "",
        "tags": model.get("tags") or [],
        "visibility": model.get("visibility") or "",
        "like_count": model.get("like_count"),
        "mark_count": model.get("mark_count"),
        "task_count": model.get("task_count"),
        "reference_audio": reference_name,
        "sample_audio": sample_name,
        "reference_text": reference_text,
        "sample_title": selected_sample.get("title") or "",
        "sample_task_id": selected_sample.get("task_id") or "",
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }
    (voice_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return voice_dir


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


class TTSWorkerClient:
    def __init__(
        self,
        *,
        conda_env: str,
        model: str,
        codec: str | None = None,
    ) -> None:
        self.conda_env = conda_env
        self.model = model
        self.codec = codec
        self._next_id = 1
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self) -> "TTSWorkerClient":
        python_path = resolve_conda_env_python(self.conda_env)
        command = [
            python_path, "-u", str(TTS_WORKER_PATH),
            "--model", self.model,
        ]
        if self.codec:
            command.extend(["--codec", self.codec])

        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(ROOT),
        )

        ready = self._read_response()
        if ready.get("type") != "ready" or ready.get("status") != "ok":
            self.close()
            error = ready.get("error") or "worker did not become ready"
            raise RuntimeError(f"audio: TTS worker failed to start: {error}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _read_response(self) -> dict:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("audio: TTS worker is not running")

        line = self._process.stdout.readline()
        if not line:
            stderr = ""
            if self._process.stderr is not None:
                stderr = self._process.stderr.read().strip()
            raise RuntimeError(f"audio: TTS worker stopped unexpectedly: {stderr}")

        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"audio: invalid response from TTS worker: {line.strip()}") from exc

    def synthesize(
        self,
        *,
        text: str,
        output_path: Path,
        reference_audio: str | None = None,
        reference_text: str | None = None,
        max_new_tokens: int | None = None,
    ) -> dict:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("audio: TTS worker is not running")

        request_id = str(self._next_id)
        self._next_id += 1
        request = {
            "id": request_id,
            "text": text,
            "output": str(output_path),
            "reference_audio": reference_audio,
            "reference_text": reference_text,
            "max_new_tokens": max_new_tokens,
        }
        self._process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self._process.stdin.flush()

        response = self._read_response()
        if response.get("id") != request_id:
            raise RuntimeError(f"audio: mismatched TTS worker response id: {response}")
        if response.get("status") != "ok":
            raise RuntimeError(str(response.get("error") or "TTS worker task failed"))
        return response

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return

        try:
            if process.stdin is not None and process.poll() is None:
                process.stdin.write(json.dumps({"type": "shutdown", "id": "shutdown"}) + "\n")
                process.stdin.flush()
                process.wait(timeout=10)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def load_prompt_template() -> str:
    if not PROMPT_PATH.exists():
        raise RuntimeError(f"Prompt template not found: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def normalize_markdown_for_tts(markdown: str) -> str:
    text = re.sub(r"```.*?```", "\n", markdown, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`>|~]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text_for_tts(text: str, max_chars: int) -> list[str]:
    if max_chars < 100:
        raise ValueError("--tts-chunk-chars must be at least 100")

    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        normalized = current.strip()
        if normalized:
            chunks.append(normalized)
        current = ""

    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    for paragraph in paragraphs:
        sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])", paragraph) if item.strip()]
        if not sentences:
            sentences = [paragraph]

        for sentence in sentences:
            while len(sentence) > max_chars:
                if current:
                    flush_current()
                chunks.append(sentence[:max_chars].strip())
                sentence = sentence[max_chars:].strip()

            separator = "\n" if current else ""
            if current and len(current) + len(separator) + len(sentence) > max_chars:
                flush_current()
            current = f"{current}{separator}{sentence}" if current else sentence

        if current and len(current) + 1 <= max_chars:
            current = f"{current}\n"

    flush_current()
    return chunks


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


def extract_codex_token_usage(stdout: str) -> tuple[int, int]:
    input_tokens = 0
    output_tokens = 0

    def walk(value):
        nonlocal input_tokens, output_tokens
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = key.replace("-", "_")
                if normalized_key in {"input_tokens", "inputTokens"} and isinstance(item, int):
                    input_tokens += item
                elif normalized_key in {"output_tokens", "outputTokens"} and isinstance(item, int):
                    output_tokens += item
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        walk(event)

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


def call_codex_cli(text: str, prompt_template: str) -> tuple[str, dict[str, int]]:
    full_prompt = f"{prompt_template}\n\n# 输入文本\n\n{text}"

    with tempfile.TemporaryDirectory(prefix="pdf2epub-codex-") as temp_dir:
        output_path = Path(temp_dir) / "last-message.md"
        command = [
            "codex", "exec",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--color", "never",
            "--json",
            "--cd", str(ROOT),
            "--output-last-message", str(output_path),
            "-",
        ]

        try:
            completed = run_command(command, "llm_clean", input_text=full_prompt)
        except RuntimeError as exc:
            raise RuntimeError(str(exc)) from exc

        cleaned_text = ""
        if output_path.exists():
            cleaned_text = output_path.read_text(encoding="utf-8").strip()
        if not cleaned_text:
            cleaned_text = completed.stdout.strip()
        if not cleaned_text:
            raise RuntimeError("llm_clean: empty output from codex")

        input_tokens, output_tokens = extract_codex_token_usage(completed.stdout)

    stats = {
        "input_chars": len(text),
        "output_chars": len(cleaned_text),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    return cleaned_text, stats


def call_llm_agent(agent: str, text: str, prompt_template: str) -> tuple[str, dict[str, int]]:
    if agent == "codex":
        return call_codex_cli(text, prompt_template)
    if agent == "claude":
        return call_claude_cli(text, prompt_template)
    raise RuntimeError(f"llm_clean: unsupported agent: {agent}")


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


def stage_ocr_tesseract(input_pdf: Path, ocr_pdf: Path, sidecar_txt: Path, language: str) -> StageResult:
    command = [
        "ocrmypdf", "-l", language,
        "--rotate-pages", "--deskew",
        "--sidecar", str(sidecar_txt),
        str(input_pdf), str(ocr_pdf),
    ]
    if shutil.which("unpaper"):
        command.insert(5, "--clean")

    try:
        run_command(command, "ocr")
    except RuntimeError as exc:
        code = exc.__cause__.returncode if isinstance(exc.__cause__, subprocess.CalledProcessError) else None
        return StageResult(status="failed", error=str(exc), exit_code=code, command=command)

    stats = {
        "ocr_engine": "tesseract",
        "clean_enabled": "yes" if "--clean" in command else "no",
        "ocr_pdf_bytes": ocr_pdf.stat().st_size if ocr_pdf.exists() else 0,
        "sidecar_bytes": sidecar_txt.stat().st_size if sidecar_txt.exists() else 0,
    }
    return StageResult(status="ok", output=str(ocr_pdf), command=command, stats=stats)


def stage_ocr(
    input_pdf: Path,
    ocr_pdf: Path,
    sidecar_txt: Path,
    language: str,
    skip_ocr: bool,
    ocr_engine: str,
) -> StageResult:
    if skip_ocr and sidecar_txt.exists() and sidecar_txt.stat().st_size > 0:
        reused_output = ocr_pdf if ocr_pdf.exists() else sidecar_txt
        return StageResult(
            status="skipped",
            output=str(reused_output),
            reused=True,
            stats={"ocr_engine": ocr_engine, "sidecar_bytes": sidecar_txt.stat().st_size},
        )

    if ocr_engine == "tesseract":
        return stage_ocr_tesseract(input_pdf, ocr_pdf, sidecar_txt, language)
    return StageResult(status="failed", error=f"ocr: unsupported OCR engine: {ocr_engine}")


def stage_clean_llm(sidecar_txt: Path, markdown_path: Path, title: str, agent: str) -> StageResult:
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
            cleaned_chunk, chunk_stats = call_llm_agent(agent, chunk, prompt_template)
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


def combine_wav_files(part_paths: list[Path], output_path: Path) -> None:
    if not part_paths:
        raise RuntimeError("audio: no wav parts to combine")

    audio_format = None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as output_wav:
        for part_path in part_paths:
            with wave.open(str(part_path), "rb") as part_wav:
                part_params = part_wav.getparams()
                part_format = (
                    part_params.nchannels,
                    part_params.sampwidth,
                    part_params.framerate,
                    part_params.comptype,
                    part_params.compname,
                )
                if audio_format is None:
                    audio_format = part_format
                    output_wav.setparams(part_params)
                elif part_format != audio_format:
                    raise RuntimeError(f"audio: wav params mismatch in {part_path}")
                output_wav.writeframes(part_wav.readframes(part_wav.getnframes()))


def stage_audio_fish_mlx(
    markdown_path: Path,
    audio_path: Path,
    *,
    title: str,
    tts_conda_env: str,
    tts_model: str,
    tts_chunk_chars: int,
    keep_audio_parts: bool,
    tts_worker: TTSWorkerClient | None,
    tts_reference_audio: str | None,
    tts_reference_text: str | None,
    tts_max_new_tokens: int | None,
) -> StageResult:
    if not markdown_path.exists():
        return StageResult(status="failed", error=f"Missing markdown: {markdown_path}")

    try:
        text = normalize_markdown_for_tts(markdown_path.read_text(encoding="utf-8", errors="ignore"))
        chunks = split_text_for_tts(text, tts_chunk_chars)
    except (OSError, ValueError) as exc:
        return StageResult(status="failed", error=f"audio: {exc}")

    if not chunks:
        return StageResult(status="failed", error=f"audio: no speakable text in {markdown_path}")

    safe_title = "".join(char if char.isalnum() or char in "._-" else "_" for char in title).strip("_") or "audio"
    parts_dir = AUDIO_PARTS_DIR / safe_title
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)

    part_paths: list[Path] = []
    chunk_records: list[dict[str, str | int]] = []
    for index, chunk in enumerate(chunks, start=1):
        part_path = parts_dir / f"{index:04d}.wav"
        try:
            if tts_worker is not None:
                tts_worker.synthesize(
                    text=chunk,
                    output_path=part_path,
                    reference_audio=tts_reference_audio,
                    reference_text=tts_reference_text,
                    max_new_tokens=tts_max_new_tokens,
                )
            else:
                command = [
                    "conda", "run", "-n", tts_conda_env,
                    "mlx-speech", "tts",
                    "--model", tts_model,
                    "--text", chunk,
                    "-o", str(part_path),
                ]
                if tts_reference_audio or tts_reference_text:
                    if not tts_reference_audio or not tts_reference_text:
                        return StageResult(status="failed", error="audio: --tts-reference-audio and --tts-reference-text must both be provided")
                    command.extend(["--reference-audio", tts_reference_audio, "--reference-text", tts_reference_text])
                if tts_max_new_tokens is not None:
                    command.extend(["--max-new-tokens", str(tts_max_new_tokens)])
                run_command(command, "audio")
        except RuntimeError as exc:
            return StageResult(status="failed", error=f"audio: chunk {index}/{len(chunks)} failed: {exc}")

        part_paths.append(part_path)
        chunk_records.append({
            "index": index,
            "chars": len(chunk),
            "text": chunk,
            "part": str(part_path),
        })

    try:
        combine_wav_files(part_paths, audio_path)
    except RuntimeError as exc:
        return StageResult(status="failed", error=str(exc))

    duration_seconds = 0.0
    with wave.open(str(audio_path), "rb") as output_wav:
        duration_seconds = output_wav.getnframes() / float(output_wav.getframerate())

    manifest_path = AUDIO_LOG_DIR / f"{safe_title}.json"
    manifest = {
        "title": title,
        "source_markdown": str(markdown_path),
        "output_audio": str(audio_path),
        "tts_engine": "fish-mlx",
        "tts_conda_env": tts_conda_env,
        "tts_model": tts_model,
        "tts_chunk_chars": tts_chunk_chars,
        "tts_reference_audio": tts_reference_audio,
        "tts_reference_text": tts_reference_text,
        "chunks": chunk_records,
        "duration_seconds": duration_seconds,
        "audio_bytes": audio_path.stat().st_size if audio_path.exists() else 0,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if not keep_audio_parts:
        shutil.rmtree(parts_dir)

    return StageResult(
        status="ok",
        output=str(audio_path),
        stats={
            "audio_engine": "fish-mlx",
            "chunks": len(chunks),
            "duration_seconds": round(duration_seconds, 3),
            "audio_bytes": audio_path.stat().st_size if audio_path.exists() else 0,
            "manifest": str(manifest_path),
        },
    )


def stage_audio(
    markdown_path: Path,
    audio_path: Path,
    *,
    title: str,
    tts_engine: str,
    tts_conda_env: str,
    tts_model: str,
    tts_chunk_chars: int,
    audio_format: str,
    keep_audio_parts: bool,
    tts_worker: TTSWorkerClient | None = None,
    tts_reference_audio: str | None = None,
    tts_reference_text: str | None = None,
    tts_max_new_tokens: int | None = None,
) -> StageResult:
    if audio_format != "wav":
        return StageResult(status="failed", error=f"audio: unsupported audio format: {audio_format}")
    if tts_engine == "fish-mlx":
        return stage_audio_fish_mlx(
            markdown_path,
            audio_path,
            title=title,
            tts_conda_env=tts_conda_env,
            tts_model=tts_model,
            tts_chunk_chars=tts_chunk_chars,
            keep_audio_parts=keep_audio_parts,
            tts_worker=tts_worker,
            tts_reference_audio=tts_reference_audio,
            tts_reference_text=tts_reference_text,
            tts_max_new_tokens=tts_max_new_tokens,
        )
    return StageResult(status="failed", error=f"audio: unsupported TTS engine: {tts_engine}")


def cleanup_success_outputs(ocr_pdf: Path, markdown_path: Path, keep_md: bool) -> None:
    paths = [ocr_pdf]
    if not keep_md:
        paths.append(markdown_path)

    for path in paths:
        if path.exists():
            path.unlink()


def build_archive_path(source: Path, archive_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = archive_dir / f"{source.stem}--{timestamp}{source.suffix}"
    counter = 1
    while candidate.exists():
        candidate = archive_dir / f"{source.stem}--{timestamp}-{counter}{source.suffix}"
        counter += 1
    return candidate


def archive_file(source: Path, archive_dir: Path) -> Path | None:
    if not source.exists():
        return None
    archive_path = build_archive_path(source, archive_dir)
    shutil.move(str(source), str(archive_path))
    return archive_path


def archive_existing_epub(epub_path: Path) -> Path | None:
    return archive_file(epub_path, ARCHIVED_EPUB_DIR)


def archive_input_pdf(input_pdf: Path) -> Path | None:
    try:
        input_pdf.resolve().relative_to(INPUT_DIR.resolve())
    except ValueError:
        return None
    return archive_file(input_pdf, ARCHIVED_INPUT_DIR)


def find_input_pdf_for_markdown(markdown_path: Path) -> Path | None:
    candidate = INPUT_DIR / f"{markdown_path.stem}.pdf"
    if candidate.exists():
        return candidate
    return None


def archive_stale_epubs(current_batch_outputs: set[Path]) -> list[str]:
    archived: list[str] = []
    current_batch_resolved = {path.resolve() for path in current_batch_outputs}
    for epub_path in sorted(OUTPUT_DIR.glob("*.epub")):
        if epub_path.resolve() in current_batch_resolved:
            continue
        archived_path = archive_file(epub_path, ARCHIVED_EPUB_DIR)
        if archived_path:
            archived.append(str(archived_path))
    return archived


def process_book(
    input_pdf: Path,
    language: str,
    skip_ocr: bool,
    ocr_engine: str,
    agent: str,
    keep_md: bool,
    md_only: bool,
    generate_audio: bool,
    tts_engine: str,
    tts_conda_env: str,
    tts_model: str,
    tts_chunk_chars: int,
    audio_format: str,
    keep_audio_parts: bool,
    tts_worker: TTSWorkerClient | None,
    tts_reference_audio: str | None,
    tts_reference_text: str | None,
    tts_max_new_tokens: int | None,
) -> BookResult:
    started_at = datetime.now().isoformat(timespec="seconds")
    base_name = input_pdf.stem
    display_title = display_title_from_stem(base_name)
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
    audio_path = AUDIO_DIR / f"{base_name}.{audio_format}"

    ocr_result = stage_ocr(input_pdf, ocr_pdf, sidecar_txt, language, skip_ocr, ocr_engine)
    result.stages["ocr"] = ocr_result
    if ocr_result.status == "failed":
        result.status = "failed"
        result.finished_at = datetime.now().isoformat(timespec="seconds")
        return result

    clean_result = stage_clean_llm(sidecar_txt, markdown_path, display_title, agent)
    result.stages["llm_clean"] = clean_result
    if clean_result.status == "failed":
        result.status = "failed"
        result.finished_at = datetime.now().isoformat(timespec="seconds")
        return result

    if md_only:
        result.status = "ok"
        result.finished_at = datetime.now().isoformat(timespec="seconds")
        return result

    epub_result = stage_epub(markdown_path, epub_path, display_title)
    result.stages["epub"] = epub_result
    result.status = "ok" if epub_result.status == "ok" else "failed"

    if result.status == "ok" and generate_audio:
        audio_result = stage_audio(
            markdown_path,
            audio_path,
            title=display_title,
            tts_engine=tts_engine,
            tts_conda_env=tts_conda_env,
            tts_model=tts_model,
            tts_chunk_chars=tts_chunk_chars,
            audio_format=audio_format,
            keep_audio_parts=keep_audio_parts,
            tts_worker=tts_worker,
            tts_reference_audio=tts_reference_audio,
            tts_reference_text=tts_reference_text,
            tts_max_new_tokens=tts_max_new_tokens,
        )
        result.stages["audio"] = audio_result
        result.status = "ok" if audio_result.status == "ok" else "failed"

    if result.status == "ok":
        cleanup_success_outputs(ocr_pdf, markdown_path, keep_md)
        archived_input = archive_input_pdf(input_pdf)
        if archived_input:
            result.stages["archive_input"] = StageResult(status="ok", output=str(archived_input))
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


def iter_markdown_inputs(single_input: str | None, process_all: bool):
    if single_input:
        candidate = Path(single_input).expanduser()
        if not candidate.is_absolute():
            candidate = (ROOT / candidate).resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"Markdown not found: {candidate}")
        yield candidate
        return

    if process_all:
        yield from sorted(MD_DIR.glob("*.md"))
        return

    raise ValueError("Use --md-input <md> or --all-md")


def process_markdown_audio(
    markdown_path: Path,
    *,
    tts_engine: str,
    tts_conda_env: str,
    tts_model: str,
    tts_chunk_chars: int,
    audio_format: str,
    keep_audio_parts: bool,
    tts_worker: TTSWorkerClient | None = None,
    tts_reference_audio: str | None = None,
    tts_reference_text: str | None = None,
    tts_max_new_tokens: int | None = None,
) -> StageResult:
    title = markdown_path.stem
    audio_path = AUDIO_DIR / f"{title}.{audio_format}"
    return stage_audio(
        markdown_path,
        audio_path,
        title=title,
        tts_engine=tts_engine,
        tts_conda_env=tts_conda_env,
        tts_model=tts_model,
        tts_chunk_chars=tts_chunk_chars,
        audio_format=audio_format,
        keep_audio_parts=keep_audio_parts,
        tts_worker=tts_worker,
        tts_reference_audio=tts_reference_audio,
        tts_reference_text=tts_reference_text,
        tts_max_new_tokens=tts_max_new_tokens,
    )


def print_tool_status(ocr_engine: str, agent: str, include_tts: bool, tts_conda_env: str) -> int:
    detected = detect_tools(ocr_engine, agent)
    if include_tts:
        detected["conda"] = shutil.which("conda")
        detected[f"mlx-speech ({tts_conda_env})"] = detect_tts_cli(tts_conda_env)
    missing = [name for name, path in detected.items() if not path]
    print(f"container_env: {'yes' if CONTAINER_MARKER.exists() else 'no'}")
    print(f"ocr_engine: {ocr_engine}")
    print(f"llm_agent: {agent}")
    if include_tts:
        print(f"tts_engine: {DEFAULT_TTS_ENGINE}")
        print(f"tts_conda_env: {tts_conda_env}")
    for name, path in detected.items():
        print(f"{name}: {path or 'MISSING'}")
    return 1 if missing else 0


def path_to_html_src(path: Path, html_path: Path) -> str:
    try:
        relative = os.path.relpath(path, start=html_path.parent)
    except ValueError:
        relative = str(path)
    return Path(relative).as_posix()


def render_voice_gallery(profiles: list[VoiceProfile], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    for profile in profiles:
        metadata = profile.metadata
        tags = metadata.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tag_html = "".join(f"<span>{html.escape(str(tag))}</span>" for tag in tags)
        fields = [
            ("ID", profile.voice_id),
            ("Language", metadata.get("language") or metadata.get("languages") or ""),
            ("Gender", metadata.get("gender") or ""),
            ("Style", metadata.get("style") or ""),
            ("Source", metadata.get("source") or ""),
            ("License", metadata.get("license") or ""),
        ]
        field_html = "\n".join(
            f"<dt>{html.escape(label)}</dt><dd>{html.escape(str(value))}</dd>"
            for label, value in fields
            if value
        )
        description = str(metadata.get("description") or "").strip()
        sample_html = "<p class=\"missing\">No sample audio yet</p>"
        if profile.sample_audio and profile.sample_audio.exists():
            sample_src = html.escape(path_to_html_src(profile.sample_audio, output_path))
            sample_html = f"<audio controls preload=\"metadata\" src=\"{sample_src}\"></audio>"
        reference_note = "ready" if profile.reference_audio and profile.reference_text else "missing reference"
        cards.append(
            f"""
            <article class="voice-card">
              <div class="voice-head">
                <h2>{html.escape(profile.title)}</h2>
                <code>{html.escape(profile.voice_id)}</code>
              </div>
              {sample_html}
              <dl>{field_html}</dl>
              <div class="tags">{tag_html}</div>
              <p>{html.escape(description)}</p>
              <p class="status">{html.escape(reference_note)}</p>
            </article>
            """
        )

    body = "\n".join(cards) if cards else "<p class=\"empty\">No voices found in voices/.</p>"
    output_path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Voice Selection</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f7f7f5;
      color: #1f2428;
    }}
    body {{
      margin: 0;
      padding: 28px;
    }}
    header {{
      max-width: 1120px;
      margin: 0 auto 20px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    header p {{
      margin: 0;
      color: #606a73;
      line-height: 1.5;
    }}
    main {{
      max-width: 1120px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 14px;
    }}
    .voice-card {{
      background: #ffffff;
      border: 1px solid #d8ddd8;
      border-radius: 8px;
      padding: 16px;
    }}
    .voice-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
      margin-bottom: 12px;
    }}
    h2 {{
      margin: 0;
      font-size: 18px;
      line-height: 1.25;
      letter-spacing: 0;
    }}
    code {{
      color: #4c5963;
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    audio {{
      width: 100%;
      margin: 4px 0 12px;
    }}
    dl {{
      display: grid;
      grid-template-columns: 80px 1fr;
      gap: 6px 10px;
      margin: 0 0 12px;
      font-size: 13px;
    }}
    dt {{
      color: #6f7780;
    }}
    dd {{
      margin: 0;
      overflow-wrap: anywhere;
    }}
    .tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 10px;
    }}
    .tags span {{
      border: 1px solid #cad2cc;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      color: #405049;
    }}
    .voice-card p {{
      margin: 8px 0 0;
      color: #48515a;
      line-height: 1.45;
      font-size: 13px;
    }}
    .status {{
      font-weight: 600;
    }}
    .missing, .empty {{
      color: #9a5b20;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Voice Selection</h1>
    <p>候选说话人来自本地 voices/。试听 sample 后，记录要使用的 voice ID，并在生成音频时传入 --tts-voice。</p>
  </header>
  <main>
    {body}
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
    return output_path


def print_voice_profiles(profiles: list[VoiceProfile]) -> None:
    if not profiles:
        print(f"No voices found in {VOICES_DIR}")
        return
    for profile in profiles:
        status = "ready" if profile.reference_audio and profile.reference_text else "missing-reference"
        sample = "sample" if profile.sample_audio and profile.sample_audio.exists() else "no-sample"
        print(f"{profile.voice_id}\t{profile.title}\t{status}\t{sample}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OCR scanned PDFs and convert to EPUB with CLI agent cleaning.",
        epilog=(
            "Usage:\n"
            "  conda run -n pdf2epub python src/pdf2epub.py --check-tools\n"
            "  conda run -n pdf2epub python src/pdf2epub.py --all\n"
            "  conda run -n pdf2epub python src/pdf2epub.py --all --md-only\n"
            "  conda run -n pdf2epub python src/pdf2epub.py --skip-ocr --all\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--input", help="Process a single PDF file.")
    parser.add_argument("--all", action="store_true", help="Process all PDFs in input/.")
    parser.add_argument("--md-input", help="Generate audio from a single Markdown file.")
    parser.add_argument("--all-md", action="store_true", help="Generate audio from all Markdown files in output/clean_md/.")
    parser.add_argument("--skip-ocr", action="store_true", help="Reuse existing sidecar text.")
    parser.add_argument("--md-only", action="store_true", help="Run OCR and LLM clean only; skip EPUB, audio, and archiving.")
    parser.add_argument("--audio", action="store_true", help="Also generate audio after EPUB generation.")
    parser.add_argument("--audio-only", action="store_true", help="Only generate audio from Markdown input.")
    parser.add_argument(
        "--keep-md",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_KEEP_MD,
        help=f"Keep intermediate Markdown after successful EPUB generation. Default: {DEFAULT_KEEP_MD}",
    )
    parser.add_argument("--lang", default=DEFAULT_LANG, help=f"OCR language. Default: {DEFAULT_LANG}")
    parser.add_argument(
        "--ocr-engine",
        choices=OCR_ENGINE_CHOICES,
        default=DEFAULT_OCR_ENGINE,
        help=f"OCR engine. Default: {DEFAULT_OCR_ENGINE}",
    )
    parser.add_argument(
        "--agent",
        choices=AGENT_CHOICES,
        default=DEFAULT_AGENT,
        help=f"LLM agent for text cleaning. Default: {DEFAULT_AGENT}",
    )
    parser.add_argument(
        "--tts-engine",
        choices=TTS_ENGINE_CHOICES,
        default=DEFAULT_TTS_ENGINE,
        help=f"TTS engine. Default: {DEFAULT_TTS_ENGINE}",
    )
    parser.add_argument("--tts-conda-env", default=DEFAULT_TTS_CONDA_ENV, help=f"TTS conda environment. Default: {DEFAULT_TTS_CONDA_ENV}")
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL, help=f"TTS model name. Default: {DEFAULT_TTS_MODEL}")
    parser.add_argument("--tts-chunk-chars", type=int, default=DEFAULT_TTS_CHUNK_CHARS, help=f"Max chars per TTS chunk. Default: {DEFAULT_TTS_CHUNK_CHARS}")
    parser.add_argument("--tts-voice", help="Local voice id from voices/<voice_id>/ for Fish S2 Pro voice cloning.")
    parser.add_argument("--tts-reference-audio", help="Reference audio for Fish S2 Pro voice cloning.")
    parser.add_argument("--tts-reference-text", help="Transcript for --tts-reference-audio.")
    parser.add_argument(
        "--tts-max-new-tokens",
        type=int,
        default=DEFAULT_TTS_MAX_NEW_TOKENS,
        help=f"Maximum tokens per TTS chunk. Default: {DEFAULT_TTS_MAX_NEW_TOKENS}",
    )
    parser.add_argument(
        "--audio-format",
        choices=SUPPORTED_AUDIO_FORMATS,
        default=DEFAULT_AUDIO_FORMAT,
        help=f"Audio output format. Default: {DEFAULT_AUDIO_FORMAT}",
    )
    parser.add_argument("--keep-audio-parts", action="store_true", help="Keep generated per-chunk wav files.")
    parser.add_argument("--list-voices", action="store_true", help="List local voices from voices/.")
    parser.add_argument("--voice-gallery", action="store_true", help=f"Generate a local voice selection page at {VOICE_GALLERY_PATH}.")
    parser.add_argument("--import-fish-voice", help="Import public Fish Audio model sample into voices/fish-<model_id>/.")
    parser.add_argument("--overwrite-voice", action="store_true", help="Allow --import-fish-voice to replace an existing voice directory.")
    parser.add_argument("--check-tools", action="store_true", help="Check tool availability.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_directories()

    if args.check_tools:
        return print_tool_status(args.ocr_engine, args.agent, args.audio or args.audio_only, args.tts_conda_env)

    if args.list_voices:
        try:
            print_voice_profiles(iter_voice_profiles())
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0

    if args.voice_gallery:
        try:
            gallery_path = render_voice_gallery(iter_voice_profiles(), VOICE_GALLERY_PATH)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"voice gallery: {gallery_path}")
        return 0

    if args.import_fish_voice:
        try:
            voice_dir = import_fish_voice(args.import_fish_voice, overwrite=args.overwrite_voice)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"imported fish voice: {voice_dir}")
        return 0

    if args.md_only and (args.audio or args.audio_only):
        print("--md-only cannot be combined with --audio or --audio-only", file=sys.stderr)
        return 2

    try:
        tts_reference_audio, tts_reference_text, tts_voice_profile = resolve_tts_reference(
            tts_voice=args.tts_voice,
            tts_reference_audio=args.tts_reference_audio,
            tts_reference_text=args.tts_reference_text,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.audio_only:
        try:
            markdown_inputs = list(iter_markdown_inputs(args.md_input, args.all_md))
        except (FileNotFoundError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2

        if not markdown_inputs:
            print(f"No Markdown files found in {MD_DIR}", file=sys.stderr)
            return 2

        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        had_failure = False
        audio_summary = {
            "run_id": run_id,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "audio_only": True,
            "tts_engine": args.tts_engine,
            "tts_conda_env": args.tts_conda_env,
            "tts_model": args.tts_model,
            "tts_chunk_chars": args.tts_chunk_chars,
            "tts_voice": tts_voice_profile.voice_id if tts_voice_profile else None,
            "tts_reference_audio": tts_reference_audio,
            "inputs": [str(path) for path in markdown_inputs],
            "results": [],
            "archived_inputs": [],
        }

        try:
            with TTSWorkerClient(conda_env=args.tts_conda_env, model=args.tts_model) as tts_worker:
                for markdown_path in markdown_inputs:
                    audio_result = process_markdown_audio(
                        markdown_path,
                        tts_engine=args.tts_engine,
                        tts_conda_env=args.tts_conda_env,
                        tts_model=args.tts_model,
                        tts_chunk_chars=args.tts_chunk_chars,
                        audio_format=args.audio_format,
                        keep_audio_parts=args.keep_audio_parts,
                        tts_worker=tts_worker,
                        tts_reference_audio=tts_reference_audio,
                        tts_reference_text=tts_reference_text,
                        tts_max_new_tokens=args.tts_max_new_tokens,
                    )
                    result_record = {"input": str(markdown_path), "stage": audio_result.to_dict()}
                    if audio_result.status != "ok":
                        had_failure = True
                    else:
                        input_pdf = find_input_pdf_for_markdown(markdown_path)
                        if input_pdf is not None:
                            archived_input = archive_input_pdf(input_pdf)
                            if archived_input:
                                archive_stage = StageResult(status="ok", output=str(archived_input))
                                result_record["archive_input"] = archive_stage.to_dict()
                                audio_summary["archived_inputs"].append(str(archived_input))
                    detail = audio_result.output or audio_result.error or ""
                    print(f"[{audio_result.status.upper()}] {markdown_path.name} {detail}".rstrip())
                    archive_detail = result_record.get("archive_input")
                    if archive_detail:
                        print(f"  - archive_input: ok {archive_detail['output']}")
                    audio_summary["results"].append(result_record)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        audio_summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        summary_path = AUDIO_LOG_DIR / f"{run_id}.json"
        summary_path.write_text(json.dumps(audio_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"audio summary log: {summary_path}")
        return 1 if had_failure else 0

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
        "ocr_engine": args.ocr_engine,
        "skip_ocr": args.skip_ocr,
        "keep_md": args.keep_md,
        "md_only": args.md_only,
        "llm_agent": args.agent,
        "generate_audio": args.audio,
        "tts_engine": args.tts_engine if args.audio else None,
        "tts_voice": tts_voice_profile.voice_id if args.audio and tts_voice_profile else None,
        "tts_reference_audio": tts_reference_audio if args.audio else None,
        "inputs": [str(p) for p in inputs],
        "retention_mode": "batch_latest",
        "books": [],
    }

    had_failure = False
    successful_epubs: set[Path] = set()
    try:
        tts_context = TTSWorkerClient(conda_env=args.tts_conda_env, model=args.tts_model) if args.audio else nullcontext(None)
        with tts_context as tts_worker:
            for input_pdf in inputs:
                book_result = process_book(
                    input_pdf,
                    args.lang,
                    args.skip_ocr,
                    args.ocr_engine,
                    args.agent,
                    args.keep_md,
                    args.md_only,
                    args.audio,
                    args.tts_engine,
                    args.tts_conda_env,
                    args.tts_model,
                    args.tts_chunk_chars,
                    args.audio_format,
                    args.keep_audio_parts,
                    tts_worker,
                    tts_reference_audio,
                    tts_reference_text,
                    args.tts_max_new_tokens,
                )
                summary["books"].append(book_result.to_dict())
                if book_result.status != "ok":
                    had_failure = True
                else:
                    epub_stage = (book_result.stages or {}).get("epub")
                    if epub_stage and epub_stage.output:
                        successful_epubs.add(Path(epub_stage.output))

                status = book_result.status.upper()
                print(f"[{status}] {input_pdf.name}")
                for stage_name, stage in (book_result.stages or {}).items():
                    detail = stage.output or stage.error or ""
                    print(f"  - {stage_name}: {stage.status} {detail}".rstrip())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    archived_epubs = [] if args.md_only else archive_stale_epubs(successful_epubs)
    summary["batch_epub_outputs"] = [str(path) for path in sorted(successful_epubs)]
    summary["archived_epubs"] = archived_epubs

    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    summary_path = LOG_DIR / f"{run_id}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary log: {summary_path}")
    return 1 if had_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
