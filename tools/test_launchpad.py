"""启动页（LaunchPad）测试（无需人工点击）。

覆盖：
- 书签 + 最近连接合并成入口列表，同一站点不重复
- 搜索过滤、回车打开、跳过不触发跳转
- has_content 的门槛判断（无书签无最近连接时不弹）
- 最近连接的持久化与去重
- 主题层能正常渲染（QSS + 图标不崩）
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LINFP_CONF_DIR"] = tempfile.mkdtemp(prefix="linftp-conf-")

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
    from PySide6.QtWidgets import QApplication

    from linftp import theme

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


def _sample():
    from linftp.backend import SessionConfig
    from linftp.bookmarks import BookmarkStore

    cfg = SessionConfig(protocol="ftp", host="ftp.example.com", port=21,
                        username="deploy", password="x")
    bookmarks = [
        BookmarkStore.make_local("工作文档", tempfile.mkdtemp()),
        BookmarkStore.make_local("不存在目录", "/no/such/dir"),
        BookmarkStore.make_remote("测试版本", "/srv/releases/7.0", cfg),
        BookmarkStore.make_remote("发布包", "/srv/releases", cfg),
    ]
    recents = [
        SessionConfig(protocol="ftp", host="192.0.2.20", port=2121,
                      username="deploy"),
        # 与远端书签同站点 → 不应重复出现
        SessionConfig(protocol="ftp", host="ftp.example.com", port=21,
                      username="deploy"),
    ]
    return bookmarks, recents, cfg


def test_entries() -> None:
    from linftp.launchpad import LaunchPad, has_content

    app = _app()
    bookmarks, recents, _ = _sample()
    dlg = LaunchPad(bookmarks, recents, None)

    # 4 条书签 + 1 条最近连接（ftp.example.com 已被书签覆盖）
    assert dlg.list.count() == 5, dlg.list.count()
    titles = [dlg.list.item(i).data(0x0100).title for i in range(dlg.list.count())]
    assert titles == ["工作文档", "不存在目录", "测试版本", "发布包", "192.0.2.20"], titles

    # 本地在前，远端在后
    kinds = [dlg.list.item(i).data(0x0100).kind for i in range(dlg.list.count())]
    assert kinds.count("bookmark") == 4 and kinds[-1] == "recent", kinds

    assert has_content([], []) is False, "空内容不应弹出启动页"
    assert has_content(bookmarks, []) is True
    assert has_content([], recents) is True
    dlg.close()


def test_filter_and_open() -> None:
    from linftp.launchpad import LaunchPad

    app = _app()
    bookmarks, recents, _ = _sample()
    dlg = LaunchPad(bookmarks, recents, None)

    dlg.search.setText("releases")
    app.processEvents()
    assert dlg._visible_count() == 2, dlg._visible_count()

    dlg.search.setText("192.0.2")
    app.processEvents()
    assert dlg._visible_count() == 1, dlg._visible_count()

    dlg.search.setText("完全不匹配的关键词")
    app.processEvents()
    assert dlg._visible_count() == 0
    assert dlg.current_entry() is None
    assert dlg.btn_open.isEnabled() is False, "没有可选项时打开按钮应禁用"

    dlg.search.setText("")
    app.processEvents()

    # 选中"测试版本"后回车 → 应发出打开书签信号并关闭对话框
    got = []
    dlg.open_bookmark_requested.connect(got.append)
    for i in range(dlg.list.count()):
        if dlg.list.item(i).data(0x0100).title == "测试版本":
            dlg.list.setCurrentRow(i)
            break
    app.processEvents()
    dlg._on_open()
    assert len(got) == 1 and got[0].name == "测试版本", got
    assert dlg.result() == LaunchPad.Accepted

    # 跳过：不应发出任何信号
    dlg2 = LaunchPad(bookmarks, recents, None)
    fired = []
    dlg2.open_bookmark_requested.connect(fired.append)
    dlg2.reject()
    assert fired == [], "跳过不应触发跳转"
    dlg2.close()


def test_recents_store() -> None:
    from linftp.backend import SessionConfig
    from linftp.sites import SiteStore

    tmp = os.path.join(tempfile.mkdtemp(), "sites.json")
    st = SiteStore(tmp)
    assert st.recents() == []

    a = SessionConfig(protocol="ftp", host="a.example", port=21,
                      username="u1", password="secret")
    b = SessionConfig(protocol="ftp", host="b.example", port=21, username="u2")
    st.touch_recent(a)
    st.touch_recent(b)
    st.touch_recent(a)          # 再次连接 a → 提到最前，不重复
    rec = st.recents()
    assert [c.host for c in rec] == ["a.example", "b.example"], rec
    assert rec[0].password == "", "最近连接不应持久化口令"

    # 重新读盘仍在，且站点列表没被污染
    st2 = SiteStore(tmp)
    assert [c.host for c in st2.recents()] == ["a.example", "b.example"]
    assert st2.sites() == [], "recents 不应写入 sites"


def test_theme() -> None:
    from linftp import theme

    app = _app()
    assert app.styleSheet(), "主题应已应用"
    for name in theme._PATH:
        assert not theme.icon(name).isNull(), name
    pix = theme.avatar_text("测试", 28)
    assert not pix.isNull() and pix.width() == 28


def test_app_icon() -> None:
    """应用图标要能被解析出来，否则任务栏和 Alt-Tab 会显示空白图标。

    图标优先走 hicolor 里装好的那份，没装时回退到工程里的 assets/。
    这条断言用来防止改了资源路径之后悄悄失效。
    """
    from linftp import theme

    _app()
    icon = theme.app_icon()
    assert not icon.isNull(), "应用图标解析失败"

    # availableSizes() 对 SVG 图标天然为空（矢量图可任意缩放），
    # 所以真正可靠的判断是"能不能渲染出像素"——多试几个尺寸
    for size in (16, 32, 64, 256):
        pix = icon.pixmap(size, size)
        assert not pix.isNull(), f"{size}px 渲染失败"
        # 带 DPI 缩放的桌面上 pixmap 返回的是物理像素（dpr=1.5 时 16px 得到
        # 24×24），所以按逻辑像素换算后再比，不能直接拿 width() 比 size
        dpr = pix.devicePixelRatio() or 1.0
        got = round(pix.width() / dpr)
        assert got == size, (
            f"{size}px 实际渲染成 {pix.width()}×{pix.height()}（dpr={dpr}）")
        # 不能是一张全透明/全白的空图
        img = pix.toImage()
        colors = {img.pixelColor(x, y).rgba()
                  for x in range(0, pix.width(), max(1, pix.width() // 8))
                  for y in range(0, pix.height(), max(1, pix.height() // 8))}
        assert len(colors) > 1, f"{size}px 渲染结果只有单一颜色，图标可能是空的"

    # 工程里的 SVG 源文件必须在，安装脚本靠它渲染整套尺寸
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    svg = os.path.join(root, "assets", "linftp-icon.svg")
    assert os.path.exists(svg), f"图标源文件缺失：{svg}"

    # apply() 里应已设过应用级图标，窗口才能自动继承
    app = _app()
    assert not app.windowIcon().isNull(), "app.setWindowIcon 未生效"


def main() -> int:
    print("--- 启动页测试 ---")
    check("入口合并 / 站点去重 / has_content", test_entries)
    check("搜索过滤 / 回车打开 / 跳过", test_filter_and_open)
    check("最近连接持久化与去重", test_recents_store)
    check("主题与图标渲染", test_theme)
    check("应用图标可解析", test_app_icon)
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
