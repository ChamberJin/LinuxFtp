"""界面主题：现代扁平风格。

三部分：
- ``palette``  颜色常量，全局唯一来源，改一处即可整体换肤
- ``icons``    线性图标（运行时按主题色渲染成 QIcon，不依赖外部资源）
- ``qss``      统一 QSS 样式表

设计取向：浅色底 + 卡片式分区 + 柔和描边，避免默认 QSS 那种
"到处是 2px 黑框、按钮像 Windows 98" 的观感。
"""
from __future__ import annotations

from typing import Dict

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


# ---------------------------------------------------------------- 颜色
class Palette:
    BG = "#F5F6F8"            # 窗口底色
    SURFACE = "#FFFFFF"       # 卡片 / 列表底色
    SURFACE_ALT = "#FAFBFC"   # 隔行 / 次级面板
    BORDER = "#E3E6EB"        # 主描边
    BORDER_STRONG = "#D2D7DE"
    TEXT = "#1F2328"          # 主文字
    TEXT_MUTED = "#6B7280"    # 次要文字
    TEXT_FAINT = "#9AA1AC"    # 占位 / 禁用
    PRIMARY = "#2F6FED"       # 主色
    PRIMARY_HOVER = "#2560D4"
    PRIMARY_SOFT = "#EAF1FE"  # 主色浅底（选中行）
    ACCENT = "#0F7A63"        # 本地侧点缀色
    ACCENT_SOFT = "#E6F4F0"
    DANGER = "#C0392B"
    WARN = "#8A5A00"
    DIR = "#2563C9"           # 目录名颜色（比主色稍深，避免和选中底色混）
    LINK = "#6B4BC4"          # 符号链接
    HOVER = "#F0F2F5"
    PRESSED = "#E6E9EE"


# ---------------------------------------------------------------- 尺寸
# 两套尺寸规格：标准 / 紧凑。所有"像素值"都从这里取，
# 切换模式时只需重新套一遍，不用到处改 setFixedHeight。
class Metrics:
    """标准模式：留白充足，适合日常浏览。"""

    name = "标准"

    # 卡片
    card_margin = (10, 8, 10, 10)
    card_spacing = 6
    card_radius = 10
    card_title_size = 12

    # 面板内部
    pane_margins = (10, 9, 10, 10)
    pane_spacing = 8
    pane_row_h = 26          # 文件行高
    path_h = 30              # 路径框高度
    filter_h = 28
    nav_btn = 30             # 上级 / 主目录按钮
    side_label_h = 24

    # 工具栏
    toolbar_icon = 17
    toolbar_padding = "6px 10px"
    toolbar_sep_margin = "4px 6px"

    # 两侧文件区之间的分栏手柄
    splitter_handle = 8
    # 整个中央区域的边距
    body_margins = (10, 8, 10, 6)

    # 列宽（文件列表：大小/类型/修改时间/权限）
    col_size = 82
    col_type = 78
    col_mtime = 132
    col_perm = 104
    name_min_w = 170

    # 队列 / 日志面板
    queue_empty_h = 92
    log_collapsed_h = 40

    # 状态栏：高度 + 圆点字号 + 内边距（QSS）
    # Qt 默认 24px 是把 13px 字体上下各留 5px，紧凑模式下能压到 19px
    statusbar_h = 24
    statusbar_dot_size = 12
    statusbar_padding = "0px 8px"

    # 列表单元格内边距（给 QSS 用）
    item_padding = "2px 4px"
    header_padding = "6px 8px"
    # 通用控件内边距（QSS）
    button_padding = "5px 13px"
    button_min_h = 20
    input_padding = "5px 9px"
    input_min_h = 20
    toolbtn_padding = "5px 9px"

    # 启动页（独立对话框，留白比主窗口更宽松）
    pad_w = 620
    pad_h = 520
    pad_margins = (22, 20, 22, 18)
    pad_spacing = 14
    pad_row_h = 56
    pad_avatar = 30
    pad_row_margins = (10, 6, 12, 6)
    pad_row_spacing = 11
    pad_search_h = 34
    pad_title_size = 19


class CompactMetrics(Metrics):
    """紧凑模式：挤压纵向留白，优先多显示几行文件。"""

    name = "紧凑"

    card_margin = (6, 4, 6, 6)
    card_spacing = 3
    card_radius = 8
    card_title_size = 11

    pane_margins = (6, 5, 6, 6)
    pane_spacing = 4
    pane_row_h = 21
    path_h = 25
    filter_h = 24
    nav_btn = 25
    side_label_h = 21

    toolbar_icon = 15
    toolbar_padding = "3px 7px"
    toolbar_sep_margin = "3px 5px"

    splitter_handle = 5
    body_margins = (8, 6, 8, 5)

    # 紧凑模式只收窄"冗余留白"，不能收到文字放不下：
    # "2026-11-16 15:06" 实测约 102px，"drwxrwxrwx" 约 75px
    col_size = 76
    col_type = 72
    col_mtime = 124
    col_perm = 96
    name_min_w = 150

    queue_empty_h = 74
    log_collapsed_h = 30

    # 状态栏压到 19px：13px 字体 + 上下各 3px，再窄就会切字
    statusbar_h = 19
    statusbar_dot_size = 11
    statusbar_padding = "0px 7px"

    item_padding = "0px 4px"
    header_padding = "4px 7px"
    button_padding = "2px 10px"
    button_min_h = 16
    input_padding = "2px 7px"
    input_min_h = 16
    toolbtn_padding = "2px 7px"

    pad_w = 580
    pad_h = 448
    pad_margins = (14, 12, 14, 11)
    pad_spacing = 9
    pad_row_h = 40
    pad_avatar = 24
    pad_row_margins = (8, 3, 10, 3)
    pad_row_spacing = 8
    pad_search_h = 28
    pad_title_size = 17


STANDARD = Metrics()
COMPACT = CompactMetrics()

# 当前生效的规格。各界面模块通过 `theme.metrics` 读取，
# 不要缓存成模块级变量，否则切换后不会生效。
metrics: Metrics = STANDARD


def set_compact(compact: bool) -> Metrics:
    """切换尺寸规格并返回新的规格对象。

    这里必须连带把全局 QSS 一起换掉。QSS 里的 toolbar/toolbutton/input 等
    padding 全都取自 Metrics：如果只改 metrics 而 QSS 还是旧的那份，
    图标会缩小但按钮内边距不变，工具栏高度纹丝不动——看上去就像
    “紧凑模式对工具栏无效”，这正是重启后读偏好那条路径踩到的坑
    （theme.apply() 先跑、set_compact() 后跑，中间没人重新生成 QSS）。
    """
    global metrics
    metrics = COMPACT if compact else STANDARD
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(qss())
    return metrics


# ---------------------------------------------------------------- 图标
# 统一 24x24 视图框、1.7px 线宽、圆角端点，保持视觉一致
_PATH: Dict[str, str] = {
    "connect": '<path d="M9 7V5a3 3 0 0 1 3-3h1a3 3 0 0 1 3 3v2"/>'
               '<rect x="3.5" y="7" width="17" height="13" rx="3"/>'
               '<circle cx="12" cy="13" r="2"/>'
               '<path d="M12 15v2.5"/>',
    "disconnect": '<path d="M12 3v8"/>'
                  '<path d="M6.3 6.3a8 8 0 1 0 11.4 0"/>',
    "upload": '<path d="M12 20V5"/><path d="M6 11l6-6 6 6"/>'
              '<path d="M4 21h16"/>',
    "download": '<path d="M12 4v15"/><path d="M6 13l6 6 6-6"/>'
                '<path d="M4 21h16"/>',
    "newdir": '<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h3.2l1.8 2.2h5.8a2 2 0 0 1 2 2v7.3a2 2 0 0 1-2 2H5.5a2 2 0 0 1-2-2z"/>'
              '<path d="M12 11.5v5"/><path d="M9.5 14h5"/>',
    "rename": '<path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17z"/>'
              '<path d="M14.5 6.5l3 3"/>',
    "delete": '<path d="M4 7h16"/><path d="M9.5 7V4.8h5V7"/>'
              '<path d="M6.5 7l.9 12.2a1.8 1.8 0 0 0 1.8 1.6h5.6a1.8 1.8 0 0 0 1.8-1.6L17.5 7"/>'
              '<path d="M10.5 11v6"/><path d="M13.5 11v6"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.6-5.9"/>'
               '<path d="M20 4.5V10h-5.5"/>',
    "sites": '<circle cx="12" cy="12" r="8.5"/>'
             '<path d="M3.5 12h17"/>'
             '<path d="M12 3.5c2.6 3 2.6 14 0 17c-2.6-3-2.6-14 0-17z"/>',
    "bookmark": '<path d="M6.5 4h11a1 1 0 0 1 1 1v15l-6.5-4.2L5.5 20V5a1 1 0 0 1 1-1z"/>',
    "folder": '<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h3.2l1.8 2.2h5.8a2 2 0 0 1 2 2v7.3a2 2 0 0 1-2 2H5.5a2 2 0 0 1-2-2z"/>',
    "server": '<rect x="3.5" y="4" width="17" height="6.5" rx="2"/>'
              '<rect x="3.5" y="13.5" width="17" height="6.5" rx="2"/>'
              '<path d="M7 7.2v.1"/><path d="M7 16.8v.1"/>',
    "arrow_right": '<path d="M4 12h15"/><path d="M13 6l6 6-6 6"/>',
    "arrow_left": '<path d="M20 12H5"/><path d="M11 6l-6 6 6 6"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21"/>',
    "up": '<path d="M12 19V6"/><path d="M5.5 12.5L12 6l6.5 6.5"/>',
    "home": '<path d="M4 10.5L12 4l8 6.5"/>'
            '<path d="M6.5 9.5V19a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1V9.5"/>'
            '<path d="M10 20v-5.5h4V20"/>',
    "compact": '<path d="M4 6h16"/><path d="M4 10h16"/><path d="M4 14h11"/>'
               '<path d="M4 18h7"/>',
    "chevron_down": '<path d="M6.5 9.5L12 15l5.5-5.5"/>',
    "chevron_up": '<path d="M6.5 14.5L12 9l5.5 5.5"/>',
    "close": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
}

_SVG_TMPL = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{color}" stroke-width="1.7" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)


_cached: Dict[tuple, QIcon] = {}


def _render_key(name: str, color: str, size: int, fill: bool) -> QIcon:
    """把一个 SVG 图标渲染成指定尺寸、指定颜色的 QIcon。"""
    body = _PATH[name]
    if fill:
        body = body.replace('fill="none"', "")
    svg = _SVG_TMPL.format(color=color, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))

    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QIcon(pix)


def icon(name: str, color: str = Palette.TEXT, size: int = 18,
         fill: bool = False) -> QIcon:
    """取一个线性图标。颜色/尺寸不同的组合各自缓存。"""
    key = (name, color, size, fill)
    hit = _cached.get(key)
    if hit is None:
        hit = _render_key(name, color, size, fill)
        _cached[key] = hit
    return hit


def avatar_text(text: str, size: int = 32, bg: str = Palette.PRIMARY,
                fg: str = "#FFFFFF") -> QPixmap:
    """生成圆形文字头像，用于书签列表左侧的圆形标记。"""
    from PySide6.QtGui import QFont

    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setBrush(QColor(bg))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(0, 0, size, size))
    p.setPen(QColor(fg))
    f = QFont()
    f.setPixelSize(int(size * 0.48))
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, (text or "?")[:1].upper())
    p.end()
    return pix


# ---------------------------------------------------------------- 样式表
def inherit_app_font(widget) -> None:
    """让独立窗口跟进当前应用字体。

    QDialog/QWidget 的字体在构造时就从前一刻的父级快照下来了，
    之后 app.setFont() 不会再传进来。切换字号（如紧凑模式）后
    新建的对话框会沿用旧字号，所以构造末尾要显式同步一次。
    """
    app = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication
    inst = app.instance()
    if inst is not None and widget is not None:
        widget.setFont(inst.font())


def base_font():
    """当前应用字体。

    对话框等独立窗口的字体是构造时从父窗口快照来的，之后 app.setFont()
    不会自动传进去，需要显式取一次再 setFont。
    """
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    return QFont(app.font()) if app is not None else QFont()


def _screen_of(widget, app):
    """取窗口实际所在的那块屏，拿不准就回退到主屏。"""
    screen = None
    if widget is not None:
        screen = widget.screen()
        if screen is None:
            geo = widget.frameGeometry()
            screen = app.screenAt(geo.center())
        if screen is None:
            # frameGeometry 还不可信时，退一步看它的 topLevel 位置
            win = widget.window() if hasattr(widget, "window") else None
            if win is not None and win is not widget:
                screen = win.screen()
    return screen or app.primaryScreen()


def center_on_parent(widget, parent=None, offset_y: int = 0) -> None:
    """把弹窗摆在它所属窗口的同一块屏幕上，并相对父窗口居中。

    多屏环境下（尤其两块屏逻辑坐标差很大、缩放还不同时），
    窗口管理器摆放独立对话框时完全可能挑到另一块屏上去，
    表现为"启动页和主窗口各占一块屏"。这里显式指定屏幕与坐标。

    调用时机很要紧：必须在 widget 已经 show() 且布局定型之后调，
    否则拿到的是 show 之前的初始尺寸，居中会算歪；所以这里顺手
    把布局先刷一遍（activate + adjustSize），拿到最终尺寸再摆。
    """
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance()
    if app is None or widget is None:
        return

    ref = parent if isinstance(parent, QWidget) else None

    # 先让布局算完，widget.size() 才是最终值
    lay = widget.layout()
    if lay is not None:
        lay.activate()
    hint = widget.sizeHint()
    size = widget.size()
    if hint.isValid() and hint.width() > 0 and hint.height() > 0:
        # 取两者较大的那个：有些布局的 sizeHint 比实际 size 更贴近内容
        size = size.expandedTo(hint) if size.isValid() else hint

    # 屏幕优先跟着父窗口走，父窗口没有再退回自己所在屏
    screen = None
    if ref is not None:
        screen = _screen_of(ref, app)
    if screen is None:
        screen = _screen_of(widget, app)
    if screen is None:
        return

    avail = screen.availableGeometry()

    if ref is not None and ref.isVisible():
        fg = ref.frameGeometry()
        x = fg.x() + (fg.width() - size.width()) // 2
        y = fg.y() + (fg.height() - size.height()) // 2 + offset_y
    else:
        x = avail.x() + (avail.width() - size.width()) // 2
        y = avail.y() + (avail.height() - size.height()) // 2 + offset_y

    # 收进该屏的可用区域内，避免探出屏幕边缘
    x = max(avail.x(), min(x, avail.x() + max(0, avail.width() - size.width())))
    y = max(avail.y(), min(y, avail.y() + max(0, avail.height() - size.height())))
    widget.move(x, y)


def place_on_primary(widget, offset_y: int = 0) -> None:
    """把主窗口摆到主屏可用区居中。

    顶层窗口第一次上屏时屏幕由窗口管理器决定，多屏下经常不是主屏。
    主窗口一旦跑偏，跟着它居中的启动页也会跑偏，而两块屏逻辑坐标
    往往差得很远（这里内置屏在 (775,2160)、外接屏在 (0,0)），
    结果就是用户看到两个窗口各占一块屏。

    这里只做"没有历史位置时的初始摆放"，用户自己拖过的位置不覆盖。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None or widget is None:
        return
    screen = app.primaryScreen() or (app.screens() or [None])[0]
    if screen is None:
        return
    avail = screen.availableGeometry()
    size = widget.size()
    if not size.isValid() or size.isEmpty():
        size = widget.sizeHint()
    x = avail.x() + max(0, (avail.width() - size.width()) // 2)
    y = avail.y() + max(0, (avail.height() - size.height()) // 2) + offset_y
    x = max(avail.x(), min(x, avail.x() + max(0, avail.width() - size.width())))
    y = max(avail.y(), min(y, avail.y() + max(0, avail.height() - size.height())))
    widget.move(x, y)


def keep_on_screen(widget, wanted_screen=None) -> None:
    """确保窗口落在指定屏幕（默认它当前所属屏幕）内。

    用户在多屏间切换后重新打开窗口时，存下来的位置可能已经落在
    已拔掉的显示器上，这里做个兜底校正。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    screen = wanted_screen or widget.screen() or app.primaryScreen()
    if screen is None:
        return
    avail = screen.availableGeometry()
    geo = widget.frameGeometry()
    if avail.contains(geo):
        return
    x = max(avail.x(), min(geo.x(), avail.x() + avail.width() - geo.width()))
    y = max(avail.y(), min(geo.y(), avail.y() + avail.height() - geo.height()))
    widget.move(x, y)


def qss(m: "Metrics | None" = None) -> str:
    p = Palette
    m = m or metrics
    return f"""
/* ---------- 全局 ---------- */
QWidget {{
    color: {p.TEXT};
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {p.BG};
}}
QToolTip {{
    background: #2A2F36;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 12px;
}}

/* ---------- 工具栏 ---------- */
QToolBar {{
    background: {p.SURFACE};
    border: none;
    border-bottom: 1px solid {p.BORDER};
    spacing: 2px;
    padding: {m.toolbar_padding};
}}
QToolBar::separator {{
    background: {p.BORDER};
    width: 1px;
    margin: {m.toolbar_sep_margin};
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: {m.toolbtn_padding};
    color: {p.TEXT};
}}
QToolButton:hover {{
    background: {p.HOVER};
    border-color: {p.BORDER};
}}
QToolButton:pressed {{
    background: {p.PRESSED};
}}
QToolButton:disabled {{
    color: {p.TEXT_FAINT};
}}
QToolButton::menu-indicator {{
    image: none;
    width: 0;
}}
QToolButton[popupMode="1"] {{
    padding-right: 20px;
}}
QToolButton::menu-button {{
    border: none;
    border-left: 1px solid {p.BORDER};
    margin: 3px 0 3px 4px;
    width: 13px;
    background: transparent;
}}
QToolButton[popupMode="1"]::menu-arrow {{
    image: url({_combo_arrow_path()});
    width: 10px;
    height: 10px;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {p.SURFACE};
    border: 1px solid {p.BORDER_STRONG};
    border-radius: 7px;
    padding: {m.button_padding};
    min-height: {m.button_min_h}px;
    color: {p.TEXT};
}}
QPushButton:hover {{
    background: {p.HOVER};
    border-color: #BFC6D0;
}}
QPushButton:pressed {{
    background: {p.PRESSED};
}}
QPushButton:disabled {{
    color: {p.TEXT_FAINT};
    background: {p.SURFACE_ALT};
    border-color: {p.BORDER};
}}
QPushButton[flat="true"] {{
    background: transparent;
    border-color: transparent;
    color: {p.TEXT_MUTED};
}}
QPushButton[flat="true"]:hover {{
    background: {p.HOVER};
    color: {p.TEXT};
}}
QPushButton[primary="true"] {{
    background: {p.PRIMARY};
    border-color: {p.PRIMARY};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{
    background: {p.PRIMARY_HOVER};
    border-color: {p.PRIMARY_HOVER};
}}
QPushButton[primary="true"]:pressed {{
    background: #1E4FBB;
}}
QPushButton[danger="true"]:hover {{
    background: #FCEDEB;
    color: {p.DANGER};
    border-color: #E8B9B2;
}}

/* ---------- 输入控件 ---------- */
QLineEdit, QSpinBox, QComboBox {{
    background: {p.SURFACE};
    border: 1px solid {p.BORDER_STRONG};
    border-radius: 7px;
    padding: {m.input_padding};
    min-height: {m.input_min_h}px;
    selection-background-color: {p.PRIMARY_SOFT};
    selection-color: {p.TEXT};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{
    border-color: #BFC6D0;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {p.PRIMARY};
    background: {p.SURFACE};
}}
QLineEdit:read-only {{
    background: {p.SURFACE_ALT};
    color: {p.TEXT_MUTED};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: url({_combo_arrow_path()});
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background: {p.SURFACE};
    border: 1px solid {p.BORDER};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: {p.PRIMARY_SOFT};
    selection-color: {p.TEXT};
}}
QComboBox QAbstractItemView::item {{
    border-radius: 5px;
    padding: 5px 8px;
    min-height: 22px;
}}

/* ---------- 数字输入框上下箭头 ---------- */
QSpinBox {{
    padding-right: 22px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border;
    width: 18px;
    background: transparent;
    border: none;
    border-left: 1px solid {p.BORDER};
}}
QSpinBox::up-button {{
    subcontrol-position: top right;
    margin: 3px 3px 0 0;
    border-top-right-radius: 6px;
}}
QSpinBox::down-button {{
    subcontrol-position: bottom right;
    margin: 0 3px 3px 0;
    border-bottom-right-radius: 6px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {p.HOVER};
}}
QSpinBox::up-arrow {{
    image: url({_spin_up_path()});
    width: 9px;
    height: 9px;
}}
QSpinBox::down-arrow {{
    image: url({_spin_down_path()});
    width: 9px;
    height: 9px;
}}

/* ---------- 列表 / 表格 ---------- */
QTableView, QTreeWidget, QListView {{
    background: {p.SURFACE};
    border: 1px solid {p.BORDER};
    border-radius: 8px;
    outline: none;
    gridline-color: transparent;
    alternate-background-color: {p.SURFACE_ALT};
    selection-background-color: {p.PRIMARY_SOFT};
    selection-color: {p.TEXT};
}}
QTableView::item, QTreeWidget::item {{
    padding: {m.item_padding};
    border: none;
}}
QTableView::item:hover, QTreeWidget::item:hover {{
    background: {p.HOVER};
}}
QTableView::item:selected, QTreeWidget::item:selected {{
    background: {p.PRIMARY_SOFT};
    color: {p.TEXT};
}}
QHeaderView {{
    background: transparent;
}}
QHeaderView::section {{
    background: {p.SURFACE_ALT};
    color: {p.TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {p.BORDER};
    padding: {m.header_padding};
    font-weight: 600;
    font-size: 12px;
}}
QHeaderView::section:hover {{
    background: #F2F4F7;
    color: {p.TEXT};
}}
/* 表头最右侧补一条竖线，否则右边会缺一个缺口 */
QHeaderView::section:last {{
    border-right: 1px solid {p.BORDER};
}}
QTableView QTableCornerButton::section,
QTableCornerButton::section {{
    background: {p.SURFACE_ALT};
    border: none;
    border-bottom: 1px solid {p.BORDER};
}}
/* 表格底部留一点内缩，让最后一行不顶到圆角边框上 */
QTableView {{ padding-bottom: 2px; }}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 0;
}}
QScrollBar::handle:vertical {{
    background: #C9CFD8;
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: #AFB7C2;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0 4px 2px 4px;
}}
QScrollBar::handle:horizontal {{
    background: #C9CFD8;
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #AFB7C2;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---------- 分栏 ---------- */
QSplitter::handle {{
    background: transparent;
}}
QSplitter::handle:horizontal {{
    width: 6px;
}}
QSplitter::handle:vertical {{
    height: 6px;
}}
QSplitter::handle:hover {{
    background: {p.PRIMARY_SOFT};
}}

/* ---------- 状态栏 ---------- */
QStatusBar {{
    background: {p.SURFACE};
    border-top: 1px solid {p.BORDER};
    color: {p.TEXT_MUTED};
    padding: {m.statusbar_padding};
    min-height: {m.statusbar_h - 1}px;
    max-height: {m.statusbar_h - 1}px;
    font-size: 13px;
}}
QStatusBar::item {{
    border: none;
}}

/* ---------- 菜单 ---------- */
QMenu {{
    background: {p.SURFACE};
    border: 1px solid {p.BORDER};
    border-radius: 9px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 26px 6px 26px;
    border-radius: 6px;
    color: {p.TEXT};
}}
QMenu::item:selected {{
    background: {p.PRIMARY_SOFT};
    color: {p.TEXT};
}}
QMenu::item:disabled {{
    color: {p.TEXT_FAINT};
}}
QMenu::separator {{
    height: 1px;
    background: {p.BORDER};
    margin: 5px 8px;
}}
QMenu::section {{
    padding: 7px 10px 3px 10px;
    color: {p.TEXT_FAINT};
    font-size: 11px;
    font-weight: 600;
}}

/* ---------- 复选框 ---------- */
QCheckBox {{
    spacing: 6px;
    color: {p.TEXT_MUTED};
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {p.BORDER_STRONG};
    border-radius: 4px;
    background: {p.SURFACE};
}}
QCheckBox::indicator:hover {{
    border-color: {p.PRIMARY};
}}
QCheckBox::indicator:checked {{
    background: {p.PRIMARY};
    border-color: {p.PRIMARY};
    image: url({_check_path()});
}}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background: {p.HOVER};
    border: none;
    border-radius: 4px;
    text-align: center;
    color: {p.TEXT};
    height: 8px;
}}
QProgressBar::chunk {{
    background: {p.PRIMARY};
    border-radius: 4px;
}}
"""


# QSS 里引用的图片资源（ComboBox 箭头、CheckBox 勾）在运行时生成到临时目录，
# 避免依赖任何外部文件
_asset_dir: str | None = None


def _asset(name: str, svg: str) -> str:
    import os
    import tempfile

    global _asset_dir
    if _asset_dir is None:
        _asset_dir = tempfile.mkdtemp(prefix="linftp-qss-")
    path = os.path.join(_asset_dir, name)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
    return path.replace("\\", "/")


def _combo_arrow_path() -> str:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{Palette.TEXT_MUTED}" stroke-width="2.2" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M6.5 9.5L12 15l5.5-5.5"/></svg>'
    )
    return _asset("chevron_down.svg", svg)


def _check_path() -> str:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="#FFFFFF" stroke-width="3.2" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M4.5 12.5l5 5 10-10.5"/></svg>'
    )
    return _asset("check.svg", svg)


def _spin_up_path() -> str:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{Palette.TEXT_MUTED}" stroke-width="2.4" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M6.5 14.5L12 9l5.5 5.5"/></svg>'
    )
    return _asset("spin_up.svg", svg)


def _spin_down_path() -> str:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{Palette.TEXT_MUTED}" stroke-width="2.4" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M6.5 9.5L12 15l5.5-5.5"/></svg>'
    )
    return _asset("spin_down.svg", svg)


def _pick_font() -> str:
    """挑一个本机存在的现代无衬线字体，逐个回退到系统默认。"""
    from PySide6.QtGui import QFontDatabase

    families = set(QFontDatabase.families())
    for name in ("Noto Sans CJK SC", "Source Han Sans SC", "Noto Sans SC",
                 "Microsoft YaHei UI", "PingFang SC", "WenQuanYi Micro Hei",
                 "Droid Sans Fallback"):
        if name in families:
            return name
    return ""


def app_icon():
    """取应用图标。

    优先用安装到 hicolor 里的那份 PNG（多尺寸，窗口管理器会自己挑），
    没有就直接读工程里的 SVG。两边都缺时返回空 QIcon，
    窗口照样能开，只是没图标。
    """
    import os

    from PySide6.QtGui import QIcon

    name = "linftp"
    icon = QIcon.fromTheme(name)
    if not icon.isNull():
        return icon

    data_home = os.environ.get("XDG_DATA_HOME",
                               os.path.expanduser("~/.local/share"))
    for cand in (
        os.path.join(data_home, "icons", "hicolor", "scalable", "apps", f"{name}.svg"),
        os.path.join(data_home, "icons", "hicolor", "256x256", "apps", f"{name}.png"),
    ):
        if os.path.exists(cand):
            icon = QIcon(cand)
            if not icon.isNull():
                return icon

    # 源码目录里跑（未安装）时，直接用工程自带的 SVG
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(os.path.dirname(here), "assets", "linftp-icon.svg")
    if os.path.exists(local):
        return QIcon(local)
    return QIcon()


def apply(app) -> None:
    """给整个应用套上主题。"""
    from PySide6.QtGui import QFont

    family = _pick_font()
    font = QFont()
    if family:
        font.setFamily(family)
    font.setPixelSize(13)
    app.setFont(font)

    # Fusion 风格对各平台渲染最一致，也最容易被 QSS 覆盖
    app.setStyle("Fusion")
    app.setStyleSheet(qss())

    # 应用级图标：设一次就够，所有窗口和对话框自动继承，
    # 不用在四个 setWindowTitle 旁边各补一行 setWindowIcon
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
