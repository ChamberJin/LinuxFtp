"""本地文件系统，伪装成与远端一致的接口，方便双栏复用同一套列表模型。"""
from __future__ import annotations

import os
import shutil
from typing import List

from .backend import RemoteFile, SessionError


class LocalFs:
    """本地栏不建连接，直接读磁盘。"""

    protocol = "local"

    def __init__(self, root: str = ""):
        self.root = root or os.path.expanduser("~")

    def listdir(self, path: str) -> List[RemoteFile]:
        target = path or self.root
        try:
            names = os.listdir(target)
        except PermissionError as e:
            raise SessionError(f"没有权限访问：{target}") from e
        except FileNotFoundError as e:
            raise SessionError(f"目录不存在：{target}") from e
        except OSError as e:
            raise SessionError(f"无法读取目录：{e}") from e

        out: List[RemoteFile] = []
        for name in names:
            full = os.path.join(target, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            out.append(
                RemoteFile(
                    name=name,
                    path=full,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    is_dir=os.path.isdir(full) and not os.path.islink(full),
                    is_link=os.path.islink(full),
                    mode=st.st_mode & 0o7777,
                )
            )
        out.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return out

    def mkdir(self, path: str) -> None:
        try:
            os.mkdir(path)
        except OSError as e:
            raise SessionError(f"创建目录失败：{e}") from e

    def remove(self, path: str, is_dir: bool = False) -> None:
        try:
            if is_dir and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as e:
            raise SessionError(f"删除失败：{e}") from e

    def rename(self, src: str, dst: str) -> None:
        try:
            os.rename(src, dst)
        except OSError as e:
            raise SessionError(f"重命名失败：{e}") from e

    def parent(self, path: str) -> str:
        p = os.path.dirname(path.rstrip("/"))
        return p or "/"
