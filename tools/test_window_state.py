"""窗口大小 / 位置记忆测试（无需人工点击）。

覆盖：
- 首次启动没有记录时 restore 返回 False（调用方改走"主屏居中"）
- 关闭时把位置、尺寸、最大化状态写进 ui.json
- 新窗口按记录还原，且与紧凑模式偏好互不覆盖
- 记录里的坐标已经不在任何显示器上（换屏 / 拔屏）时不吃掉窗口
- 坏数据、字段缺失、零尺寸都能安全退回默认

配置目录用 LINFP_CONF_DIR 指到临时目录，不会碰到用户真实的
~/.config/linftp/ui.json。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 用例里没人点"收进托盘"，真托盘一律不起，免得关窗口时弹出询问框卡住
os.environ["LINFP_NO_TRAY"] = "1"
# 必须在 import linftp.sites 之前设好：CONFIG_DIR 是导入时求值的
_CONF = tempfile.mkdtemp(prefix="linftp-winstate-")
os.environ["LINFP_CONF_DIR"] = _CONF

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

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


def _ui_path() -> str:
    from linftp.mainwindow import _pref_path

    return _pref_path()


def _reset() -> None:
    """每个用例都从干净偏好开始。"""
    try:
        os.remove(_ui_path())
    except FileNotFoundError:
        pass


def _new_window():
    from linftp.mainwindow import MainWindow

    return MainWindow()


def _close_window(w) -> None:
    """走真正的 close()，让 closeEvent 里的保存逻辑执行。"""
    try:
        w.close()
        w._keepalive.stop()
        w.queue.shutdown()
    except Exception:  # noqa: BLE001
        pass


def test_first_launch_has_no_record() -> None:
    """没有记录时 restore 必须返回 False，让调用方去摆主屏居中。"""
    _reset()
    app = _app()
    w = _new_window()
    assert w.restore_window_state() is False
    # 没记录时窗口尺寸应保持构造函数里的默认值
    assert w.size().width() >= 320 and w.size().height() >= 240
    _close_window(w)
    w.deleteLater()
    app.processEvents()


def test_save_and_restore() -> None:
    """关掉时记下位置尺寸，下次开起来应该一模一样。"""
    _reset()
    app = _app()
    w = _new_window()
    w.show()
    app.processEvents()
    w.setGeometry(60, 40, 700, 560)
    app.processEvents()
    saved_geo = w.geometry()
    _close_window(w)
    w.deleteLater()
    app.processEvents()

    raw = json.load(open(_ui_path(), encoding="utf-8"))
    assert "window" in raw, raw
    rec = raw["window"]
    assert (rec["x"], rec["y"]) == (saved_geo.x(), saved_geo.y()), rec
    assert abs(rec["w"] - saved_geo.width()) <= 1, rec
    assert abs(rec["h"] - saved_geo.height()) <= 1, rec
    assert rec["maximized"] is False, rec

    w2 = _new_window()
    assert w2.restore_window_state() is True
    geo = w2.geometry()
    assert abs(geo.x() - saved_geo.x()) <= 2, (geo, saved_geo)
    assert abs(geo.y() - saved_geo.y()) <= 2, (geo, saved_geo)
    assert abs(geo.width() - saved_geo.width()) <= 2, (geo, saved_geo)
    assert abs(geo.height() - saved_geo.height()) <= 2, (geo, saved_geo)
    _close_window(w2)
    w2.deleteLater()
    app.processEvents()


def test_prefs_do_not_clobber_each_other() -> None:
    """窗口位置和紧凑模式写在同一个文件里，谁都不能把对方冲掉。"""
    _reset()
    app = _app()
    from linftp.mainwindow import save_compact_pref

    save_compact_pref(True)

    w = _new_window()
    w.show()
    app.processEvents()
    w.setGeometry(30, 20, 640, 520)
    app.processEvents()
    _close_window(w)
    w.deleteLater()
    app.processEvents()

    raw = json.load(open(_ui_path(), encoding="utf-8"))
    assert raw.get("compact") is True, f"窗口位置把紧凑偏好冲掉了：{raw}"
    assert "window" in raw, raw

    # 反过来：改紧凑偏好不能把窗口位置冲掉
    save_compact_pref(False)
    raw2 = json.load(open(_ui_path(), encoding="utf-8"))
    assert "window" in raw2, f"紧凑偏好把窗口位置冲掉了：{raw2}"
    assert raw2["compact"] is False
    _reset()


def test_offscreen_record_is_rejected() -> None:
    """记录的坐标落在所有显示器之外（换了屏 / 拔了屏）时不能采用。"""
    _reset()
    app = _app()
    from linftp.mainwindow import _save_prefs

    _save_prefs({"window": {"x": 99999, "y": 99999, "w": 700, "h": 500,
                            "maximized": False}})
    w = _new_window()
    assert w.restore_window_state() is False, "屏幕外的坐标被采用了"
    _close_window(w)
    w.deleteLater()
    app.processEvents()
    _reset()


def test_bad_records_fall_back() -> None:
    """坏数据不能让程序起不来，一律退回默认。"""
    _reset()
    app = _app()
    from linftp.mainwindow import _save_prefs, load_window_geometry

    cases = [
        {"x": "abc", "y": 10, "w": 700, "h": 500},      # 类型不对
        {"x": 10, "y": 10, "w": 700},                    # 缺字段
        {"x": 10, "y": 10, "w": 0, "h": 0},              # 零尺寸（最小化时误存）
        {"x": 10, "y": 10, "w": 10, "h": 500},           # 窄得不像窗口
        "not-a-dict",                                    # 整个结构都不对
    ]
    for case in cases:
        _save_prefs({"window": case})
        assert load_window_geometry() is None, f"坏数据没被过滤：{case}"
        w = _new_window()
        assert w.restore_window_state() is False, f"坏数据被采用了：{case}"
        _close_window(w)
        w.deleteLater()
        app.processEvents()
    _reset()


def test_maximized_restores_normal_geometry() -> None:
    """最大化时关闭，重开应该还是最大化；取消最大化后回到原来那块区域。

    关键在保存时取 normalGeometry()：最大化状态下 geometry() 是铺满屏幕
    的那份，拿它还原会丢掉"最大化之前"的大小，用户取消最大化就变成满屏。
    """
    _reset()
    app = _app()
    w = _new_window()
    w.show()
    app.processEvents()
    w.setGeometry(50, 30, 680, 520)
    app.processEvents()
    normal = w.normalGeometry()
    w.setWindowState(w.windowState() | Qt.WindowMaximized)
    app.processEvents()
    _close_window(w)
    w.deleteLater()
    app.processEvents()

    rec = json.load(open(_ui_path(), encoding="utf-8"))["window"]
    assert rec["maximized"] is True, rec
    # 存下来的必须是最大化之前那份尺寸，不能是铺满屏幕的尺寸
    assert abs(rec["w"] - normal.width()) <= 2, (rec, normal)
    assert abs(rec["h"] - normal.height()) <= 2, (rec, normal)

    w2 = _new_window()
    assert w2.restore_window_state() is True
    w2.show()
    app.processEvents()
    assert w2.isMaximized(), "最大化状态没恢复"
    _close_window(w2)
    w2.deleteLater()
    app.processEvents()
    _reset()


def main() -> int:
    print("--- 窗口大小 / 位置记忆测试 ---")
    check("首次启动无记录时不做还原", test_first_launch_has_no_record)
    check("关闭时记录、重开时还原", test_save_and_restore)
    check("与紧凑模式偏好互不覆盖", test_prefs_do_not_clobber_each_other)
    check("屏幕外的坐标不被采用", test_offscreen_record_is_rejected)
    check("坏数据 / 零尺寸安全退回", test_bad_records_fall_back)
    check("最大化保存的是普通尺寸（回归）", test_maximized_restores_normal_geometry)
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
