"""文本预览：双击文件看一眼内容，不必先下载到本地。

三个要点：
- **只读文件头**（默认 2 MB）。远端因此不用把几百兆的日志整份拉下来，
  代价是超过上限的文件只能看到开头，界面上会明说被截断。
- **编码自动探测**。UTF-8 → GB18030 → Big5 → latin-1 依次试，
  UTF-16/32 走 BOM 判定；探测结果可以手动改。
- **二进制不硬塞乱码**。含 NUL 或大量控制字节就提示"这不是文本"，
  本地文件额外给一个"用系统程序打开"的出口。
"""
from __future__ import annotations

import codecs

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFontDatabase, QKeySequence, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from . import theme
from .backend import SessionError, create_session
from .models import human_size, human_time
from .theme import Palette

# 展示上限 / 实际读取上限：多读 4KB 用来判断"是不是还有后续内容"
MAX_PREVIEW = 2 * 1024 * 1024
READ_LIMIT = MAX_PREVIEW + 4096

# 手动可选的编码。utf-16 只在这里出现——没有 BOM 时按 utf-16 解会错得离谱
ENC_CHOICES = ("utf-8", "gb18030", "utf-16", "big5", "latin-1")

TEXT_EXTS = {
    "txt", "text", "log", "out", "err", "lst", "md", "markdown", "rst", "adoc",
    "csv", "tsv", "json", "jsonl", "ndjson", "xml", "yaml", "yml", "toml",
    "ini", "cfg", "conf", "config", "properties", "env", "sql", "html", "htm",
    "xhtml", "css", "scss", "sass", "less", "js", "mjs", "cjs", "ts", "tsx",
    "jsx", "vue", "svelte", "php", "py", "pyi", "pyw", "sh", "bash", "zsh",
    "fish", "bat", "cmd", "ps1", "psm1", "java", "kt", "kts", "scala", "go",
    "rs", "rb", "pl", "lua", "c", "h", "cc", "cpp", "cxx", "hpp", "hxx", "cs",
    "m", "mm", "swift", "dart", "gradle", "groovy", "cmake", "mk", "ninja",
    "service", "socket", "desktop", "pem", "crt", "csr", "pub", "asc", "gpg",
    "svg", "diff", "patch", "srt", "vtt", "tex", "bib", "org", "dot", "graphql",
    "gql", "proto", "thrift", "avsc", "plist", "make", "am", "ac", "in", "spec",
}

# 明确不是文本的，省掉读取判断（也避免把 pdf 的前 8KB 当文本试解）
BINARY_EXTS = {
    "png", "jpg", "jpeg", "gif", "bmp", "webp", "ico", "tif", "tiff", "psd",
    "pdf", "zip", "gz", "tgz", "bz2", "xz", "zst", "7z", "rar", "tar", "jar",
    "war", "ear", "apk", "deb", "rpm", "iso", "img", "dmg", "exe", "msi",
    "dll", "so", "dylib", "a", "o", "class", "pyc", "pyd", "obj", "lib", "bin",
    "dat", "db", "sqlite", "sqlite3", "mdb", "mp3", "mp4", "avi", "mkv", "mov",
    "flv", "wmv", "wav", "flac", "aac", "ogg", "webm", "ppt", "pptx", "doc",
    "docx", "xls", "xlsx", "odt", "ods", "odp", "ttf", "otf", "woff", "woff2",
    "eot", "swf", "wasm",
}

# 没有扩展名但基本一定是文本的
TEXT_NAMES = {
    "makefile", "gnumakefile", "dockerfile", "containerfile", "license",
    "licence", "copying", "readme", "changelog", "changes", "authors",
    "contributors", "notice", "install", "vagrantfile", "gemfile", "rakefile",
    "procfile", "brewfile", "justfile", "makefile.am", ".gitignore",
    ".gitattributes", ".editorconfig", ".npmrc", ".htaccess", ".env",
    ".bashrc", ".zshrc", ".profile", ".vimrc",
}


def _basename(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def ext_of(name: str) -> str:
    base = _basename(name)
    if "." not in base.lstrip("."):
        return ""
    return base.rsplit(".", 1)[-1].lower()


def guess_text_kind(name: str) -> str:
    """按文件名猜内容类型："text" / "binary" / "unknown"（需要看内容）。"""
    base = _basename(name).lower()
    if base in TEXT_NAMES:
        return "text"
    ext = ext_of(name)
    if not ext:
        return "unknown"
    if ext in TEXT_EXTS:
        return "text"
    if ext in BINARY_EXTS:
        return "binary"
    return "unknown"


def _bom_encoding(data: bytes) -> str:
    """按 BOM 判编码。UTF-16 文本天然带 NUL，必须在二进制判定之前认出来。"""
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith(codecs.BOM_UTF32_LE) or data.startswith(codecs.BOM_UTF32_BE):
        return "utf-32"
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    return ""


def looks_binary(data: bytes) -> bool:
    """内容层面判断是不是二进制。

    只看头部 8KB：含 NUL 直接判死（BOM 开头的 UTF-16 除外），
    否则数控制字符占比——终端日志里的 ESC 要放行，否则带颜色的日志会被误判。
    """
    if not data:
        return False
    if _bom_encoding(data):
        return False
    sample = data[:8192]
    if b"\x00" in sample:
        return True
    bad = sum(1 for b in sample
              if b < 32 and b not in (9, 10, 11, 12, 13, 27))
    return bad / len(sample) > 0.05


def decode_text(data: bytes, preferred: str = "", may_truncate: bool = False):
    """把字节解成文本，返回 (文本, 实际编码, 是否有损)。

    `may_truncate` 用于"只取了前 N 字节"的场景：截断点可能落在多字节字符
    中间，这时把尾部切掉 1~3 字节再试，避免好好的 UTF-8 被误判成 GBK。
    """
    if not data:
        return "", preferred or "utf-8", False

    order: list[str] = []
    bom = _bom_encoding(data)
    if bom:
        order.append(bom)
    if preferred:
        order.append(preferred)
    order += [e for e in ENC_CHOICES if e not in order]

    cuts = (0, 1, 2, 3) if may_truncate else (0,)
    for enc in order:
        for cut in cuts:
            chunk = data[:len(data) - cut] if cut else data
            try:
                return chunk.decode(enc), enc, False
            except (UnicodeDecodeError, LookupError):
                continue
    return data.decode("utf-8", "replace"), "utf-8", True


class PreviewLoader(QThread):
    """后台取文件头部：本地直接读文件，远端只拉前几 MB。"""

    done = Signal(bytes, str)      # 内容, 错误

    def __init__(self, path: str, cfg=None, limit: int = READ_LIMIT, parent=None):
        super().__init__(parent)
        self._path = path
        self._cfg = cfg
        self._limit = limit

    def run(self) -> None:
        try:
            if self._cfg is None:
                with open(self._path, "rb") as f:
                    data = f.read(self._limit)
            else:
                with create_session(self._cfg) as sess:
                    data = sess.read_head(self._path, self._limit)
        except SessionError as e:
            self.done.emit(b"", str(e))
            return
        except FileNotFoundError:
            self.done.emit(b"", f"文件不存在：{self._path}")
            return
        except Exception as e:  # noqa: BLE001 兜底，别让线程静默死掉
            self.done.emit(b"", f"{type(e).__name__}: {e}")
            return
        self.done.emit(data, "")


class PreviewDialog(QDialog):
    """只读预览窗口。非模态：开着看内容的同时还能操作主窗口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("预览")
        self.resize(940, 690)
        self._data = b""
        self._total = 0
        self._truncated = False
        self._binary = False
        self._local_path = ""
        self._loader: PreviewLoader | None = None
        self._find_hits = 0
        self._base_hint = ""

        self.name_label = QLabel("")
        self.name_label.setStyleSheet(
            f"color: {Palette.TEXT}; font-size: 15px; font-weight: 600;")
        self.name_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet(f"color: {Palette.TEXT_FAINT};")

        self.enc_label = QLabel("编码")
        self.enc_label.setStyleSheet(f"color: {Palette.TEXT_MUTED};")
        self.enc_box = QComboBox()
        self.enc_box.addItems(["自动", *ENC_CHOICES])
        self.enc_box.setFixedWidth(104)
        self.enc_box.setToolTip("自动探测不准时手动指定编码")
        self.enc_box.activated.connect(lambda _i: self._render())

        self.wrap_box = QCheckBox("自动换行")
        self.wrap_box.toggled.connect(self._on_wrap_toggled)

        self.btn_find = QPushButton("查找")
        self.btn_find.setToolTip("在当前内容里查找（Ctrl+F）")
        self.btn_find.clicked.connect(self.show_find)

        self.btn_system = QPushButton("用系统程序打开")
        self.btn_system.setToolTip("交给系统默认程序处理（仅本地文件）")
        self.btn_system.clicked.connect(self._open_in_system)

        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.close)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("查找内容，回车下一个（Esc 关闭查找）")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.returnPressed.connect(self.find_next)
        self.find_edit.textChanged.connect(self._on_find_text)
        self.find_edit.setVisible(False)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {Palette.TEXT_MUTED};")

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setPlaceholderText("")
        fixed = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        base_pt = theme.base_font().pointSizeF()
        fixed.setPointSizeF(max(9.0, base_pt - 0.5) if base_pt > 0 else 10.0)
        self.text.setFont(fixed)
        self.text.setTabStopDistance(
            self.text.fontMetrics().horizontalAdvance(" ") * 4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.name_label, 1)
        head.addWidget(self.meta_label)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        bar.addWidget(self.enc_label)
        bar.addWidget(self.enc_box)
        bar.addWidget(self.wrap_box)
        bar.addWidget(self.btn_find)
        bar.addWidget(self.find_edit, 1)
        bar.addStretch(0)
        bar.addWidget(self.btn_system)
        bar.addWidget(self.btn_close)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)
        root.addLayout(head)
        root.addLayout(bar)
        root.addWidget(self.hint)
        root.addWidget(self.text, 1)

        self.btn_system.setVisible(False)
        theme.inherit_app_font(self)

    # ------------------------------------------------------------ 入口
    def preview(self, *, name: str, path: str, size: int = 0, mtime: float = 0.0,
                cfg=None, local_path: str = "") -> None:
        """开始预览。cfg 为 None 表示本地文件，否则走远端 read_head。"""
        self.setWindowTitle(f"预览 - {name}")
        self.name_label.setText(name)
        self.meta_label.setText(
            " · ".join(x for x in (human_size(size), human_time(mtime) if mtime else "")
                       if x))
        self._local_path = local_path
        self._total = size
        self.btn_system.setVisible(bool(local_path))
        self.hint.setStyleSheet(f"color: {Palette.TEXT_MUTED};")
        self.hint.setText("正在读取…")
        self.text.setPlainText("")

        self._loader = PreviewLoader(path, cfg, parent=self)
        self._loader.done.connect(self._on_loaded)
        self._loader.start()

    # ------------------------------------------------------------ 加载完成
    def _on_loaded(self, data: bytes, error: str) -> None:
        if error:
            self._data = b""
            self.hint.setStyleSheet(f"color: {Palette.DANGER};")
            self.hint.setText(f"读取失败：{error}")
            return

        self._truncated = len(data) > MAX_PREVIEW
        self._data = data[:MAX_PREVIEW] if self._truncated else data
        kind = guess_text_kind(self.name_label.text())

        # 明确是二进制扩展名，或内容本身不像文本 → 不往编辑器里塞乱码
        if kind == "binary" or looks_binary(self._data):
            self._binary = True
            self.text.setPlainText("")
            self.hint.setStyleSheet(f"color: {Palette.WARN};")
            tail = ("本地文件可以点右上角「用系统程序打开」。"
                    if self._local_path else "远端文件可以先下载到本地再打开。")
            self.hint.setText(f"这不是文本文件，无法以文本方式预览。{tail}")
            return

        self._binary = False
        self._render()

    # ------------------------------------------------------------ 渲染
    def _render(self) -> None:
        if self._binary or not self._data:
            if not self._data and not self._binary:
                self.hint.setStyleSheet(f"color: {Palette.TEXT_MUTED};")
                self.hint.setText("文件是空的。")
            return

        manual = self.enc_box.currentText()
        preferred = "" if manual == "自动" else manual
        text, enc, lossy = decode_text(self._data, preferred,
                                       may_truncate=self._truncated)
        self.text.setPlainText(text)
        # setPlainText 会把光标留在末尾并自动滚到底，预览要从头看
        self.text.moveCursor(QTextCursor.Start)
        self.text.verticalScrollBar().setValue(0)

        lines = text.count("\n") + 1 if text else 0
        bits = [f"编码 {enc}", f"{lines} 行"]
        if self._truncated:
            bits.append(f"仅显示前 {human_size(MAX_PREVIEW)}"
                        f"（共 {human_size(self._total or len(self._data))}）")
        elif self._total and self._total > len(self._data):
            bits.append(f"读取 {human_size(len(self._data))} / {human_size(self._total)}")
        if lossy:
            bits.append("部分字符无法解码，可手动换编码")
        self._base_hint = " · ".join(bits)
        self.hint.setStyleSheet(
            f"color: {Palette.WARN if lossy else Palette.TEXT_MUTED};")
        self.hint.setText(self._base_hint)

    def _on_wrap_toggled(self, on: bool) -> None:
        self.text.setLineWrapMode(
            QPlainTextEdit.WidgetWidth if on else QPlainTextEdit.NoWrap)

    def _open_in_system(self) -> None:
        if self._local_path:
            import subprocess

            subprocess.Popen(["xdg-open", self._local_path])

    # ------------------------------------------------------------ 查找
    def show_find(self) -> None:
        self.find_edit.setVisible(True)
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def hide_find(self) -> None:
        self.find_edit.setVisible(False)
        self.text.setFocus()

    def _on_find_text(self, text: str) -> None:
        self._find_hits = 0
        if not text:
            # 清空查找词就把提示还原成编码/行数，别留着"没找到 xxx"
            if self._base_hint:
                self.hint.setStyleSheet(f"color: {Palette.TEXT_MUTED};")
                self.hint.setText(self._base_hint)
            return
        self._find(text, forward=True, restart=True)

    def find_next(self) -> None:
        self._find(self.find_edit.text(), forward=True)

    def _find(self, needle: str, forward: bool = True, restart: bool = False) -> bool:
        if not needle:
            return False
        flags = QTextDocument.FindFlag(0)
        if not forward:
            flags |= QTextDocument.FindBackward
        if restart:
            self.text.moveCursor(QTextCursor.Start)
        if self.text.find(needle, flags):
            self._find_hits += 1
            self._after_find()
            return True
        # 到尾了就从另一头再来一圈，别让用户以为没找到
        self.text.moveCursor(QTextCursor.End if forward else QTextCursor.Start)
        if self.text.find(needle, flags):
            self._after_find()
            return True
        self.hint.setStyleSheet(f"color: {Palette.WARN};")
        self.hint.setText(f"没找到：{needle}")
        return False

    def _after_find(self) -> None:
        # QPlainTextEdit 失焦时用非激活调色板画选区，浅色主题下几乎看不见，
        # 所以把命中行滚到中间，位置感比颜色更可靠
        self.text.centerCursor()
        if self._base_hint:
            self.hint.setStyleSheet(f"color: {Palette.TEXT_MUTED};")
            self.hint.setText(f"{self._base_hint} · 第 {self._find_hits} 处匹配")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self.find_edit.isVisible():
            self.hide_find()
            return
        if (event.matches(QKeySequence.StandardKey.Find)
                or event.matches(QKeySequence.StandardKey.FindNext)):
            self.show_find()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        # 窗口不带 WA_DeleteOnClose 时也会走到这里；带上的话更得先收线程，
        # 否则 QThread 在运行中被析构会直接 abort 整个进程
        self._stop_loader()
        super().closeEvent(event)

    def _stop_loader(self) -> None:
        loader = self._loader
        self._loader = None
        if loader is None:
            return
        try:
            loader.done.disconnect()
        except (RuntimeError, TypeError):
            pass
        if loader.isRunning():
            loader.wait(5000)
