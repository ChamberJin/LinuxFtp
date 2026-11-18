"""路径栏：只读输入框，点路径里的某一层直接跳过去。

看起来还是普通路径栏（没有换成面包屑按钮串），因为面包屑遇到
`/项目文档/BUILD7.0/终版归档/子目录/...` 这种长路径会挤爆面板宽度，
而输入框天然会裁剪。可点击性靠鼠标移到路径上时变成手型 + tooltip 提示。

分段规则和资源管理器 / WinSCP 一致：`/a/b/c` 可点的是 `/`、`/a`、`/a/b`、
`/a/b/c` 四层；点在分隔符 `/` 上算它**左边**那一层。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLineEdit


def split_segments(path: str) -> list[tuple[int, int, str]]:
    """把路径切成可点击的层级：`[(起始字符, 结束字符, 该层路径)]`。

    结束位置不含；点击落在分隔符上时按"左边那一层"处理，
    所以判断命中用的是 `pos <= 结束位置`。
    """
    if not path:
        return []
    if not path.startswith("/"):
        # 少数服务端 / 平台会给相对路径，整条当一层，点了就跳它
        return [(0, len(path), path)]

    out = [(0, 1, "/")]      # 根
    i, n = 1, len(path)
    while i < n:
        if path[i] == "/":   # 连续斜杠不产生空层级
            i += 1
            continue
        start = i
        while i < n and path[i] != "/":
            i += 1
        out.append((start, i, path[:i]))
    return out


def segment_index_at(path: str, pos: int) -> int:
    """字符位置 `pos` 落在第几层；没层级时返回 -1。"""
    segs = split_segments(path)
    if not segs:
        return -1
    for idx, (_start, end, _target) in enumerate(segs):
        if pos <= end:
            return idx
    return len(segs) - 1


class PathEdit(QLineEdit):
    """只读路径栏，点击某一层发出 `segment_clicked(目标路径)`。"""

    segment_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMouseTracking(True)      # 不按键也要收 mouseMove，才能改光标
        # 只读展示位，不参与焦点：点完路径不该留下一个蓝色聚焦框，
        # 也不该把键盘焦点从文件列表上抢走
        self.setFocusPolicy(Qt.NoFocus)
        self._hover_target = ""

    # ---------------------------------------------------------- 设置路径
    def set_path(self, path: str) -> None:
        """设置路径并把显示位置复位到开头。

        `setText()` 会把光标留在末尾，长路径就会滚动到末尾、把根和上层裁掉，
        而"点上层"正是这个路径栏的用处 —— 所以固定从左边开始显示。
        """
        self.setText(path)
        self.setCursorPosition(0)

    # ---------------------------------------------------------- 命中测试
    def segment_at(self, pos) -> tuple[int, str] | None:
        """窗口坐标 `pos` 对应的 (层号, 该层路径)。"""
        path = self.text()
        segs = split_segments(path)
        if not segs:
            return None
        idx = segment_index_at(path, self.cursorPositionAt(pos))
        return idx, segs[idx][2]

    # ---------------------------------------------------------- 事件
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            hit = self.segment_at(event.position().toPoint())
            if hit is not None:
                target = hit[1]
                # 点的是当前目录这一层：不跳，也不打断列表
                if target and target != self.text():
                    self.segment_clicked.emit(target)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        hit = self.segment_at(event.position().toPoint())
        target = hit[1] if hit else ""
        if target != self._hover_target:
            self._hover_target = target
            if target:
                self.setCursor(Qt.PointingHandCursor)
                self.setToolTip(f"点击跳到 {target}")
            else:
                self.unsetCursor()
                self.setToolTip("")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_target = ""
        self.unsetCursor()
        self.setToolTip("")
        super().leaveEvent(event)
