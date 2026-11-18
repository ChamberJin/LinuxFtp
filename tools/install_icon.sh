#!/usr/bin/env bash
# 生成并安装 LinFTP 图标 + 桌面入口。
#
# 做三件事：
#   1. 用 rsvg-convert 把 assets/linftp-icon.svg 渲染成整套标准尺寸 PNG
#   2. 装到 ~/.local/share/icons/hicolor/<尺寸>/apps/，同时留一份 scalable/
#   3. 写 ~/.local/share/applications/linftp.desktop 和桌面快捷方式
#
# 幂等：重复执行只会覆盖，不会堆重复项。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"          # tools/
ROOT="$(cd "$HERE/.." && pwd)"                  # 工程根目录
SVG="$ROOT/assets/linftp-icon.svg"
APP_ID="linftp"

ICON_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

if [ ! -f "$SVG" ]; then
    echo "找不到图标源文件：$SVG" >&2
    exit 1
fi

# ---- 1. 渲染各尺寸 -------------------------------------------------------
render() {
    local size="$1" dest="$2"
    mkdir -p "$dest"
    rsvg-convert -w "$size" -h "$size" "$SVG" -o "$dest/$APP_ID.png"
}

for size in 16 22 24 32 48 64 96 128 256 512; do
    render "$size" "$ICON_ROOT/${size}x${size}/apps"
    echo "  + ${size}x${size}/apps/$APP_ID.png"
done

# scalable 目录直接放 SVG，高分屏缩放时不会糊
mkdir -p "$ICON_ROOT/scalable/apps"
cp "$SVG" "$ICON_ROOT/scalable/apps/$APP_ID.svg"
echo "  + scalable/apps/$APP_ID.svg"

# ---- 2. 桌面入口 ---------------------------------------------------------
# Exec 走工程的 run.sh，这样虚拟环境、TMPDIR 这些都沿用同一套逻辑
LAUNCHER="$ROOT/run.sh"
LAUNCHER="$(readlink -f "$LAUNCHER")"

mkdir -p "$APP_DIR"
write_desktop() {
    local target="$1"
    cat > "$target" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=LinFTP
Name[zh_CN]=LinFTP 文件传输
GenericName=FTP Client
GenericName[zh_CN]=FTP 客户端
Comment=Linux 上的双栏 FTP 客户端（SFTP/SCP/FTPS 规划中）
Comment[zh_CN]=Linux 上的双栏 FTP 客户端，支持书签、编码自动识别、传输队列
Exec=$LAUNCHER %U
Icon=$APP_ID
Terminal=false
Categories=Network;FileTransfer;
Keywords=ftp;sftp;ftps;scp;file;transfer;
Keywords[zh_CN]=文件传输;上传;下载;远程;
StartupNotify=true
StartupWMClass=linftp
EOF
    chmod 755 "$target"
    # DDE/GNOME 要求桌面快捷方式标记为可信，否则双击只弹属性框
    gio set "$target" metadata::trusted true 2>/dev/null || true
}

install -Dm755 /dev/null "$APP_DIR/$APP_ID.desktop" 2>/dev/null || true
write_desktop "$APP_DIR/$APP_ID.desktop"
echo "  + $APP_DIR/$APP_ID.desktop"

if [ -d "$DESKTOP_DIR" ]; then
    write_desktop "$DESKTOP_DIR/$APP_ID.desktop"
    echo "  + $DESKTOP_DIR/$APP_ID.desktop"
fi

# ---- 3. 刷新缓存 ---------------------------------------------------------
# 不刷新的话，文件管理器可能还挂着旧的图标缓存
gtk-update-icon-cache -f -t "$ICON_ROOT" >/dev/null 2>&1 || true
update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true

echo
echo "完成。可以从桌面双击，或在启动器里搜 “LinFTP”。"
