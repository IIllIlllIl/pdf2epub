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
六个单位被通报，23名党员干部被问责，招牌历时700年。
线下采摘价能到15元/斤。杨梅要先进入0—9℃的冷库。
甲烷浓度5%至16%，一氧化碳浓度12.5%至75%，存在650度以上的明火。
5月24日晚，一箱5斤的杨梅发往北京。
一家年产能120万吨的煤矿，仍停留在20世纪的模式。
8点班的工人早晨5点多出门，核定产能10万吨。
今年 2 月发布意见，2025 年 5 月在港上市。
微软签订了 3 年至 5 年的协议，覆盖 20 岁-29 岁群体。
股权融资 800 亿美元，人口 1.28 亿人，资本支出 1,318 亿美元。
2022年不足5%升至2023年约20%，《绿皮书：2025—2026年中国旅游发展分析与预测》。
金额从3915亿韩元上调至5500亿韩元。
数据库录入了300余名人员，法院对4起案件宣判。
AI眼镜出货量将达到2207.1万台，登记795000人。

## 封面文章

也是行业硬核技术壁垒聚集地。

“这个跨度是很大的。
”他表示。

国 际 购 物 中 心 协 会 市场调查。
"""
    normalized = normalize_markdown_for_tts(markdown)

    assert_contains(normalized, "分别在二〇二五年九月开始。")
    assert_contains(normalized, "二〇二三年至二〇二六年周期预算")
    assert_contains(normalized, "[emphasis]一千七百美元")
    assert_contains(normalized, "[emphasis]一千七百多元")
    assert_contains(normalized, "每小时一百一十二公里")
    assert_contains(normalized, "五十五岁到七十岁")
    assert_contains(normalized, "七千亩到一万亩")
    assert_contains(normalized, "[emphasis]二十三名党员干部")
    assert_contains(normalized, "[emphasis]七百年")
    assert_contains(normalized, "[emphasis]每斤十五元")
    assert_contains(normalized, "零到九摄氏度")
    assert_contains(normalized, "[emphasis]百分之五到百分之十六")
    assert_contains(normalized, "[emphasis]百分之十二点五到百分之七十五")
    assert_contains(normalized, "[emphasis]六百五十度以上")
    assert_contains(normalized, "五月二十四日晚")
    assert_contains(normalized, "[emphasis]五斤")
    assert_contains(normalized, "[emphasis]一百二十万吨")
    assert_contains(normalized, "二十世纪")
    assert_contains(normalized, "八点班")
    assert_contains(normalized, "五点多")
    assert_contains(normalized, "[emphasis]十万吨")
    assert_contains(normalized, "今年二月发布意见")
    assert_contains(normalized, "二〇二五年五月在港上市")
    assert_contains(normalized, "三年到五年")
    assert_contains(normalized, "二十岁到二十九岁")
    assert_contains(normalized, "[emphasis]八百亿美元")
    assert_contains(normalized, "[emphasis]一点二八亿人")
    assert_contains(normalized, "[emphasis]一千三百一十八亿美元")
    assert_contains(normalized, "二〇二二年不足[emphasis]百分之五升至二〇二三年约[emphasis]百分之二十")
    assert_contains(normalized, "二〇二五年至二〇二六年中国旅游发展分析与预测")
    assert_contains(normalized, "[emphasis]三千九百一十五亿韩元")
    assert_contains(normalized, "[emphasis]五千五百亿韩元")
    assert_contains(normalized, "[emphasis]三百余名人员")
    assert_contains(normalized, "[emphasis]四起案件")
    assert_contains(normalized, "[emphasis]二千二百零七点一万台")
    assert_contains(normalized, "[emphasis]七十九万五千人")
    assert_not_contains(normalized, "封面文章")
    assert_not_contains(normalized, "五、五、零、零")
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
        "金额5500亿韩元。",
        "国 际购物中心协会。",
        "正常句子。[short pause]",
        "[pause]\n封面文章\n[short pause]",
        "报告称2025—2026年会增长。",
    ]
    kinds = {issue.kind for issue in preflight_tts_chunks(risky_chunks)}
    expected = {
        "split_closing_punctuation",
        "raw_year_range",
        "raw_money",
        "raw_number_unit",
        "cjk_space",
        "trailing_pause_tag",
        "section_heading",
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
