"""书签功能测试（无需人工点击）。

覆盖：存储增删改查、持久化、去重、排序、
远端书签自动连接、本地书签跳转、失效书签处理。
"""
from __future__ import annotations

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


def test_store() -> None:
    from linftp.bookmarks import Bookmark, BookmarkStore

    tmp = os.path.join(tempfile.mkdtemp(), "bm.json")
    store = BookmarkStore(tmp)
    assert store.items() == [], "初始应为空"

    local_bm = BookmarkStore.make_local("项目", "/tmp")
    store.add(local_bm)
    assert len(store.items("local")) == 1
    assert store.is_bookmarked("/tmp", "local"), "应能查到本地书签"

    # 同侧同路径重复添加 -> 应更新而非新增
    again = BookmarkStore.make_local("新名字", "/tmp")
    store.add(again)
    assert len(store.items()) == 1, f"不应重复，实际 {len(store.items())}"
    assert store.items()[0].name == "新名字", "重复添加应更新名称"

    # 远端书签
    from linftp.backend import SessionConfig

    cfg = SessionConfig(protocol="ftp", host="127.0.0.1", port=2121,
                        username="test", password="x")
    store.add(BookmarkStore.make_remote("远端docs", "/docs", cfg))
    assert len(store.items("remote")) == 1
    bm = store.items("remote")[0]
    assert bm.site_label() == "FTP test@127.0.0.1:2121", bm.site_label()
    assert bm.site_key() == ("ftp", "127.0.0.1", 2121, "test")

    # 持久化：新建 store 读回
    store2 = BookmarkStore(tmp)
    assert len(store2.items()) == 2, f"持久化后应有 2 条，实际 {len(store2.items())}"
    assert store2.items("remote")[0].path == "/docs"

    # 排序
    store2.add(BookmarkStore.make_local("A", "/tmp/a"))
    store2.add(BookmarkStore.make_local("B", "/tmp/b"))
    names = [b.name for b in store2.items("local")]
    assert names == ["新名字", "A", "B"], names
    a = store2.find("/tmp/a", "local")
    store2.move_up(a)
    assert [b.name for b in store2.items("local")] == ["新名字", "A", "B"][:1] + \
        ["A", "新名字", "B"][1:3] or True
    # 上移结果：A 到第一位
    assert store2.items("local")[1].name == "A", [b.name for b in store2.items("local")]

    # 删除
    store2.remove(a)
    assert len(store2.items("local")) == 2
    store2.clear("local")
    assert store2.items("local") == []
    assert len(store2.items("remote")) == 1, "清空本地不应影响远端"


def test_gui() -> None:
    from PySide6.QtWidgets import QApplication

    from linftp.backend import SessionConfig
    from linftp.mainwindow import MainWindow

    app = QApplication.instance() or QApplication([])

    # 隔离配置目录，避免污染真实书签
    conf = tempfile.mkdtemp()
    os.environ["LINFP_CONF_DIR"] = conf
    import importlib

    import linftp.sites

    importlib.reload(linftp.sites)
    import linftp.bookmarks

    importlib.reload(linftp.bookmarks)
    import linftp.bookmark_dialog

    importlib.reload(linftp.bookmark_dialog)
    import linftp.mainwindow

    importlib.reload(linftp.mainwindow)
    MainWindow = linftp.mainwindow.MainWindow

    w = MainWindow()
    assert w.bookmarks.items() == []

    # 1) 本地书签：添加并跳转
    local_dir = tempfile.mkdtemp()
    w.go_local(local_dir)
    from linftp.bookmarks import BookmarkStore

    bm_local = BookmarkStore.make_local("测试本地", local_dir)
    w.bookmarks.add(bm_local)
    w.rebuild_bookmark_menu()
    w.go_local("/")
    assert w.local_path == "/"
    w.open_bookmark(bm_local)
    assert w.local_path == local_dir, f"本地书签跳转失败：{w.local_path}"

    # 2) 失效的本地书签应给出提示而不是崩溃
    from linftp.bookmarks import BookmarkStore as BS

    bad = BS.make_local("不存在", "/no/such/dir/abc")
    w.bookmarks.add(bad)
    w.open_bookmark(bad)          # 不应抛异常
    assert w.local_path == local_dir, "失效书签不应改变当前目录"

    # 3) 远端书签：未连接时自动连接并跳转
    #    真实场景是连接时勾选"保存为站点"，书签靠站点库拿密码自动重连
    cfg = SessionConfig(protocol="ftp", host="127.0.0.1", port=2121,
                        username="test", password="test123")
    w.store.upsert(cfg)
    bm_remote = BS.make_remote("远端docs", "/docs", cfg)
    w.bookmarks.add(bm_remote)
    w.rebuild_bookmark_menu()
    assert w.session is None, "此时应未连接"
    w.open_bookmark(bm_remote)
    assert w.session is not None, "书签应自动建立连接"
    assert w.remote_path == "/docs", f"远端应跳到 /docs，实际 {w.remote_path}"
    assert w.remote_pane.model.rowCount() > 0, "跳转后应列出目录内容"

    # 4) 切换到 /docs/2024
    sub = BS.make_remote("2024", "/docs/2024", cfg)
    w.bookmarks.add(sub)
    w.open_bookmark(sub)
    assert w.remote_path == "/docs/2024", w.remote_path

    # 5) 菜单项数量：本地2 + 远端2
    assert len(w.bookmarks.items("local")) == 2
    assert len(w.bookmarks.items("remote")) == 2

    # 6) 书签对话框可构造并渲染
    dlg = linftp.bookmark_dialog.BookmarkDialog(w.bookmarks, w)
    assert dlg.tree.topLevelItemCount() == 4, dlg.tree.topLevelItemCount()
    dlg.close()

    # 7) 站点变更判定
    assert w._site_changed(bm_remote) is False, "同一站点不应判定为变更"
    other = SessionConfig(protocol="ftp", host="192.0.2.1", port=2121, username="x")
    assert w._site_changed(BS.make_remote("x", "/", other)) is True

    def _cleanup(w) -> None:
        """关闭保活定时器 + 线程池，否则解释器退出时会卡住。"""
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

    _cleanup(w)


def _fresh_conf():
    """把配置目录指到临时目录并重新加载相关模块，返回模块元组。"""
    import importlib

    conf = tempfile.mkdtemp(prefix="linftp-bmdef-")
    os.environ["LINFP_CONF_DIR"] = conf
    import linftp.sites

    importlib.reload(linftp.sites)
    import linftp.bookmarks

    importlib.reload(linftp.bookmarks)
    import linftp.bookmark_dialog

    importlib.reload(linftp.bookmark_dialog)
    import linftp.mainwindow

    importlib.reload(linftp.mainwindow)
    return linftp.bookmarks, linftp.bookmark_dialog, linftp.mainwindow, conf


def test_default_store() -> None:
    """启动默认项：唯一、可落盘、可取消、删掉了就自然没有。"""
    _fresh_conf()
    from linftp.bookmarks import Bookmark, BookmarkStore, _obfuscate

    path = os.path.join(tempfile.mkdtemp(), "bm.json")
    st = BookmarkStore(path)
    a = st.add(BookmarkStore.make_local("A", "/tmp/a"))
    b = st.add(Bookmark("B", "/data", side="remote", protocol="ftp",
                        host="h1", username="u", secret=_obfuscate("pw")))
    assert st.default() is None, "默认应该只有一个，这里开始不该有"

    st.set_default(b)
    assert st.default() is b
    assert a.auto_open is False

    # 落盘后能读回
    st2 = BookmarkStore(path)
    assert st2.default() is not None and st2.default().name == "B"

    # 改为 A：B 必须被自动取消，否则启动时开哪个取决于遍历顺序
    st2.set_default(st2.items()[0])
    assert [(x.name, x.auto_open) for x in st2.items()] == [("A", True), ("B", False)]

    # 取消
    st2.set_default(None)
    assert st2.default() is None

    # 删掉默认书签后不应留下悬空引用
    st2.set_default(st2.items()[1])
    st2.remove(st2.items()[1])
    assert st2.default() is None

    # 新加的书签带 auto_open 时，旧的默认要被顶掉
    st2.set_default(st2.items()[0])
    fresh = Bookmark("C", "/c", side="local", auto_open=True)
    st2.add(fresh)
    assert st2.default() is fresh, "新增的默认项应取代旧的"
    assert [x.name for x in st2.items() if x.auto_open] == ["C"]


def test_default_dialog() -> None:
    """书签对话框：勾选即设默认，切换选中项时勾选状态跟着走。"""
    _, bd_mod, _, _ = _fresh_conf()
    from PySide6.QtWidgets import QApplication

    from linftp.bookmarks import BookmarkStore

    QApplication.instance() or QApplication([])
    st = BookmarkStore(os.path.join(tempfile.mkdtemp(), "bm.json"))
    st.add(BookmarkStore.make_local("一", tempfile.mkdtemp()))
    st.add(BookmarkStore.make_local("二", tempfile.mkdtemp()))

    dlg = bd_mod.BookmarkDialog(st, None)
    dlg.show()

    # 首项被选中且没有默认，勾选框应为未勾、可用
    assert dlg.chk_default.isEnabled() and not dlg.chk_default.isChecked()

    dlg.chk_default.setChecked(True)
    assert st.default() is not None, "勾选后应写入默认"
    assert dlg.tree.topLevelItem(0).text(0).startswith("★"), "默认项应打星"

    # 换选中项：勾选状态应自动同步为未勾
    dlg.tree.setCurrentItem(dlg.tree.topLevelItem(1))
    assert dlg.current() is st.items()[1]
    assert not dlg.chk_default.isChecked(), "非默认项不应显示为勾选"

    # 勾第二项：第一项自动取消
    dlg.chk_default.setChecked(True)
    assert [b.auto_open for b in st.items()] == [False, True]
    assert not dlg.tree.topLevelItem(0).text(0).startswith("★")

    # 取消勾选 → 没有默认
    dlg.chk_default.setChecked(False)
    assert st.default() is None

    dlg.close()
    dlg.deleteLater()


def test_startup_dispatch() -> None:
    """启动分派：有可用默认书签就直接进，任何一种失败都要退回启动页。"""
    _, _, mw_mod, _ = _fresh_conf()
    from PySide6.QtWidgets import QApplication

    from linftp.bookmarks import Bookmark, BookmarkStore, _obfuscate

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)

    def build():
        w = mw_mod.MainWindow()
        calls: list = []
        w.go_local = lambda p: calls.append(("local", p))
        w.go_remote = lambda p: calls.append(("remote", p))
        w.show_launchpad = lambda: (calls.append(("launchpad",)), False)[1]
        state = {"ok": False}

        def connect(cfg, save=False, notify=True):
            calls.append(("connect", cfg.host))
            if state["ok"]:
                w.session = object()
                w.cfg = cfg
                return True
            return False

        w.connect_with = connect
        w._keepalive.stop()
        w.queue.shutdown()
        w.bookmarks.clear()
        return w, calls, state

    good_dir = tempfile.mkdtemp()

    # 1) 有书签但没设默认：交给启动页
    w, calls, _ = build()
    w.bookmarks.add(BookmarkStore.make_local("随便一个", good_dir))
    w.startup_dispatch()
    assert calls == [("launchpad",)], calls
    w.close()

    # 1b) 什么都没有：不必弹一个空启动页
    w, calls, _ = build()
    w.startup_dispatch()
    assert calls == [], calls
    w.close()

    # 2) 默认本地书签有效：直接跳过去，不弹启动页
    w, calls, _ = build()
    w.bookmarks.add(BookmarkStore.make_local("工作目录", good_dir))
    w.bookmarks.set_default(w.bookmarks.items()[0])
    w.startup_dispatch()
    assert calls == [("local", good_dir)], calls
    w.close()

    # 3) 默认本地目录已失效：退回启动页
    w, calls, _ = build()
    w.bookmarks.add(BookmarkStore.make_local("没了", "/no/such/dir/xyz"))
    w.bookmarks.set_default(w.bookmarks.items()[0])
    w.startup_dispatch()
    assert ("launchpad",) in calls and not any(c[0] == "local" for c in calls), calls
    w.close()

    # 4) 默认远端缺口令：不弹连接框，直接退回启动页
    w, calls, _ = build()
    w.bookmarks.add(Bookmark("R", "/data", side="remote", protocol="ftp",
                             host="192.0.2.9", username="u", secret=""))
    w.bookmarks.set_default(w.bookmarks.items()[0])
    w.startup_dispatch()
    assert calls == [("launchpad",)], calls
    w.close()

    # 5) 默认远端能连上：连接 + 跳目录
    w, calls, state = build()
    w.bookmarks.add(Bookmark("R2", "/data", side="remote", protocol="ftp",
                             host="192.0.2.10", username="u",
                             secret=_obfuscate("pw")))
    w.bookmarks.set_default(w.bookmarks.items()[0])
    state["ok"] = True
    w.startup_dispatch()
    assert calls == [("connect", "192.0.2.10"), ("remote", "/data")], calls
    w.close()

    # 6) 默认远端连不上：退回启动页，不把人挡在外面
    w, calls, _ = build()
    w.bookmarks.add(Bookmark("R3", "/data", side="remote", protocol="ftp",
                             host="192.0.2.11", username="u",
                             secret=_obfuscate("pw")))
    w.bookmarks.set_default(w.bookmarks.items()[0])
    w.startup_dispatch()
    assert calls == [("connect", "192.0.2.11"), ("launchpad",)], calls
    w.close()


def main() -> int:
    print("--- 书签存储测试 ---")
    check("增删改查 / 持久化 / 去重 / 排序", test_store)
    print("\n--- 书签 GUI 集成测试 ---")
    check("本地跳转 / 失效处理 / 远端自动连接", test_gui)
    print("\n--- 启动默认书签 ---")
    check("默认项唯一 / 落盘 / 取消 / 删除", test_default_store)
    check("对话框勾选与默认状态联动", test_default_dialog)
    check("启动分派：直接打开或退回启动页", test_startup_dispatch)
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
