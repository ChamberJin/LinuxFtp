"""列表排序测试（名称 / 大小 / 类型 / 修改时间 / 权限）。

    .venv/bin/python tools/test_sort.py

覆盖：各列排序规则、目录永远置顶、大小按数值而非显示文本、过滤与排序
共存、切目录后排序状态保持、表头点击切换升降序、排序后选择不丢。
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from linftp import theme  # noqa: E402
from linftp.backend import RemoteFile  # noqa: E402
from linftp.models import (  # noqa: E402
    COL_MTIME,
    COL_NAME,
    COL_PERM,
    COL_SIZE,
    COL_TYPE,
    FileListModel,
)

passed = failed = 0
_app = None


def app() -> QApplication:
    global _app
    if _app is None:
        _app = QApplication([])
        theme.apply(_app)
    return _app


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


def _sample() -> list:
    return [
        RemoteFile("zeta.log", "/zeta.log", size=5000, mtime=100, mode=0o644),
        RemoteFile("Alpha.txt", "/Alpha.txt", size=900, mtime=300, mode=0o600),
        RemoteFile("beta.md", "/beta.md", size=1536, mtime=200, mode=0o755),
        RemoteFile("Docs", "/Docs", size=4096, mtime=900, is_dir=True, mode=0o755),
        RemoteFile("archive", "/archive", size=4096, mtime=50, is_dir=True, mode=0o700),
    ]


def _model() -> FileListModel:
    m = FileListModel()
    m.set_entries(_sample())
    return m


def _names(m: FileListModel) -> list:
    return [e.name for e in m.entries()]


def _pane(entries=None):
    from linftp.mainwindow import FilePane

    app()
    pane = FilePane("本地", False)
    pane.resize(900, 520)
    pane.set_entries(entries if entries is not None else _sample())
    pane.show()
    QApplication.processEvents()
    return pane


# ---------------------------------------------------------------- 基本排序列
def test_name_ascending() -> None:
    m = _model()
    assert _names(m) == ["archive", "Docs", "Alpha.txt", "beta.md", "zeta.log"], _names(m)


def test_name_descending_keeps_dirs_first() -> None:
    m = _model()
    m.sort(COL_NAME, Qt.DescendingOrder)
    assert _names(m) == ["Docs", "archive", "zeta.log", "beta.md", "Alpha.txt"], _names(m)


def test_name_sort_is_case_insensitive() -> None:
    """Alpha.txt 不应该因为 A(65) < b(98) 就排到 beta 前面去以外的地方——
    大小写敏感会让所有大写名整体窜到最前，这里验证是正常的字母序。"""
    m = FileListModel()
    m.set_entries([
        RemoteFile("b.txt", "/b.txt"),
        RemoteFile("A.txt", "/A.txt"),
        RemoteFile("C.txt", "/C.txt"),
    ])
    assert _names(m) == ["A.txt", "b.txt", "C.txt"], _names(m)


def test_size_sorts_numerically() -> None:
    """按显示文本排会得到 "1536 B" < "5000 B" < "900 B"，必须按字节数。"""
    m = _model()
    m.sort(COL_SIZE, Qt.AscendingOrder)
    # 目录没有有意义的大小，两个目录的键相同 → 稳定排序保持原顺序
    assert _names(m) == ["Docs", "archive", "Alpha.txt", "beta.md", "zeta.log"], _names(m)
    m.sort(COL_SIZE, Qt.DescendingOrder)
    assert _names(m) == ["Docs", "archive", "zeta.log", "beta.md", "Alpha.txt"], _names(m)


def test_mtime_sort() -> None:
    m = _model()
    m.sort(COL_MTIME, Qt.AscendingOrder)
    assert _names(m) == ["archive", "Docs", "zeta.log", "beta.md", "Alpha.txt"], _names(m)
    m.sort(COL_MTIME, Qt.DescendingOrder)
    assert _names(m) == ["Docs", "archive", "Alpha.txt", "beta.md", "zeta.log"], _names(m)


def test_type_and_perm_sort() -> None:
    m = _model()
    m.sort(COL_TYPE, Qt.AscendingOrder)
    heads = _names(m)[:2]
    assert heads == ["Docs", "archive"], heads      # 目录类型排最前
    m.sort(COL_PERM, Qt.AscendingOrder)
    # 权限 600 < 644 < 700 < 755，但目录先分组
    files = [e.name for e in m.entries() if not e.is_dir]
    assert files == ["Alpha.txt", "zeta.log", "beta.md"], files


# ---------------------------------------------------------------- 过滤 / 状态
def test_filter_then_clear_restores_all() -> None:
    """修过的坑：过滤是在已过滤结果上二次过滤，删字符时有行回不来。"""
    m = _model()
    m.set_filter("a")
    n1 = len(m.entries())
    m.set_filter("al")
    m.set_filter("a")
    assert len(m.entries()) == n1, (len(m.entries()), n1)
    m.set_filter("")
    assert len(m.entries()) == 5, len(m.entries())


def test_filter_and_sort_together() -> None:
    m = _model()
    m.set_filter(".txt")
    m.sort(COL_SIZE, Qt.DescendingOrder)
    assert _names(m) == ["Alpha.txt"], _names(m)
    m.set_filter("")
    # 过滤清掉后排序状态不该丢
    assert m.sort_column() == COL_SIZE
    assert _names(m)[-1] == "Alpha.txt", _names(m)


def test_sort_survives_new_entries() -> None:
    """切目录换一批数据，排序规则要保持（否则每进一个目录都得重新点）。"""
    m = _model()
    m.sort(COL_MTIME, Qt.DescendingOrder)
    m.set_entries([
        RemoteFile("old.txt", "/old.txt", size=1, mtime=10),
        RemoteFile("new.txt", "/new.txt", size=1, mtime=99),
    ])
    assert _names(m) == ["new.txt", "old.txt"], _names(m)


# ---------------------------------------------------------------- GUI
def test_header_click_toggles_order() -> None:
    pane = _pane()
    hdr = pane.view.horizontalHeader()
    pane._on_header_clicked(COL_MTIME)
    assert pane.model.sort_column() == COL_MTIME
    assert pane.model.sort_order() == Qt.AscendingOrder
    assert hdr.sortIndicatorSection() == COL_MTIME
    pane._on_header_clicked(COL_MTIME)
    assert pane.model.sort_order() == Qt.DescendingOrder, pane.model.sort_order()
    # 换一列回到升序
    pane._on_header_clicked(COL_SIZE)
    assert pane.model.sort_column() == COL_SIZE
    assert pane.model.sort_order() == Qt.AscendingOrder


def test_selection_survives_sort() -> None:
    pane = _pane()
    store = pane.model
    row = [i for i, e in enumerate(store.entries()) if e.name == "beta.md"][0]
    pane.view.selectRow(row)
    QApplication.processEvents()
    assert [e.name for e in pane.selected_entries()] == ["beta.md"]

    pane.apply_sort(COL_MTIME, Qt.DescendingOrder)
    QApplication.processEvents()
    assert [e.name for e in pane.selected_entries()] == ["beta.md"], \
        [e.name for e in pane.selected_entries()]
    cur = pane.current_entry()
    assert cur is not None and cur.name == "beta.md", cur


def test_header_indicator_defaults_to_name() -> None:
    pane = _pane()
    hdr = pane.view.horizontalHeader()
    assert hdr.sortIndicatorSection() == COL_NAME
    assert hdr.sortIndicatorOrder() == Qt.AscendingOrder
    assert hdr.sectionsClickable() is True


def main() -> int:
    print("列表排序测试")
    check("名称升序", test_name_ascending)
    check("名称降序且目录置顶", test_name_descending_keeps_dirs_first)
    check("名称排序不区分大小写", test_name_sort_is_case_insensitive)
    check("大小按字节数排序", test_size_sorts_numerically)
    check("修改时间排序", test_mtime_sort)
    check("类型 / 权限排序", test_type_and_perm_sort)
    check("过滤后清空能恢复全部", test_filter_then_clear_restores_all)
    check("过滤与排序共存", test_filter_and_sort_together)
    check("切目录后排序状态保持", test_sort_survives_new_entries)
    check("表头点击切换升降序", test_header_click_toggles_order)
    check("排序后选中项不丢", test_selection_survives_sort)
    check("表头默认按名称升序", test_header_indicator_defaults_to_name)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
