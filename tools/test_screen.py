"""多屏摆位测试（无需人工点击）。

覆盖：
- center_on_parent 的坐标算法：相对父窗口居中、收进可用区、跟随父窗口屏幕
- place_on_primary 把窗口摆到主屏居中
- keep_on_screen 在窗口探出屏幕时做兜底校正
- LaunchPad 上屏后（showEvent）会再校正一次位置

离屏平台只有一块 800x800 的屏，所以算法部分用"注入假几何"的方式测；
真实双屏的端到端验证见 test_screen.py 末尾的真实显示检查（有屏才跑）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 用例里没人点"收进托盘"，真托盘一律不起，免得关窗口时弹出询问框卡住
os.environ["LINFP_NO_TRAY"] = "1"

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

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
    theme.apply(app)
    return app


def _single_screen_only() -> bool:
    """算法类用例只在单屏环境下跑。

    这些用例靠"打桩 frameGeometry"来构造各种父窗口位置，但
    center_on_parent 内部还要按 frameGeometry 反查屏幕、再拿那块屏的
    可用区做收拢。真实双屏下反查结果会和打桩坐标打架（打桩点落在
    副屏，可用区却是主屏的），属于测试构造本身的矛盾，不是被测代码
    的问题。所以双屏时跳过，交给 test_real_dual_screen 做端到端校验。
    """
    app = _app()
    if len(app.screens()) > 1:
        print("    （多屏环境，算法用例跳过，见端到端用例）")
        return False
    return True


def _fake_rect(widget, rect: QRect) -> None:
    """把窗口的 frameGeometry 打桩成指定矩形（离屏下没法真的多屏）。"""
    widget.frameGeometry = lambda: rect  # type: ignore[method-assign]


def test_center_relative_to_parent() -> None:
    """父窗口在某处时，子窗口应以父窗口为中心（未被屏幕边界收拢之前）。"""
    from linftp import theme

    if not _single_screen_only():
        return
    app = _app()
    avail = app.primaryScreen().availableGeometry()
    # 离屏平台屏很小，父窗口必须整体落在可用区内，否则居中结果会被 clamp，
    # 那是"贴边收拢"用例该管的事
    pw, ph = 700, 600
    px = avail.x() + 20
    py = avail.y() + 20
    parent = QWidget()
    parent.resize(pw, ph)
    _fake_rect(parent, QRect(px, py, pw, ph))
    parent.isVisible = lambda: True  # type: ignore[method-assign]

    child = QWidget()
    cw, ch = 300, 200
    child.resize(cw, ch)

    theme.center_on_parent(child, parent)
    pos = child.pos()
    exp_x = px + (pw - cw) // 2
    exp_y = py + (ph - ch) // 2
    assert abs(pos.x() - exp_x) <= 2, (pos.x(), exp_x)
    assert abs(pos.y() - exp_y) <= 2, (pos.y(), exp_y)

    child.deleteLater()
    parent.deleteLater()


def test_center_clamped_into_screen() -> None:
    """父窗口贴边时，子窗口不能探出屏幕可用区。"""
    from linftp import theme

    if not _single_screen_only():
        return
    app = _app()
    screen = app.primaryScreen()
    avail = screen.availableGeometry()

    parent = QWidget()
    # 父窗口贴着右下角，只有 200x150 露在屏内
    _fake_rect(parent, QRect(avail.right() - 200, avail.bottom() - 150, 1179, 800))
    parent.isVisible = lambda: True  # type: ignore[method-assign]

    child = QWidget()
    child.resize(580, 448)
    theme.center_on_parent(child, parent)

    pos = child.pos()
    assert avail.contains(QRect(pos, child.size())), (pos, child.size(), avail)

    child.deleteLater()
    parent.deleteLater()


def test_no_parent_centers_on_primary() -> None:
    """没有父窗口时，退化为在主屏可用区居中。"""
    from linftp import theme

    if not _single_screen_only():
        return
    app = _app()
    avail = app.primaryScreen().availableGeometry()
    child = QWidget()
    child.resize(600, 400)
    theme.center_on_parent(child, None)
    pos = child.pos()
    exp_x = avail.x() + (avail.width() - 600) // 2
    exp_y = avail.y() + (avail.height() - 400) // 2
    assert abs(pos.x() - exp_x) <= 2, (pos.x(), exp_x)
    assert abs(pos.y() - exp_y) <= 2, (pos.y(), exp_y)
    child.deleteLater()


def test_place_on_primary() -> None:
    """主窗口应被摆到主屏居中，而不是交给窗口管理器随便挑屏。"""
    from linftp import theme

    app = _app()
    avail = app.primaryScreen().availableGeometry()
    # 窗口要比屏幕小，否则"居中"本身就没意义（会被 clamp 到 0）
    ww = min(600, avail.width() - 100)
    wh = min(400, avail.height() - 100)
    w = QWidget()
    w.resize(ww, wh)
    theme.place_on_primary(w)
    pos = w.pos()
    exp_x = avail.x() + (avail.width() - ww) // 2
    exp_y = avail.y() + (avail.height() - wh) // 2
    assert abs(pos.x() - exp_x) <= 2, (pos.x(), exp_x)
    assert abs(pos.y() - exp_y) <= 2, (pos.y(), exp_y)
    assert avail.contains(QRect(pos, w.size())), (pos, w.size(), avail)
    w.deleteLater()


def test_keep_on_screen_reels_in() -> None:
    """窗口落在屏幕外时，keep_on_screen 应把它拉回来。"""
    from linftp import theme

    app = _app()
    avail = app.primaryScreen().availableGeometry()
    w = QWidget()
    w.resize(400, 300)
    # 故意摆到屏幕右侧外面
    w.move(avail.right() + 500, avail.top() + 100)
    theme.keep_on_screen(w)
    assert avail.contains(QRect(w.pos(), w.size())), (w.pos(), w.size(), avail)

    # 已经在屏内时不该乱动
    w2 = QWidget()
    w2.resize(400, 300)
    w2.move(avail.x() + 20, avail.y() + 20)
    before = w2.pos()
    theme.keep_on_screen(w2)
    assert w2.pos() == before, (w2.pos(), before)

    w.deleteLater()
    w2.deleteLater()


def test_launchpad_showevent_recenters() -> None:
    """LaunchPad 上屏后应再校正一次，覆盖窗口管理器的擅自挪动。"""
    if not _single_screen_only():
        return
    conf = tempfile.mkdtemp(prefix="linftp-screen-")
    os.environ["LINFP_CONF_DIR"] = conf
    import importlib

    import linftp.sites

    importlib.reload(linftp.sites)
    import linftp.bookmarks

    importlib.reload(linftp.bookmarks)
    import linftp.launchpad

    importlib.reload(linftp.launchpad)

    app = _app()
    from linftp import theme
    from linftp.backend import SessionConfig
    from linftp.bookmarks import BookmarkStore

    parent = QWidget()
    parent.resize(1179, 800)
    app.processEvents()
    avail = app.primaryScreen().availableGeometry()
    _fake_rect(parent, QRect(avail.x() + 40, avail.y() + 40, 1179, 800))
    parent.show()
    parent.isVisible = lambda: True  # type: ignore[method-assign]

    bms = [BookmarkStore.make_local("A", tempfile.mkdtemp())]
    recents = [SessionConfig(protocol="ftp", host="h1", port=21, username="u")]
    dlg = linftp.launchpad.LaunchPad(bms, recents, parent)

    # 先丢到一个离谱的位置，再走 show() 触发 showEvent
    dlg.move(-5000, -5000)
    dlg.show()
    for _ in range(3):
        app.processEvents()

    pos = dlg.pos()
    assert pos.x() > -1000 and pos.y() > -1000, f"showEvent 没有校正位置：{pos}"
    assert avail.contains(QRect(pos, dlg.size())), (pos, dlg.size(), avail)

    dlg.close()
    dlg.deleteLater()
    parent.close()
    parent.deleteLater()
    app.processEvents()
    theme.set_compact(False)


def test_real_dual_screen() -> None:
    """真实双屏下的端到端校验（只有一块屏时跳过）。"""
    from linftp import theme

    app = _app()
    screens = app.screens()
    if len(screens) < 2:
        print("    （当前只有一块屏，跳过真实双屏校验）")
        return

    import importlib

    conf = tempfile.mkdtemp(prefix="linftp-screen2-")
    os.environ["LINFP_CONF_DIR"] = conf
    import linftp.sites

    importlib.reload(linftp.sites)
    import linftp.bookmarks

    importlib.reload(linftp.bookmarks)
    import linftp.mainwindow

    importlib.reload(linftp.mainwindow)
    import linftp.launchpad

    importlib.reload(linftp.launchpad)

    mw_mod = linftp.mainwindow
    lp_mod = linftp.launchpad

    win = mw_mod.MainWindow()
    theme.place_on_primary(win)
    win.show()
    for _ in range(5):
        app.processEvents()
    assert win.screen() is not None
    main_screen = win.screen().name()
    assert main_screen == app.primaryScreen().name(), \
        f"主窗口没落在主屏：{main_screen} vs {app.primaryScreen().name()}"

    dlg = lp_mod.LaunchPad(win.bookmarks.items(), win.store.recents(), win)
    dlg.show()
    theme.center_on_parent(dlg, win)
    for _ in range(5):
        app.processEvents()
    assert dlg.screen() is not None
    assert dlg.screen().name() == main_screen, \
        f"启动页跑到了别的屏：{dlg.screen().name()} vs {main_screen}"

    dlg.close()
    dlg.deleteLater()
    try:
        win._keepalive.stop()
        win.queue.shutdown()
    except Exception:  # noqa: BLE001
        pass
    win.close()
    win.deleteLater()
    app.processEvents()


def main() -> int:
    print("--- 多屏摆位测试 ---")
    check("相对父窗口居中", test_center_relative_to_parent)
    check("贴边时收进屏幕可用区", test_center_clamped_into_screen)
    check("无父窗口时在主屏居中", test_no_parent_centers_on_primary)
    check("主窗口初始摆到主屏", test_place_on_primary)
    check("窗口出屏时拉回可用区", test_keep_on_screen_reels_in)
    check("启动页上屏后再次校正（回归）", test_launchpad_showevent_recenters)
    check("真实双屏端到端同屏", test_real_dual_screen)
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
