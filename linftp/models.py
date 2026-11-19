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


def _type_text(entry: RemoteFile) -> str:
    if entry.is_link:
        return "链接"
    if entry.is_dir:
        return "目录"
    return _ext_of(entry.name) or "文件"


def sort_key(column: int, entry: RemoteFile):
    """排序键。必须返回可直接比较的值——不能拿显示文本排。

    大小列显示的是 "1.5 KB"，按字符串排会把 900 B 排到 1.5 KB 后面；
    修改时间如果按格式化后的文本排，"-"（未知）会跑到中间去。
    """
    if column == COL_SIZE:
        # 目录没有有意义的大小，给 -1 让它排在升序最前面（目录本来就置顶）
        return -1 if entry.is_dir else entry.size
    if column == COL_TYPE:
        return _type_text(entry).lower()
    if column == COL_MTIME:
        return entry.mtime
    if column == COL_PERM:
        return -1 if entry.mode is None else entry.mode
    return entry.name.lower()


class FileListModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw: List[RemoteFile] = []      # 未过滤的全量，过滤从它重算
        self._entries: List[RemoteFile] = []
        self._filter = ""
        self._sort_col = COL_NAME
        self._sort_order = Qt.AscendingOrder

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
        self._raw = list(entries)
        self._rebuild()

    def set_filter(self, text: str) -> None:
        # 从 _raw 重算，不能在上一次的结果上再过滤：
        # 否则删掉几个字符时，被前一次过滤掉的行回不来
        self._filter = (text or "").strip().lower()
        self._rebuild()

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        self._sort_col = int(column)
        self._sort_order = order
        self._rebuild()

    def sort_column(self) -> int:
        return self._sort_col

    def sort_order(self):
        return self._sort_order

    def _rebuild(self) -> None:
        rows = [e for e in self._raw if self._matches(e)]
        self.beginResetModel()
        self._entries = self._sort_entries(rows)
        self.endResetModel()

    def _sort_entries(self, rows: List[RemoteFile]) -> List[RemoteFile]:
        desc = self._sort_order == Qt.DescendingOrder
        ordered = sorted(rows, key=lambda e: sort_key(self._sort_col, e), reverse=desc)
        # 目录永远置顶：再按 is_dir 稳定分一次组，升降序只作用于组内
        return sorted(ordered, key=lambda e: not e.is_dir)

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
