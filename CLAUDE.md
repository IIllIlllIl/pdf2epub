# CLAUDE.md

本项目用于把扫描版中文杂志 PDF 转成 EPUB，优先目标是"正文可读、手机友好"。

## 技术方案

- OCR: `ocrmypdf + tesseract`（当前唯一实现的 OCR engine，参数名为 `tesseract`；`unpaper` 存在时才启用 OCR 清理）
- 清洗: **codex 命令默认 / claude 命令可选 (LLM)**
- EPUB: `pandoc`
- 音频: `fish-mlx`（独立 `tts-mlx` conda 环境中的常驻 `mlx-speech` worker）

## 工作边界

- 主脚本 `src/pdf2epub.py`，保持精简
- 清洗逻辑通过 `src/clean_prompt.md` 控制
- Python 仅用标准库（调用 codex 或 claude）
- 最新待处理 PDF 放在 `input/`
- 处理成功后的输入 PDF 归档到 `input/archived_pdf/`
- 最新生成的 EPUB 放在 `output/epub/`
- 生成的朗读音频放在 `output/audio/`
- 被替换的旧 EPUB 归档到 `output/archived_epub/`
- 清洗后的中间 Markdown 默认保留在 `output/clean_md/`，可用 `--no-keep-md` 恢复成功后清理

## 常用命令

```bash
conda run -n pdf2epub python src/pdf2epub.py --check-tools
conda run -n pdf2epub python src/pdf2epub.py --all
conda run -n pdf2epub python src/pdf2epub.py --all --md-only
conda run -n pdf2epub python src/pdf2epub.py --all --skip-ocr
conda run -n pdf2epub python src/pdf2epub.py --all --ocr-engine tesseract
conda run -n pdf2epub python src/pdf2epub.py --all --agent claude
conda run -n pdf2epub python src/pdf2epub.py --all --no-keep-md
conda run -n pdf2epub python src/pdf2epub.py --audio-only --md-input output/clean_md/example.md
conda run -n pdf2epub python src/pdf2epub.py --list-voices
conda run -n pdf2epub python src/pdf2epub.py --voice-gallery
conda run -n pdf2epub python src/pdf2epub.py --import-fish-voice 0f08cacd3e354471a4b94dd00b4cc4a3

# 可选：Docker
# docker build -f src/Dockerfile -t pdf2epub .
# docker run --rm -v "$PWD":/workspace pdf2epub --check-tools
# docker run --rm -v "$PWD":/workspace pdf2epub --all
```

## 开发重点

- 优化 `src/clean_prompt.md` 中的清洗指令
- 用 `--skip-ocr` 复用 `output/sidecar_txt/*.txt` 做快速迭代
- 检查 `output/logs/run/*.json` 中的 token 消耗
- 默认 LLM agent 是 `codex`；需要回退 Claude CLI 时使用 `--agent claude`
- 默认 OCR engine 是 `tesseract`；当前实现通过 `ocrmypdf` 调用 Tesseract
- 默认保留 `output/clean_md/*.md`；只有传入 `--no-keep-md` 才在成功后删除
- 用 `--md-only` 可只生成 `output/clean_md/*.md`，不生成 EPUB/音频，也不归档输入 PDF
- 音频生成默认启动一次 `tts-mlx` worker，加载 `fish-s2-pro` 后复用到本轮所有分段/杂志
- TTS 默认使用 `--tts-chunk-chars 120 --tts-max-new-tokens 512`，这是当前本机测试通过且避免 MLX/Metal 单 buffer 超限的保守值
- Fish S2 Pro MLX 没有固定说话人列表；指定音色优先使用 `voices/<voice_id>/` 和 `--tts-voice`
- `--voice-gallery` 生成 `output/voice_gallery.html`，用于人工试听筛选本地候选说话人
- `--import-fish-voice` 可导入 Fish Audio 公开模型样本到 `voices/`，但 metadata 会标记 license unknown，正式使用前需确认授权

## 验收标准

- `output/sidecar_txt/*.txt` 可复用
- `output/clean_md/*.md` 默认保留
- `output/epub/*.epub` 可打开
- `output/audio/*.wav` 可播放（使用音频路径时）
- `input/archived_pdf/*.pdf` 中保留已成功处理的输入 PDF
- 日志完整（含 input_tokens、output_tokens）

## 当前流程约定

- `--all` 只处理 `input/` 顶层的 PDF，不扫描 `input/archived_pdf/`
- `output/epub/` 表示“本批次成功生成的 EPUB 结果集”
- 批次结束后，`output/epub/` 中不属于本批次成功结果的 EPUB 会归档到 `output/archived_epub/`
- OCR 或 LLM 清洗失败时，输入 PDF 保留在 `input/`，不归档
- `--audio-only` 成功后，如果 `input/` 顶层存在与 Markdown 同 stem 的 PDF，也会归档该 PDF
- `--md-only` 成功时，输入 PDF 也保留在 `input/`，便于后续继续测试完整链路
- 如果本批次全部失败，则 `output/epub/` 最终可为空；这是当前接受的行为
