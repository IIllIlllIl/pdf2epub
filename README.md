# pdf2epub

[中文](README.md) | [English](README.en.md)

将扫描版中文杂志 PDF 转成适合手机阅读的 EPUB。

## 技术方案

- **OCR**: `ocrmypdf + tesseract`（当前唯一实现的 OCR engine，参数名为 `tesseract`）
- **文本清洗**: `claude` 命令（默认）或 `codex` 命令（LLM 智能清洗）
- **EPUB 生成**: `pandoc`
- **音频生成**: `fish-mlx`（通过独立 `tts-mlx` conda 环境启动常驻 `mlx-speech` worker）
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
    clean_md/          # 清洗后 Markdown（默认保留，可用 --no-keep-md 清理）
    audio/             # 最新生成的 Markdown 朗读音频
    archived_audio/    # 被替换的旧 WAV
    voice_gallery.html # 本地候选说话人选择页
    logs/run/          # 运行日志
    logs/audio/        # 音频生成日志
  voices/              # 本地说话人库
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
conda run -n pdf2epub python -m pip install fpdf2 pi-heif
npm install -g @anthropic-ai/claude-code
```

如果需要使用 `--agent codex`，请另外安装 Codex CLI：`npm install -g @openai/codex`。

如果需要从 Markdown 生成音频，另建独立 TTS 环境：

```bash
conda create -y -n tts-mlx python=3.13
conda run -n tts-mlx python -m pip install mlx-speech
```

如需 Docker，也可以继续使用：

```bash
colima start  # macOS
docker build -f src/Dockerfile -t pdf2epub .
```

### 2. 准备 LLM CLI 认证

默认使用 Claude CLI。请确保当前运行环境能访问 Claude CLI 认证。常见做法：
- 设置 `ANTHROPIC_API_KEY`；或
- 设置 `ANTHROPIC_AUTH_TOKEN`

如果需要继续使用 Codex CLI，可运行时传入 `--agent codex`，并确保满足以下至少一种 Codex CLI 认证方式：
- 已通过 `codex login` 登录；或
- 设置 `OPENAI_API_KEY`

如果使用 Docker，`run.sh` 会传递常见认证环境变量，但不会挂载宿主机 `$HOME/.codex` 或 `$HOME/.claude`，以保持容器内 CLI 运行环境独立。

### 3. 检查工具

```bash
# conda（默认）
conda run -n pdf2epub python src/pdf2epub.py --check-tools

# Docker（可选）
docker run --rm -v "$PWD":/workspace pdf2epub --check-tools
```

预期输出应包含：`claude`、`ocrmypdf`、`tesseract`、`pandoc`。如果使用 `--agent codex`，则应包含 `codex`。

### 4. 处理 PDF

```bash
# conda：单个 PDF
conda run -n pdf2epub python src/pdf2epub.py --input input/三联生活周刊2026年第10期.pdf

# conda：批量处理 input/ 中的全部 PDF
conda run -n pdf2epub python src/pdf2epub.py --all

# conda：只生成中间 Markdown，便于后续测试音频或检查清洗质量
conda run -n pdf2epub python src/pdf2epub.py --all --md-only

# conda：显式指定 OCR engine（当前默认且唯一实现为 tesseract）
conda run -n pdf2epub python src/pdf2epub.py --all --ocr-engine tesseract

# conda：切换 OCR 语言；中文简体+英文为默认值
conda run -n pdf2epub python src/pdf2epub.py --all --lang chi_sim+eng

# conda：恢复使用 Codex CLI
conda run -n pdf2epub python src/pdf2epub.py --all --agent codex

# conda：复用已有 sidecar，仅重跑清洗与 EPUB
conda run -n pdf2epub python src/pdf2epub.py --all --skip-ocr

# conda：恢复旧行为，成功生成 EPUB 后删除中间 Markdown
conda run -n pdf2epub python src/pdf2epub.py --all --no-keep-md

# conda：PDF -> EPUB 后继续生成 wav 音频
conda run -n pdf2epub python src/pdf2epub.py --input input/三联生活周刊2026年第10期.pdf --audio

# conda：只从已有 Markdown 生成 wav 音频
conda run -n pdf2epub python src/pdf2epub.py --audio-only --md-input output/clean_md/三联生活周刊2026年第10期.md

# conda：查看本地候选说话人，并生成试听选择页
conda run -n pdf2epub python src/pdf2epub.py --list-voices
conda run -n pdf2epub python src/pdf2epub.py --voice-gallery

# conda：导入 Fish Audio 公开模型样本用于内部评估
conda run -n pdf2epub python src/pdf2epub.py --import-fish-voice 0f08cacd3e354471a4b94dd00b4cc4a3

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
| `--md-input <md>` | 从 Markdown 生成音频；可重复传入多个文件以作为同一音频批次处理 |
| `--all-md` | 从 `output/clean_md/` 下全部 Markdown 生成音频 |
| `--skip-ocr` | 复用已有 sidecar 文本 |
| `--md-only` | 只运行 OCR 和 LLM 清洗，生成 `output/clean_md/*.md` 后停止；不生成 EPUB/音频，不归档输入 PDF |
| `--audio` | PDF 转 EPUB 成功后继续生成音频 |
| `--audio-only` | 只从 Markdown 生成音频，不跑 OCR/LLM/EPUB；运行后 `output/audio/` 仅保留本批次成功结果，成功后若 `input/` 下存在同名 PDF，会归档该 PDF |
| `--keep-md` / `--no-keep-md` | 是否保留中间 Markdown，默认保留；使用 `--no-keep-md` 可在成功后删除 |
| `--lang <lang>` | OCR 语言，默认 `chi_sim+eng`；例如英文可用 `eng`，繁体中文可用 `chi_tra+eng`，取决于本机 Tesseract 语言包 |
| `--ocr-engine <tesseract>` | OCR engine，默认 `tesseract`；当前通过 `ocrmypdf` 调用 Tesseract |
| `--agent <codex\|claude>` | LLM 清洗 agent，默认 `claude` |
| `--tts-engine <fish-mlx>` | TTS engine，默认 `fish-mlx` |
| `--tts-conda-env <env>` | TTS conda 环境，默认 `tts-mlx` |
| `--tts-model <model>` | `mlx-speech` 模型名，默认 `fish-s2-pro` |
| `--tts-chunk-chars <n>` | 每段 TTS 最大字符数，默认 `120`；这是当前在本机 Fish S2 Pro MLX 上验证过的保守值 |
| `--tts-voice <voice_id>` | 使用 `voices/<voice_id>/` 下的本地 reference audio/text，优先用于稳定分段音色 |
| `--tts-reference-audio <wav>` | Fish S2 Pro 音色克隆参考音频 |
| `--tts-reference-text <text>` | 参考音频对应文本，需与 `--tts-reference-audio` 同时提供 |
| `--tts-max-new-tokens <n>` | 每个 TTS 分段的最大生成 token 数，默认 `512` |
| `--tts-max-runtime-hours <hours>` | 单次 TTS 批次最长运行时间，默认 `5` 小时；到期后停止 worker、释放资源并保留分片，重新运行相同命令可断点续跑 |
| `--audio-format <wav>` | 音频格式，当前支持 `wav` |
| `--keep-audio-parts` | 保留每段生成的分片 wav；默认合并后清理分片 |
| `--list-voices` | 列出 `voices/` 下的本地候选说话人 |
| `--voice-gallery` | 生成 `output/voice_gallery.html`，用于浏览器试听候选说话人 |
| `--import-fish-voice <id\|url>` | 导入 Fish Audio 公开模型的 sample audio/text 到本地 `voices/`，用于内部评估 |
| `--overwrite-voice` | 允许 `--import-fish-voice` 覆盖已有 voice 目录 |
| `--check-tools` | 检查工具可用性 |

## 处理流程

1. **OCR**: 按 `--ocr-engine` 选择 OCR backend；当前 `tesseract` backend 通过 `ocrmypdf` 调用 Tesseract，生成可搜索 PDF 和 sidecar 文本；若系统存在 `unpaper` 则启用 `--clean`，否则自动跳过清理以避免 OCR 中断
2. **LLM 清洗**: 按分页分组调用所选 agent 清洗文本并合并为 Markdown；默认调用 `claude -p --output-format json`，传入 `--agent codex` 时调用 `codex exec`
3. **Markdown-only（可选）**: 传入 `--md-only` 时到此停止，保留输入 PDF 和中间 Markdown，不生成 EPUB/音频，也不归档旧 EPUB
4. **EPUB**: Markdown 转 EPUB，输出到 `output/epub/`
5. **音频生成（可选）**: `--audio` 或 `--audio-only` 会启动一个常驻 TTS worker，加载一次 `fish-s2-pro`，再把 Markdown 规整、分段，逐段生成 wav，最后合并为 `output/audio/*.wav`；默认运行上限为 5 小时，到期后关闭 worker 并保留已完成分片，重新运行相同命令会校验并续跑；若同名 wav 已存在，旧文件会先归档到 `output/archived_audio/`
6. **批次归档旧 EPUB**: 本次运行结束后，`output/epub/` 中所有不属于本批次成功结果的 EPUB 都会移到 `output/archived_epub/`
7. **批次归档旧 WAV**: 生成音频的运行结束后，`output/audio/` 中所有不属于本批次成功结果的 wav 都会移到 `output/archived_audio/`
8. **成功清理/归档**: 成功后自动删除 `output/ocr_pdf/*.ocr.pdf`，默认保留 `output/clean_md/*.md`；传入 `--no-keep-md` 时也会删除中间 Markdown，并把已处理输入 PDF 移到 `input/archived_pdf/`
9. **Audio-only 输入归档**: `--audio-only` 成功后，会按 Markdown 文件名在 `input/` 顶层查找同名 PDF；找到时归档到 `input/archived_pdf/`

默认情况下，`--all` 只扫描 `input/`，不会处理归档目录中的历史文件。

失败时的当前行为：
- OCR 或 LLM 清洗失败：输入 PDF 保留在 `input/`，不会归档
- `output/epub/` 表示“本批次成功生成的 EPUB 结果集”，不是“每本书各自最后一次成功版本”
- `output/audio/` 表示“本批次成功生成的 WAV 结果集”，不是“每本书各自最后一次成功版本”
- 若本批次只有部分 PDF 成功，则 `output/epub/` 只保留这些成功结果，其他原有 EPUB 会归档到 `output/archived_epub/`
- 若本批次只有部分音频成功，则 `output/audio/` 只保留这些成功结果，其他原有 WAV 会归档到 `output/archived_audio/`
- 若本批次全部失败，则 `output/epub/` 最终为空。这是当前接受的行为。

## 运行要求

当前默认使用 Claude CLI。运行环境需满足以下至少一种 Claude CLI 认证方式：

| 方式 | 说明 |
|------|------|
| `ANTHROPIC_API_KEY` | 走 API key |
| `ANTHROPIC_AUTH_TOKEN` | 走 bearer token |

使用 `--agent codex` 时，运行环境需满足以下至少一种 Codex CLI 认证方式：

| 方式 | 说明 |
|------|------|
| `codex login` | 使用本机 Codex 登录状态 |
| `OPENAI_API_KEY` | 走 API key |

Fish S2 Pro MLX 当前不提供固定说话人列表。默认使用模型自身音色；如需指定音色，优先把候选放入 `voices/<voice_id>/` 并使用 `--tts-voice`。也可以直接使用 `--tts-reference-audio` 和 `--tts-reference-text` 做一次性参考音频克隆。

## 本地说话人筛选

每个候选说话人使用一个目录：

```text
voices/<voice_id>/
  metadata.json
  reference.wav
  reference.txt
  sample.wav
```

- `reference.wav` 和 `reference.txt` 用于实际 TTS 生成，文本必须与音频内容匹配。
- `sample.wav` 用于人工试听筛选，可先用同一段测试文字生成。
- `metadata.json` 记录来源、license、语言、性别、风格和备注。

生成选择页：

```bash
conda run -n pdf2epub python src/pdf2epub.py --voice-gallery
```

然后在浏览器打开 `output/voice_gallery.html`，试听后记录 `voice_id`，生成音频时传入：

```bash
conda run -n pdf2epub python src/pdf2epub.py --audio-only --md-input output/clean_md/example.md --tts-voice <voice_id>
```

Fish Audio 公开模型样本也可以导入为候选：

```bash
conda run -n pdf2epub python src/pdf2epub.py --import-fish-voice <model_id_or_url>
```

导入结果会标记 `license: unknown - confirm permission before production use`。公开可试听不等于可长期复用；正式使用前需要确认作者授权或 license。

## Git 忽略规则

项目默认不把本地输入数据和生成结果提交到 git：

- `input/`：原始 PDF 和已归档 PDF。
- `output/`：OCR PDF、sidecar 文本、Markdown、EPUB、音频、试听页和日志。
- `voices/*`：本地说话人素材、导入的第三方音频和元数据。
- `!voices/README.md`：只保留本地说话人库的目录约定文档。

这样可以避免把大文件、私人 PDF、生成音频、日志和授权不明确的第三方声音样本提交到仓库。

## 验证建议

成功处理后，重点检查：
- `output/sidecar_txt/*.txt` 可供 `--skip-ocr` 复用
- `output/clean_md/*.md` 默认保留，可用于检查 LLM 清洗后的中间结果
- `output/epub/*.epub` 可正常打开
- `output/audio/*.wav` 可正常播放（使用 `--audio` 或 `--audio-only` 时）
- `input/archived_pdf/` 中保留已成功处理的输入 PDF
- `output/archived_epub/` 中保留被替换的旧 EPUB
- `output/archived_audio/` 中保留被替换的旧 WAV
- `output/logs/run/*.json` 中保留 `input_tokens`、`output_tokens` 等统计字段

默认应检查 `output/clean_md/*.md` 是否保留；只有显式使用 `--no-keep-md` 时，成功生成 EPUB 后才会自动清理。

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
- `output/audio/` 表示最近一次音频运行中成功产出的结果集；重新运行单本或一批 Markdown 时，旧批次 WAV 会被归档到 `output/archived_audio/`
