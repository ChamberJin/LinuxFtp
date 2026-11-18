"""双栏共用的文件列表模型，同时负责本地与远端两种数据源。"""
from __future__ import annotations

import datetime as dt
from typing import Callable, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor

from .backend import RemoteFile
from .theme import Palette

COL_NAME, COL_SIZE, COL_TYPE, COL_MTIME, COL_PERM = range(5)
HEADERS = ["名称", "大小", "类型", "修改时间", "权限"]


def human_size(n: int, is_dir: bool = False) -> str:
    if is_dir:
        return "<目录>"
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} PB"


def human_time(ts: float) -> str:
    if not ts:
        return "-"
    try:
        return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return "-"


class FileListModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: List[RemoteFile] = []
        self._filter = ""

    # ---------------------------------------------------------- Qt 接口
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        entry = self._entries[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == COL_NAME:
                return entry.name + ("/" if entry.is_dir else "")
            if col == COL_SIZE:
                # 目录大小在 FTP 上通常是无意义值（4096），
                # 类型列已经标明"目录"，这里留空避免重复信息
                return "" if entry.is_dir else human_size(entry.size)
            if col == COL_TYPE:
                if entry.is_link:
                    return "链接"
                return "目录" if entry.is_dir else _ext_of(entry.name) or "文件"
            if col == COL_MTIME:
                return human_time(entry.mtime)
            if col == COL_PERM:
                return entry.mode_text
        elif role == Qt.TextAlignmentRole:
            if col in (COL_SIZE,):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        elif role == Qt.ForegroundRole:
            if entry.is_dir:
                return QBrush(QColor(Palette.DIR))
            if entry.is_link:
                return QBrush(QColor(Palette.LINK))
        elif role == Qt.ToolTipRole:
            return (
                f"{entry.path}\n"
                f"大小: {human_size(entry.size, entry.is_dir)}\n"
                f"修改: {human_time(entry.mtime)}"
            )
        elif role == Qt.UserRole:
            return entry
        return None

    # ---------------------------------------------------------- 自定义
    def set_entries(self, entries: List[RemoteFile]) -> None:
        self.beginResetModel()
        self._entries = [e for e in entries if self._matches(e)]
        self.endResetModel()

    def set_filter(self, text: str) -> None:
        self._filter = (text or "").strip().lower()
        self.beginResetModel()
        self._entries = [e for e in self._entries if self._matches(e)]
        self.endResetModel()

    def _matches(self, e: RemoteFile) -> bool:
        if not self._filter:
            return True
        pat = self._filter
        if "*" in pat or "?" in pat:
            import fnmatch

            return fnmatch.fnmatch(e.name.lower(), pat)
        return pat in e.name.lower()

    def entry_at(self, row: int) -> Optional[RemoteFile]:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def entries(self) -> List[RemoteFile]:
        return list(self._entries)


def _ext_of(name: str) -> str:
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].upper() + " 文件"
