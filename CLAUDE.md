# CLAUDE.md

本项目用于把扫描版中文杂志 PDF 转成 EPUB，优先目标是"正文可读、手机友好"。

## 技术方案

- OCR: `ocrmypdf + tesseract`
- 清洗: **claude 命令 (LLM)**
- EPUB: `pandoc`

## 工作边界

- 主脚本 `src/pdf2epub.py`，保持精简
- 清洗逻辑通过 `src/clean_prompt.md` 控制
- Python 仅用标准库（调用 claude）
- 最新待处理 PDF 放在 `input/`
- 处理成功后的输入 PDF 归档到 `archived/pdf/`
- 最新生成的 EPUB 放在 `epub/`
- 被替换的旧 EPUB 归档到 `archived/epub/`

## 常用命令

```bash
conda run -n pdf2epub python src/pdf2epub.py --check-tools
conda run -n pdf2epub python src/pdf2epub.py --all
conda run -n pdf2epub python src/pdf2epub.py --all --skip-ocr

# 可选：Docker
# docker build -f src/Dockerfile -t pdf2epub .
# docker run --rm -v "$PWD":/workspace pdf2epub --check-tools
# docker run --rm -v "$PWD":/workspace pdf2epub --all
```

## 开发重点

- 优化 `src/clean_prompt.md` 中的清洗指令
- 用 `--skip-ocr` 复用 `output/sidecar_txt/*.txt` 做快速迭代
- 检查 `output/logs/run/*.json` 中的 token 消耗

## 验收标准

- `output/sidecar_txt/*.txt` 可复用
- `epub/*.epub` 可打开
- 日志完整（含 input_tokens、output_tokens）
