"""书签管理。

书签分两侧：
- local  ：本地目录，点了直接跳转
- remote ：远端目录，需要带着"属于哪个站点"的信息，
           这样没连接时也能自动连上去再跳转

持久化到与站点同一个配置目录，方便一起备份。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from .backend import SessionConfig
from .sites import CONFIG_DIR

log = logging.getLogger(__name__)

BOOKMARKS_FILE = os.path.join(CONFIG_DIR, "bookmarks.json")

# 用固定的混淆前缀区分"已加密"与"历史明文"
_ENC_PREFIX = "enc:"


def _machine_key() -> bytes:
    """由机器信息派生密钥，换机器后旧口令自然失效（需要重输，不会泄露）。"""
    seed = f"{os.uname().nodename}|linftp-bookmark-v1"
    return hashlib.sha256(seed.encode()).digest()


def _obfuscate(plain: str) -> str:
    """可逆混淆。目的不是抗定向破解，而是避免密码以明文躺在配置文件里。"""
    if not plain:
        return ""
    data = plain.encode("utf-8")
    key = _machine_key()
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return _ENC_PREFIX + base64.urlsafe_b64encode(out).decode("ascii")


def _reveal(stored: str) -> str:
    if not stored:
        return ""
    if not stored.startswith(_ENC_PREFIX):
        return stored          # 兼容旧版明文
    try:
        raw = base64.urlsafe_b64decode(stored[len(_ENC_PREFIX):].encode("ascii"))
        key = _machine_key()
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


@dataclass
class Bookmark:
    name: str
    path: str
    side: str = "remote"          # local | remote
    # 远端书签绑定站点，用于"未连接时自动连接"
    protocol: str = "ftp"
    host: str = ""
    port: Optional[int] = None
    username: str = ""
    # 记住密码，避免每次点书签都要重输。
    # 口令用机器绑定的密钥混淆后存，不落明文（见 _obfuscate / _reveal）。
    secret: str = ""
    # 启动时默认打开：设了的那个启动后直接连上去，不再弹启动页。
    # 全库同一时间只允许一个（见 BookmarkStore.set_default）。
    auto_open: bool = False
    # 显示顺序
    order: int = 0

    @property
    def is_remote(self) -> bool:
        return self.side == "remote"

    def site_key(self) -> tuple:
        return (self.protocol, self.host, self.port or 0, self.username)

    def site_label(self) -> str:
        if not self.is_remote or not self.host:
            return ""
        port = self.port or (21 if self.protocol == "ftp" else 22)
        return f"{self.protocol.upper()} {self.username}@{self.host}:{port}"

    def password(self) -> str:
        """取出书签记录的口令（已解密）。"""
        return _reveal(self.secret)

    def as_session_config(self) -> SessionConfig:
        """把书签还原成一份可直接连接的配置。"""
        return SessionConfig(
            protocol=self.protocol or "ftp",
            host=self.host,
            port=self.port,
            username=self.username or "anonymous",
            password=self.password(),
        )


class BookmarkStore:
    """书签的增删改查与持久化。"""

    def __init__(self, path: str = BOOKMARKS_FILE):
        self.path = path
        self._items: List[Bookmark] = []
        self.load()

    # ------------------------------------------------------------ 持久化
    def load(self) -> None:
        self._items = []
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for i, item in enumerate(raw.get("bookmarks", [])):
                kwargs = {k: v for k, v in item.items()
                          if k in Bookmark.__dataclass_fields__}
                kwargs.setdefault("order", i)
                self._items.append(Bookmark(**kwargs))
            self._items.sort(key=lambda b: b.order)
        except Exception as e:  # noqa: BLE001
            log.warning("读取书签失败：%s", e)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        for i, b in enumerate(self._items):
            b.order = i
        data = {"bookmarks": [asdict(b) for b in self._items]}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ 查询
    def items(self, side: Optional[str] = None) -> List[Bookmark]:
        if side is None:
            return list(self._items)
        return [b for b in self._items if b.side == side]

    def find(self, path: str, side: str) -> Optional[Bookmark]:
        for b in self._items:
            if b.side == side and b.path == path:
                return b
        return None

    def is_bookmarked(self, path: str, side: str) -> bool:
        return self.find(path, side) is not None

    def default(self) -> Optional[Bookmark]:
        """启动时要自动打开的书签；没设置返回 None。"""
        for b in self._items:
            if b.auto_open:
                return b
        return None

    def set_default(self, bm: Optional[Bookmark]) -> None:
        """把某个书签设为启动默认，传 None 表示取消。

        全局只能有一个：设新的会顺手把旧的取消，否则启动时到底开哪个
        取决于遍历顺序，属于自找麻烦。
        """
        for b in self._items:
            b.auto_open = b is bm
        self.save()

    # ------------------------------------------------------------ 修改
    def add(self, bm: Bookmark) -> Bookmark:
        """同一侧同路径视为同一个书签，存在则改名覆盖，避免重复。"""
        old = self.find(bm.path, bm.side)
        if old is not None:
            old.name = bm.name
            old.protocol = bm.protocol
            old.host = bm.host
            old.port = bm.port
            old.username = bm.username
            self.save()
            return old
        self._items.append(bm)
        if bm.auto_open:
            # 新加的就是默认，那就把旧的默认取消掉（set_default 内部会存盘）
            self.set_default(bm)
        else:
            self.save()
        return bm

    def remove(self, bm: Bookmark) -> None:
        for i, b in enumerate(self._items):
            if b is bm or (b.side == bm.side and b.path == bm.path
                           and b.name == bm.name):
                del self._items[i]
                self.save()
                return

    def remove_at(self, index: int) -> None:
        if 0 <= index < len(self._items):
            del self._items[index]
            self.save()

    def clear(self, side: Optional[str] = None) -> None:
        if side is None:
            self._items = []
        else:
            self._items = [b for b in self._items if b.side != side]
        self.save()

    def rename(self, bm: Bookmark, new_name: str) -> None:
        bm.name = new_name
        self.save()

    def move_up(self, bm: Bookmark) -> None:
        i = self._index_of(bm)
        if i > 0:
            self._items[i - 1], self._items[i] = self._items[i], self._items[i - 1]
            self.save()

    def move_down(self, bm: Bookmark) -> None:
        i = self._index_of(bm)
        if 0 <= i < len(self._items) - 1:
            self._items[i + 1], self._items[i] = self._items[i], self._items[i + 1]
            self.save()

    def _index_of(self, bm: Bookmark) -> int:
        for i, b in enumerate(self._items):
            if b is bm:
                return i
        return -1

    # ------------------------------------------------------------ 构造
    @staticmethod
    def make_remote(name: str, path: str, cfg: SessionConfig) -> Bookmark:
        """从连接配置生成远端书签，顺带记住口令。"""
        return Bookmark(
            name=name,
            path=path,
            side="remote",
            protocol=cfg.protocol,
            host=cfg.host,
            port=cfg.port,
            username=cfg.username,
            secret=_obfuscate(cfg.password),
        )

    @staticmethod
    def make_local(name: str, path: str) -> Bookmark:
        return Bookmark(name=name, path=path, side="local")
