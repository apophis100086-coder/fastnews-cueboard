# 重启电脑后启动修复验收｜2026-09-16

## 问题与处理

原入口在前台进程中运行。关闭终端或重启电脑后，`127.0.0.1:8765` 没有服务响应；原来没有 Windows 登录启动项。用户已明确要求解决重启电脑后的重复启动问题。

本次增加当前 Windows 用户登录后启动，并提供桌面恢复入口。自动启动只提供本地看板，不触发新闻采集、搜索、模型调用或写稿。

| 实际改动 | 作用 |
|---|---|
| `tools/open_dashboard.py` | 后台启动与健康等待、正确数据库身份检查、重复启动互斥锁、禁止健康检查走代理或跳转、启动日志、手动打开失败提示 |
| `打开看板.cmd` | 优先使用项目 `.venv`，启动完成后允许关闭命令窗口 |
| `tools/install_startup.ps1` | 安装、检查和移除当前用户登录启动项；创建桌面入口；不覆盖同名的其他应用快捷方式 |
| `tools/WindowsShortcut.cs` | 用 Windows Unicode 快捷方式接口保留中文与空格路径；修复本机 WScript.Shell 接口产生问号路径的问题 |
| `.venv` | 项目专用 Python 3.11.15 环境，仅安装已有两个锁定依赖 |
| `tests/test_launcher.py` | 新增 18 项离线启动边界测试 |
| `RUNNING.md` | 更新日常打开、恢复、关闭自动启动和故障日志说明 |

业务代码、数据库结构和 26 来源 / 9 主题 / 4 产品配置本次未改。

## 本机实际安装

- 登录启动项：`C:\Users\86150\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\华泰柏瑞快讯看板.lnk`
- 桌面入口：`C:\Users\86150\Desktop\华泰柏瑞快讯看板.lnk`
- 目标解释器：`C:\Users\86150\Desktop\快讯AI看板 项目\.venv\Scripts\pythonw.exe`
- 启动参数：项目内 `tools/open_dashboard.py --serve`；桌面入口不加 `--serve`，负责检查、恢复并打开网页。
- 数据库：原来的 `data/fastnews.db`；导出目录仍为 `exports`。
- 监听：只在 `127.0.0.1:8765`；无需管理员、无公网部署、无系统服务和防火墙变更。
- 日志：`data/logs/dashboard.log`、`data/logs/launcher.log`，各 1 MB / 3 份轮转备份；不记录请求正文、密钥或 CSRF token。

安装脚本仅对归属本项目的指定快捷方式进行更新或删除。`-Disable` 保留桌面入口、正在运行的服务和所有数据；再次登录时不再自动启动。

## 实际运行命令

以下均在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.lock
powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_startup.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe tools\validate_handoff.py
.\.venv\Scripts\python.exe tools\open_dashboard.py --check
.\打开看板.cmd --no-browser
powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_startup.ps1 -Disable
powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_startup.ps1
```

同时实际通过 `Start-Process -FilePath <上述登录启动项> -WindowStyle Hidden` 启动安装后的快捷方式；随后重复触发两次。最终状态为自动启动已安装、桌面入口已安装、服务 `ready`。

## 验收结果

| 检查 | 实际结果 |
|---|---|
| Python 全量离线回归 | **325 项通过，0 失败，52.412 秒**，包括 18 项新增启动测试 |
| 原交接包完整性 | **40 个文件 hash 一致** |
| 已安装启动项冷启动 | 成功启动后台服务，`/` 返回 HTTP 200，`/api/state` 确认为原 live 数据库 |
| 重复打开 | 重复触发启动项并运行恢复入口后，监听服务 PID 保持不变，无端口冲突 |
| 服务中断恢复 | 确认没有采集/研究任务，核对进程身份后停止本次启动的服务；健康检查显示 stopped；通过 `打开看板.cmd --no-browser` 恢复，出现新服务 PID |
| 与终端独立 | 启动命令退出后，后续独立命令仍可访问网页和 API |
| 数据保留 | 启动前后 **30 张表的行数和内容 hash 全部相同**；51 篇新闻、6 个期次保留 |
| 启动项可撤销/重复安装 | 关闭后仅登录入口消失，桌面入口保留；重新启用及重复安装均成功，最后保持启用 |
| 中文路径 | 安装项读回的解释器、工作目录、参数均正确，已实际启动验证 |

数据比对证据位于 `data/startup_acceptance/before.json`、`after.json`、`report.json`。本次没有新增新闻，未把本地页面读取描述成真实联网采集。

## 尚未验证及能力边界

没有替用户重启整台电脑；已验证实际登录启动项的执行、服务冷启动、退出父命令后持续访问和恢复路径。下一次用户正常重启并登录，是整机登录场景的最终实机验证。启动项以当前用户登录为触发条件；项目路径、基础 Python 或 Windows 启动项若被删除/禁用，需要恢复后使用。

当前已接通的新闻来源、仍缺的晨报来源、搜索/模型/Wind 未接入等状态沿用 [晨报与产品检索验收](SOURCE_PRODUCT_REPAIR_ACCEPTANCE.md)。这次修复解决的是本地启动生命周期，不新增数据接口。
