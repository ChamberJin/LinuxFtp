#!/usr/bin/env bash
# LinFTP 打包脚本：PyInstaller 目录模式 + 产物瘦身
# 产物：dist/LinFTP/LinFTP（可执行文件，运行时不需要解压到 /tmp）
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/pyinstaller ]; then
    echo "未找到 PyInstaller，请先执行："
    echo "  TMPDIR=\$PWD/.tmp .venv/bin/pip install pyinstaller"
    exit 1
fi

# 部分发行版 /tmp 很小，构建过程统一指到工程目录
export TMPDIR="${TMPDIR_OVERRIDE:-$PWD/.tmp}"
mkdir -p "$TMPDIR"

# 排除用不到的 Qt 大模块（WebEngine/Qml/Quick/多媒体等）
EXCLUDES=(
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets
  --exclude-module PySide6.QtWebEngineQuick --exclude-module PySide6.QtQml
  --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQuick3D
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtCharts
  --exclude-module PySide6.QtDataVisualization --exclude-module PySide6.QtPdf
  --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtNetworkAuth
  --exclude-module PySide6.QtWebSockets --exclude-module PySide6.QtSql
  --exclude-module PySide6.QtTest --exclude-module tkinter
)

.venv/bin/pyinstaller --noconfirm --clean --onedir --windowed \
  --name LinFTP \
  --add-data "assets:assets" \
  "${EXCLUDES[@]}" \
  main.py

# 产物瘦身：去掉 Python/系统库的调试符号（本机 libpython 自带符号可达 200M+）
strip --strip-unneeded dist/LinFTP/_internal/libpython3.13.so.1.0 \
  dist/LinFTP/_internal/libgtk-3.so.0 \
  dist/LinFTP/_internal/libstdc++.so.6 \
  dist/LinFTP/_internal/libcrypto.so.3 2>/dev/null || true

# 纯 Widgets 应用不需要的 Qt 库（依赖分析会连带拉进来，手动剔除）
cd dist/LinFTP/_internal/PySide6
rm -f Qt/lib/libQt6Qml*.so* Qt/lib/libQt6Quick.so* Qt/lib/libQt6VirtualKeyboard*.so* \
      Qt/lib/libQt6Pdf.so* QtQml.abi3.so QtQuick*.abi3.so QtPdf*.abi3.so
rm -rf Qt/plugins/qmltooling
cd ../../..

echo "打包完成：dist/LinFTP/LinFTP"
du -sh dist/LinFTP
