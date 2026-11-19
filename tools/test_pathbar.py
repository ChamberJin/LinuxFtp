"""路径栏层级点击跳转测试（无需人工点击）。

覆盖：
- 路径切层：根 /a/b/c、中文、尾随斜杠、连续斜杠、相对路径、空串
- 字符位置 → 层级：点在 `/` 上算左边那层，点超出文本算最后一层
- 点击某层发出正确目标路径；点当前目录那一层不发信号
- 鼠标移上去变手型 + tooltip 提示，移开还原
- FilePane → 主窗口：本地侧真的跳过去，远端侧走 go_remote

远端跳转用打桩替换 go_remote（不连服务器），本地跳转用真实临时目录。
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
_CONF = tempfile.mkdtemp(prefix="linftp-pathbar-")
os.environ["LINFP_CONF_DIR"] = _CONF

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

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


def _edit(text: str = "") -> "object":
    """一个装好文本、宽度够用的 PathEdit（不带前置图标，坐标好算）。"""
    from linftp.pathbar import PathEdit

    app = _app()
    edit = PathEdit()
    edit.setText(text)
    edit.resize(520, 26)
    edit.show()
    app.processEvents()
    return edit


def _click_x(edit, char_index: int) -> int:
    """落在第 char_index 个字符**之后**那一侧的像素 x。

    QLineEdit 的命中测试按"点落在哪个字符的格子里 → 返回该字符的插入位置"，
    所以点字符内部只会回到它自己（点 '/' 里侧还是根目录）。
    要点到"第 N 层"就得点在第 N 个字符之后。
    """
    edit.setCursorPosition(char_index)
    x0 = edit.cursorRect().x()
    if char_index >= len(edit.text()):
        return x0
    edit.setCursorPosition(char_index + 1)
    x1 = edit.cursorRect().x()
    if x1 <= x0:
        return x0 + 1
    return min(x1 + 1, edit.width() - 2)


def _click(edit, char_index: int) -> None:
    QTest.mouseClick(edit, Qt.LeftButton, Qt.NoModifier,
                     QPoint(_click_x(edit, char_index), edit.height() // 2))
    _app().processEvents()


# ---------------------------------------------------------------- 纯逻辑
def test_split_basic() -> None:
    from linftp.pathbar import split_segments

    assert split_segments("/") == [(0, 1, "/")]
    assert split_segments("/home/deploy") == [
        (0, 1, "/"), (1, 5, "/home"), (6, 12, "/home/deploy")]
    assert split_segments("") == []


def test_split_chinese_and_trailing_slash() -> None:
    from linftp.pathbar import split_segments

    segs = split_segments("/项目文档/BUILD7.0/终版归档")
    assert [s[2] for s in segs] == [
        "/", "/项目文档", "/项目文档/BUILD7.0", "/项目文档/BUILD7.0/终版归档"], segs
    # 尾随斜杠不该多出一层空目录
    assert [s[2] for s in split_segments("/a/b/")] == ["/", "/a", "/a/b"]
    # 连续斜杠同理
    assert [s[2] for s in split_segments("/a//b")] == ["/", "/a", "/a//b"]


def test_split_relative_path() -> None:
    """不是绝对路径时整条当一层，点了就跳它（别切出乱七八糟的层）。"""
    from linftp.pathbar import split_segments

    assert split_segments("rel/path") == [(0, 8, "rel/path")]


def test_segment_index_boundaries() -> None:
    from linftp.pathbar import segment_index_at

    path = "/home/deploy"
    # 0..1 → 根；2..5 → /home；6.. → /home/deploy
    assert [segment_index_at(path, i) for i in (0, 1)] == [0, 0]
    assert [segment_index_at(path, i) for i in (2, 3, 4, 5)] == [1, 1, 1, 1]
    assert all(segment_index_at(path, i) == 2 for i in range(6, len(path) + 3))
    assert segment_index_at("", 0) == -1


# ---------------------------------------------------------------- 点击行为
def test_click_parent_level() -> None:
    from linftp.pathbar import PathEdit

    edit = _edit("/srv/app/conf")
    got = []
    edit.segment_clicked.connect(got.append)
    _click(edit, 3)                 # 落在 "srv" 里 → /srv
    assert got == ["/srv"], got
    _click(edit, 6)                 # 落在 "app" 里 → /srv/app
    assert got == ["/srv", "/srv/app"], got
    edit.close()


def test_click_root() -> None:
    edit = _edit("/srv/app")
    got = []
    edit.segment_clicked.connect(got.append)
    _click(edit, 0)
    assert got == ["/"], got
    edit.close()


def test_click_current_level_does_nothing() -> None:
    """点最末层（就是当前目录）不该发信号，避免白刷一次列表。"""
    edit = _edit("/srv/app")
    got = []
    edit.segment_clicked.connect(got.append)
    _click(edit, 8)                 # 末尾
    assert got == [], got
    edit.close()


def test_click_when_empty() -> None:
    edit = _edit("")
    got = []
    edit.segment_clicked.connect(got.append)
    _click(edit, 0)                 # 不该崩，也不该发信号
    assert got == [], got
    edit.close()


def test_click_chinese_path() -> None:
    edit = _edit("/项目文档/BUILD7.0/终版归档")
    got = []
    edit.segment_clicked.connect(got.append)
    _click(edit, 2)                 # "发" 中间 → /项目文档
    assert got == ["/项目文档"], got
    _click(edit, 9)                 # "BUILD7.0" 里 → /项目文档/BUILD7.0
    assert got == ["/项目文档", "/项目文档/BUILD7.0"], got
    edit.close()


# ---------------------------------------------------------------- 鼠标提示
def _move(edit, x: int) -> None:
    y = edit.height() // 2
    ev = QMouseEvent(QEvent.MouseMove, QPointF(x, y),
                     QPointF(edit.mapToGlobal(QPoint(x, y))),
                     Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    edit.mouseMoveEvent(ev)
    _app().processEvents()


def test_hover_cursor_and_tooltip() -> None:
    edit = _edit("/srv/app/conf")
    _move(edit, _click_x(edit, 3))
    assert edit.cursor().shape() == Qt.PointingHandCursor, edit.cursor().shape()
    assert "/srv" in edit.toolTip() and "点击" in edit.toolTip(), edit.toolTip()

    # 移到别的层，提示要跟着变
    _move(edit, _click_x(edit, 6))
    assert edit.toolTip().endswith("/srv/app"), edit.toolTip()

    # 移出控件，光标与提示都还原
    edit.leaveEvent(QEvent(QEvent.Leave))
    assert edit.cursor().shape() != Qt.PointingHandCursor
    assert edit.toolTip() == ""
    edit.close()


# ---------------------------------------------------------------- 面板集成
def _get_window():
    """复用一个主窗口（构造一次就够，offscreen 下重复构造很慢）。"""
    global _window
    from linftp.mainwindow import MainWindow

    app = _app()
    if _window is None:
        _window = MainWindow()
        _window.resize(1180, 720)
        _window.show()
        app.processEvents()
    return _window, app


def test_local_pane_jumps() -> None:
    """本地栏点上层，真的把当前目录切过去。"""
    win, app = _get_window()
    root = tempfile.mkdtemp(prefix="linftp-nav-")
    target = os.path.join(root, "项目文档", "BUILD7.0", "终版归档")
    os.makedirs(target, exist_ok=True)

    win.go_local(target)
    app.processEvents()
    assert win.local_path == target, win.local_path

    got = []
    win.local_pane.navigate_requested.connect(got.append)
    win.local_pane.path_edit.segment_clicked.emit(os.path.join(root, "项目文档"))
    app.processEvents()

    assert got == [os.path.join(root, "项目文档")], got
    assert win.local_path == os.path.join(root, "项目文档"), win.local_path
    assert win.local_pane.path_edit.text() == win.local_path


def test_remote_pane_routes_to_go_remote() -> None:
    """远端栏点上层走 go_remote（这里打桩，不连服务器）。"""
    win, app = _get_window()
    calls = []
    orig = type(win).go_remote
    type(win).go_remote = lambda self, p: calls.append(p)
    try:
        win.remote_pane.path_edit.segment_clicked.emit("/srv")
        app.processEvents()
    finally:
        type(win).go_remote = orig
    assert calls == ["/srv"], calls


def test_long_path_starts_from_left() -> None:
    """长路径要从左边开始显示，否则根和上层被裁掉就没法点了。"""
    edit = _edit("/项目文档/BUILD7.0/终版归档/子目录/更长的目录名字")
    edit.set_path(edit.text())
    assert edit.cursorPosition() == 0, edit.cursorPosition()
    edit.close()


def test_readonly_and_no_focus() -> None:
    """只读、不抢焦点：点在路径栏上不会留下聚焦框，键盘焦点还在列表里。"""
    edit = _edit("/srv/app")
    assert edit.isReadOnly(), "路径栏应保持只读"
    assert edit.focusPolicy() == Qt.NoFocus, edit.focusPolicy()
    edit.close()


def main() -> int:
    print("--- 路径栏层级点击测试 ---")
    check("基础切层", test_split_basic)
    check("中文路径 / 尾斜杠 / 连续斜杠", test_split_chinese_and_trailing_slash)
    check("相对路径整条一层", test_split_relative_path)
    check("字符位置 → 层级边界", test_segment_index_boundaries)
    check("点上层发正确路径", test_click_parent_level)
    check("点根目录", test_click_root)
    check("点当前层不发信号", test_click_current_level_does_nothing)
    check("空路径不崩不发信号", test_click_when_empty)
    check("中文路径点击", test_click_chinese_path)
    check("鼠标移入变手型 + 提示", test_hover_cursor_and_tooltip)
    check("长路径从左显示", test_long_path_starts_from_left)
    check("只读且不抢焦点", test_readonly_and_no_focus)
    check("本地栏真的跳过去", test_local_pane_jumps)
    check("远端栏走 go_remote", test_remote_pane_routes_to_go_remote)
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
