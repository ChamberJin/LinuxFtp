"""启动页：列书签 + 最近连接，双击直接进入。

和 ``BookmarkDialog``（管理用途）不同，这里只做"选一个进去"：
- 卡片式两条目，左侧圆形首字标记，右侧显示路径与所属站点
- 顶部搜索框，输入即过滤（名称 / 路径 / 站点都能匹配）
- 双击 / 回车 / 点"打开"直接进入
- Esc / "跳过" 打开空白主窗口

列出内容 = 书签 + 最近连接过的站点。后者解决"还没建书签时启动页是空的"，
让这个页面在第一次使用后就有实际价值。
"""
from __future__ import annotations

import os
from typing import List, Optional, Sequence
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .backend import SessionConfig
from .bookmarks import Bookmark
from .theme import Palette


class _Entry:
    """启动页里的一条记录：可能是书签，也可能是最近连接。"""

    def __init__(self, *, kind: str, title: str, subtitle: str,
                 badge: str, badge_color: str,
                 bookmark: Optional[Bookmark] = None,
                 site: Optional[SessionConfig] = None,
                 path: str = ""):
        self.kind = kind                 # "bookmark" | "recent"
        self.title = title
        self.subtitle = subtitle
        self.badge = badge
        self.badge_color = badge_color
        self.bookmark = bookmark
        self.site = site
        self.path = path

    def search_text(self) -> str:
        return " ".join((self.title, self.subtitle, self.badge, self.path)).lower()


class LaunchPad(QDialog):
    """启动时的入口选择页。"""

    open_bookmark_requested = Signal(object)   # Bookmark
    open_site_requested = Signal(object)       # SessionConfig

    def __init__(self, bookmarks: Sequence[Bookmark] = (),
                 recents: Sequence[SessionConfig] = (), parent=None):
        super().__init__(parent)
        self.setWindowTitle("LinFTP")
        self.setModal(True)

        self._entries: List[_Entry] = []
        self._build_entries(bookmarks, recents)

        root = QVBoxLayout(self)
        self.root_layout = root

        # ---------------- 标题区
        self.title_label = QLabel("选择要进入的位置")
        self.title_label.setStyleSheet(f"color: {Palette.TEXT};")

        subtitle = QLabel("双击进入；也可以跳过，从主窗口手动连接")
        self.subtitle_label = subtitle
        subtitle.setStyleSheet(f"color: {Palette.TEXT_MUTED};")

        head = QVBoxLayout()
        head.setSpacing(3)
        head.addWidget(self.title_label)
        head.addWidget(subtitle)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addLayout(head, 1)
        mark = QLabel()
        mark.setPixmap(theme.icon("bookmark", Palette.PRIMARY, 22).pixmap(22, 22))
        mark.setFixedSize(22, 22)
        title_row.addWidget(mark, 0, Qt.AlignTop)
        root.addLayout(title_row)

        # ---------------- 搜索框
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索书签或站点…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(theme.icon("search", Palette.TEXT_FAINT, 16),
                              QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._apply_filter)
        self.search.returnPressed.connect(self._on_open)

        # ---------------- 列表
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setUniformItemSizes(False)
        self.list.setAlternatingRowColors(False)
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; }}"
            f"QListWidget::item {{ border-radius: 9px; margin: 2px 0; padding: 0; }}"
            f"QListWidget::item:hover {{ background: {Palette.HOVER}; }}"
            f"QListWidget::item:selected {{ background: {Palette.PRIMARY_SOFT}; }}"
        )
        self.list.itemDoubleClicked.connect(lambda *_: self._on_open())
        self.list.itemActivated.connect(lambda *_: self._on_open())
        self.list.currentItemChanged.connect(lambda *_: self._sync_count())
        self._fill_list()

        # ---------------- 底部
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color: {Palette.TEXT_FAINT};")

        btn_skip = QPushButton("跳过")
        btn_skip.setProperty("flat", True)
        btn_skip.clicked.connect(self.reject)

        self.btn_open = QPushButton("打开")
        self.btn_open.setProperty("primary", True)
        self.btn_open.setDefault(True)
        self.btn_open.clicked.connect(self._on_open)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addWidget(self.count_label, 1)
        foot.addWidget(btn_skip)
        foot.addWidget(self.btn_open)

        root.addWidget(self.search)
        root.addWidget(self.list, 1)
        root.addLayout(foot)

        self.apply_metrics()
        self._sync_count()
        if self.list.count():
            self.list.setCurrentRow(0)
            self.list.setFocus()

    # ------------------------------------------------------------ 数据
    def _build_entries(self, bookmarks: Sequence[Bookmark],
                       recents: Sequence[SessionConfig]) -> None:
        # 书签优先，本地在前、远端在后，保持各自原有顺序
        for bm in list(bookmarks):
            if not bm.is_remote:
                self._entries.append(_Entry(
                    kind="bookmark", title=bm.name, subtitle=bm.path,
                    badge="本", badge_color=Palette.ACCENT,
                    bookmark=bm, path=bm.path))
        for bm in list(bookmarks):
            if bm.is_remote:
                self._entries.append(_Entry(
                    kind="bookmark", title=bm.name,
                    subtitle=bm.site_label() or bm.path,
                    badge="远", badge_color=Palette.PRIMARY,
                    bookmark=bm, path=bm.path))

        # 最近连接：排除已经有远端书签指向的站点，避免同一个站点出现两次
        covered = {b.site_key() for b in bookmarks if b.is_remote}
        for cfg in recents:
            key = (cfg.protocol, cfg.host, cfg.port or 0, cfg.username)
            if key in covered or not cfg.host:
                continue
            self._entries.append(_Entry(
                kind="recent", title=cfg.host,
                subtitle=f"{cfg.username}@{cfg.host}  ·  最近连接",
                badge="近", badge_color=Palette.TEXT_FAINT,
                site=cfg))

    def _row_height(self) -> int:
        return theme.metrics.pad_row_h

    def apply_metrics(self) -> None:
        """按当前尺寸规格重排整个启动页。

        字号必须在这里显式设置：QDialog 的字体是在构造那一刻从父窗口
        继承下来的快照，之后 app.setFont() 不会再传进来，
        所以"重启后紧凑模式对启动页无效"的根因就在这里。
        """
        m = theme.metrics

        # 字体：显式跟随应用字体（含紧凑模式下的字号）
        theme.inherit_app_font(self)
        base = theme.base_font()

        self.resize(m.pad_w, m.pad_h)
        self.setMinimumSize(int(m.pad_w * 0.78), int(m.pad_h * 0.74))

        self.root_layout.setContentsMargins(*m.pad_margins)
        self.root_layout.setSpacing(m.pad_spacing)

        title_font = QFont(base)
        title_font.setPixelSize(m.pad_title_size)
        title_font.setWeight(QFont.DemiBold)
        self.title_label.setFont(title_font)

        self.search.setFixedHeight(m.pad_search_h)
        self.btn_open.setFixedWidth(76 if m.pad_row_h < 50 else 96)

    def showEvent(self, event) -> None:
        """上屏后再校正一次位置。

        调用方（MainWindow.show_launchpad）在 show() 之后已经摆过位，
        但窗口管理器在真正映射窗口时还可能自行挪一次；而且这里列表已经
        填完、布局尺寸才是最终值。所以在 showEvent 里再钉一遍，
        确保它老老实实待在父窗口那块屏上。
        """
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            theme.center_on_parent(self, parent)

    def _fill_list(self) -> None:
        self.list.clear()
        row_h = self._row_height()
        for entry in self._entries:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, entry)
            item.setSizeHint(QSize(0, row_h))
            self.list.addItem(item)
            self.list.setItemWidget(item, self._make_row(entry))
        self._apply_filter(self.search.text())

    def _make_row(self, entry: _Entry) -> QWidget:
        row = QWidget()
        row.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(row)
        m = theme.metrics
        lay.setContentsMargins(*m.pad_row_margins)
        lay.setSpacing(m.pad_row_spacing)

        avatar = theme.metrics.pad_avatar
        badge = QLabel()
        badge.setPixmap(theme.avatar_text(entry.badge, avatar, entry.badge_color))
        badge.setFixedSize(avatar, avatar)
        lay.addWidget(badge)

        name = QLabel(entry.title)
        nf = QFont(theme.base_font())
        nf.setWeight(QFont.DemiBold)
        name.setFont(nf)
        name.setStyleSheet(f"color: {Palette.TEXT};")

        path = QLabel(entry.subtitle)
        path.setStyleSheet(f"color: {Palette.TEXT_MUTED}; font-size: 12px;")

        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(name)
        text.addWidget(path)
        lay.addLayout(text, 1)

        if entry.kind == "bookmark":
            icon_name = "folder" if not (entry.bookmark and entry.bookmark.is_remote) else "server"
            tag = QLabel()
            tag.setPixmap(theme.icon(icon_name, Palette.TEXT_FAINT, 16).pixmap(16, 16))
            lay.addWidget(tag)
        return row

    # ------------------------------------------------------------ 交互
    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        first_visible = None
        for i in range(self.list.count()):
            item = self.list.item(i)
            entry: _Entry = item.data(Qt.UserRole)
            hit = not needle or needle in entry.search_text()
            item.setHidden(not hit)
            if hit and first_visible is None:
                first_visible = item
        current = self.list.currentItem()
        if current is None or current.isHidden():
            self.list.setCurrentItem(first_visible)
        self._sync_count()

    def _visible_count(self) -> int:
        return sum(1 for i in range(self.list.count())
                   if not self.list.item(i).isHidden())

    def _sync_count(self) -> None:
        # 构造过程中列表可能先于底部状态栏就绪（currentItemChanged 会提前触发）
        if not hasattr(self, "count_label"):
            return
        total = self._visible_count()
        if total == 0:
            self.count_label.setText("没有匹配项" if self.search.text() else "暂无书签")
        else:
            self.count_label.setText(f"共 {total} 项")
        self.btn_open.setEnabled(self.current_entry() is not None)

    def current_entry(self) -> Optional[_Entry]:
        item = self.list.currentItem()
        if item is None or item.isHidden():
            return None
        return item.data(Qt.UserRole)

    def _on_open(self) -> None:
        entry = self.current_entry()
        if entry is None:
            return
        if entry.kind == "bookmark" and entry.bookmark is not None:
            self.open_bookmark_requested.emit(entry.bookmark)
        elif entry.site is not None:
            self.open_site_requested.emit(entry.site)
        self.accept()

    def keyPressEvent(self, event) -> None:
        # 回车统一走"打开"，避免焦点在列表与按钮之间时行为不一致
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._on_open()
            return
        super().keyPressEvent(event)


def has_content(bookmarks: Sequence[Bookmark],
                recents: Sequence[SessionConfig]) -> bool:
    """启动页是否值得弹出来。"""
    return bool(bookmarks) or bool(recents)
