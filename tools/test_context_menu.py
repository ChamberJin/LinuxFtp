"""右键菜单里的「上级目录 / 主目录 / 刷新」测试（无需人工点击）。

覆盖：
- 三项排在菜单最前面（导航不挑落点，右键点空白处照样能用）
- 本地面板：从子目录回上级、回 ~、只刷自己那一侧
- 远端的「上级目录」在根目录时禁用（点了没反应像坏了，不如置灰）
- 远端未连接时三项全禁用，连上后恢复
- 菜单里的「复制路径」仍然复制右键落点那一行（重构后不能退回去）

菜单构造拆成了 build_context_menu()（只搭不弹），所以这些用例
不需要 exec()，也就不需要真的把菜单显示出来。
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
# 必须在 import linftp.sites 之前设好：CONFIG_DIR 是导入时求值的
_CONF = tempfile.mkdtemp(prefix="linftp-ctxmenu-")
os.environ["LINFP_CONF_DIR"] = _CONF

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from linftp.backend import RemoteFile  # noqa: E402

passed = failed = 0
_win = None
_local_root = tempfile.mkdtemp(prefix="linftp-ctxfs-")


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


class StubSession:
    """够用的远端桩：只记调用、返回一份固定列表。"""

    def __init__(self):
        self.calls = []

    def change_dir(self, path):
        self.calls.append(("change_dir", path))
        return path

    def listdir(self, path):
        self.calls.append(("listdir", path))
        return [RemoteFile(name="a.txt", path=path.rstrip("/") + "/a.txt", size=1)]


def _window():
    global _win
    app = _app()          # 窗口构造前必须先有 QApplication
    if _win is None:
        from linftp.mainwindow import MainWindow

        _win = MainWindow()
        _win.resize(1180, 720)
        _win.show()
    app.processEvents()
    return _win


def _local_tree():
    """建一个 sub/deep 两级目录，供"上级目录"用。"""
    sub = os.path.join(_local_root, "sub")
    deep = os.path.join(sub, "deep")
    os.makedirs(deep, exist_ok=True)
    with open(os.path.join(sub, "one.txt"), "w", encoding="utf-8") as fh:
        fh.write("x")
    return sub, deep


def _prepare(remote_path="/srv/app", connected=False):
    """一个摆好状态的主窗口：本地在 sub、远端在 remote_path。"""
    from linftp.backend import RemoteFile

    w = _window()
    sub, _deep = _local_tree()
    w.go_local(sub)
    w.remote_path = remote_path
    w.remote_pane.set_current_dir(remote_path)
    w.remote_pane.set_entries([
        RemoteFile(name="app.log", path=f"{remote_path}/app.log", size=1024),
        RemoteFile(name="conf", path=f"{remote_path}/conf", is_dir=True),
    ])
    w.session = StubSession() if connected else None
    w.remote_pane.view.clearSelection()
    _app().processEvents()
    return w


def _menu(pane, pos=None):
    w = _window()
    if pos is None:
        pos = QPoint(6, pane.view.viewport().height() - 4)   # 列表底部空白
    return w.build_context_menu(pane, pos)


def _act(menu, text):
    for act in menu.actions():
        if act.text() == text:
            return act
    raise AssertionError(f"菜单里没有「{text}」：{[a.text() for a in menu.actions()]}")


def test_nav_items_come_first() -> None:
    """三项排在菜单最前面，且顺次相接（中间没有分隔线打断）。"""
    w = _prepare()
    menu = _menu(w.local_pane)
    try:
        acts = menu.actions()
        head = [a.text() for a in acts[:4]]
        assert head == ["上级目录", "主目录", "刷新", ""], head
        assert acts[3].isSeparator(), "导航组后面应该跟一条分隔线"
        assert not any(a.isSeparator() for a in acts[:3]), "导航三项之间不该有分隔线"
    finally:
        menu.deleteLater()


def test_nav_works_without_selection() -> None:
    """右键落在空白处、没有任何选中行，三项也能用。"""
    w = _prepare()
    menu = _menu(w.local_pane)
    try:
        for text in ("上级目录", "主目录", "刷新"):
            assert _act(menu, text).isEnabled(), f"没选中任何行时「{text}」不该禁用"
    finally:
        menu.deleteLater()


def test_up_disabled_at_root() -> None:
    """根目录再往上还是根目录 → 禁用，而不是点了没反应。"""
    w = _prepare(remote_path="/", connected=True)
    try:
        assert not _act(_menu(w.remote_pane), "上级目录").isEnabled(), "远端在 / 时应禁用"

        w.go_local("/")
        assert not _act(_menu(w.local_pane), "上级目录").isEnabled(), "本地在 / 时应禁用"
    finally:
        pass


def test_remote_nav_disabled_when_disconnected() -> None:
    """远端没连上：三项置灰，不给点了之后什么都不发生的按钮。"""
    w = _prepare(connected=False)
    menu = _menu(w.remote_pane)
    try:
        for text in ("上级目录", "主目录", "刷新"):
            assert not _act(menu, text).isEnabled(), f"未连接时「{text}」应禁用"
        assert not _act(menu, "复制路径").isEnabled(), "未连接时没有路径可复制"
    finally:
        menu.deleteLater()


def test_remote_nav_enabled_when_connected() -> None:
    w = _prepare(remote_path="/srv/app", connected=True)
    menu = _menu(w.remote_pane)
    try:
        for text in ("上级目录", "主目录", "刷新"):
            assert _act(menu, text).isEnabled(), f"已连接时「{text}」应可用"
    finally:
        menu.deleteLater()


def test_up_goes_to_parent_local() -> None:
    """本地面板点「上级目录」→ 上一层。"""
    w = _prepare()
    sub, _deep = _local_tree()
    w.go_local(os.path.join(sub, "deep"))
    _act(_menu(w.local_pane), "上级目录").trigger()
    _app().processEvents()
    assert os.path.realpath(w.local_path) == os.path.realpath(sub), w.local_path


def test_up_goes_to_parent_remote() -> None:
    """远端的「上级目录」按 remote_path 逐层往上。"""
    w = _prepare(remote_path="/srv/app/logs", connected=True)
    _act(_menu(w.remote_pane), "上级目录").trigger()
    _app().processEvents()
    assert w.remote_path == "/srv/app", w.remote_path
    assert w.session.calls[-1] == ("listdir", "/srv/app"), w.session.calls


def test_home_local_and_remote() -> None:
    """主目录：本地回 ~，远端回 /。"""
    w = _prepare(remote_path="/srv/app/deep", connected=True)
    _act(_menu(w.remote_pane), "主目录").trigger()
    _app().processEvents()
    assert w.remote_path == "/", w.remote_path

    _act(_menu(w.local_pane), "主目录").trigger()
    _app().processEvents()
    assert os.path.realpath(w.local_path) == os.path.realpath(
        os.path.expanduser("~")), w.local_path


def test_refresh_touches_only_that_pane() -> None:
    """右键刷哪侧就刷哪侧：本地的刷新不该顺手重建远端列表。"""
    w = _prepare(remote_path="/srv/app", connected=True)
    calls = []
    w.refresh_local = lambda: calls.append("local")
    before = list(w.session.calls)
    try:
        _act(_menu(w.local_pane), "刷新").trigger()
        _app().processEvents()
        assert calls == ["local"], calls
        assert w.session.calls == before, "本地面板的刷新动到了远端"
        assert w.remote_path == "/srv/app", w.remote_path

        _act(_menu(w.remote_pane), "刷新").trigger()
        _app().processEvents()
        assert w.session.calls[-1] == ("listdir", "/srv/app"), w.session.calls
        assert calls == ["local"], "远端的刷新不该去刷本地"
    finally:
        # 实例属性盖住了类方法，后面用例还要用真的 refresh_local
        del w.refresh_local


def test_copy_still_follows_clicked_row() -> None:
    """重构后回归：右键点在未选中的行上，复制的是那一行。"""
    w = _prepare(remote_path="/srv/app", connected=True)
    pane = w.remote_pane
    pane.view.selectRow(0)
    _app().processEvents()

    row = next(i for i, e in enumerate(pane.model.entries()) if e.name == "conf")
    rect = pane.view.visualRect(pane.model.index(row, 0))
    assert rect.isValid() and not rect.isEmpty(), "那一行不在视口里，落点算错了"

    QApplication.clipboard().setText("")
    act = _act(_menu(pane, rect.center()), "复制路径")
    act.trigger()
    _app().processEvents()
    assert QApplication.clipboard().text() == "/srv/app/conf", \
        repr(QApplication.clipboard().text())


def test_preview_disabled_for_dir() -> None:
    """顺手确认分隔线重排后「预览」仍然按落点判断（目录不给预览）。"""
    w = _prepare(remote_path="/srv/app", connected=True)
    pane = w.remote_pane
    menu = _menu(pane)
    try:
        assert not _act(menu, "预览").isEnabled(), "空白处没有可预览的文件"
    finally:
        menu.deleteLater()


def main() -> int:
    print("--- 右键导航菜单测试 ---")
    check("三项排在菜单最前面", test_nav_items_come_first)
    check("不选中也能用", test_nav_works_without_selection)
    check("根目录时上级置灰", test_up_disabled_at_root)
    check("未连接时远端三项置灰", test_remote_nav_disabled_when_disconnected)
    check("已连接时远端三项可用", test_remote_nav_enabled_when_connected)
    check("本地上级跳转", test_up_goes_to_parent_local)
    check("远端上级跳转", test_up_goes_to_parent_remote)
    check("主目录（本地 ~ / 远端 /）", test_home_local_and_remote)
    check("刷新只作用于当前面板", test_refresh_touches_only_that_pane)
    check("复制路径仍按落点取行", test_copy_still_follows_clicked_row)
    check("目录不可预览", test_preview_disabled_for_dir)
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
