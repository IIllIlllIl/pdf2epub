# pdf2epub

将扫描版中文杂志 PDF 转成适合手机阅读的 EPUB。

## 技术方案

- **OCR**: `ocrmypdf + tesseract`
- **文本清洗**: `claude` 命令（LLM 智能清洗）
- **EPUB 生成**: `pandoc`
- **运行环境**: 本地 conda 环境（默认）/ Docker（可选）

## 目录结构

```text
pdf2epub/
  input/               # 最新待处理 PDF
    archived_pdf/      # 已处理完成的输入 PDF
  output/
    epub/              # 最新生成的 EPUB
    archived_epub/     # 被替换的旧 EPUB
    ocr_pdf/           # OCR 后 PDF（成功后自动清理）
    sidecar_txt/       # OCR 原始文本（可供 --skip-ocr 复用）
    clean_md/          # 清洗后 Markdown（成功后自动清理）
    logs/run/          # 运行日志
  src/
    pdf2epub.py        # 主脚本
    clean_prompt.md    # LLM 清洗提示词
    epub.css           # EPUB 样式
    Dockerfile         # Docker 构建入口
  run.sh               # Docker 可选包装脚本
  README.md
  CLAUDE.md
```

## 快速开始

### 1. 准备运行环境

默认使用本地 conda：

```bash
conda create -y -n pdf2epub python=3.11 pandoc tesseract ocrmypdf -c conda-forge
conda run -n pdf2epub pip install "ocrmypdf>=16,<17"
```

如需 Docker，也可以继续使用：

```bash
colima start  # macOS
docker build -f src/Dockerfile -t pdf2epub .
```

### 2. 准备 Claude CLI 认证

请确保当前运行环境能访问 Claude CLI 认证。常见做法：
- 设置 `ANTHROPIC_AUTH_TOKEN`；或
- 设置 `ANTHROPIC_API_KEY`

如果使用 Docker，`run.sh` 会传递常见认证环境变量，但不会挂载宿主机 `$HOME/.claude`，以保持容器内 Claude 运行环境独立。

### 3. 检查工具

```bash
# conda（默认）
conda run -n pdf2epub python src/pdf2epub.py --check-tools

# Docker（可选）
docker run --rm -v "$PWD":/workspace pdf2epub --check-tools
```

预期输出应包含：`claude`、`ocrmypdf`、`tesseract`、`pandoc`。

### 4. 处理 PDF

```bash
# conda：单个 PDF
conda run -n pdf2epub python src/pdf2epub.py --input input/三联生活周刊2026年第10期.pdf

# conda：批量处理 input/ 中的全部 PDF
conda run -n pdf2epub python src/pdf2epub.py --all

# conda：复用已有 sidecar，仅重跑清洗与 EPUB
conda run -n pdf2epub python src/pdf2epub.py --all --skip-ocr

# Docker：单个 PDF
./run.sh --input input/三联生活周刊2026年第10期.pdf

# Docker：批量处理
./run.sh --all
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--input <pdf>` | 处理单个 PDF |
| `--all` | 处理 `input/` 下全部 PDF（运行后 `output/epub/` 仅保留本批次成功结果） |
| `--skip-ocr` | 复用已有 sidecar 文本 |
| `--lang <lang>` | OCR 语言，默认 `chi_sim+eng` |
| `--check-tools` | 检查工具可用性 |

## 处理流程

1. **OCR**: 生成可搜索 PDF 和 sidecar 文本
2. **LLM 清洗**: 按分页分组调用 `claude -p --output-format json` 清洗文本并合并为 Markdown
3. **EPUB**: Markdown 转 EPUB，输出到 `output/epub/`
4. **批次归档旧 EPUB**: 本次运行结束后，`output/epub/` 中所有不属于本批次成功结果的 EPUB 都会移到 `output/archived_epub/`
5. **成功清理/归档**: 成功后自动删除 `output/ocr_pdf/*.ocr.pdf` 与 `output/clean_md/*.md`，并把已处理输入 PDF 移到 `input/archived_pdf/`

默认情况下，`--all` 只扫描 `input/`，不会处理归档目录中的历史文件。

失败时的当前行为：
- OCR 或 LLM 清洗失败：输入 PDF 保留在 `input/`，不会归档
- `output/epub/` 表示“本批次成功生成的 EPUB 结果集”，不是“每本书各自最后一次成功版本”
- 若本批次只有部分 PDF 成功，则 `output/epub/` 只保留这些成功结果，其他原有 EPUB 会归档到 `output/archived_epub/`
- 若本批次全部失败，则 `output/epub/` 最终为空。这是当前接受的行为。

## 运行要求

当前运行环境需满足以下至少一种 Claude CLI 认证方式：

| 方式 | 说明 |
|------|------|
| `ANTHROPIC_AUTH_TOKEN` | 走 bearer token |
| `ANTHROPIC_API_KEY` | 走 API key |

## 验证建议

成功处理后，重点检查：
- `output/sidecar_txt/*.txt` 可供 `--skip-ocr` 复用
- `output/epub/*.epub` 可正常打开
- `input/archived_pdf/` 中保留已成功处理的输入 PDF
- `output/archived_epub/` 中保留被替换的旧 EPUB
- `output/logs/run/*.json` 中保留 `input_tokens`、`output_tokens` 等统计字段

不需要把 `output/clean_md/*.md` 是否仍然存在作为默认验收条件，因为成功生成 EPUB 后会自动清理。

## 清洗效果

LLM 清洗相比规则清洗的优势：
- ✅ 段落完整合并（无页边界切断）
- ✅ 智能识别并删除目录条目、页眉页脚
- ✅ 修复 OCR 错误（如"大三"→"大厂"）
- ✅ 自动添加章节结构

## 已知限制

- OCR 错字可能仍然存在
- 复杂版面（双栏、跨栏）可能顺序不完美
- LLM 清洗仍有 token 成本
- 默认假设 `input/` 中 PDF 文件名 stem 唯一
- `output/epub/` 表示最近一次运行中成功产出的结果集；重新运行单本或一批文件时，旧批次结果会被归档到 `output/archived_epub/`
