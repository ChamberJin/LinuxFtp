# LinFTP

Linux 上的 WinSCP 风格双栏文件传输客户端。基于 PySide6（Qt Widgets）+ Python，
支持 **FTP** 与 **SFTP** 双协议，界面为浅色卡片式主题，支持标准 / 紧凑两种布局。

## 功能一览

- **双协议**：FTP（MLSD 优先、REST 断点续传、SITE CHMOD）与 SFTP
  （paramiko：密码 / 私钥认证、句柄续传、chmod、known_hosts 主机密钥）
- **双栏浏览**：本地 ←→ 远端上传下载，后台传输队列（进度、限速显示、取消、断点续传）
- **文件名编码自动探测**（UTF-8 / GBK / GB18030 / Big5），国产化 GBK 服务器开箱即用，
  工具栏可手动切换
- **文件管理**：新建 / 重命名 / 删除 / chmod / 文件名通配过滤
- **导航**：路径栏层级点击跳转、右键菜单导航（上级 / 主目录 / 刷新）、
  列表点表头排序（目录恒置顶）
- **文本预览**：只读前 2 MB、编码探测、二进制识别、Ctrl+F 查找
- **站点与书签**：站点管理器、本地 / 远端书签、启动默认书签、最近连接（不存口令）、
  启动页一键进入
- **桌面体验**：现代化主题与矢量图标、窗口大小 / 位置记忆、紧凑模式（Ctrl+Shift+M）、
  常驻托盘（关窗口收进托盘，传输完成系统通知）
- **打包分发**：PyInstaller 目录模式一键打包，产物无需 Python 环境

## 快速开始（源码运行）

```bash
git clone https://github.com/ChamberJin/LinuxFtp.git
cd LinuxFtp

python3 -m venv .venv
TMPDIR=$PWD/.tmp .venv/bin/pip install -r requirements.txt

./run.sh                     # 启动（有书签/最近连接时先弹启动页）
./run.sh --no-start          # 跳过启动页，直接进主窗口
./run.sh --host ftp.example.com --user bob --password yourpass --port 21
./run.sh --protocol sftp --host 192.0.2.10 --user deploy --keyfile ~/.ssh/id_ed25519
```

依赖：Python ≥ 3.10、`PySide6`、`paramiko`（见 `requirements.txt`）。

> 部分发行版 `/tmp` 只有 10MB，pip / Qt 解包会报"设备上没有空间"，
> 工程内脚本统一用 `TMPDIR=$PWD/.tmp` 规避。

## 编译打包（可执行文件）

一条命令打出独立可执行文件，产物不需要目标机器装 Python：

```bash
.venv/bin/pip install pyinstaller   # 仅首次
./build.sh
```

产物在 **`dist/LinFTP/`**：主程序是目录里的 `LinFTP` 可执行文件，
把整个目录拷走即可运行，命令行参数与 `./run.sh` 完全一致。

| 设计点 | 说明 |
|---|---|
| 目录模式（--onedir） | 单文件模式运行时要把上百 MB 解压到 `/tmp`，在小 `/tmp` 机器上必然失败；目录模式直接加载、不碰 `/tmp`，启动也更快 |
| 产物瘦身 | `build.sh` 内置 `strip` 去调试符号 + 剔除 Qml/Quick/VirtualKeyboard/Pdf 等未用 Qt 库，产物约 193M |
| 打包入口 | `main.py`（等价 `python -m linftp`）；`assets/` 图标资源由 `--add-data` 随包携带 |

### 桌面入口（可选）

源码或打包产物都可以再装一套图标和桌面快捷方式：

```bash
bash tools/install_icon.sh
```

## 自测

`tools/` 下按功能模块放置了自动化测试脚本（协议层、编码、书签、启动页、
排序、预览、托盘、窗口记忆等），运行方式：

```bash
.venv/bin/python tools/test_backend.py     # 协议层（需本地 FTP 服务）
.venv/bin/python tools/test_tray.py        # 托盘行为（无需真托盘）
# 其余见 tools/test_*.py
```

部分脚本需要自备本地测试 FTP 服务（2121/2122 端口），详见各脚本头部注释。

## 目录结构

```
run.sh                  源码运行入口（虚拟环境 + TMPDIR 设置）
main.py                 PyInstaller 打包入口
build.sh                一键打包脚本（目录模式 + 产物瘦身）
requirements.txt        依赖清单（PySide6、paramiko）
linftp/                 程序主体
├── __main__.py         程序入口与命令行参数
├── backend/            FTP / SFTP 协议实现（统一 FtpSession 抽象）
├── mainwindow.py       主窗口（双栏、右键菜单、状态栏）
├── queue.py            传输队列（线程池 + 状态机 + 续传判定）
├── launchpad.py        启动页（书签 + 最近连接）
├── bookmarks.py        书签存储（口令可逆混淆）
├── theme.py            主题（调色板 / 矢量图标 / QSS / 多屏摆位）
└── …
tools/                  自动化测试与图标安装脚本
assets/                 应用图标资源
```

## 路线图

- **P2 收尾**：覆盖策略（询问/覆盖/跳过/重命名）、传输限速
- **P3 体验**：拖拽传输、双击本地文件外部编辑回写、侧栏目录树
- **P4 协议**：FTPS（显式 AUTH TLS + 隐式 990）
- **P5 自动化**：同步/镜像目录、命令行批处理
- **P6 打包**：AppImage / deb（PyInstaller 目录模式已完成）
