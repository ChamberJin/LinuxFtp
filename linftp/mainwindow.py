"""主窗口：WinSCP 风格双栏布局。

布局结构：
    工具栏
    ├──────────── 左栏（本地） ───────────┬──── 右栏（远端） ────┐
    │ 路径栏                              │ 路径栏                │
    │ 文件列表                            │ 文件列表              │
    └─────────────────────────────────────┴──────────────────────┘
    传输队列面板（可折叠）
    日志面板（可折叠）
"""
from __future__ import annotations

import json
import logging
import os
import subprocess

from PySide6.QtCore import (
    QItemSelectionModel,
    QRect,
    QSize,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .backend import RemoteFile, SessionConfig, SessionError, create_session
from .bookmark_dialog import BookmarkDialog
from .bookmarks import Bookmark, BookmarkStore, _obfuscate
from .dialogs import ConnectDialog
from .launchpad import LaunchPad, has_content
from .localfs import LocalFs
from .models import (
    COL_MTIME,
    COL_NAME,
    COL_PERM,
    COL_SIZE,
    COL_TYPE,
    FileListModel,
    human_size,
)
from .pathbar import PathEdit
from .preview import PreviewDialog
from .queue import TaskState, TransferQueue
from .sites import SiteStore
from .theme import Palette
from .tray import (
    CLOSE_ASK,
    CLOSE_CANCEL,
    CLOSE_TRAY,
    TrayIcon,
    ask_close_action,
    normalize_close_pref,
    resolve_close_action,
)

log = logging.getLogger(__name__)

# 记录工具栏动作对应的图标名，切换紧凑模式时要按新尺寸重画图标
_ACTION_ICON_NAME: dict = {}


def _pref_path() -> str:
    from .sites import CONFIG_DIR

    return os.path.join(CONFIG_DIR, "ui.json")


def load_prefs() -> dict:
    """读出整份界面偏好。文件坏了、缺了都当作空偏好，不让程序起不来。"""
    try:
        with open(_pref_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_prefs(data: dict) -> None:
    """整份写回。先写临时文件再 replace，避免写到一半崩掉留下半截 JSON。"""
    path = _pref_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("保存界面偏好失败：%s", e)


def load_compact_pref() -> bool:
    """读取上次的紧凑模式选择。读不到就默认标准模式。"""
    return bool(load_prefs().get("compact", False))


def save_compact_pref(compact: bool) -> None:
    data = load_prefs()
    data["compact"] = bool(compact)
    _save_prefs(data)


def load_close_pref() -> str:
    """读上次选的关闭动作：ask（每次问）/ tray（收进托盘）/ quit（退出）。

    没存过、存坏了都当 ask —— 问一句总比悄悄退出、或者悄悄消失强。
    """
    return normalize_close_pref(load_prefs().get("close_action"))


def save_close_pref(action: str) -> None:
    data = load_prefs()
    data["close_action"] = normalize_close_pref(action)
    _save_prefs(data)


def load_window_geometry() -> dict | None:
    """读取上次退出时的窗口大小与位置。字段不全或类型不对就当没存过。"""
    raw = load_prefs().get("window")
    if not isinstance(raw, dict):
        return None
    try:
        geo = {
            "x": int(raw["x"]),
            "y": int(raw["y"]),
            "w": int(raw["w"]),
            "h": int(raw["h"]),
            "maximized": bool(raw.get("maximized", False)),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if geo["w"] < 320 or geo["h"] < 240:
        # 存成这种尺寸基本是异常状态（最小化时取到 0 之类），宁可不用
        return None
    return geo


def save_window_geometry(win) -> None:
    """记下窗口大小、位置和最大化状态。

    最大化时 geometry() 是铺满屏幕的那份，拿它还原会导致取消最大化后
    窗口还是满屏，所以要取 normalGeometry()（最大化之前的那份）。
    """
    try:
        maximized = win.isMaximized()
        geo = win.normalGeometry() if maximized else win.geometry()
        data = load_prefs()
        data["window"] = {
            "x": geo.x(), "y": geo.y(),
            "w": geo.width(), "h": geo.height(),
            "maximized": bool(maximized),
        }
        _save_prefs(data)
    except Exception as e:  # noqa: BLE001
        log.warning("保存窗口位置失败：%s", e)


class Card(QFrame):
    """卡片式容器：圆角白底 + 柔和描边，用来兜住每一块功能区。"""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")

        self.head = QHBoxLayout()
        self.head.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(title)
        self.head.addWidget(self.title_label)
        self.head.addStretch(1)

        self.body = QVBoxLayout(self)
        # 标题栏必须挂进布局，否则 QLabel 不会显示（只在布局里的控件才参与排版）
        self.body.addLayout(self.head)

        self.apply_metrics()

    def apply_metrics(self) -> None:
        """按当前尺寸规格重排内边距、圆角与标题字号。"""
        m = theme.metrics
        self.setStyleSheet(
            f"QFrame#card {{ background: {Palette.SURFACE};"
            f" border: 1px solid {Palette.BORDER};"
            f" border-radius: {m.card_radius}px; }}")
        self.body.setContentsMargins(*m.card_margin)
        self.body.setSpacing(m.card_spacing)
        self.head.setSpacing(m.card_spacing)
        self.title_label.setStyleSheet(
            f"color: {Palette.TEXT_MUTED}; font-size: {m.card_title_size}px;"
            f" font-weight: 600;")

    def add_body(self, widget: QWidget, stretch: int = 0) -> None:
        self.body.addWidget(widget, stretch)


class FilePane(QWidget):
    """单侧面板：路径栏 + 过滤框 + 文件列表。本地与远端共用。"""

    activated = Signal(object)      # 双击目录
    request_download = Signal(object)
    request_upload = Signal(object)
    context_requested = Signal(object, object)   # (pane, pos)
    copy_path_requested = Signal(object)         # (pane)
    navigate_requested = Signal(str)             # 路径栏点了某一层

    def __init__(self, title: str, is_remote: bool, parent=None):
        super().__init__(parent)
        self.title = title
        self.is_remote = is_remote
        self.accent = Palette.PRIMARY if is_remote else Palette.ACCENT

        # 当前目录。路径栏是只读的展示位，目录状态记在面板上，
        # 逻辑不必回头去读 UI 文本（路径栏哪天支持输入跳转也不会串）。
        self.current_dir = ""

        # ------ 顶部：侧别标记 + 导航按钮 + 路径
        self.side_label = QLabel(title)

        self.btn_up = self._icon_button("up", "上级目录")
        self.btn_home = self._icon_button("home", "主目录")

        self.path_edit = PathEdit()
        self.path_edit.setMinimumWidth(120)
        self.path_edit.addAction(
            theme.icon("folder", Palette.TEXT_FAINT, 15), QLineEdit.LeadingPosition)
        # 路径栏点某一层 → 交给主窗口按侧别跳转
        self.path_edit.segment_clicked.connect(self.navigate_requested)

        self.top_layout = QHBoxLayout()
        self.top_layout.setContentsMargins(0, 0, 0, 0)
        self.top_layout.addWidget(self.side_label)
        self.top_layout.addWidget(self.btn_up)
        self.top_layout.addWidget(self.btn_home)
        self.top_layout.addWidget(self.path_edit, 1)

        # ------ 列表
        self.model = FileListModel(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setAlternatingRowColors(True)
        self.view.setSortingEnabled(False)
        self.view.setShowGrid(False)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setStretchLastSection(False)
        self.view.horizontalHeader().setHighlightSections(False)
        # 排序由面板自己接管（不用 setSortingEnabled(True)）：Qt 自带那套
        # 在排序时会把选择清掉，这里要保住用户选中的行。
        hdr = self.view.horizontalHeader()
        hdr.setSectionsClickable(True)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(self.model.sort_column(), self.model.sort_order())
        hdr.setToolTip("点击表头排序，再点一次切换升降序（目录始终在最前）")
        hdr.sectionClicked.connect(self._on_header_clicked)
        # 名称列固定宽度：用 Interactive 而不是 Stretch，
        # 否则列宽会随最长文件名自适应，切换目录时宽度跳动、观感割裂。
        # 名称列只吃掉"面板富余空间"，不跟随文件名内容变化（见 _fit_columns）。
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)

        self.count_label = QLabel("0 项")

        # ------ 底部：过滤
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("过滤，支持通配符如 *.log")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.addAction(
            theme.icon("search", Palette.TEXT_FAINT, 14), QLineEdit.LeadingPosition)

        self.bottom_layout = QHBoxLayout()
        self.bottom_layout.setContentsMargins(0, 0, 0, 0)
        self.bottom_layout.addWidget(self.filter_edit, 1)
        self.bottom_layout.addWidget(self.count_label)

        self.root_layout = QVBoxLayout(self)
        self.root_layout.addLayout(self.top_layout)
        self.root_layout.addWidget(self.view, 1)
        self.root_layout.addLayout(self.bottom_layout)

        self.apply_metrics()

        # 信号
        self.view.doubleClicked.connect(self._on_double_click)
        self.view.customContextMenuRequested.connect(
            lambda pos: self.context_requested.emit(self, pos))
        self.filter_edit.textChanged.connect(self._apply_filter)

        # 复制路径：右键菜单和 Ctrl+Shift+C 共用同一个 QAction，
        # 文案（带选中数量）在菜单弹出前刷新；快捷键只在列表有焦点时响应
        self.act_copy_path = QAction("复制路径", self)
        self.act_copy_path.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self.act_copy_path.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.act_copy_path.triggered.connect(
            lambda: self.copy_path_requested.emit(self))
        self.addAction(self.act_copy_path)

    def _icon_button(self, icon_name: str, tip: str) -> QToolButton:
        m = theme.metrics
        btn = QToolButton()
        btn.setIcon(theme.icon(icon_name, Palette.TEXT_MUTED, m.toolbar_icon))
        btn.setToolTip(tip)
        btn.setFixedSize(m.nav_btn, m.nav_btn)
        btn.setIconSize(QSize(m.toolbar_icon, m.toolbar_icon))
        return btn

    def apply_metrics(self) -> None:
        """按当前尺寸规格重排本面板（切换紧凑模式时调用）。"""
        m = theme.metrics
        self.root_layout.setContentsMargins(*m.pane_margins)
        self.root_layout.setSpacing(m.pane_spacing)

        self.side_label.setFixedHeight(m.side_label_h)
        self.side_label.setStyleSheet(
            f"QLabel {{ background: {self.accent}1A; color: {self.accent};"
            f" border-radius: 5px; padding: 0 8px;"
            f" font-size: 11px; font-weight: 600; }}")

        self.top_layout.setSpacing(max(4, m.pane_spacing - 2))
        self.bottom_layout.setSpacing(m.pane_spacing)

        for btn in (self.btn_up, self.btn_home):
            btn.setFixedSize(m.nav_btn, m.nav_btn)
            btn.setIconSize(QSize(m.toolbar_icon, m.toolbar_icon))
            btn.setIcon(theme.icon(
                "up" if btn is self.btn_up else "home",
                Palette.TEXT_MUTED, m.toolbar_icon))

        self.path_edit.setFixedHeight(m.path_h)
        self.filter_edit.setFixedHeight(m.filter_h)

        self.view.verticalHeader().setDefaultSectionSize(m.pane_row_h)
        # 列宽规格可能变了，重新铺一遍（名称列随后由 _fit_columns 覆盖）
        self.OTHER_COLS_WIDTH = {
            COL_SIZE: m.col_size, COL_TYPE: m.col_type,
            COL_MTIME: m.col_mtime, COL_PERM: m.col_perm,
        }
        self.NAME_MIN_WIDTH = m.name_min_w
        for col, w in self.OTHER_COLS_WIDTH.items():
            self.view.setColumnWidth(col, w)
        self.count_label.setStyleSheet(
            f"color: {Palette.TEXT_FAINT}; font-size: 12px;")
        self._fit_columns()

    # -------------------------------------------------------- 数据
    def set_current_dir(self, path: str) -> None:
        """记录当前目录并同步到路径栏。"""
        self.current_dir = path or ""
        self.path_edit.set_path(self.current_dir)

    def paths_for_copy(self) -> list:
        """待复制的路径：选中行 > 当前目录。"""
        picked = [e.path for e in self.selected_entries()]
        if picked:
            return picked
        return [self.current_dir] if self.current_dir else []

    def set_entries(self, entries) -> None:
        # 不过滤清零：用户填的过滤词在切目录后继续生效，和输入框保持一致
        self.model.set_entries(entries)
        self.count_label.setText(f"{self.model.rowCount()} 项")
        self._fit_columns()

    # -------------------------------------------------------- 排序
    def _on_header_clicked(self, column: int) -> None:
        """同列再点反转，换列回到升序。"""
        if column == self.model.sort_column():
            order = (Qt.DescendingOrder
                     if self.model.sort_order() == Qt.AscendingOrder
                     else Qt.AscendingOrder)
        else:
            order = Qt.AscendingOrder
        self.apply_sort(column, order)

    def apply_sort(self, column: int, order) -> None:
        """改排序，并保住原来的选中行与当前行。"""
        keep_sel = [e.path for e in self.selected_entries()]
        keep_cur = self.current_entry()
        self.model.sort(column, order)
        self.view.horizontalHeader().setSortIndicator(column, order)
        self._restore_selection(keep_sel, keep_cur.path if keep_cur else "")

    def _restore_selection(self, paths, current_path: str) -> None:
        rows_by_path = {e.path: i for i, e in enumerate(self.model.entries())}
        sm = self.view.selectionModel()
        sm.clearSelection()
        for path in paths:
            row = rows_by_path.get(path)
            if row is None:          # 该行被过滤掉了，跳过
                continue
            sm.select(self.model.index(row, 0),
                      QItemSelectionModel.Select | QItemSelectionModel.Rows)
        row = rows_by_path.get(current_path)
        if row is not None:
            self.view.setCurrentIndex(self.model.index(row, 0))

    def _fit_columns(self) -> None:
        """名称列宽度只跟面板宽度走，跟文件名内容无关。

        这样切换目录时列宽恒定，不会因为文件名变长变短而跳动。
        """
        avail = self.view.viewport().width()
        others = sum(self.OTHER_COLS_WIDTH.values())
        name_w = max(self.NAME_MIN_WIDTH, avail - others - 2)
        self.view.setColumnWidth(COL_NAME, name_w)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_columns()

    def _apply_filter(self, text: str) -> None:
        self.model.set_filter(text)
        self.count_label.setText(f"{self.model.rowCount()} 项")

    def selected_entries(self):
        rows = {i.row() for i in self.view.selectionModel().selectedRows()}
        out = [self.model.entry_at(r) for r in sorted(rows)]
        return [e for e in out if e is not None]

    def current_entry(self):
        idx = self.view.currentIndex()
        return self.model.entry_at(idx.row()) if idx.isValid() else None

    def _on_double_click(self, index) -> None:
        entry = self.model.entry_at(index.row())
        if entry is not None:
            self.activated.emit(entry)


class MainWindow(QMainWindow):
    # 传输队列在工作线程里回调，先转成信号派回 GUI 线程再动界面
    task_updated = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("LinFTP —— Linux 上的 FTP 客户端")
        self.resize(1180, 760)

        self.store = SiteStore()
        self.bookmarks = BookmarkStore()
        # 紧凑模式要在建 UI 之前定好：FilePane/Card 构造时就会读 theme.metrics
        self.compact = load_compact_pref()
        theme.set_compact(self.compact)
        self.cfg: SessionConfig | None = None
        self.session = None
        self.local_fs = LocalFs(os.path.expanduser("~"))
        self.local_path = self.local_fs.root
        self.remote_path = "/"

        self.queue = TransferQueue(lambda: self.cfg, max_workers=2)
        # 直接 connect 回调会在工作线程里刷队列表格、弹托盘气泡 ——
        # Qt 的界面对象只能在自己的线程里碰，绕一层信号让它排队回到主线程
        self.task_updated.connect(self._on_task_update)
        self.queue.add_listener(self.task_updated.emit)

        self._build_toolbar()
        self._build_body()
        self._build_queue_panel()
        self._build_log_panel()
        self._build_statusbar()

        # True 表示"这次真的要退出"：托盘菜单点退出时置位，
        # closeEvent 见到它就不再问要不要收进托盘
        self._quitting = False
        self.tray: TrayIcon | None = None
        self._setup_tray()

        # 定时保活，防止服务端空闲断连
        self._keepalive = QTimer(self)
        self._keepalive.setInterval(60_000)
        self._keepalive.timeout.connect(self._do_keepalive)
        self._keepalive.start()

        self.refresh_local()

        # 初始比例：文件区占主导，队列次要，日志折叠
        QTimer.singleShot(0, lambda: self._apply_initial_layout())

    def _apply_initial_layout(self) -> None:
        self._set_log_visible(False)
        self._auto_size_queue()

    # ------------------------------------------------------------ 构建
    def _build_toolbar(self) -> None:
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.setIconSize(QSize(theme.metrics.toolbar_icon, theme.metrics.toolbar_icon))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        self.addToolBarBreak()

        self.act_connect = self._action(
            tb, "连接…", "connect", self.on_connect, "Ctrl+N",
            tip="新建一个连接")
        self.act_disconnect = self._action(
            tb, "断开", "disconnect", self.on_disconnect, None, tip="断开当前连接")
        tb.addSeparator()
        self.act_upload = self._action(
            tb, "上传", "arrow_right", lambda: self.on_upload(), "F5",
            tip="把本地选中的项目上传到远端当前目录（F5）")
        self.act_download = self._action(
            tb, "下载", "arrow_left", lambda: self.on_download(), "F6",
            tip="把远端选中的项目下载到本地当前目录（F6）")
        tb.addSeparator()
        self.act_newdir = self._action(
            tb, "新建", "newdir", self.on_newdir, None, tip="在当前面板新建目录")
        self.act_rename = self._action(
            tb, "重命名", "rename", self.on_rename, None, tip="重命名选中项")
        self.act_delete = self._action(
            tb, "删除", "delete", self.on_delete, "Del", tip="删除选中项")
        self.act_refresh = self._action(
            tb, "刷新", "refresh", self.refresh_all, "Ctrl+R", tip="刷新两侧列表")
        tb.addSeparator()
        self.act_sites = self._action(
            tb, "站点", "sites", self.on_sites, None, tip="从已保存的站点连接")
        tb.addSeparator()

        self._build_bookmark_menu(tb)

        spacer = QWidget()
        sp = spacer.sizePolicy()
        sp.setHorizontalPolicy(sp.Policy.Expanding)
        sp.setVerticalPolicy(sp.Policy.Preferred)
        spacer.setSizePolicy(sp)
        tb.addWidget(spacer)

        self.enc_label = QLabel("编码")
        # 不写死字号：工具栏里的文字一律跟着全局字体走，
        # 否则切到紧凑模式时只有它一个显得突兀
        self.enc_label.setStyleSheet(f"color: {Palette.TEXT_MUTED};")
        tb.addWidget(self.enc_label)
        self.encoding_box = QComboBox()
        self.encoding_box.addItems(["自动", "utf-8", "gbk", "gb18030", "big5", "latin-1"])
        self.encoding_box.setToolTip(
            "服务端文件名编码。默认自动识别；出现中文乱码时可手动指定后刷新")
        self.encoding_box.setFixedWidth(94)
        self.encoding_box.setEnabled(False)
        self.encoding_box.activated.connect(self._on_encoding_changed)
        tb.addWidget(self.encoding_box)

        self.act_keepalive = QCheckBox("保持连接")
        self.act_keepalive.setChecked(True)
        self.act_keepalive.setToolTip("每 60 秒发一次 NOOP，防止服务端空闲断连")
        tb.addWidget(self.act_keepalive)

        tb.addSeparator()
        self.btn_compact = QToolButton()
        self.btn_compact.setText("紧凑")
        self.btn_compact.setCheckable(True)
        self.btn_compact.setChecked(self.compact)
        self.btn_compact.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_compact.setToolTip("紧凑模式：压缩行高与留白，一屏多看几行（Ctrl+Shift+M）")
        self.btn_compact.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self.btn_compact.toggled.connect(self.set_compact)
        tb.addWidget(self.btn_compact)

        self._sync_actions()
        self._sync_compact_button()

    def _action(self, tb: QToolBar, text: str, icon_name: str, slot,
                shortcut: str | None, tip: str = "") -> QAction:
        size = theme.metrics.toolbar_icon
        act = QAction(theme.icon(icon_name, Palette.TEXT_MUTED, size), text, self)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        if tip:
            act.setToolTip(tip)
            act.setStatusTip(tip)
        act.triggered.connect(slot)
        _ACTION_ICON_NAME[act] = icon_name
        tb.addAction(act)
        return act

    def _sync_actions(self) -> None:
        """根据是否已连接，统一开关工具栏动作。"""
        connected = self.session is not None
        self.act_disconnect.setEnabled(connected)
        self.act_upload.setEnabled(connected)
        self.act_download.setEnabled(connected)
        self.act_connect.setText("重新连接…" if connected else "连接…")

    # ------------------------------------------------------------ 紧凑模式
    def _sync_compact_button(self) -> None:
        """按钮外观跟状态走：开=实心高亮，关=普通图标。"""
        color = Palette.PRIMARY if self.compact else Palette.TEXT_MUTED
        self.btn_compact.setIcon(theme.icon("compact", color, theme.metrics.toolbar_icon))
        self.btn_compact.setIconSize(
            QSize(theme.metrics.toolbar_icon, theme.metrics.toolbar_icon))
        self.btn_compact.setText("紧凑" if self.compact else "标准")
        self.btn_compact.setToolTip(
            "当前为紧凑模式，点击切回标准（Ctrl+Shift+M）" if self.compact
            else "紧凑模式：压缩行高与留白，一屏多看几行（Ctrl+Shift+M）")

    def set_compact(self, on: bool) -> None:
        """切换紧凑 / 标准布局。

        只改尺寸与留白，不动数据与逻辑；切换后尽量保持用户手动调整过的
        分栏比例，不把窗口布局整个重置。
        """
        on = bool(on)
        if on == self.compact and getattr(self, "_compact_ready", False):
            return
        self.compact = on

        # 记住切换前的两处分栏比例，重排后按同样比例还原
        body_ratio = self._ratio(self.body_splitter.sizes())
        pane_ratio = self._ratio(self._pane_splitter.sizes())

        theme.set_compact(on)  # 内部已连带刷新全局 QSS

        # 逐个重排依赖尺寸的部件
        for card in (self._queue_panel, self._log_panel):
            card.apply_metrics()
        for pane in (self.local_pane, self.remote_pane):
            pane.apply_metrics()
        self._apply_toolbar_metrics()
        self.apply_statusbar_metrics()
        self._body_layout.setContentsMargins(*theme.metrics.body_margins)
        self._pane_splitter.setStyleSheet(
            f"QSplitter::handle:horizontal {{ width: {theme.metrics.splitter_handle}px; }}")
        self.setIconSize(QSize(theme.metrics.toolbar_icon,
                               theme.metrics.toolbar_icon))
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        # 分栏比例还原（尺寸变化后总像素不同，用比例算）
        self._restore_ratio(self._pane_splitter, pane_ratio)
        self._restore_ratio(self.body_splitter, body_ratio)
        self._set_log_visible(self.log_view.isVisible())
        self._auto_size_queue()

        self._compact_ready = True
        self._sync_compact_button()
        save_compact_pref(on)
        self.add_log("已切换到紧凑模式" if on else "已切换到标准模式")

    @staticmethod
    def _ratio(sizes) -> list:
        total = sum(sizes) or 1
        return [s / total for s in sizes]

    @staticmethod
    def _restore_ratio(splitter, ratio) -> None:
        if not ratio or len(ratio) != len(splitter.sizes()):
            return
        total = sum(splitter.sizes()) or 1
        splitter.setSizes([max(1, int(total * r)) for r in ratio])

    def _apply_toolbar_metrics(self) -> None:
        """工具栏的内边距与间距写在 QSS 里，这里只需刷新图标尺寸。"""
        m = theme.metrics
        size = QSize(m.toolbar_icon, m.toolbar_icon)
        # QToolBar 一旦被显式 setIconSize 过就不再看主窗口的 iconSize 了，
        # 所以工具栏本体要单独同步，否则只有重启才跟得上
        for tb in self.findChildren(QToolBar):
            tb.setIconSize(size)
        self.setIconSize(size)
        for act in self.findChildren(QAction):
            if act.icon().isNull():
                continue
            name = _ACTION_ICON_NAME.get(act)
            if name:
                act.setIcon(theme.icon(name, Palette.TEXT_MUTED, m.toolbar_icon))
        # 书签按钮不是 QAction，得单独跟一次图标尺寸
        btn_bm = getattr(self, "btn_bookmark", None)
        if btn_bm is not None:
            btn_bm.setIcon(theme.icon("bookmark", Palette.TEXT_MUTED, m.toolbar_icon))
            btn_bm.setIconSize(QSize(m.toolbar_icon, m.toolbar_icon))

    def _build_bookmark_menu(self, tb: QToolBar) -> None:
        """书签按本地/远端分组，点击直接跳转。"""
        _s = theme.metrics.toolbar_icon
        self.btn_bookmark = QToolButton()
        self.btn_bookmark.setText("书签")
        self.btn_bookmark.setIcon(theme.icon("bookmark", Palette.TEXT_MUTED, _s))
        self.btn_bookmark.setIconSize(QSize(_s, _s))
        self.btn_bookmark.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_bookmark.setPopupMode(QToolButton.InstantPopup)
        self.btn_bookmark.setToolTip("打开书签直接跳转到对应目录")
        self.bookmark_menu = QMenu(self)
        self.btn_bookmark.setMenu(self.bookmark_menu)

        self.act_add_bookmark = QAction("添加当前目录为书签…", self)
        self.act_add_bookmark.setShortcut(QKeySequence("Ctrl+D"))
        self.act_add_bookmark.triggered.connect(lambda: self.add_bookmark(None))

        self.act_manage_bookmark = QAction("管理书签…", self)
        self.act_manage_bookmark.setShortcut(QKeySequence("Ctrl+B"))
        self.act_manage_bookmark.triggered.connect(self.on_bookmarks)

        tb.addWidget(self.btn_bookmark)
        self.rebuild_bookmark_menu()

    def _build_body(self) -> None:
        self.local_pane = FilePane("本地", is_remote=False)
        self.remote_pane = FilePane("远端", is_remote=True)
        self.remote_pane.setEnabled(False)

        for pane in (self.local_pane, self.remote_pane):
            pane.activated.connect(lambda e, p=pane: self._on_pane_activated(p, e))
            pane.context_requested.connect(self._on_context_menu)
            pane.copy_path_requested.connect(self.copy_path)
            pane.navigate_requested.connect(
                lambda path, p=pane: self.navigate_to(p, path))

        self.local_pane.btn_up.clicked.connect(lambda: self._go_up(self.local_pane))
        self.local_pane.btn_home.clicked.connect(
            lambda: self._go_home(self.local_pane))
        self.remote_pane.btn_up.clicked.connect(lambda: self._go_up(self.remote_pane))
        self.remote_pane.btn_home.clicked.connect(
            lambda: self._go_home(self.remote_pane))

        # 两侧各包一层卡片，视觉上明确分区
        left_card = Card()
        left_card.body.addWidget(self.local_pane, 1)
        right_card = Card()
        right_card.body.addWidget(self.remote_pane, 1)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(left_card)
        split.addWidget(right_card)
        split.setSizes([560, 560])
        # 卡片自身有内边距，分栏再给一圈会让两侧贴得太挤，这里收窄
        split.setStyleSheet(
            f"QSplitter::handle:horizontal {{ width: {theme.metrics.splitter_handle}px; }}")

        self.body_splitter = QSplitter(Qt.Vertical)
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.addWidget(split)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(*theme.metrics.body_margins)
        lay.setSpacing(0)
        lay.addWidget(self.body_splitter)
        self.setCentralWidget(central)
        self._body_layout = lay
        self._pane_splitter = split

    def _build_queue_panel(self) -> None:
        self.queue_tree = QTreeWidget()
        self.queue_tree.setHeaderLabels(["任务", "方向", "进度", "速度", "状态"])
        self.queue_tree.setRootIsDecorated(False)
        self.queue_tree.setAlternatingRowColors(True)
        self.queue_tree.setFrameShape(QFrame.NoFrame)
        self.queue_tree.header().setStretchLastSection(False)
        self.queue_tree.header().setHighlightSections(False)
        self.queue_tree.setColumnWidth(0, 260)
        self.queue_tree.setColumnWidth(1, 60)
        self.queue_tree.setColumnWidth(2, 180)
        self.queue_tree.setColumnWidth(3, 100)

        self.queue_summary = QLabel("空闲")
        self.queue_summary.setStyleSheet(
            f"color: {Palette.TEXT_MUTED}; font-size: 12px;")

        btn_clear = QPushButton("清除已完成")
        btn_clear.setProperty("flat", True)
        btn_clear.clicked.connect(self._clear_queue)
        btn_cancel = QPushButton("全部取消")
        btn_cancel.setProperty("flat", True)
        btn_cancel.clicked.connect(self.queue.cancel_all)

        card = Card("传输队列")
        card.head.addWidget(self.queue_summary)
        card.head.addWidget(btn_clear)
        card.head.addWidget(btn_cancel)
        card.body.addWidget(self.queue_tree, 1)
        self.body_splitter.addWidget(card)
        self._queue_panel = card
        # 空队列时不占大片空白，有任务再加高（见 _auto_size_queue）
        self.queue_tree.setMinimumHeight(0)

    def _build_log_panel(self) -> None:
        self.log_view = QTreeWidget()
        self.log_view.setHeaderLabels(["时间", "级别", "消息"])
        self.log_view.setRootIsDecorated(False)
        self.log_view.setFrameShape(QFrame.NoFrame)
        self.log_view.header().setHighlightSections(False)
        self.log_view.setColumnWidth(0, 80)
        self.log_view.setColumnWidth(1, 52)

        self.btn_toggle_log = QPushButton("收起")
        self.btn_toggle_log.setProperty("flat", True)
        self.btn_toggle_log.setFixedWidth(56)
        self.btn_toggle_log.clicked.connect(self._toggle_log)

        btn_clear = QPushButton("清空")
        btn_clear.setProperty("flat", True)
        btn_clear.setFixedWidth(56)
        btn_clear.clicked.connect(self.log_view.clear)

        card = Card("日志")
        card.head.addWidget(btn_clear)
        card.head.addWidget(self.btn_toggle_log)
        card.body.addWidget(self.log_view, 1)
        self.body_splitter.addWidget(card)
        self._log_panel = card

    def _auto_size_queue(self) -> None:
        """队列面板高度跟随任务条数：空时只留一两行，满了再撑开。

        否则空闲时它会白占两三百像素，把文件区挤扁。
        """
        m = theme.metrics
        n = self.queue_tree.topLevelItemCount()
        row_h = self.queue_tree.sizeHintForRow(0) if n else m.pane_row_h
        row_h = row_h if row_h > 0 else m.pane_row_h
        if n == 0:
            want = m.queue_empty_h      # 只剩表头 + 一点呼吸空间
        else:
            want = m.queue_empty_h - 18 + min(n, 7) * (row_h + 2)
        self._queue_panel.setMaximumHeight(16777215)
        sizes = self.body_splitter.sizes()
        if len(sizes) == 3:
            total = sum(sizes) or 1
            log_h = sizes[2]
            self.body_splitter.setSizes(
                [max(200, total - want - log_h), want, log_h])

    def _set_log_visible(self, visible: bool) -> None:
        """折叠日志时把整张卡片压成一行标题，而不是留一条空卡片。

        只隐藏 log_view 的话卡片仍然占着高度，会变成一块空白区域。
        """
        collapsed_h = theme.metrics.log_collapsed_h
        self.log_view.setVisible(visible)
        self._log_panel.setMaximumHeight(16777215 if visible else collapsed_h)
        self._log_panel.setMinimumHeight(0 if visible else collapsed_h)
        self.btn_toggle_log.setText("收起" if visible else "展开")
        total = sum(self.body_splitter.sizes()) or 1
        if visible:
            self.body_splitter.setSizes([int(total * 0.74), int(total * 0.16), 150])
        else:
            self.body_splitter.setSizes([int(total * 0.78), int(total * 0.22),
                                         collapsed_h])

    def _toggle_log(self) -> None:
        self._set_log_visible(not self.log_view.isVisible())

    def _build_statusbar(self) -> None:
        self.conn_dot = QLabel("●")
        self.status_label = QLabel("未连接")
        self.statusBar().addWidget(self.conn_dot)
        self.statusBar().addWidget(self.status_label, 1)
        self.apply_statusbar_metrics()

    def apply_statusbar_metrics(self) -> None:
        """状态栏高度与圆点字号都跟当前规格走。

        高度本身写在 QSS 里（QStatusBar 设了 min/max-height），
        这里只负责两件事：把状态栏钉到目标高度、重设圆点字号。
        圆点是 QLabel，字号得单独设，QSS 管不到它的 font-size。
        """
        m = theme.metrics
        bar = self.statusBar()
        bar.setFixedHeight(m.statusbar_h)
        if getattr(self, "conn_dot", None) is not None:
            dot = m.statusbar_dot_size
            color = Palette.ACCENT if self.session is not None else Palette.TEXT_FAINT
            self.conn_dot.setStyleSheet(f"color: {color}; font-size: {dot}px;")
            self.status_label.setStyleSheet(
                f"color: {Palette.TEXT_MUTED};")

    def _set_status(self, text: str, connected: bool) -> None:
        self.status_label.setText(text)
        color = Palette.ACCENT if connected else Palette.TEXT_FAINT
        dot = theme.metrics.statusbar_dot_size
        self.conn_dot.setStyleSheet(f"color: {color}; font-size: {dot}px;")
        self.status_label.setStyleSheet(
            f"color: {Palette.TEXT if connected else Palette.TEXT_MUTED};")
        self._sync_tray()

    # ------------------------------------------------------------ 日志
    def add_log(self, message: str, level: str = "INFO") -> None:
        import datetime as dt

        item = QTreeWidgetItem([
            dt.datetime.now().strftime("%H:%M:%S"),
            level,
            message,
        ])
        color = {"ERROR": Palette.DANGER, "WARN": Palette.WARN}.get(level)
        if color:
            from PySide6.QtGui import QBrush, QColor

            for c in range(3):
                item.setForeground(c, QBrush(QColor(color)))
        self.log_view.addTopLevelItem(item)
        self.log_view.scrollToItem(item)

    def notify_error(self, title: str, msg: str) -> None:
        self.add_log(f"{title}: {msg}", "ERROR")
        QMessageBox.warning(self, title, msg)

    # ------------------------------------------------------------ 连接
    def on_connect(self) -> None:
        dlg = ConnectDialog(self.cfg, self)
        if dlg.exec() != ConnectDialog.Accepted:
            return
        cfg = dlg.config()
        self.connect_with(cfg, save=dlg.wants_save())

    def connect_with(self, cfg: SessionConfig, save: bool = False,
                     notify: bool = True) -> bool:
        """建立连接。返回是否连上了。

        notify=False 时不弹错误框、只写日志 —— 启动时自动打开默认书签
        走这条路径，连不上就直接退回启动页，别拿一个弹框把人挡在外面。
        """
        self.setCursor(Qt.BusyCursor)
        try:
            if self.session:
                try:
                    self.session.close()
                except Exception:
                    pass
            self.session = create_session(cfg)
            self.session.connect()
            self.cfg = cfg
            if save:
                self.store.upsert(cfg)
            self.remote_pane.setEnabled(True)
            self._set_status(
                f"已连接 {cfg.protocol.upper()} {cfg.username}@{cfg.host}:{cfg.default_port()}",
                True)
            self.store.touch_recent(cfg)
            self.add_log(
                f"已连接 {cfg.protocol.upper()} {cfg.username}@{cfg.host}:{cfg.default_port()}")
            self.go_remote(self.session.cwd() or "/")
            self.rebuild_bookmark_menu()
            self._sync_encoding_box()
            if getattr(self.session, "encoding_autodetected", False):
                self.add_log(
                    f"检测到服务端文件名编码为 {self.session.effective_encoding}（已自动适配）")
            return True
        except SessionError as e:
            self.session = None
            self.rebuild_bookmark_menu()
            if notify:
                self.notify_error("连接失败", str(e))
            else:
                self.add_log(f"连接失败：{e}", "ERROR")
            return False
        except Exception as e:  # noqa: BLE001
            self.session = None
            self.rebuild_bookmark_menu()
            if notify:
                self.notify_error("连接失败", f"{type(e).__name__}: {e}")
            else:
                self.add_log(f"连接失败：{type(e).__name__}: {e}", "ERROR")
            return False
        finally:
            self._sync_actions()
            self.unsetCursor()

    def on_disconnect(self) -> None:
        if self.session:
            try:
                self.session.close()
            except Exception:
                pass
        self.session = None
        self.cfg = None
        self.remote_pane.setEnabled(False)
        self.remote_pane.set_entries([])
        self.remote_pane.path_edit.clear()
        self._sync_actions()
        self._set_status("未连接", False)
        self.add_log("已断开")
        self.rebuild_bookmark_menu()
        self._sync_encoding_box()

    def _do_keepalive(self) -> None:
        if self.session and self.act_keepalive.isChecked():
            self.session.noop()

    # ------------------------------------------------------------ 编码
    def _sync_encoding_box(self) -> None:
        """把会话实际使用的编码回显到工具栏。"""
        if not self.session:
            self.encoding_box.setEnabled(False)
            self.encoding_box.setCurrentIndex(0)
            return
        if getattr(self.session, "protocol", "ftp") != "ftp":
            # SFTP 的文件名固定 UTF-8，切换编码没有意义
            self.encoding_box.setEnabled(False)
            self.encoding_box.setCurrentIndex(1)
            self.encoding_box.setToolTip("SFTP 的文件名固定 UTF-8，无需选择编码")
            return
        self.encoding_box.setEnabled(True)
        self.encoding_box.setToolTip(
            "服务端文件名编码。默认自动识别；出现中文乱码时可手动指定后刷新")
        enc = getattr(self.session, "effective_encoding", "") or ""
        auto = getattr(self.session, "encoding_autodetected", False)
        if auto:
            self.encoding_box.setCurrentIndex(0)   # 自动
            self.encoding_box.setToolTip(
                f"已自动识别为 {enc}。若中文仍显示为乱码，可手动指定后刷新")
        else:
            idx = self.encoding_box.findText(enc)
            self.encoding_box.setCurrentIndex(idx if idx > 0 else 1)
            self.encoding_box.setToolTip(f"当前使用编码：{enc}")

    def _on_encoding_changed(self, index: int) -> None:
        if not self.session:
            return
        if index == 0:
            # 回到自动：重新探测
            try:
                self.session._detect_encoding()
            except Exception as e:  # noqa: BLE001
                self.add_log(f"重新探测编码失败：{e}", "WARN")
            enc = self.session.effective_encoding
            self.add_log(f"已重新自动识别编码：{enc}")
        else:
            enc = self.encoding_box.currentText()
            try:
                self.session.set_encoding(enc)
            except Exception as e:  # noqa: BLE001
                self.add_log(f"切换编码失败：{e}", "WARN")
                return
            self.add_log(f"已切换文件名编码为 {enc}，正在刷新")

        self._sync_encoding_box()
        if self.session:
            self.go_remote(self.remote_path)

    # ------------------------------------------------------------ 浏览
    def refresh_local(self) -> None:
        try:
            entries = self.local_fs.listdir(self.local_path)
        except SessionError as e:
            self.notify_error("读取本地目录失败", str(e))
            return
        self.local_pane.set_entries(entries)
        self.local_pane.set_current_dir(self.local_path)

    def go_local(self, path: str) -> None:
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            self.notify_error("无法进入", f"不是目录：{path}")
            return
        self.local_path = path
        self.refresh_local()

    def go_remote(self, path: str) -> None:
        if not self.session:
            return
        target = path
        try:
            if hasattr(self.session, "change_dir"):
                target = self.session.change_dir(path)
            entries = self.session.listdir(target)
        except SessionError as e:
            # 失败时不能让 remote_path 停留在"已切换但没内容"的错位状态
            self.notify_error("读取远端目录失败", str(e))
            return
        except Exception as e:  # noqa: BLE001
            self.notify_error("读取远端目录失败", f"{type(e).__name__}: {e}")
            return
        self.remote_path = target
        self.remote_pane.set_entries(entries)
        self.remote_pane.set_current_dir(self.remote_path)

    def refresh_all(self) -> None:
        self.refresh_local()
        if self.session:
            self.go_remote(self.remote_path)

    def _on_pane_activated(self, pane: FilePane, entry: RemoteFile) -> None:
        if entry.is_dir:
            if pane is self.local_pane:
                self.go_local(entry.path)
            else:
                self.go_remote(entry.path)
            return
        # 双击文件直接开预览：看日志 / 改配置前先瞄一眼是最高频的动作
        self.preview_entry(pane, entry)

    # ------------------------------------------------------------ 预览
    def preview_entry(self, pane: FilePane, entry: RemoteFile) -> None:
        """打开只读预览窗口。远端只读文件头，不会整份下载。"""
        if entry is None or entry.is_dir:
            return
        is_local = pane is self.local_pane
        if not is_local and not self.session:
            QMessageBox.information(self, "提示", "请先连接服务器")
            return
        dlg = PreviewDialog(self)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.preview(
            name=entry.name,
            path=entry.path,
            size=entry.size,
            mtime=entry.mtime,
            cfg=None if is_local else self.cfg,
            local_path=entry.path if is_local else "",
        )
        theme.center_on_parent(dlg, self)
        dlg.show()

    def navigate_to(self, pane: FilePane, path: str) -> None:
        """路径栏点了某一层：按面板所在侧跳过去。"""
        if not path:
            return
        if pane is self.local_pane:
            self.go_local(path)
        else:
            self.go_remote(path)

    def _go_up(self, pane: FilePane) -> None:
        if not self._can_go_up(pane):
            return
        if pane is self.local_pane:
            self.go_local(self.local_fs.parent(self.local_path))
        else:
            parent = self.remote_path.rstrip("/").rpartition("/")[0] or "/"
            self.go_remote(parent)

    def _can_go_up(self, pane: FilePane) -> bool:
        """根目录再往上还是根目录，点了没反应反而像坏了，直接禁用。"""
        if pane is self.local_pane:
            return self.local_fs.parent(self.local_path) != self.local_path
        if self.session is None:
            return False
        return self.remote_path not in ("", "/")

    def _go_home(self, pane: FilePane) -> None:
        """主目录：本地是 ~，远端是 /。"""
        if pane is self.local_pane:
            self.go_local(os.path.expanduser("~"))
        elif self.session:
            self.go_remote("/")

    def _refresh_pane(self, pane: FilePane) -> None:
        """只刷右键点的那一侧；工具栏的「刷新」是两侧都刷。"""
        if pane is self.local_pane:
            self.refresh_local()
        elif self.session:
            self.go_remote(self.remote_path)

    # ------------------------------------------------------------ 传输
    def on_upload(self) -> None:
        if not self.session:
            QMessageBox.information(self, "提示", "请先连接服务器")
            return
        entries = self.local_pane.selected_entries()
        if not entries:
            QMessageBox.information(self, "提示", "请在左侧选择要上传的文件或目录")
            return
        count = 0
        for e in entries:
            if e.is_dir:
                count += self._enqueue_dir(e.path, self.remote_path, "upload")
            else:
                self.queue.submit("upload", e.path,
                                  _remote_join(self.remote_path, e.name), e.size)
                count += 1
        self.add_log(f"已加入上传队列：{count} 项 → {self.remote_path}")

    def on_download(self) -> None:
        if not self.session:
            QMessageBox.information(self, "提示", "请先连接服务器")
            return
        entries = self.remote_pane.selected_entries()
        if not entries:
            QMessageBox.information(self, "提示", "请在右侧选择要下载的文件或目录")
            return
        count = 0
        for e in entries:
            if e.is_dir:
                count += self._enqueue_remote_dir(
                    e.path, os.path.join(self.local_path, e.name))
            else:
                self.queue.submit("download", os.path.join(self.local_path, e.name),
                                  e.path, e.size)
                count += 1
        self.add_log(f"已加入下载队列：{count} 项 → {self.local_path}")

    def _enqueue_dir(self, local_dir: str, remote_parent: str, direction: str) -> int:
        """递归收集本地目录下的文件，逐个入队。"""
        base_name = os.path.basename(local_dir.rstrip("/"))
        root_remote = _remote_join(remote_parent, base_name)
        if self.session:
            try:
                if not self.session.exists(root_remote):
                    self.session.mkdir(root_remote)
            except Exception as e:  # noqa: BLE001
                self.add_log(f"创建远端目录失败 {root_remote}: {e}", "WARN")
        n = 0
        for cur, dirs, files in os.walk(local_dir):
            rel = os.path.relpath(cur, local_dir)
            remote_cur = root_remote if rel == "." else _remote_join(
                root_remote, rel.replace(os.sep, "/"))
            if rel != "." and self.session:
                try:
                    if not self.session.exists(remote_cur):
                        self.session.mkdir(remote_cur)
                except Exception as e:  # noqa: BLE001
                    self.add_log(f"创建远端目录失败 {remote_cur}: {e}", "WARN")
            for f in files:
                lp = os.path.join(cur, f)
                try:
                    size = os.path.getsize(lp)
                except OSError:
                    size = 0
                self.queue.submit("upload", lp, _remote_join(remote_cur, f), size)
                n += 1
        return n

    def _enqueue_remote_dir(self, remote_dir: str, local_dir: str) -> int:
        """递归下载远端目录。"""
        os.makedirs(local_dir, exist_ok=True)
        n = 0
        stack = [(remote_dir, local_dir)]
        while stack:
            rdir, ldir = stack.pop()
            try:
                entries = self.session.listdir(rdir)
            except Exception as e:  # noqa: BLE001
                self.add_log(f"读取远端目录失败 {rdir}: {e}", "WARN")
                continue
            for e in entries:
                target = os.path.join(ldir, e.name)
                if e.is_dir:
                    os.makedirs(target, exist_ok=True)
                    stack.append((e.path, target))
                else:
                    self.queue.submit("download", target, e.path, e.size)
                    n += 1
        return n

    def _on_task_update(self, task) -> None:
        # 队列面板同步（简单起见整表刷新，任务量不大时开销可接受）
        items = {}
        for i in range(self.queue_tree.topLevelItemCount()):
            it = self.queue_tree.topLevelItem(i)
            items[id(it)] = it
        self._sync_queue_tree()

        running = [t for t in self.queue.tasks() if t.state == TaskState.RUNNING]
        if running:
            speed = sum(t.speed for t in running)
            self.queue_summary.setText(
                f"进行中 {len(running)} · {human_size(int(speed))}/s")
        else:
            pending = [t for t in self.queue.tasks() if t.state == TaskState.QUEUED]
            done = [t for t in self.queue.tasks() if t.state == TaskState.DONE]
            failed = [t for t in self.queue.tasks() if t.state == TaskState.FAILED]
            self.queue_summary.setText(
                f"排队 {len(pending)} · 完成 {len(done)} · 失败 {len(failed)}")

        if task.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED):
            self._finish_task(task)
            self._notify_tray_result(task)
        self._sync_tray()

    def _notify_tray_result(self, task) -> None:
        """窗口收在托盘里时，用系统通知告知传输结果。

        只在队列彻底空下来时提示一次 —— 一次拖 50 个文件，逐个弹气泡
        会把人烦死；关在托盘里的人只需要知道"跑完了没有"。
        """
        if self.tray is None or self.isVisible():
            return
        tasks = self.queue.tasks()
        if any(t.state in (TaskState.RUNNING, TaskState.QUEUED) for t in tasks):
            return
        done = sum(1 for t in tasks if t.state == TaskState.DONE)
        failed = [t for t in tasks if t.state == TaskState.FAILED]
        if failed:
            self.tray.notify(
                "有任务失败",
                f"完成 {done} 个，失败 {len(failed)} 个（如 {failed[0].name}）")
        elif done and task.state == TaskState.DONE:
            self.tray.notify("传输完成", f"{done} 个任务已全部完成")

    def _sync_queue_tree(self) -> None:
        tasks = self.queue.tasks()
        while self.queue_tree.topLevelItemCount() > len(tasks):
            self.queue_tree.takeTopLevelItem(self.queue_tree.topLevelItemCount() - 1)
        while self.queue_tree.topLevelItemCount() < len(tasks):
            it = QTreeWidgetItem(["", "", "", "", ""])
            it.setFirstColumnSpanned(False)
            self.queue_tree.addTopLevelItem(it)
        for i, t in enumerate(tasks):
            it = self.queue_tree.topLevelItem(i)
            it.setText(0, t.name)
            it.setText(1, "上传" if t.direction == "upload" else "下载")
            it.setText(2, f"{t.percent:.0f}%  {human_size(t.done)}/{human_size(t.size)}")
            it.setText(3, f"{human_size(int(t.speed))}/s" if t.state == TaskState.RUNNING else "-")
            label = {
                TaskState.QUEUED: "排队",
                TaskState.RUNNING: "传输中",
                TaskState.DONE: "完成",
                TaskState.FAILED: f"失败：{t.error}",
                TaskState.CANCELLED: "已取消",
            }[t.state]
            it.setText(4, label)
        self._auto_size_queue()

    def _finish_task(self, task) -> None:
        if task.state == TaskState.DONE:
            self.add_log(f"{'上传' if task.direction == 'upload' else '下载'}完成：{task.name}")
            self.refresh_all()
        elif task.state == TaskState.FAILED:
            self.add_log(f"传输失败 {task.name}：{task.error}", "ERROR")

    def _clear_queue(self) -> None:
        self.queue.clear_finished()
        self.queue_tree.clear()
        self._sync_queue_tree()

    # ------------------------------------------------------------ 文件操作
    def _active_pane(self) -> FilePane:
        return self.remote_pane if self.remote_pane.hasFocus() else self.local_pane

    def on_newdir(self) -> None:
        pane = self._active_pane()
        base = self.local_path if pane is self.local_pane else self.remote_path
        name, ok = QInputDialog.getText(self, "新建目录", "目录名称：")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            if pane is self.local_pane:
                self.local_fs.mkdir(os.path.join(base, name))
                self.refresh_local()
            else:
                if not self.session:
                    return
                self.session.mkdir(_remote_join(base, name))
                self.go_remote(self.remote_path)
        except SessionError as e:
            self.notify_error("新建目录失败", str(e))

    def on_rename(self) -> None:
        pane = self._active_pane()
        entry = pane.current_entry()
        if entry is None:
            return
        new_name, ok = QInputDialog.getText(self, "重命名", "新名称：", text=entry.name)
        if not ok or not new_name.strip() or new_name.strip() == entry.name:
            return
        try:
            if pane is self.local_pane:
                self.local_fs.rename(entry.path,
                                     os.path.join(os.path.dirname(entry.path),
                                                  new_name.strip()))
                self.refresh_local()
            else:
                if not self.session:
                    return
                parent = entry.path.rstrip("/").rpartition("/")[0]
                self.session.rename(entry.path, _remote_join(parent, new_name.strip()))
                self.go_remote(self.remote_path)
        except SessionError as e:
            self.notify_error("重命名失败", str(e))

    def on_delete(self) -> None:
        pane = self._active_pane()
        entries = pane.selected_entries()
        if not entries:
            return
        names = "、".join(e.name for e in entries[:5])
        more = f" 等 {len(entries)} 项" if len(entries) > 5 else ""
        side = "本地" if pane is self.local_pane else "远端"
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除{side}的 {names}{more} 吗？\n此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            for e in entries:
                if pane is self.local_pane:
                    self.local_fs.remove(e.path, e.is_dir)
                else:
                    if not self.session:
                        return
                    self.session.remove(e.path, e.is_dir)
        except SessionError as e:
            self.notify_error("删除失败", str(e))
        self.refresh_all()

    # ------------------------------------------------------------ 复制路径
    def _paths_at(self, pane: FilePane, pos) -> list:
        """右键位置对应的路径。

        点在未选中的行上就只算那一行 —— 右键某个文件再"复制路径"，
        拿到的应该是这个文件的路径，不是上一次留下的多选。
        """
        idx = pane.view.indexAt(pos)
        entry = pane.model.entry_at(idx.row()) if idx.isValid() else None
        picked = [e.path for e in pane.selected_entries()]
        if entry is not None:
            if entry.path in picked:
                return picked
            return [entry.path]
        if picked:
            return picked
        return [pane.current_dir] if pane.current_dir else []

    def _preview_target(self, pane: FilePane, pos):
        """右键位置要预览的条目；点多行或没选中时返回 None。"""
        idx = pane.view.indexAt(pos)
        if idx.isValid():
            return pane.model.entry_at(idx.row())
        picked = pane.selected_entries()
        return picked[0] if len(picked) == 1 else None

    def copy_paths(self, paths) -> None:
        """把若干路径写进剪贴板（多条按行分隔）。"""
        paths = [p for p in dict.fromkeys(paths) if p]
        if not paths:
            return
        QApplication.clipboard().setText("\n".join(paths))
        # 用状态栏临时提示：复制是高频操作，不往日志面板里灌。
        # 必须延后一帧再写：右键菜单关闭时 QMainWindow 会收到一个空的
        # StatusTip 事件，顺手把状态栏消息清掉，当场写就等于没写。
        tip = (f"已复制路径：{paths[0]}" if len(paths) == 1
               else f"已复制 {len(paths)} 条路径")
        bar = self.statusBar()
        QTimer.singleShot(0, lambda: bar.showMessage(tip, 2500))

    def copy_path(self, pane: FilePane) -> None:
        """Ctrl+Shift+C 或菜单项触发：按面板当前选择复制。"""
        self.copy_paths(pane.paths_for_copy())

    @staticmethod
    def _copy_menu_text(paths) -> str:
        """菜单项文案跟着要复制的东西走，用户一眼能看出会复制几条。"""
        if not paths:
            return "复制当前目录路径"
        if len(paths) == 1:
            return "复制路径"
        return f"复制 {len(paths)} 项路径"

    def build_context_menu(self, pane: FilePane, pos) -> QMenu:
        """搭出列表右键菜单（不 exec，方便单测和截图）。

        动作一律在构造时连好，_on_context_menu 只负责弹出来，
        省掉 exec 之后一串 `chosen is act_x` 的比对。
        """
        menu = QMenu(self)
        side = "local" if pane is self.local_pane else "remote"
        # 本地面板永远可用；远端的操作要等连上
        connected = pane is self.local_pane or self.session is not None
        icon_size = 16

        # ------ 导航组：与面板顶部的小按钮、工具栏「刷新」同源。
        # 放在最前面是因为它不挑落点，右键点在空白处照样能用。
        act_up = menu.addAction(
            theme.icon("up", Palette.TEXT_MUTED, icon_size), "上级目录")
        act_up.setEnabled(self._can_go_up(pane))
        act_up.triggered.connect(lambda: self._go_up(pane))

        act_home = menu.addAction(
            theme.icon("home", Palette.TEXT_MUTED, icon_size), "主目录")
        act_home.setEnabled(connected)
        act_home.triggered.connect(lambda: self._go_home(pane))

        act_refresh = menu.addAction(
            theme.icon("refresh", Palette.TEXT_MUTED, icon_size), "刷新")
        act_refresh.setEnabled(connected)
        act_refresh.triggered.connect(lambda: self._refresh_pane(pane))
        menu.addSeparator()

        # ------ 针对落点那一项（或当前目录）的操作
        paths = self._paths_at(pane, pos)
        act_copy = menu.addAction(self._copy_menu_text(paths))
        # 远端没连上时列表是空的，没路径可复制
        act_copy.setEnabled(connected)
        # 用菜单弹出前算好的 paths：右键点中未选中的行时，复制的是那一行，
        # 不是上一次残留的多选
        act_copy.triggered.connect(lambda: self.copy_paths(paths))

        # 预览：菜单弹出前先定好对象，避免菜单开着时选择被改掉
        target = self._preview_target(pane, pos)
        act_preview = menu.addAction("预览")
        act_preview.setEnabled(target is not None and not target.is_dir)
        act_preview.triggered.connect(lambda: self.preview_entry(pane, target))
        menu.addSeparator()

        # 上传 / 下载 / 新建 / 重命名 / 删除仍按原样可点：内部自己会提示
        # "请先连接服务器"，不在菜单里提前替它们决定
        act_upload = menu.addAction("上传到远端")
        act_upload.setVisible(pane is self.local_pane)
        act_upload.triggered.connect(self.on_upload)

        act_download = menu.addAction("下载到本地")
        act_download.setVisible(pane is self.remote_pane)
        act_download.triggered.connect(self.on_download)
        menu.addSeparator()

        act_newdir = menu.addAction("新建目录")
        act_newdir.triggered.connect(self.on_newdir)

        act_rename = menu.addAction("重命名")
        act_rename.triggered.connect(self.on_rename)

        act_delete = menu.addAction("删除")
        act_delete.triggered.connect(self.on_delete)
        menu.addSeparator()

        act_bookmark = menu.addAction("收藏当前目录为书签…")
        act_bookmark.triggered.connect(lambda: self.add_bookmark(side))

        act_open_bm = menu.addAction("打开书签…")
        act_open_bm.triggered.connect(self.on_bookmarks)
        menu.addSeparator()

        act_open_local = menu.addAction("用文件管理器打开")
        act_open_local.setVisible(pane is self.local_pane)
        act_open_local.triggered.connect(lambda: self._open_in_file_manager(pane))
        return menu

    def _open_in_file_manager(self, pane: FilePane) -> None:
        """本地面板：目录就开它本身，文件就开它所在目录。"""
        entry = pane.current_entry()
        if entry is None:
            target = pane.current_dir
        else:
            target = entry.path if entry.is_dir else os.path.dirname(entry.path)
        if target:
            subprocess.Popen(["xdg-open", target])

    def _on_context_menu(self, pane: FilePane, pos) -> None:
        menu = self.build_context_menu(pane, pos)
        menu.exec(pane.view.viewport().mapToGlobal(pos))
        # 每次右键都新建菜单，用完得回收，否则一长串挂在主窗口底下
        menu.deleteLater()

    # ------------------------------------------------------------ 站点
    def on_sites(self) -> None:
        sites = self.store.sites()
        if not sites:
            QMessageBox.information(self, "站点管理器", "还没有保存的站点。\n连接时勾选“保存为站点”即可。")
            return
        labels = [f"{s.protocol.upper()}  {s.username}@{s.host}:{s.default_port()}"
                  for s in sites]
        idx, ok = QInputDialog.getItem(self, "站点管理器", "选择站点：", labels, 0, False)
        if not ok:
            return
        cfg = sites[labels.index(idx)]
        self.connect_with(cfg, save=False)

    # ------------------------------------------------------------ 书签
    def rebuild_bookmark_menu(self) -> None:
        """重建书签菜单：本地 / 远端分组，远端书签右侧显示所属站点。"""
        m = self.bookmark_menu
        m.clear()

        local_items = self.bookmarks.items("local")
        remote_items = self.bookmarks.items("remote")

        def _mark(bm: Bookmark, label: str) -> str:
            # 给设了"启动默认"的那条打星，用户才知道为什么启动不弹启动页
            return f"★ {label}" if bm.auto_open else label

        if not local_items and not remote_items:
            empty = m.addAction("（还没有书签）")
            empty.setEnabled(False)
        else:
            if local_items:
                m.addSection("本地")
                for bm in local_items:
                    act = m.addAction(_mark(bm, bm.name))
                    act.setToolTip(bm.path)
                    act.triggered.connect(lambda _=False, b=bm: self.open_bookmark(b))
            if remote_items:
                m.addSection("远端")
                for bm in remote_items:
                    label = bm.name
                    site = bm.site_label()
                    if site:
                        label = f"{bm.name}    [{site}]"
                    act = m.addAction(_mark(bm, label))
                    act.setToolTip(bm.path)
                    act.triggered.connect(lambda _=False, b=bm: self.open_bookmark(b))

        m.addSeparator()
        add_local = m.addAction("添加当前本地目录…")
        add_local.triggered.connect(lambda: self.add_bookmark("local"))
        add_remote = m.addAction("添加当前远端目录…")
        add_remote.setEnabled(bool(self.session))
        add_remote.triggered.connect(lambda: self.add_bookmark("remote"))
        m.addSeparator()
        m.addAction(self.act_manage_bookmark)

    def add_bookmark(self, side: str | None = None) -> None:
        """把当前目录存为书签。side 为 None 时按当前焦点判断。"""
        if side is None:
            pane = self._active_pane()
            side = "local" if pane is self.local_pane else "remote"

        if side == "local":
            path = self.local_path
            default_name = os.path.basename(path.rstrip("/")) or path
            suggest = BookmarkStore.make_local(default_name, path)
        else:
            if not self.session or not self.cfg:
                QMessageBox.information(self, "提示", "请先连接服务器")
                return
            path = self.remote_path
            default_name = path.rstrip("/").rpartition("/")[2] or path
            suggest = BookmarkStore.make_remote(default_name, path, self.cfg)

        existing = self.bookmarks.find(path, side)
        if existing is not None:
            ret = QMessageBox.question(
                self, "已存在书签",
                f"「{path}」已经是书签（名称：{existing.name}）。\n要重命名吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
            name, ok = QInputDialog.getText(
                self, "重命名书签", "书签名称：", text=existing.name)
            if ok and name.strip():
                self.bookmarks.rename(existing, name.strip())
                self.rebuild_bookmark_menu()
                self.add_log(f"书签已重命名：{name.strip()}")
            return

        name, ok = QInputDialog.getText(
            self, "添加书签", f"为目录「{path}」起个名字：", text=suggest.name)
        if not ok or not name.strip():
            return
        suggest.name = name.strip()
        self.bookmarks.add(suggest)
        self.rebuild_bookmark_menu()
        self.add_log(f"已添加书签：{suggest.name} → {path}")

    def open_bookmark(self, bm: Bookmark) -> None:
        """跳转到书签目录。远端书签若站点不同会自动连接。"""
        if not bm.is_remote:
            if not os.path.isdir(bm.path):
                # 用非阻塞提示，避免批量跳转或自动化场景被弹窗卡住
                self.add_log(f"书签已失效，本地目录不存在：{bm.path}", "ERROR")
                self._set_status(f"书签失效：{bm.path} 不存在", False)
                return
            self.go_local(bm.path)
            self.add_log(f"已跳转到书签：{bm.name} → {bm.path}")
            return

        # 远端：判断当前连接是否就是这个站点
        if self.session is None or self.cfg is None or self._site_changed(bm):
            cfg = self._cfg_from_bookmark(bm)
            if cfg is None:          # 用户在连接框里取消了
                return
            self.add_log(f"书签需要连接 {bm.site_label()}，正在连接…")
            self.connect_with(cfg, save=False)
            if self.session is None:      # 连接失败，connect_with 已弹窗
                return

        self.go_remote(bm.path)
        self.add_log(f"已跳转到书签：{bm.name} → {bm.path}")

    def _site_changed(self, bm: Bookmark) -> bool:
        if self.cfg is None:
            return True
        cfg = self.cfg
        return (cfg.protocol, cfg.host, cfg.port or 0, cfg.username) != bm.site_key()

    def _cfg_from_bookmark(self, bm: Bookmark,
                           interactive: bool = True) -> SessionConfig | None:
        """由书签构造连接配置。

        优先用书签自带的凭证（创建书签时已记住口令），这样点书签就能直接连；
        书签没记口令时再看站点库里有没有同站点配置；都没有才弹框让用户补。
        interactive=False 时（启动自动打开默认书签）不弹框，直接返回 None，
        交给调用方退回到启动页让用户自己选。
        返回 None 表示拿不到可用配置。
        """
        if bm.password():
            return bm.as_session_config()

        for site in self.store.sites():
            if (site.protocol, site.host, site.port or 0, site.username) == bm.site_key():
                return site

        if not interactive:
            return None

        base = bm.as_session_config()
        base.password = ""
        dlg = ConnectDialog(base, self)
        if dlg.exec() != ConnectDialog.Accepted:
            return None
        cfg = dlg.config()
        if dlg.wants_save():
            self.store.upsert(cfg)
        # 把补上的口令写回书签，下次就不用再输
        bm.secret = _obfuscate(cfg.password)
        self.bookmarks.save()
        return cfg

    def on_bookmarks(self) -> None:
        dlg = BookmarkDialog(self.bookmarks, self)
        dlg.open_requested.connect(self.open_bookmark)
        dlg.exec()
        self.rebuild_bookmark_menu()

    # ------------------------------------------------------------ 启动页
    def has_startup_entries(self) -> bool:
        return has_content(self.bookmarks.items(), self.store.recents())

    def open_default_bookmark(self) -> bool:
        """启动时直接打开"设为默认"的书签，不弹启动页。

        返回 False 表示这次没打开（没设置默认、书签失效、缺口令、连不上），
        由调用方退回到启动页 —— 不能因为一个坏掉的默认书签就把人挡在门外。
        """
        bm = self.bookmarks.default()
        if bm is None:
            return False

        if not bm.is_remote:
            if not os.path.isdir(bm.path):
                self.add_log(f"默认书签已失效，本地目录不存在：{bm.path}", "ERROR")
                return False
            self.go_local(bm.path)
            self.add_log(f"已打开默认书签：{bm.name} → {bm.path}")
            return True

        # interactive=False：缺口令时不弹连接框。启动阶段突然弹个框问密码，
        # 比直接退回启动页让用户选更难理解。
        cfg = self._cfg_from_bookmark(bm, interactive=False)
        if cfg is None:
            self.add_log("默认书签缺少口令，改为打开启动页", "WARN")
            return False

        self.add_log(f"正在连接默认书签 {bm.site_label()}…")
        if not self.connect_with(cfg, save=False, notify=False):
            # 不弹框：直接退回启动页让用户自己选，失败原因留在日志里
            self.add_log("默认书签连接失败，改为打开启动页", "WARN")
            return False

        self.go_remote(bm.path)
        self.add_log(f"已打开默认书签：{bm.name} → {bm.path}")
        return True

    def startup_dispatch(self) -> None:
        """启动后决定入口：有默认书签就直接进，否则弹启动页。"""
        if self.open_default_bookmark():
            return
        if self.has_startup_entries():
            self.show_launchpad()

    def show_launchpad(self) -> bool:
        """弹启动页；返回 True 表示用户选了某个条目并已触发跳转。

        位置必须显式指定：多屏 + 不同缩放下，窗口管理器给独立对话框
        挑屏幕的结果不可控，会出现启动页跑到另一块屏上的情况。
        """
        dlg = LaunchPad(self.bookmarks.items(), self.store.recents(), self)
        dlg.open_bookmark_requested.connect(self.open_bookmark)
        dlg.open_site_requested.connect(self.open_recent_site)

        # 先 show() 让窗口尺寸定型，再把位置钉到主窗口同一块屏，
        # 最后进模态循环（顺序反了就会被默认位置覆盖）
        dlg.show()
        theme.center_on_parent(dlg, self)
        dlg.raise_()
        dlg.activateWindow()
        accepted = dlg.exec() == LaunchPad.Accepted
        return accepted

    def open_recent_site(self, cfg: SessionConfig) -> None:
        """从启动页的"最近连接"进入。

        最近连接本身不存口令，站点库里通常有一份带口令的配置，
        这里先尝试补上，避免用户每次都要重新输密码。
        """
        target = cfg
        for site in self.store.sites():
            if (site.protocol, site.host, site.port or 0, site.username) == \
                    (cfg.protocol, cfg.host, cfg.port or 0, cfg.username):
                target = site
                break
        else:
            base = SessionConfig(
                protocol=cfg.protocol, host=cfg.host, port=cfg.port,
                username=cfg.username, password="", passive=cfg.passive,
                encoding=cfg.encoding, timeout=cfg.timeout)
            dlg = ConnectDialog(base, self)
            if dlg.exec() != ConnectDialog.Accepted:
                return
            target = dlg.config()
            if dlg.wants_save():
                self.store.upsert(target)
        self.connect_with(target, save=False)

    # ------------------------------------------------------------ 退出
    def restore_window_state(self) -> bool:
        """按上次退出时的大小和位置摆好窗口。

        返回 False 表示没有可用的记录（首次启动，或存下来的位置已经不在
        任何一块显示器上，比如外接屏被拔掉了），调用方改为摆到主屏居中。
        """
        geo = load_window_geometry()
        if geo is None:
            return False

        app = QApplication.instance()
        rect = QRect(geo["x"], geo["y"], geo["w"], geo["h"])
        # 按中心点反查屏幕：位置落在哪块屏上就用哪块屏的可用区做收拢。
        # 中心点都找不到屏幕，说明那块显示器已经不在了，交给调用方重新摆。
        screen = app.screenAt(rect.center()) if app is not None else None
        if screen is None:
            return False

        avail = screen.availableGeometry()
        # 换了小屏之后，原来的尺寸可能装不下，先缩到可用区大小再收位置
        w = min(geo["w"], avail.width())
        h = min(geo["h"], avail.height())
        x = max(avail.x(), min(geo["x"], avail.x() + avail.width() - w))
        y = max(avail.y(), min(geo["y"], avail.y() + avail.height() - h))
        self.setGeometry(x, y, w, h)

        if geo["maximized"]:
            # setWindowState 在 show() 之前调用也能生效，show() 出来就是最大化
            self.setWindowState(self.windowState() | Qt.WindowMaximized)
        return True

    # ------------------------------------------------------------ 托盘 / 关闭
    def _setup_tray(self) -> None:
        """建系统托盘。拿不到托盘就退回老规矩：关窗口即退出。"""
        if not TrayIcon.available():
            log.info("系统托盘不可用，关闭窗口将直接退出")
            return

        self.tray = TrayIcon(self)
        self.tray.show_window_requested.connect(self.show_from_tray)
        self.tray.disconnect_requested.connect(self.on_disconnect)
        self.tray.quit_requested.connect(self.quit_from_tray)
        self.tray.close_tray_pref_changed.connect(self._on_close_tray_pref_changed)
        self.tray.set_close_tray_pref(load_close_pref() == CLOSE_TRAY)
        self.tray.show()

        # 收进托盘后一个窗口都不剩，Qt 默认会顺手把程序退掉，
        # 那“常驻托盘”就成了一句空话。关掉这条默认行为后，退出只剩两个入口：
        # closeEvent 里用户明确选“退出程序”，或托盘菜单的“退出”。
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)

        self._sync_tray()

    def _sync_tray(self) -> None:
        """把连接状态、传输中任务数刷到托盘上。"""
        tray = getattr(self, "tray", None)
        label = getattr(self, "status_label", None)
        if tray is None or label is None:
            return
        tray.set_status(label.text(), self.session is not None,
                        self._running_count())

    def _running_count(self) -> int:
        """还没结束的任务数（传输中 + 排队中）。"""
        return sum(1 for t in self.queue.tasks()
                   if t.state in (TaskState.RUNNING, TaskState.QUEUED))

    def _on_close_tray_pref_changed(self, on: bool) -> None:
        save_close_pref(CLOSE_TRAY if on else CLOSE_ASK)
        tip = ("关闭窗口时收进托盘" if on else "关闭窗口时先询问要托盘还是退出")
        # 菜单关闭时会给主窗口发一个空 StatusTip，把刚写进去的提示清掉，
        # 所以压到下一帧再写
        QTimer.singleShot(0, lambda: self.statusBar().showMessage(tip, 2500))

    def show_from_tray(self) -> None:
        """从托盘把主窗口叫回来。最小化过就先还原，再抬到最前。"""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self) -> None:
        """收进托盘：藏起窗口，进程、定时器、传输队列都留着。"""
        # 窗口藏起来以后就拿不到 geometry 了，先记一份；
        # 不然"收进托盘 → 从托盘退出"这条路上位置会丢
        save_window_geometry(self)
        self.hide()
        if self.tray is not None:
            running = self._running_count()
            body = "程序仍在后台运行，点击托盘图标可重新打开。"
            if running:
                body = (f"{running} 个传输任务在后台继续。"
                        "点击托盘图标可重新打开窗口。")
            self.tray.notify("LinFTP 已收进托盘", body)
        self._sync_tray()

    def quit_from_tray(self) -> None:
        """从托盘菜单退出。

        有任务没完成时 closeEvent 会弹确认框，父窗口藏在托盘里是看不见的，
        所以先把窗口叫回来，再走正常的关闭流程。
        """
        self._quitting = True
        self.show_from_tray()
        self.close()

    def _confirm_quit_with_tasks(self, running: int) -> bool:
        """还有任务在跑时，退出前再问一遍。"""
        if running <= 0:
            return True
        ret = QMessageBox.question(
            self, "仍有传输任务",
            f"还有 {running} 个任务未完成，确定退出吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return ret == QMessageBox.Yes

    def _teardown(self) -> None:
        """真正退出前的收尾：记窗口状态、摘托盘、停后台。"""
        # 放在 accept() 之前：此时窗口还在、normalGeometry() 还有效；
        # 写在 destroy 之后就拿不到了。
        save_window_geometry(self)
        if self.tray is not None:
            # 不摘掉的话，进程没了托盘里还会留个点不动的空图标
            self.tray.hide()
        self._keepalive.stop()
        self.queue.shutdown()
        if self.session:
            try:
                self.session.close()
            except Exception:
                pass

    def closeEvent(self, event) -> None:
        running = self._running_count()

        # 明确要退出（托盘菜单点的 / 上一轮已经问过了）：不再提托盘
        if self._quitting:
            if not self._confirm_quit_with_tasks(running):
                self._quitting = False      # 用户反悔，窗口留着
                event.ignore()
                return
            self._teardown()
            event.accept()
            return

        action = resolve_close_action(load_close_pref(), self.tray is not None)

        if action == CLOSE_ASK:
            choice, remember = ask_close_action(self, running)
            if choice == CLOSE_CANCEL:
                event.ignore()
                return
            if remember:
                save_close_pref(choice)
                if self.tray is not None:
                    self.tray.set_close_tray_pref(choice == CLOSE_TRAY)
            action = choice

        if action == CLOSE_TRAY:
            self.hide_to_tray()
            event.ignore()      # 窗口没关，只是藏起来了
            return

        if not self._confirm_quit_with_tasks(running):
            event.ignore()
            return
        self._teardown()
        event.accept()


def _remote_join(parent: str, name: str) -> str:
    p = (parent or "/").rstrip("/")
    return f"{p}/{name}" if p else f"/{name}"
