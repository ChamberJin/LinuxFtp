#!/usr/bin/env bash
# LinFTP 启动脚本
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "未找到虚拟环境，请先执行："
    echo "  python3 -m venv .venv"
    echo "  TMPDIR=\$PWD/.tmp .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# 部分发行版的 /tmp 很小，pip / Qt 解包时会失败，这里统一指到工程目录
export TMPDIR="${TMPDIR_OVERRIDE:-$PWD/.tmp}"
mkdir -p "$TMPDIR"

exec .venv/bin/python -m linftp "$@"
