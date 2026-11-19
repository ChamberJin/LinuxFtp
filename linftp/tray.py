"""系统托盘：关窗口不等于退程序。

WinSCP 的默认行为是关窗即退出，但上传/下载跑到一半时把窗口关掉、
进程也没了，任务就断了。所以关闭动作给三个选择：

    最小化到托盘（默认） / 退出程序 / 取消

选过一次并勾上“记住我的选择”，以后不再问；托盘菜单里随时能改回来。

本模块不认识 ``MainWindow``：托盘菜单只发信号，由主窗口接。
``normalize_close_pref`` / ``resolve_close_action`` / ``tray_tooltip``
都是纯函数，没有托盘的环境（CI、offscreen）也能单独测。
"""
from __future__ import annotations

import logging
import os

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from . import theme

log = logging.getLogger(__name__)

# 关闭动作的三个取值，同时也是 ui.json 里 close_action 的持久化取值
CLOSE_ASK = "ask"        # 每次询问
CLOSE_TRAY = "tray"      # 收进托盘，程序继续跑
CLOSE_QUIT = "quit"      # 直接退出
CLOSE_CANCEL = "cancel"  # 只作为对话框返回值，不写进偏好

_PREF_VALUES = (CLOSE_ASK, CLOSE_TRAY, CLOSE_QUIT)


# ---------------------------------------------------------------- 纯逻辑
def normalize_close_pref(value) -> str:
    """把偏好值收进合法集合。缺省、写坏、类型不对一律当作“每次询问”。"""
    if isinstance(value, str) and value in _PREF_VALUES:
        return value
    return CLOSE_ASK


def resolve_close_action(pref, tray_available: bool) -> str:
    """算出这一次点关闭该做什么。

    没有系统托盘（多半是没装托盘扩展的桌面）就别提“收进托盘”了 ——
    藏起来的窗口再也没有入口，只能算退出。
    """
    if not tray_available:
        return CLOSE_QUIT
    return normalize_close_pref(pref)


def running_hint(running: int) -> str:
    """托盘提示/对话框里那句“还有 N 个任务”的文案，N=0 时为空。"""
    if running <= 0:
        return ""
    return f"还有 {running} 个传输任务未完成"


def tray_tooltip(status: str, running: int = 0) -> str:
    """托盘图标悬停提示：连接状态 + 传输中任务数。"""
    text = (status or "").strip() or "未连接"
    if running > 0:
        text = f"{text} · {running} 个任务传输中"
    return f"LinFTP —— {text}"


def _tray_icon() -> QIcon:
    """托盘图标。主题里没有就用系统主题的兜底图标，都没有也不影响运行。"""
    icon = theme.app_icon()
    if icon.isNull():
        icon = QIcon.fromTheme("folder-remote")
    return icon


def disabled_by_env() -> bool:
    """``LINFP_NO_TRAY=1`` 时不起托盘。

    给三种场景留的后门：没装托盘扩展的桌面、只有裸 X 的远程会话，
    以及自动化测试 —— 测试里没有真人点"最小化到托盘"，弹出来只会卡住。
    """
    raw = os.environ.get("LINFP_NO_TRAY", "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- 对话框
class CloseDialog(QMessageBox):
    """“关闭窗口后要做什么”三选一。

    单独成类是为了留住三个自定义按钮的引用：PySide6 的
    ``QMessageBox.button()`` 只接受标准按钮，按角色取自定义按钮会报参数类型错。
    """

    def __init__(self, parent=None, running_tasks: int = 0):
        super().__init__(parent)
        self.setWindowTitle("关闭 LinFTP")
        self.setIcon(QMessageBox.Question)
        self.setText("关闭窗口后要做什么？")

        lines = ["最小化到托盘：程序继续在后台运行，点击托盘图标即可重新打开。",
                 "退出程序：断开连接并结束进程。"]
        hint = running_hint(running_tasks)
        body = "\n".join(lines)
        if hint:
            body = f"{hint}，收进托盘不会中断传输。\n\n{body}"
        self.setInformativeText(body)
        # QMessageBox 自己定尺寸，setMinimumWidth() 会被它忽略；
        # 想放宽只能在说明文字那个 QLabel 上使劲
        label = self.findChild(QLabel, "qt_msgbox_informativelabel")
        if label is not None:
            label.setMinimumWidth(360)

        self.btn_tray = self.addButton("最小化到托盘", QMessageBox.AcceptRole)
        self.btn_quit = self.addButton("退出程序", QMessageBox.DestructiveRole)
        self.btn_cancel = self.addButton("取消", QMessageBox.RejectRole)
        # 默认落在"收进托盘"上：误按回车也不至于把任务掐了
        self.setDefaultButton(self.btn_tray)
        self.setEscapeButton(self.btn_cancel)
        # 唯一会造成损失的那个按钮，标红让人停一下
        self.btn_quit.setStyleSheet(f"color: {theme.Palette.DANGER};")

        self.remember = QCheckBox("记住我的选择")
        self.remember.setToolTip("以后关闭窗口不再询问。想改回来：托盘菜单里取消勾选"
                                 "「关闭窗口时收进托盘」")
        self.setCheckBox(self.remember)

    def chosen_action(self) -> str:
        clicked = self.clickedButton()
        if clicked is self.btn_tray:
            return CLOSE_TRAY
        if clicked is self.btn_quit:
            return CLOSE_QUIT
        return CLOSE_CANCEL


def ask_close_action(parent=None, running_tasks: int = 0) -> tuple[str, bool]:
    """问一次。返回 (动作, 是否记住)；取消时动作为 CLOSE_CANCEL。"""
    dlg = CloseDialog(parent, running_tasks)
    dlg.exec()
    action = dlg.chosen_action()
    remember = action != CLOSE_CANCEL and dlg.remember.isChecked()
    return action, remember


# ---------------------------------------------------------------- 托盘图标
class TrayIcon(QSystemTrayIcon):
    """托盘图标：左键单击/双击叫回主窗口，右键菜单里断开与退出。"""

    show_window_requested = Signal()
    disconnect_requested = Signal()
    quit_requested = Signal()
    close_tray_pref_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(_tray_icon(), parent)

        menu = QMenu(parent)
        self._menu = menu

        # 第一行只做状态展示，不可点
        self.act_status = menu.addAction("未连接")
        self.act_status.setEnabled(False)
        menu.addSeparator()

        self.act_show = menu.addAction("打开主窗口")
        self.act_show.triggered.connect(lambda *_: self.show_window_requested.emit())
        self.act_disconnect = menu.addAction("断开连接")
        self.act_disconnect.setEnabled(False)
        self.act_disconnect.triggered.connect(
            lambda *_: self.disconnect_requested.emit())
        menu.addSeparator()

        self.act_close_tray = menu.addAction("关闭窗口时收进托盘")
        self.act_close_tray.setCheckable(True)
        self.act_close_tray.setToolTip(
            "勾选后关闭窗口只是收进托盘，程序继续运行；"
            "取消勾选则每次关闭都先询问")
        self.act_close_tray.toggled.connect(self._on_close_tray_toggled)
        menu.addSeparator()

        self.act_quit = menu.addAction("退出")
        self.act_quit.triggered.connect(lambda *_: self.quit_requested.emit())

        # 默认动作在菜单里会加粗显示，双击托盘图标时也是它
        menu.setDefaultAction(self.act_show)
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    # ------------------------------------------------------------ 对外
    @staticmethod
    def available() -> bool:
        return not disabled_by_env() and QSystemTrayIcon.isSystemTrayAvailable()

    def set_status(self, text: str, connected: bool, running: int = 0) -> None:
        """同步连接状态：菜单首行文字、提示气泡、断开项是否可用。"""
        label = (text or "").strip() or "未连接"
        if running > 0:
            label = f"{label} · {running} 个任务传输中"
        self.act_status.setText(label)
        self.act_disconnect.setEnabled(bool(connected))
        self.setToolTip(tray_tooltip(text, running))

    def set_close_tray_pref(self, on: bool) -> None:
        """回显偏好。程序设置不该反过来触发“用户改了偏好”的信号。"""
        self.act_close_tray.blockSignals(True)
        self.act_close_tray.setChecked(bool(on))
        self.act_close_tray.blockSignals(False)

    def notify(self, title: str, body: str) -> None:
        """系统通知。桌面不支持气泡提示时静默跳过。"""
        if not self.supportsMessages():
            return
        self.showMessage(title, body, QSystemTrayIcon.Information, 4000)

    # ------------------------------------------------------------ 内部
    def _on_activated(self, reason) -> None:
        # 右键（Context）交给菜单，左键单击/双击都当“打开窗口”
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window_requested.emit()

    def _on_close_tray_toggled(self, on: bool) -> None:
        self.close_tray_pref_changed.emit(bool(on))
