"""书签管理对话框：查看、跳转、重命名、排序、删除。"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import theme
from .bookmarks import Bookmark, BookmarkStore
from .theme import Palette


class BookmarkDialog(QDialog):
    """打开书签。双击或点"打开"回传要跳转的书签。"""

    open_requested = Signal(object)   # Bookmark

    def __init__(self, store: BookmarkStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("书签")
        self.resize(720, 486)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称", "类型", "路径", "所属站点"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setHighlightSections(False)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        # 名称、类型、站点固定；路径吃掉剩余宽度（窄窗时才横向滚动）
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.Fixed)
        hdr.setStretchLastSection(False)
        self.tree.setColumnWidth(0, 150)
        self.tree.setColumnWidth(1, 56)
        self.tree.setColumnWidth(3, 180)
        self._path_col_min = 200

        self.hint = QLabel("双击书签即可跳转到对应目录")
        self.hint.setStyleSheet(
            f"color: {Palette.TEXT_FAINT}; font-size: 12px;")

        # 勾上之后启动直接进这个书签，不再弹启动页。
        # 用 _syncing 防止 refresh() 里回填状态时反过来又触发 toggled。
        self.chk_default = QCheckBox("启动时默认打开选中的书签（不再弹启动页）")
        self.chk_default.toggled.connect(self._on_default_toggled)
        self._syncing = False

        btn_open = QPushButton("打开")
        btn_open.setProperty("primary", True)
        btn_rename = QPushButton("重命名")
        btn_up = QPushButton("上移")
        btn_down = QPushButton("下移")
        btn_del = QPushButton("删除")
        btn_del.setProperty("danger", True)
        self.btn_clear = QPushButton("清空全部")
        self.btn_clear.setProperty("flat", True)
        for b in (btn_open, btn_rename, btn_up, btn_down, btn_del, self.btn_clear):
            b.setMinimumHeight(30)

        btn_open.clicked.connect(self._on_open)
        btn_rename.clicked.connect(self._on_rename)
        btn_up.clicked.connect(self._on_move_up)
        btn_down.clicked.connect(self._on_move_down)
        btn_del.clicked.connect(self._on_delete)
        self.btn_clear.clicked.connect(self._on_clear)
        self.tree.itemDoubleClicked.connect(lambda *_: self._on_open())
        self.tree.currentItemChanged.connect(lambda *_: self._sync_default_check())

        title = QLabel("书签")
        f = QFont()
        f.setPixelSize(17)
        f.setWeight(QFont.DemiBold)
        title.setFont(f)
        title.setStyleSheet(f"color: {Palette.TEXT};")

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.tree)
        left.addWidget(self.chk_default)
        left.addWidget(self.hint)

        right = QVBoxLayout()
        right.setSpacing(6)
        for b in (btn_open, btn_rename, btn_up, btn_down, btn_del):
            right.addWidget(b)
        right.addSpacing(10)
        right.addWidget(self.btn_clear)
        right.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(10)
        body.addLayout(left, 1)
        body.addLayout(right)

        btn_close = QPushButton("关闭")
        btn_close.setProperty("flat", True)
        btn_close.clicked.connect(self.reject)
        btn_close.setFixedWidth(88)

        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(btn_close)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)
        root.addWidget(title)
        root.addLayout(body, 1)
        root.addLayout(foot)

        theme.inherit_app_font(self)
        self.refresh()

    # ------------------------------------------------------------ 渲染
    def refresh(self) -> None:
        self.tree.clear()
        items = self.store.items()
        for bm in items:
            # 名称前打星标出"启动时默认打开"的那个，一眼能看出为什么
            # 有时启动不弹启动页
            it = QTreeWidgetItem([
                f"★ {bm.name}" if bm.auto_open else bm.name,
                "本地" if not bm.is_remote else "远端",
                bm.path,
                bm.site_label() or "-",
            ])
            it.setData(0, Qt.UserRole, bm)
            from PySide6.QtGui import QBrush, QColor

            color = Palette.TEXT_FAINT if not bm.is_remote else Palette.PRIMARY
            it.setForeground(1, QBrush(QColor(color)))
            it.setIcon(0, theme.icon("folder" if not bm.is_remote else "server",
                                     color, 15))
            it.setSizeHint(0, QSize(0, 30))
            if bm.auto_open:
                it.setToolTip(0, "启动时默认打开这个书签")
            self.tree.addTopLevelItem(it)
        self.btn_clear.setEnabled(bool(items))
        self._fit_path_column()
        if items:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._sync_default_check()

    def _sync_default_check(self) -> None:
        """把复选框回填成"当前选中项是不是默认"。"""
        bm = self.current()
        self.chk_default.setEnabled(bm is not None)
        self._syncing = True
        try:
            self.chk_default.setChecked(bool(bm is not None and bm.auto_open))
        finally:
            self._syncing = False

    def _on_default_toggled(self, on: bool) -> None:
        if self._syncing:
            return
        bm = self.current()
        if bm is None:
            return
        self.store.set_default(bm if on else None)
        self.refresh()
        self._select(bm)
        if on:
            self.hint.setText(f"启动时将直接打开「{bm.name}」，不再弹启动页")
        else:
            self.hint.setText("双击书签即可跳转到对应目录")

    def _fit_path_column(self) -> None:
        """路径列占满剩余宽度，避免窗口变宽后右侧留一条空白。"""
        avail = self.tree.viewport().width()
        if avail <= 1:                       # 还没完成首次布局
            avail = max(self.width() - 250, self._path_col_min)
        other = (self.tree.columnWidth(0) + self.tree.columnWidth(1)
                 + self.tree.columnWidth(3) + 2)
        self.tree.setColumnWidth(2, max(self._path_col_min, avail - other))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_path_column()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_path_column()

    def current(self) -> Bookmark | None:
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    # ------------------------------------------------------------ 操作
    def _on_open(self) -> None:
        bm = self.current()
        if bm is None:
            return
        self.open_requested.emit(bm)
        self.accept()

    def _on_rename(self) -> None:
        bm = self.current()
        if bm is None:
            return
        name, ok = QInputDialog.getText(self, "重命名书签", "名称：", text=bm.name)
        if ok and name.strip():
            self.store.rename(bm, name.strip())
            self.refresh()

    def _on_move_up(self) -> None:
        bm = self.current()
        if bm is None:
            return
        self.store.move_up(bm)
        self.refresh()
        self._select(bm)

    def _on_move_down(self) -> None:
        bm = self.current()
        if bm is None:
            return
        self.store.move_down(bm)
        self.refresh()
        self._select(bm)

    def _select(self, bm: Bookmark) -> None:
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.UserRole) is bm:
                self.tree.setCurrentItem(it)
                return

    def _on_delete(self) -> None:
        bm = self.current()
        if bm is None:
            return
        ret = QMessageBox.question(
            self, "删除书签", f"确定删除书签「{bm.name}」吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self.store.remove(bm)
            self.refresh()

    def _on_clear(self) -> None:
        ret = QMessageBox.question(
            self, "清空书签", "确定删除全部书签吗？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self.store.clear()
            self.refresh()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._on_open()
            return
        super().keyPressEvent(event)
