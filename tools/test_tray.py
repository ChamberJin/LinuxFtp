"""关闭窗口收进托盘 / 从托盘打开测试（无需人工点击，也不需要真托盘）。

覆盖：
- 关闭偏好取值归一化：合法值原样、缺省或写坏一律回落到"每次询问"
- 没有系统托盘时，不管偏好怎么写都只能退出（否则窗口藏起来就没入口了）
- 托盘提示文案：连接状态 + 传输中任务数
- 偏好落在 ui.json 里，且不冲掉窗口位置 / 紧凑模式
- 关闭对话框：三个按钮、记住勾选框、默认按钮、任务数文案
- 托盘菜单：偏好回显不触发信号、状态同步到"断开连接"与提示气泡
- 偏好=tray：关窗口只是藏起来，保活定时器与传输队列都还在（这才是"不关程序"）
- 从托盘叫回窗口 / 从托盘退出
- 偏好=ask：选取消不关窗口，选托盘 + 记住会把偏好写下去
- 有任务在跑时退出要二次确认，选"否"窗口留着

窗口用 offscreen 平台建，托盘用桩对象顶替 —— offscreen 下本来就没有托盘，
正好顺便验证"没有托盘就退回退出"这条兜底路径。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 测试里没有真人点"最小化到托盘"，真托盘一律不起，需要托盘的用例用桩对象顶替
os.environ["LINFP_NO_TRAY"] = "1"
# 必须在 import linftp.sites 之前设好：CONFIG_DIR 是导入时求值的
_CONF = tempfile.mkdtemp(prefix="linftp-tray-")
os.environ["LINFP_CONF_DIR"] = _CONF

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

passed = failed = 0


def check(name: str, fn) -> None:
    global passed, failed
    try:
        fn()
        print(f"  PASS  {name}")
        passed += 1
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        failed += 1


def _app():
    from linftp import theme

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    theme.apply(app)
    return app


def _reset() -> None:
    from linftp.mainwindow import _pref_path

    try:
        os.remove(_pref_path())
    except FileNotFoundError:
        pass


class StubTray:
    """顶替 TrayIcon：只记下主窗口调用过什么，不碰真实托盘。"""

    def __init__(self):
        self.status = None
        self.notifications = []
        self.pref = None
        self.hidden = False

    def set_status(self, text: str, connected: bool, running: int = 0) -> None:
        self.status = (text, connected, running)

    def set_close_tray_pref(self, on: bool) -> None:
        self.pref = bool(on)

    def notify(self, title: str, body: str) -> None:
        self.notifications.append((title, body))

    def hide(self) -> None:
        self.hidden = True


def _new_window():
    from linftp.mainwindow import MainWindow

    return MainWindow()


def _dispose(w) -> None:
    """收尾：别把保活定时器和后台线程池留给下一个用例。"""
    app = QApplication.instance()
    w._running_count = lambda: 0       # 有的用例把任务数打了桩，收尾时别再弹确认框
    try:
        w._keepalive.stop()
        w._quitting = True
        w.close()
        w.queue.shutdown()
        w.deleteLater()
    except Exception:  # noqa: BLE001
        pass
    if app is not None:
        app.processEvents()


# ---------------------------------------------------------------- 纯逻辑
def test_normalize_close_pref() -> None:
    from linftp.tray import (CLOSE_ASK, CLOSE_QUIT, CLOSE_TRAY,
                             normalize_close_pref)

    assert normalize_close_pref(CLOSE_TRAY) == CLOSE_TRAY
    assert normalize_close_pref(CLOSE_QUIT) == CLOSE_QUIT
    assert normalize_close_pref(CLOSE_ASK) == CLOSE_ASK
    for bad in (None, "", "TRAY", "close", 0, 1, [], {}, True):
        assert normalize_close_pref(bad) == CLOSE_ASK, f"坏值没被收住：{bad!r}"


def test_resolve_close_action() -> None:
    from linftp.tray import (CLOSE_ASK, CLOSE_QUIT, CLOSE_TRAY,
                             resolve_close_action)

    # 没有托盘：偏好写什么都不作数，只能退出
    for pref in (CLOSE_ASK, CLOSE_TRAY, CLOSE_QUIT, "garbage", None):
        assert resolve_close_action(pref, tray_available=False) == CLOSE_QUIT, pref
    # 有托盘：照偏好走，ask 继续 ask
    assert resolve_close_action(CLOSE_TRAY, True) == CLOSE_TRAY
    assert resolve_close_action(CLOSE_QUIT, True) == CLOSE_QUIT
    assert resolve_close_action(CLOSE_ASK, True) == CLOSE_ASK
    assert resolve_close_action("garbage", True) == CLOSE_ASK


def test_running_hint_and_tooltip() -> None:
    from linftp.tray import running_hint, tray_tooltip

    assert running_hint(0) == ""
    assert running_hint(-1) == ""
    assert "3" in running_hint(3)

    assert tray_tooltip("") == "LinFTP —— 未连接"
    assert tray_tooltip("   ") == "LinFTP —— 未连接"
    assert tray_tooltip("已连接 FTP u@h:21", 0) == "LinFTP —— 已连接 FTP u@h:21"
    tip = tray_tooltip("已连接 FTP u@h:21", 2)
    assert "2 个任务传输中" in tip and tip.startswith("LinFTP —— "), tip


def test_close_pref_persists() -> None:
    from linftp.mainwindow import (_save_prefs, load_close_pref, load_prefs,
                                   save_close_pref, save_compact_pref)
    from linftp.tray import CLOSE_ASK, CLOSE_TRAY

    _reset()
    assert load_close_pref() == CLOSE_ASK, "没存过时应默认每次询问"

    save_close_pref(CLOSE_TRAY)
    assert load_close_pref() == CLOSE_TRAY

    # 窗口位置与紧凑模式写在同一个文件里，谁都不能把关闭偏好冲掉
    _save_prefs({"close_action": CLOSE_TRAY,
                 "window": {"x": 1, "y": 2, "w": 700, "h": 500}})
    save_compact_pref(True)
    assert load_close_pref() == CLOSE_TRAY, "改紧凑模式把关闭偏好冲掉了"
    assert "window" in load_prefs(), "改关闭偏好把窗口位置冲掉了"

    save_close_pref(CLOSE_TRAY)
    assert load_prefs().get("compact") is True, "改关闭偏好把紧凑模式冲掉了"

    _save_prefs({"close_action": "garbage"})
    assert load_close_pref() == CLOSE_ASK, "坏值没回落到每次询问"
    _reset()


def test_close_dialog_shape() -> None:
    from linftp.tray import CLOSE_CANCEL, CLOSE_QUIT, CLOSE_TRAY, CloseDialog

    app = _app()
    box = CloseDialog(None, running_tasks=3)
    try:
        assert box.btn_tray.text() == "最小化到托盘"
        assert box.btn_quit.text() == "退出程序"
        assert box.btn_cancel.text() == "取消"
        assert box.defaultButton() is box.btn_tray, \
            "默认按钮应该是“最小化到托盘”（误按回车不至于把任务掐了）"
        assert box.escapeButton() is box.btn_cancel, "Esc 应该等于取消"
        assert box.remember.isChecked() is False, "“记住”默认不该勾上"
        text = box.text() + box.informativeText()
        assert "3" in text, f"任务数没写进说明：{text}"

        # 没点任何按钮时按取消处理
        assert box.chosen_action() == CLOSE_CANCEL

        # 点哪个按钮就该返回哪个动作
        box.btn_tray.click()
        assert box.chosen_action() == CLOSE_TRAY
        assert box.remember.isChecked() is False
        box.btn_quit.click()
        assert box.chosen_action() == CLOSE_QUIT
    finally:
        box.deleteLater()
        app.processEvents()


def test_tray_icon_pref_and_status() -> None:
    from linftp.tray import TrayIcon

    app = _app()
    t = TrayIcon()
    try:
        got = []
        t.close_tray_pref_changed.connect(got.append)

        # 程序回显偏好不该被当成"用户改了偏好"
        t.set_close_tray_pref(True)
        assert t.act_close_tray.isChecked() is True
        assert got == [], f"回显触发了信号：{got}"

        # 用户点勾选要发信号出去
        t.act_close_tray.setChecked(False)
        assert got == [False], f"用户操作没发信号：{got}"

        t.set_status("已连接 FTP u@h:21", True, 2)
        assert t.act_disconnect.isEnabled() is True
        assert "2 个任务传输中" in t.toolTip(), t.toolTip()
        assert "2 个任务" in t.act_status.text(), t.act_status.text()

        t.set_status("未连接", False)
        assert t.act_disconnect.isEnabled() is False
        assert t.toolTip() == "LinFTP —— 未连接", t.toolTip()
        assert t.act_status.text() == "未连接"
    finally:
        t.deleteLater()
        app.processEvents()


# ---------------------------------------------------------------- 主窗口
def test_no_tray_falls_back_to_quit() -> None:
    """offscreen 平台没有托盘：不能给"收进托盘"这个选项，关窗即退出。"""
    from linftp.mainwindow import save_close_pref
    from linftp.tray import CLOSE_TRAY

    _reset()
    save_close_pref(CLOSE_TRAY)
    app = _app()
    w = _new_window()
    w.show()
    app.processEvents()
    try:
        assert w.tray is None, "offscreen 下不该拿到真托盘"
        closed = w.close()
        app.processEvents()
        assert closed is True, "没有托盘时窗口应该真的关掉"
        assert w._keepalive.isActive() is False
    finally:
        _dispose(w)


def test_close_hides_to_tray_but_keeps_running() -> None:
    from linftp.mainwindow import save_close_pref
    from linftp.tray import CLOSE_TRAY

    _reset()
    save_close_pref(CLOSE_TRAY)
    app = _app()
    w = _new_window()
    w.tray = StubTray()
    w.show()
    app.processEvents()
    try:
        closed = w.close()
        app.processEvents()
        assert closed is False, "窗口被真的关掉了"
        assert w.isVisible() is False, "窗口没藏起来"
        assert w._keepalive.isActive() is True, "收进托盘后保活定时器停了"
        assert w.queue is not None
        assert w.tray.notifications, "收进托盘没给提示"
        assert "仍在后台运行" in w.tray.notifications[0][1], w.tray.notifications
        # 窗口位置要顺手记下来，"收进托盘 → 从托盘退出"这条路上不能丢
        import json

        from linftp.mainwindow import _pref_path

        raw = json.load(open(_pref_path(), encoding="utf-8"))
        assert "window" in raw, raw
    finally:
        _dispose(w)


def test_show_from_tray() -> None:
    from linftp.mainwindow import save_close_pref
    from linftp.tray import CLOSE_TRAY

    _reset()
    save_close_pref(CLOSE_TRAY)
    app = _app()
    w = _new_window()
    w.tray = StubTray()
    w.show()
    app.processEvents()
    try:
        w.close()
        app.processEvents()
        assert w.isVisible() is False

        w.show_from_tray()
        app.processEvents()
        assert w.isVisible() is True, "从托盘没能叫回窗口"
    finally:
        _dispose(w)


def test_quit_from_tray() -> None:
    from linftp.mainwindow import save_close_pref
    from linftp.tray import CLOSE_TRAY

    _reset()
    save_close_pref(CLOSE_TRAY)
    app = _app()
    w = _new_window()
    tray = StubTray()
    w.tray = tray
    w.show()
    app.processEvents()
    try:
        w.close()                       # 先收进托盘
        app.processEvents()
        assert w.isVisible() is False

        w.quit_from_tray()              # 再从托盘退出
        app.processEvents()
        assert w._quitting is True
        assert w.isVisible() is False, "退出后窗口还在"
        assert w._keepalive.isActive() is False, "退出时没停保活定时器"
        assert tray.hidden is True, "退出时没把托盘图标摘掉（会留个点不动的空图标）"
    finally:
        _dispose(w)


def _patch_ask(monkeypatch_action):
    """把 mainwindow 里的 ask_close_action 换成返回固定结果的假函数。"""
    import linftp.mainwindow as mw

    orig = mw.ask_close_action
    mw.ask_close_action = lambda parent=None, running=0: monkeypatch_action
    return orig


def test_ask_cancel_keeps_window() -> None:
    from linftp.mainwindow import load_close_pref
    from linftp.tray import CLOSE_ASK, CLOSE_CANCEL

    import linftp.mainwindow as mw

    _reset()
    app = _app()
    w = _new_window()
    w.tray = StubTray()
    w.show()
    app.processEvents()
    orig = _patch_ask((CLOSE_CANCEL, False))
    try:
        closed = w.close()
        app.processEvents()
        assert closed is False, "点了取消窗口却关了"
        assert w.isVisible() is True
        assert load_close_pref() == CLOSE_ASK
    finally:
        mw.ask_close_action = orig
        _dispose(w)


def test_ask_tray_with_remember() -> None:
    from linftp.mainwindow import load_close_pref
    from linftp.tray import CLOSE_TRAY

    import linftp.mainwindow as mw

    _reset()
    app = _app()
    w = _new_window()
    w.tray = StubTray()
    w.show()
    app.processEvents()
    orig = _patch_ask((CLOSE_TRAY, True))
    try:
        closed = w.close()
        app.processEvents()
        assert closed is False
        assert w.isVisible() is False
        assert load_close_pref() == CLOSE_TRAY, "勾了“记住”，偏好没写下去"
        assert w.tray.pref is True, "托盘菜单的勾选状态没跟着更新"
    finally:
        mw.ask_close_action = orig
        _dispose(w)


def test_quit_needs_confirm_when_tasks_running() -> None:
    from linftp.mainwindow import load_close_pref, save_close_pref
    from linftp.tray import CLOSE_QUIT

    import linftp.mainwindow as mw

    _reset()
    save_close_pref(CLOSE_QUIT)
    app = _app()
    w = _new_window()
    w.tray = StubTray()
    w.show()
    app.processEvents()

    asked = []

    class FakeMB:
        Yes = QMessageBox.Yes
        No = QMessageBox.No

        @staticmethod
        def question(*args, **kwargs):
            asked.append(args[1] if len(args) > 1 else "")
            return FakeMB.No

    orig_mb = mw.QMessageBox
    mw.QMessageBox = FakeMB
    w._running_count = lambda: 2
    try:
        closed = w.close()
        app.processEvents()
        assert closed is False, "还有任务在跑，选“否”却退出了"
        assert w.isVisible() is True, "窗口没了"
        assert asked, "没弹二次确认"
        assert load_close_pref() == CLOSE_QUIT
    finally:
        mw.QMessageBox = orig_mb
        _dispose(w)


def test_quit_proceeds_when_confirmed() -> None:
    from linftp.mainwindow import save_close_pref
    from linftp.tray import CLOSE_QUIT

    import linftp.mainwindow as mw

    _reset()
    save_close_pref(CLOSE_QUIT)
    app = _app()
    w = _new_window()
    tray = StubTray()
    w.tray = tray
    w.show()
    app.processEvents()

    class FakeMB:
        Yes = QMessageBox.Yes
        No = QMessageBox.No

        @staticmethod
        def question(*args, **kwargs):
            return FakeMB.Yes

    orig_mb = mw.QMessageBox
    mw.QMessageBox = FakeMB
    w._running_count = lambda: 1
    try:
        closed = w.close()
        app.processEvents()
        assert closed is True, "确认了却没退出"
        assert w._keepalive.isActive() is False
        assert tray.hidden is True
    finally:
        mw.QMessageBox = orig_mb
        _dispose(w)


def main() -> int:
    print("--- 关闭收进托盘 / 从托盘打开测试 ---")
    check("关闭偏好取值归一化", test_normalize_close_pref)
    check("没有托盘时只能退出", test_resolve_close_action)
    check("提示文案（状态 + 任务数）", test_running_hint_and_tooltip)
    check("关闭偏好持久化且不互相覆盖", test_close_pref_persists)
    check("关闭对话框的结构与默认按钮", test_close_dialog_shape)
    check("托盘菜单：偏好回显与状态同步", test_tray_icon_pref_and_status)
    check("无托盘时关窗口照旧退出", test_no_tray_falls_back_to_quit)
    check("关窗口只收进托盘，程序继续跑", test_close_hides_to_tray_but_keeps_running)
    check("从托盘叫回窗口", test_show_from_tray)
    check("从托盘退出", test_quit_from_tray)
    check("询问时选取消不关窗口", test_ask_cancel_keeps_window)
    check("询问时选托盘并记住", test_ask_tray_with_remember)
    check("有任务时退出要二次确认（选否）", test_quit_needs_confirm_when_tasks_running)
    check("有任务时确认后正常退出", test_quit_proceeds_when_confirmed)
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
