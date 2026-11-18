"""紧凑模式测试（无需人工点击）。

覆盖：
- 两套尺寸规格的差异是真实生效的（行高、列宽、边距）
- 切换后 QSS 跟着变、面板尺寸跟着变
- 来回切换不会累积漂移（尺寸完全可逆）
- 分栏比例在切换前后保持
- 偏好落盘并能读回
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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


def _fresh():
    """每个用例独立配置目录 + 干净的尺寸状态。"""
    conf = tempfile.mkdtemp(prefix="linftp-compact-")
    os.environ["LINFP_CONF_DIR"] = conf
    import linftp.sites

    importlib.reload(linftp.sites)
    import linftp.bookmarks

    importlib.reload(linftp.bookmarks)
    import linftp.launchpad

    importlib.reload(linftp.launchpad)
    import linftp.mainwindow

    importlib.reload(linftp.mainwindow)

    from linftp import theme

    theme.set_compact(False)
    return linftp.mainwindow, conf


def _app():
    from PySide6.QtWidgets import QApplication

    from linftp import theme

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


def _cleanup(w) -> None:
    try:
        w._keepalive.stop()
    except Exception:  # noqa: BLE001
        pass
    try:
        if w.session:
            w.session.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        w.queue.shutdown()
    except Exception:  # noqa: BLE001
        pass
    w.close()


def test_metrics() -> None:
    from linftp import theme

    std, cpt = theme.STANDARD, theme.COMPACT
    # 紧凑模式下每一项"纵向尺寸"都必须更小或相等
    for attr in ("pane_row_h", "path_h", "filter_h", "nav_btn", "side_label_h",
                 "card_radius", "splitter_handle", "queue_empty_h",
                 "log_collapsed_h", "toolbar_icon", "statusbar_h",
                 "statusbar_dot_size"):
        assert getattr(cpt, attr) < getattr(std, attr), \
            f"{attr}: 紧凑 {getattr(cpt, attr)} 未小于标准 {getattr(std, attr)}"

    # 状态栏不能压到切字：13px 字体在 19px 高度下还剩上下各 3px
    assert cpt.statusbar_h >= 18, cpt.statusbar_h
    assert std.statusbar_h == 24, std.statusbar_h

    # 列宽收窄但不能把文本"收没"：时间列至少 118（"2026-11-16 15:06" 约 102px）
    assert cpt.col_mtime >= 118, cpt.col_mtime
    assert cpt.col_perm >= 90, cpt.col_perm
    assert cpt.col_mtime <= std.col_mtime

    assert theme.set_compact(True) is cpt
    assert theme.metrics is cpt
    assert theme.set_compact(False) is std
    assert theme.metrics is std


def test_qss_differs() -> None:
    from linftp import theme

    theme.set_compact(True)
    compact_qss = theme.qss()
    theme.set_compact(False)
    std_qss = theme.qss()

    assert compact_qss != std_qss, "两套 QSS 不应完全相同"
    # 紧凑模式的表格单元格 padding 应更小
    assert "padding: 0px 4px" in compact_qss
    assert "padding: 2px 4px" in std_qss


def test_switch_applies() -> None:
    mw_mod, _ = _fresh()
    app = _app()
    w = mw_mod.MainWindow()
    w.resize(1180, 760)
    w.show()
    for _ in range(3):
        app.processEvents()
    w._apply_initial_layout()
    app.processEvents()

    vh = w.local_pane.view.verticalHeader()
    std_row = vh.defaultSectionSize()
    std_mtime = w.local_pane.view.columnWidth(3)
    std_margin = w.local_pane.root_layout.contentsMargins().left()
    std_card_margin = w._queue_panel.body.contentsMargins().top()
    std_btn = w.local_pane.btn_up.width()

    w.set_compact(True)
    for _ in range(4):
        app.processEvents()

    assert w.compact is True
    assert vh.defaultSectionSize() < std_row, (vh.defaultSectionSize(), std_row)
    assert w.local_pane.view.columnWidth(3) < std_mtime
    assert w.local_pane.root_layout.contentsMargins().left() < std_margin
    assert w._queue_panel.body.contentsMargins().top() < std_card_margin
    assert w.local_pane.btn_up.width() < std_btn
    assert w.btn_compact.text() == "紧凑"

    # 切回去要完全回到原样（不能残留紧凑值）
    w.set_compact(False)
    for _ in range(4):
        app.processEvents()
    assert vh.defaultSectionSize() == std_row, vh.defaultSectionSize()
    assert w.local_pane.view.columnWidth(3) == std_mtime
    assert w.local_pane.root_layout.contentsMargins().left() == std_margin
    assert w.local_pane.btn_up.width() == std_btn
    assert w.btn_compact.text() == "标准"
    _cleanup(w)


def test_splitter_ratio_kept() -> None:
    """用户手动拖过的分栏比例，切换模式后不该被重置成 1:1。"""
    mw_mod, _ = _fresh()
    app = _app()
    w = mw_mod.MainWindow()
    w.resize(1180, 760)
    w.show()
    for _ in range(3):
        app.processEvents()
    w._apply_initial_layout()
    app.processEvents()

    w._pane_splitter.setSizes([700, 300])
    app.processEvents()
    before = w._pane_splitter.sizes()
    ratio_before = before[0] / sum(before)

    w.set_compact(True)
    for _ in range(4):
        app.processEvents()
    after = w._pane_splitter.sizes()
    ratio_after = after[0] / sum(after)
    assert abs(ratio_after - ratio_before) < 0.05, (ratio_before, ratio_after)
    _cleanup(w)


def test_pref_persisted() -> None:
    mw_mod, conf = _fresh()
    app = _app()
    w = mw_mod.MainWindow()
    assert w.compact is False, "首次启动应为标准模式"

    w.set_compact(True)
    app.processEvents()
    pref_file = os.path.join(conf, "ui.json")
    assert os.path.exists(pref_file), "偏好文件应已写出"

    # 新建一个窗口应直接沿用紧凑模式
    w2 = mw_mod.MainWindow()
    assert w2.compact is True, "重启后应记住紧凑模式"
    from linftp import theme

    assert theme.metrics is theme.COMPACT

    # 切回标准并落盘，下一个窗口应回到标准
    w2.set_compact(False)
    app.processEvents()
    w3 = mw_mod.MainWindow()
    assert w3.compact is False
    assert theme.metrics is theme.STANDARD

    for x in (w, w2, w3):
        _cleanup(x)


def test_launchpad_follows() -> None:
    lp_mod, _ = _fresh()
    app = _app()
    from linftp import theme
    from linftp.backend import SessionConfig
    from linftp.bookmarks import BookmarkStore

    bms = [BookmarkStore.make_local("A", tempfile.mkdtemp())]
    recents = [SessionConfig(protocol="ftp", host="h1", port=21, username="u")]

    theme.set_compact(False)
    std = lp_mod.LaunchPad(bms, recents, None)
    h_std = std.list.item(0).sizeHint().height()
    std.close()

    theme.set_compact(True)
    cpt = lp_mod.LaunchPad(bms, recents, None)
    h_cpt = cpt.list.item(0).sizeHint().height()
    cpt.close()

    assert h_cpt < h_std, (h_cpt, h_std)
    theme.set_compact(False)


def test_statusbar_height() -> None:
    """状态栏要跟着模式变矮，且切回去能复原。

    原先 QStatusBar 没被纳入 Metrics，QSS 里写死 `padding: 2px 4px`，
    Qt 默认高度 24px 两种模式纹丝不动——紧凑模式下底部那一条显得特别厚。
    """
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    from linftp import theme

    mw_mod, _ = _fresh()
    app = _app()

    theme.set_compact(False)
    w = mw_mod.MainWindow()
    w.resize(1180, 780)
    w.show()
    for _ in range(4):
        app.processEvents()
    w._apply_initial_layout()
    app.processEvents()

    bar = w.statusBar()
    std_h = bar.height()
    assert std_h == theme.STANDARD.statusbar_h, (std_h, theme.STANDARD.statusbar_h)

    w.set_compact(True)
    for _ in range(4):
        app.processEvents()
    cpt_h = bar.height()
    assert cpt_h == theme.COMPACT.statusbar_h, (cpt_h, theme.COMPACT.statusbar_h)
    assert cpt_h < std_h, f"紧凑状态栏没变矮：{cpt_h} vs {std_h}"

    # 圆点字号也要跟着缩，否则 19px 高度里塞 12px 的圆点会顶满
    assert f"font-size: {theme.COMPACT.statusbar_dot_size}px" in w.conn_dot.styleSheet()

    # 连接状态下文字不能被裁：QLabel 高度应接近状态栏内容高度
    w._set_status("已连接 ftp://example.com:21", True)
    for _ in range(2):
        app.processEvents()
    assert w.status_label.height() <= cpt_h, (w.status_label.height(), cpt_h)
    assert w.status_label.text() == "已连接 ftp://example.com:21"

    # 切回标准要还原
    w.set_compact(False)
    for _ in range(4):
        app.processEvents()
    assert bar.height() == std_h, (bar.height(), std_h)
    _cleanup(w)


def test_toolbar_compact_on_restart() -> None:
    """回归：重启后紧凑模式必须真的把工具栏压下去。

    之前踩的坑是 theme.apply() 先按标准规格生成了 QSS，随后
    set_compact() 只换了 metrics 却没重生成 QSS，于是图标缩小了
    但按钮 padding 还是标准值，工具栏高度一点没变——用户看到的就是
    "关掉再打开，紧凑模式对工具栏无效"。
    """
    from PySide6.QtWidgets import QToolBar

    from linftp import theme

    mw_mod, conf = _fresh()
    app = _app()

    # 模拟"上次退出时是紧凑模式"
    mw_mod.save_compact_pref(True)

    w = mw_mod.MainWindow()
    w.resize(1180, 760)
    w.show()
    for _ in range(4):
        app.processEvents()

    assert w.compact is True, "应读回紧凑偏好"
    assert theme.metrics is theme.COMPACT

    # 关键断言：全局 QSS 必须已经是紧凑规格的那一份
    ss = app.styleSheet()
    assert theme.COMPACT.toolbtn_padding in ss, \
        f"QSS 未切到紧凑规格（toolbtn_padding={theme.COMPACT.toolbtn_padding!r}）"
    assert theme.COMPACT.toolbar_padding in ss, \
        f"QSS 未切到紧凑规格（toolbar_padding={theme.COMPACT.toolbar_padding!r}）"
    assert theme.STANDARD.toolbtn_padding not in ss, "QSS 里不该还留着标准 padding"

    tb = w.findChildren(QToolBar)[0]
    assert tb.iconSize().width() == theme.COMPACT.toolbar_icon
    assert tb.height() < 54, f"工具栏高度没压下去：{tb.height()}"
    assert w.btn_compact.height() < 34, f"工具栏按钮没压下去：{w.btn_compact.height()}"
    # 书签按钮也要跟着缩，不能写死 17
    assert w.btn_bookmark.iconSize().width() == theme.COMPACT.toolbar_icon

    # 反向校验：标准模式下同一批断言应给出更大的值
    w.set_compact(False)
    for _ in range(4):
        app.processEvents()
    assert theme.STANDARD.toolbtn_padding in app.styleSheet()
    assert tb.iconSize().width() == theme.STANDARD.toolbar_icon
    assert w.btn_bookmark.iconSize().width() == theme.STANDARD.toolbar_icon
    _cleanup(w)


def test_dialogs_follow_font() -> None:
    """回归：紧凑模式下新建的对话框要跟上当前应用字体。

    QDialog 的字体是构造时从父窗口快照下来的，之后 app.setFont()
    不会再传进去，所以必须显式同步一次，否则对话框保留旧字号。
    """
    from linftp import theme
    from linftp.bookmark_dialog import BookmarkDialog
    from linftp.bookmarks import BookmarkStore
    from linftp.dialogs import ConnectDialog

    _fresh()
    app = _app()
    theme.set_compact(True)

    dlg = ConnectDialog()
    assert dlg.font().pixelSize() == app.font().pixelSize(), \
        f"连接对话框字号未同步：{dlg.font().pixelSize()} vs {app.font().pixelSize()}"
    dlg.close()
    dlg.deleteLater()

    bm = BookmarkStore()
    bd = BookmarkDialog(bm)
    assert bd.font().pixelSize() == app.font().pixelSize(), \
        f"书签对话框字号未同步：{bd.font().pixelSize()}"
    bd.close()
    bd.deleteLater()
    app.processEvents()
    theme.set_compact(False)


def main() -> int:
    print("--- 紧凑模式测试 ---")
    check("两套尺寸规格的数值关系", test_metrics)
    check("QSS 随规格变化", test_qss_differs)
    check("切换后各面板尺寸真实生效且可逆", test_switch_applies)
    check("分栏比例切换前后保持一致", test_splitter_ratio_kept)
    check("偏好落盘并在新窗口生效", test_pref_persisted)
    check("启动页跟随紧凑模式", test_launchpad_follows)
    check("重启后工具栏真的变紧凑（回归）", test_toolbar_compact_on_restart)
    check("状态栏高度随模式变化且可逆", test_statusbar_height)
    check("对话框跟随当前字号（回归）", test_dialogs_follow_font)
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
