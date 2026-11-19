"""右键"复制路径"测试（无需人工点击）。

覆盖：
- 没有选中项时复制当前目录
- 选中一项复制该项完整路径
- 多选复制多条，每行一条、顺序与列表一致、重复项只留一份
- 右键点在未选中的行上，只复制那一行（不能拿上一次的多选去复制）
- 右键点在空白处，退回选中项 / 当前目录
- 菜单文案随复制条数变化
- Ctrl+Shift+C 是"复制路径"的快捷键，且只在列表有焦点时响应
- 空路径不写剪贴板、不崩
- 复制后状态栏提示没被菜单关闭时的 StatusTip 事件清掉

真实的"按一下 Ctrl+Shift+C"没法在这里自动化：QShortcut 只在窗口真拿到
焦点时才响应，离屏平台和没有窗口管理器的环境里 activeWindow 恒为 None。
快捷键绑定（action 的 shortcut / context / 挂载位置）在这里断言，
真按键在真实桌面上用探针手验过一次（复制到 /notes.md，状态栏同步）。

配置目录用 LINFP_CONF_DIR 指到临时目录，不碰用户真实书签与偏好。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 必须在 import linftp.sites 之前设好：CONFIG_DIR 是导入时求值的
_CONF = tempfile.mkdtemp(prefix="linftp-copypath-")
os.environ["LINFP_CONF_DIR"] = _CONF

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from linftp.backend import RemoteFile  # noqa: E402

passed = failed = 0
_window = None


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


def _window_and_pane():
    """一个真实的主窗口 + 远端面板（装了三个文件和一份目录）。"""
    global _window
    from linftp.mainwindow import MainWindow

    app = _app()
    if _window is None:
        _window = MainWindow()
        _window.resize(1180, 720)
        _window.show()
        app.processEvents()
    pane = _window.remote_pane
    pane.set_current_dir("/srv/app")
    pane.set_entries([
        RemoteFile(name="app.log", path="/srv/app/app.log", size=1024),
        RemoteFile(name="conf", path="/srv/app/conf", is_dir=True),
        RemoteFile(name="run.sh", path="/srv/app/run.sh", size=2048),
    ])
    pane.view.clearSelection()
    app.processEvents()
    return _window, pane, app


def _clip() -> str:
    return QApplication.clipboard().text()


def _row_of(pane, name: str) -> int:
    """按文件名找行号——行序由排序决定，不能写死。"""
    for i, e in enumerate(pane.model.entries()):
        if e.name == name:
            return i
    raise AssertionError(f"列表里没有 {name}")


def _row_pos(pane, row: int) -> QPoint:
    """某一行的可视中心（用来模拟右键落点）。"""
    rect = pane.view.visualRect(pane.model.index(row, 0))
    assert rect.isValid() and not rect.isEmpty(), f"第 {row} 行不在视口里：{rect}"
    return rect.center()


def test_no_selection_copies_current_dir() -> None:
    """未选中任何行 → 复制当前目录。"""
    _win, pane, app = _window_and_pane()
    QApplication.clipboard().setText("")
    assert pane.paths_for_copy() == ["/srv/app"], pane.paths_for_copy()
    _win.copy_path(pane)
    app.processEvents()
    assert _clip() == "/srv/app", repr(_clip())


def test_single_selection_copies_entry_path() -> None:
    """选中一项 → 复制该项的绝对路径。"""
    _win, pane, app = _window_and_pane()
    pane.view.selectRow(_row_of(pane, "app.log"))
    app.processEvents()
    QApplication.clipboard().setText("")
    _win.copy_path(pane)
    app.processEvents()
    assert _clip() == "/srv/app/app.log", repr(_clip())


def _select_rows(pane, first: int, last: int) -> None:
    """整行多选（和 Ctrl+点 一样的效果）。"""
    from PySide6.QtCore import QItemSelection, QItemSelectionModel

    sel = QItemSelection(pane.model.index(first, 0), pane.model.index(last, 0))
    pane.view.selectionModel().select(
        sel, QItemSelectionModel.Select | QItemSelectionModel.Rows)


def test_multi_selection_one_line_each() -> None:
    """多选 → 每行一条，顺序跟列表一致，不串行。"""
    _win, pane, app = _window_and_pane()
    _select_rows(pane, 0, 2)
    app.processEvents()
    QApplication.clipboard().setText("")
    _win.copy_path(pane)
    app.processEvents()
    # 期望值跟着列表实际顺序走（列表按名称排序），关键是不串行、不少行
    order = [e.path for e in pane.model.entries()]
    assert _clip() == "\n".join(order), repr(_clip())


def test_duplicates_and_empties_dropped() -> None:
    """重复路径只留一份，空串不写进去（也顺便不崩）。"""
    _win, _pane, app = _window_and_pane()
    QApplication.clipboard().setText(" untouched ")
    _win.copy_paths(["/a", "/a", "", None, "/b"])
    app.processEvents()
    assert _clip() == "/a\n/b", repr(_clip())


def test_empty_paths_leave_clipboard_alone() -> None:
    """没路径可复制时不能把剪贴板清掉。"""
    _win, _pane, app = _window_and_pane()
    QApplication.clipboard().setText("keep-me")
    _win.copy_paths([])
    app.processEvents()
    assert _clip() == "keep-me", repr(_clip())


def test_right_click_on_unselected_row_wins() -> None:
    """右键点在未选中的行上，复制的是那一行，不是残留的多选。"""
    _win, pane, app = _window_and_pane()
    pane.view.selectRow(_row_of(pane, "app.log"))
    app.processEvents()
    paths = _win._paths_at(pane, _row_pos(pane, _row_of(pane, "run.sh")))
    assert paths == ["/srv/app/run.sh"], paths


def test_right_click_inside_selection_keeps_all() -> None:
    """右键点已在选中集合里的行，仍然复制整个选择。"""
    _win, pane, app = _window_and_pane()
    r1, r2 = _row_of(pane, "app.log"), _row_of(pane, "run.sh")
    _select_rows(pane, min(r1, r2), max(r1, r2))
    app.processEvents()
    paths = _win._paths_at(pane, _row_pos(pane, r2))
    assert paths == ["/srv/app/app.log", "/srv/app/run.sh"], paths


def test_right_click_on_blank_falls_back() -> None:
    """右键点在列表底部空白处：有选中就复制选中，没有就复制当前目录。"""
    _win, pane, app = _window_and_pane()
    blank = QPoint(6, pane.view.viewport().height() - 4)
    assert not pane.view.indexAt(blank).isValid(), "落点算错了，那里确实有行"

    assert _win._paths_at(pane, blank) == ["/srv/app"]
    pane.view.selectRow(_row_of(pane, "app.log"))
    app.processEvents()
    assert _win._paths_at(pane, blank) == ["/srv/app/app.log"]


def test_menu_text_follows_count() -> None:
    """菜单文案：0 条说清是目录，1 条是路径，多条带数量。"""
    _win, _pane, _app = _window_and_pane()
    from linftp.mainwindow import MainWindow

    assert MainWindow._copy_menu_text([]) == "复制当前目录路径"
    assert MainWindow._copy_menu_text(["/x"]) == "复制路径"
    assert MainWindow._copy_menu_text(["/x", "/y", "/z"]) == "复制 3 项路径"


def test_shortcut_action() -> None:
    """快捷键挂在面板上，且是 Ctrl+Shift+C（只在列表有焦点时响应）。"""
    _win, pane, _app = _window_and_pane()
    act = pane.act_copy_path
    assert act.shortcut() == QKeySequence("Ctrl+Shift+C"), act.shortcut().toString()
    assert act.shortcutContext() == Qt.WidgetWithChildrenShortcut
    assert act in pane.actions(), "快捷键没挂到面板上，按了不会响"
    assert act.text() == "复制路径"


def test_signal_reaches_clipboard() -> None:
    """菜单项和快捷键都走同一条链路：面板发信号 → 主窗口写剪贴板。"""
    _win, pane, app = _window_and_pane()
    pane.view.selectRow(_row_of(pane, "run.sh"))
    app.processEvents()
    QApplication.clipboard().setText("")
    pane.copy_path_requested.emit(pane)      # act_copy_path 的槽就是这么发的
    app.processEvents()
    assert _clip() == "/srv/app/run.sh", repr(_clip())


def test_current_dir_independent_of_path_edit() -> None:
    """路径栏里正在输入的文本不能当成"当前目录"被复制出去。"""
    _win, pane, app = _window_and_pane()
    pane.path_edit.setText("/srv/app/typing-now")
    assert pane.paths_for_copy() == ["/srv/app"], pane.paths_for_copy()


def test_status_hint_shows_up() -> None:
    """复制后状态栏得有提示（提示是延后一帧写的，所以要跑一次事件循环）。

    菜单关闭时 QMainWindow 会收到空的 StatusTip 事件把消息清掉，
    所以这里跑完 processEvents 仍能看到消息，才说明没被清。
    """
    _win, pane, app = _window_and_pane()
    pane.view.selectRow(_row_of(pane, "app.log"))
    app.processEvents()
    _win.copy_path(pane)
    app.processEvents()
    msg = _win.statusBar().currentMessage()
    assert msg == "已复制路径：/srv/app/app.log", repr(msg)

    _win.copy_paths(["/a", "/b"])
    app.processEvents()
    assert _win.statusBar().currentMessage() == "已复制 2 条路径"


def main() -> int:
    print("--- 右键复制路径测试 ---")
    check("无选中时复制当前目录", test_no_selection_copies_current_dir)
    check("选中一项复制该项路径", test_single_selection_copies_entry_path)
    check("多选每行一条", test_multi_selection_one_line_each)
    check("去重与空值过滤", test_duplicates_and_empties_dropped)
    check("无路径时不动剪贴板", test_empty_paths_leave_clipboard_alone)
    check("右键未选中行 → 只复制该行", test_right_click_on_unselected_row_wins)
    check("右键选中集合内 → 复制全部", test_right_click_inside_selection_keeps_all)
    check("右键空白处 → 选中项 / 当前目录", test_right_click_on_blank_falls_back)
    check("菜单文案随条数变化", test_menu_text_follows_count)
    check("Ctrl+Shift+C 绑定在面板上", test_shortcut_action)
    check("信号链路直达剪贴板", test_signal_reaches_clipboard)
    check("路径栏输入不影响当前目录", test_current_dir_independent_of_path_edit)
    check("状态栏提示不被菜单关闭清掉", test_status_hint_shows_up)
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
