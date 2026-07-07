"""Text normalization and risk repair before Fish TTS synthesis."""

from __future__ import annotations

import re
from dataclasses import dataclass


TTS_DIGIT_NAMES = {
    "0": "零",
    "1": "一",
    "2": "二",
    "3": "三",
    "4": "四",
    "5": "五",
    "6": "六",
    "7": "七",
    "8": "八",
    "9": "九",
}

TTS_PAUSE_TAG_RE = re.compile(r"(?:\s*\[(?:short pause|pause)]\s*)+$")
TTS_CLOSE_QUOTE_RE = re.compile(r"([。！？!?；;，,])\s*\n\s*([”’」』）】》])")
CJK_RANGE = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"


@dataclass(frozen=True)
class TTSPreflightIssue:
    kind: str
    detail: str
    index: int | None = None


def number_under_100_to_chinese(value: int) -> str:
    if value < 0 or value >= 100:
        return "".join(TTS_DIGIT_NAMES[digit] for digit in str(value))
    if value < 10:
        return TTS_DIGIT_NAMES[str(value)]
    tens, ones = divmod(value, 10)
    prefix = "十" if tens == 1 else f"{TTS_DIGIT_NAMES[str(tens)]}十"
    return prefix if ones == 0 else f"{prefix}{TTS_DIGIT_NAMES[str(ones)]}"


def digits_to_spoken_chinese(digits: str) -> str:
    return "、".join(TTS_DIGIT_NAMES[digit] for digit in digits)


def integer_to_chinese(value: int) -> str:
    if value < 100:
        return number_under_100_to_chinese(value)
    if value >= 10000:
        high, low = divmod(value, 10000)
        high_spoken = integer_to_chinese(high)
        if low == 0:
            return f"{high_spoken}万"
        if low < 1000:
            return f"{high_spoken}万零{integer_to_chinese(low)}"
        return f"{high_spoken}万{integer_to_chinese(low)}"

    units = ["", "十", "百", "千"]
    digits = [int(digit) for digit in str(value)]
    parts: list[str] = []
    pending_zero = False
    length = len(digits)
    for offset, digit in enumerate(digits):
        unit_index = length - offset - 1
        if digit == 0:
            pending_zero = bool(parts)
            continue
        if pending_zero:
            parts.append("零")
            pending_zero = False
        parts.append(f"{TTS_DIGIT_NAMES[str(digit)]}{units[unit_index]}")
    return "".join(parts) or "零"


def decimal_to_chinese(value: str) -> str:
    integer, dot, fraction = value.partition(".")
    spoken = integer_to_chinese(int(integer))
    if dot:
        spoken += "点" + "".join(TTS_DIGIT_NAMES[digit] for digit in fraction)
    return spoken


def repair_markdown_instability_patterns(markdown: str) -> str:
    """Repair known Markdown-level defects that make TTS hallucinate."""
    text = re.sub(
        r"(分别[在于]\d{4}年\d{1,2}月)\s*(?=\n\s*#{1,6}\s)",
        r"\1开始。",
        markdown,
    )
    return text


def strip_markdown_for_tts(markdown: str) -> str:
    text = repair_markdown_instability_patterns(markdown)
    text = re.sub(r"```.*?```", "\n", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(
        r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$",
        lambda match: f"\n[pause]\n{match.group(1).strip()}\n[short pause]\n",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(r"^\s{0,3}[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}\d+[.)]\s+", "", text, flags=re.MULTILINE)
    return re.sub(r"[*_`>|]", "", text)


def sanitize_tts_input_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = TTS_CLOSE_QUOTE_RE.sub(r"\1\2", text)

    previous = None
    while previous != text:
        previous = text
        text = re.sub(fr"([{CJK_RANGE}])\s+([{CJK_RANGE}])", r"\1\2", text)

    text = re.sub(r"\s+([，。！？；：、）】》”’」』])", r"\1", text)
    text = re.sub(r"([（【《“‘「『])\s+", r"\1", text)

    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and re.fullmatch(rf"[^\w{CJK_RANGE}\[\]]+", stripped):
            continue
        cleaned_lines.append(stripped if stripped else "")

    text = "\n".join(cleaned_lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def add_tts_number_prosody(text: str) -> str:
    def spoken_year(year: str) -> str:
        return "".join(TTS_DIGIT_NAMES[digit] for digit in year)

    def spoken_quantity(value: str, magnitude: str | None = None) -> str:
        spoken = decimal_to_chinese(value) if "." in value else integer_to_chinese(int(value))
        return f"{spoken}{magnitude or ''}"

    def replace_year_range(match: re.Match[str]) -> str:
        return f"{spoken_year(match.group(1))}年至{spoken_year(match.group(2))}年"

    def replace_measure_range(match: re.Match[str]) -> str:
        start_value, start_magnitude = match.group(1), match.group(2)
        end_value, end_magnitude, unit = match.group(3), match.group(4), match.group(5)
        shared_magnitude = end_magnitude or start_magnitude
        start_spoken = spoken_quantity(start_value, start_magnitude or shared_magnitude)
        end_spoken = spoken_quantity(end_value, shared_magnitude)
        return f"{start_spoken}{unit}到{end_spoken}{unit}"

    def replace_speed(match: re.Match[str]) -> str:
        return f"每小时{spoken_quantity(match.group(1))}公里"

    def replace_date(match: re.Match[str]) -> str:
        year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
        return f"{spoken_year(year)}年{number_under_100_to_chinese(month)}月{number_under_100_to_chinese(day)}日"

    def replace_year_month(match: re.Match[str]) -> str:
        year, month = match.group(1), int(match.group(2))
        return f"{spoken_year(year)}年{number_under_100_to_chinese(month)}月"

    text = re.sub(r"(?<!\d)(\d{4})年\s*[-—~～至到]\s*(\d{4})年", replace_year_range, text)
    text = re.sub(
        r"(?<![\d.])(\d+(?:\.\d+)?)(万|亿)?\s*[-—~～至到]\s*(\d+(?:\.\d+)?)(万|亿)?"
        r"(美元|人民币|元|岁|亩|公里|分钟|小时)",
        replace_measure_range,
        text,
    )
    text = re.sub(
        r"(?<![\d.])(\d+(?:\.\d+)?)(美元|人民币|元|岁|亩|公里|分钟|小时)"
        r"\s*[-—~～至到]\s*(\d+(?:\.\d+)?)\2",
        lambda match: (
            f"{spoken_quantity(match.group(1))}{match.group(2)}"
            f"到{spoken_quantity(match.group(3))}{match.group(2)}"
        ),
        text,
    )
    text = re.sub(
        r"(?<![\d.])(\d+(?:\.\d+)?)(万|亿)(亩|公里|分钟|小时|个|人|家|座|项)",
        lambda match: f"[emphasis]{spoken_quantity(match.group(1), match.group(2))}{match.group(3)}",
        text,
    )
    text = re.sub(
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:km/h|KM/H|公里/小时|公里／小时)",
        replace_speed,
        text,
    )
    text = re.sub(r"(?<!\d)(\d{4})年(\d{1,2})月(\d{1,2})日", replace_date, text)
    text = re.sub(r"(?<!\d)(\d{4})年(\d{1,2})月", replace_year_month, text)
    text = re.sub(r"(?<!\d)(\d{4})年", lambda match: f"{spoken_year(match.group(1))}年", text)
    text = re.sub(
        r"(?<![\d.])(\d+(?:\.\d+)?)个百分点",
        lambda match: f"[emphasis]{decimal_to_chinese(match.group(1))}个百分点",
        text,
    )
    text = re.sub(
        r"(?<![\d.])(\d+(?:\.\d+)?)%",
        lambda match: f"[emphasis]百分之{decimal_to_chinese(match.group(1))}",
        text,
    )
    text = re.sub(
        r"(?<![\d.])(\d+(?:\.\d+)?)倍",
        lambda match: f"[emphasis]{decimal_to_chinese(match.group(1))}倍",
        text,
    )
    text = re.sub(
        r"(?<![\d.])(\d+(?:\.\d+)?)(万|亿)?(多)?(美元|人民币|元)(多)?",
        lambda match: (
            f"[emphasis]{decimal_to_chinese(match.group(1))}"
            f"{match.group(2) or ''}{match.group(3) or ''}{match.group(4)}{match.group(5) or ''}"
        ),
        text,
    )
    text = re.sub(
        r"第(\d{1,3})(期|章|节|名|位)",
        lambda match: f"第{integer_to_chinese(int(match.group(1)))}{match.group(2)}",
        text,
    )
    text = re.sub(
        r"(?<![\d.])(\d{1,5})(多)?(座|家|人|项|个|亩|岁|公里|分钟|小时|万标箱)",
        lambda match: f"[emphasis]{integer_to_chinese(int(match.group(1)))}{match.group(2) or ''}{match.group(3)}",
        text,
    )
    return re.sub(r"(?<![\d./-])\d{4,}(?![\d./-])", lambda match: digits_to_spoken_chinese(match.group(0)), text)


def add_pause_tags(text: str) -> str:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    paced_paragraphs: list[str] = []
    for paragraph in paragraphs:
        if paced_paragraphs and not paced_paragraphs[-1].endswith("[short pause]"):
            paced_paragraphs.append("[short pause]")
        paced_paragraphs.append(paragraph)
    return "\n\n".join(paced_paragraphs)


def normalize_markdown_for_tts(markdown: str) -> str:
    text = strip_markdown_for_tts(markdown)
    text = sanitize_tts_input_text(text)
    text = add_pause_tags(text)
    return add_tts_number_prosody(text).strip()


def normalize_tts_chunk(chunk: str) -> str:
    normalized = TTS_CLOSE_QUOTE_RE.sub(r"\1\2", chunk.strip())
    return TTS_PAUSE_TAG_RE.sub("", normalized).strip()


def choose_split_index(sentence: str, max_chars: int) -> int:
    window = sentence[:max_chars]
    minimum = max(40, max_chars // 2)
    punctuation = "，,、：:；;。！？!?"
    candidates = [window.rfind(mark) + 1 for mark in punctuation]
    candidates = [index for index in candidates if index >= minimum]
    return max(candidates) if candidates else max_chars


def split_text_for_tts(text: str, max_chars: int) -> list[str]:
    if max_chars < 100:
        raise ValueError("--tts-chunk-chars must be at least 100")

    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        normalized = normalize_tts_chunk(current)
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
                split_index = choose_split_index(sentence, max_chars)
                chunks.append(normalize_tts_chunk(sentence[:split_index]))
                sentence = sentence[split_index:].strip()

            separator = "\n" if current else ""
            if current and len(current) + len(separator) + len(sentence) > max_chars:
                flush_current()
            current = f"{current}{separator}{sentence}" if current else sentence

        if current and len(current) + 1 <= max_chars:
            current = f"{current}\n"

    flush_current()
    return chunks


def preflight_tts_chunks(chunks: list[str]) -> list[TTSPreflightIssue]:
    issues: list[TTSPreflightIssue] = []
    for index, chunk in enumerate(chunks, start=1):
        if TTS_PAUSE_TAG_RE.search(chunk):
            issues.append(TTSPreflightIssue("trailing_pause_tag", "chunk ends with pause control tag", index))
        if re.search(r"\d{4}年\s*[-—~～]\s*\d{4}年", chunk):
            issues.append(TTSPreflightIssue("raw_year_range", "year range still contains a dash", index))
        if re.search(r"\d+(?:\.\d+)?(?:多)?(?:美元|人民币|元)", chunk):
            issues.append(TTSPreflightIssue("raw_money", "money amount was not spoken-normalized", index))
        if re.search(rf"([{CJK_RANGE}])\s+([{CJK_RANGE}])", chunk):
            issues.append(TTSPreflightIssue("cjk_space", "space remains between CJK characters", index))
        if re.search(r"([。！？!?；;])\n+([”’」』）】》])", chunk):
            issues.append(TTSPreflightIssue("split_closing_punctuation", "closing punctuation is split across lines", index))
    return issues
