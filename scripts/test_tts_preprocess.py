#!/usr/bin/env python3
"""Regression tests for TTS preprocessing stability rules."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tts_preprocess import normalize_markdown_for_tts, preflight_tts_chunks, split_text_for_tts  # noqa: E402


def assert_contains(text: str, expected: str) -> None:
    if expected not in text:
        raise AssertionError(f"expected {expected!r} in:\n{text}")


def assert_not_contains(text: str, unexpected: str) -> None:
    if unexpected in text:
        raise AssertionError(f"unexpected {unexpected!r} in:\n{text}")


def test_known_instability_repairs() -> None:
    markdown = """前三阶段为抽签制，分别在2025年9月

## 资本与金融

根据该组织公布的2023年-2026年周期预算，票价1700美元，养老金1700多元。
事故发生时的车辆行驶速度约为112公里/小时。死者主要是55~70岁的女性。
2025年改种和增加的蓝莓大棚面积约7000~10000亩。

“这个跨度是很大的。
”他表示。

国 际 购 物 中 心 协 会 市场调查。
"""
    normalized = normalize_markdown_for_tts(markdown)

    assert_contains(normalized, "分别在二零二五年九月开始。")
    assert_contains(normalized, "二零二三年至二零二六年周期预算")
    assert_contains(normalized, "[emphasis]一千七百美元")
    assert_contains(normalized, "[emphasis]一千七百多元")
    assert_contains(normalized, "每小时一百一十二公里")
    assert_contains(normalized, "五十五岁到七十岁")
    assert_contains(normalized, "七千亩到一万亩")
    assert_contains(normalized, "“这个跨度是很大的。”他表示。")
    assert_contains(normalized, "国际购物中心协会市场调查。")
    assert_not_contains(normalized, "国 际")
    assert_not_contains(normalized, "一、七、零、零")

    chunks = split_text_for_tts(normalized, 120)
    issues = preflight_tts_chunks(chunks)
    if issues:
        raise AssertionError(f"expected no preflight issues, got: {issues}")
    for chunk in chunks:
        if chunk.endswith("[pause]") or chunk.endswith("[short pause]"):
            raise AssertionError(f"chunk should not end with pause tag:\n{chunk}")


def test_preflight_detects_residual_risks() -> None:
    risky_chunks = [
        "这个跨度是很大的。\n”他表示。",
        "根据2023年-2026年预算。",
        "票价1700美元。",
        "国 际购物中心协会。",
        "正常句子。[short pause]",
    ]
    kinds = {issue.kind for issue in preflight_tts_chunks(risky_chunks)}
    expected = {
        "split_closing_punctuation",
        "raw_year_range",
        "raw_money",
        "cjk_space",
        "trailing_pause_tag",
    }
    missing = expected - kinds
    if missing:
        raise AssertionError(f"missing preflight issue kinds: {sorted(missing)}")


def main() -> int:
    test_known_instability_repairs()
    test_preflight_detects_residual_risks()
    print("tts_preprocess tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
