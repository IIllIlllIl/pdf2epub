#!/bin/bash
# 便捷运行脚本 - 仅传递 Claude 认证相关环境，避免宿主机 home 污染容器环境

set -e
cd "$(dirname "$0")"

docker run --rm \
  -e ANTHROPIC_API_KEY \
  -e ANTHROPIC_AUTH_TOKEN \
  -e ANTHROPIC_BASE_URL \
  -e CLAUDE_CODE_USE_BEDROCK \
  -e CLAUDE_CODE_USE_VERTEX \
  -e CLAUDE_CODE_USE_FOUNDRY \
  -v "$PWD":/workspace \
  pdf2epub "$@"
