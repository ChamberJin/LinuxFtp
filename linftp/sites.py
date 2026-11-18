"""站点管理器：把连接配置持久化到 ~/.config/linftp/sites.json。

P0 阶段密码明文存储，但文件权限设为 0600；
后续可加主密码 + Fernet 加密（见 README 的路线图）。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from typing import List

from .backend import SessionConfig

log = logging.getLogger(__name__)

CONFIG_DIR = os.environ.get("LINFP_CONF_DIR") or os.path.expanduser("~/.config/linftp")
SITES_FILE = os.path.join(CONFIG_DIR, "sites.json")


class SiteStore:
    def __init__(self, path: str = SITES_FILE):
        self.path = path
        self._sites: List[SessionConfig] = []
        self._recents: List[SessionConfig] = []
        self.load()

    def load(self) -> None:
        self._sites = []
        self._recents = []
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for item in raw.get("sites", []):
                kwargs = {k: v for k, v in item.items()
                          if k in SessionConfig.__dataclass_fields__}
                self._sites.append(SessionConfig(**kwargs))
            # 最近连接：仅用于启动页展示，不做口令持久化
            for item in raw.get("recents", []):
                kwargs = {k: v for k, v in item.items()
                          if k in SessionConfig.__dataclass_fields__}
                kwargs["password"] = ""
                self._recents.append(SessionConfig(**kwargs))
        except Exception as e:  # noqa: BLE001
            log.warning("读取站点配置失败：%s", e)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {
            "sites": [asdict(s) for s in self._sites],
            "recents": [asdict(s) for s in self._recents],
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        try:
            os.chmod(tmp, 0o600)   # 内含密码，收紧权限
        except OSError:
            pass
        os.replace(tmp, self.path)

    def sites(self) -> List[SessionConfig]:
        return list(self._sites)

    def recents(self, limit: int = 5) -> List[SessionConfig]:
        """最近连接过的站点，越新越靠前。"""
        seen = set()
        out: List[SessionConfig] = []
        for cfg in self._recents:
            key = (cfg.protocol, cfg.host, cfg.port or 0, cfg.username)
            if key in seen:
                continue
            seen.add(key)
            out.append(cfg)
            if len(out) >= limit:
                break
        return out

    def touch_recent(self, cfg: SessionConfig) -> None:
        """记录一次成功连接。同一站点只保留最新一条，最多 10 条。"""
        key = (cfg.protocol, cfg.host, cfg.port or 0, cfg.username)
        keep = SessionConfig(
            protocol=cfg.protocol, host=cfg.host, port=cfg.port,
            username=cfg.username, password="",
            passive=cfg.passive, encoding=cfg.encoding, timeout=cfg.timeout,
        )
        self._recents = [
            c for c in self._recents
            if (c.protocol, c.host, c.port or 0, c.username) != key
        ]
        self._recents.insert(0, keep)
        del self._recents[10:]
        self.save()

    def upsert(self, cfg: SessionConfig) -> None:
        for i, s in enumerate(self._sites):
            if s.host == cfg.host and s.username == cfg.username \
                    and s.protocol == cfg.protocol and s.port == cfg.port:
                self._sites[i] = cfg
                break
        else:
            self._sites.append(cfg)
        self.save()

    def remove(self, index: int) -> None:
        if 0 <= index < len(self._sites):
            del self._sites[index]
            self.save()
