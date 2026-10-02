---
name: fastnews-dashboard
display_name: 华泰柏瑞快讯看板
description: Open the local Windows fast-news dashboard, inspect the official WorkBuddy connection, locate stage-specific failures, and continuously take over pending agent-direct tasks.
description_zh: 打开本地 Windows 华泰柏瑞快讯看板，检查 WorkBuddy 官方连接，定位晨报、检索及成稿失败，并连续接管等待智能体的看板任务（晨报、产品线索、写作）直到完成。
description_en: Open the Windows news dashboard, check its official WorkBuddy connection, diagnose failed stages, and continuously handle pending agent-direct tasks.
version: 1.2.2
author: Fastnews Cueboard project
---

# 华泰柏瑞快讯看板

用户要求打开看板、检查连接、定位看板失败或"处理看板任务"时使用本技能。实际应用是独立的本地网页，不用聊天内容模拟已经生成的晨报或稿件。

1. 默认项目在用户桌面的“快讯AI看板 WorkBuddy版”。若本技能位于项目内，向上查找 `WORKBUDDY_ORIGIN.json`。其他路径由用户指定，确认该标识后再操作。
2. 打开看板：运行项目内 `runtime/python/pythonw.exe tools/open_dashboard.py`，启动时不自动采集或调用模型。已有正确实例会复用，端口8767被其他程序占用时明确报告，不能结束不明进程。
3. 只读检查：`runtime/python/python.exe -X utf8 tools/workbuddy_connect.py`。用户要求实际检查连接时，加 `--probe`；这会使用官方账户入口调用默认模型并检查公开网页检索。WorkBuddy安全中心可能对官方组件读取自身登录状态弹出确认，由用户决定；不要改安全规则或绕过确认。后台未取得登录状态时，可用页面“设置→打开 WorkBuddy”确认客户端登录；只有安装包含交互组件时才提供“首次连接”。客户端已登录而后台失败，要报告连接尚未接通，不能继续重复要求登录。不要读取、复制或打印凭据，不转回 Codex 或付费 API。
4. 定位失败：先读取最新安全状态和 `data/logs/dashboard.log`，区分补资料、起草、编辑、核验、模板和导出。编辑或核验失败时已有正文仍然保留；不能笼统报告整篇未生成。技术检查通过不等于用户已经采用稿件。

## 连续接管看板任务（核心闭环）

看板进入 agent_direct 后，每个模型步骤落成 `data/agent-exchange/<run_id>/step-NN.request.json`，看板阻塞等待同名 `.response.json`。三类任务都会走这里，用 request 里的 `kind` 区分：
- 写作（kind=writing/compose/edit）：补搜→起草→编辑（编辑与核验已合并为一步）
- 晨报"分类总报"（kind=morning）：整理已存晨报材料并联网补漏
- 产品"写作线索"检索（kind=product）：联网补搜 + 语义评估

用户只需切到 WorkBuddy 说一句"处理看板任务"，智能体就要**连续循环处理直到当前 run 完成**，而不是每步都让用户再催一次。

1. 先 `runtime/python/python.exe -X utf8 tools/agent_bridge.py pending-writes` 列出全部待办（可能有多个 run、多个 step，按 step 顺序）。
2. 逐个处理：读取 request 文件里的 `kind`、`prompt`（含 INPUT_JSON）与 `schema`；`kind=research` 且 `web_search=true` 时用检索工具核实公开来源，其余只用给定材料与范文、不联网编造。晨报/产品检索按 prompt 里的说明联网检索并返回符合 schema 的报告；写作按写作指令处理。
3. 对每个 request，把结果写成 `{"payload": <严格符合schema的对象>, "trace": {...}}`，**原子写入**同目录同名 `.response.json`（先写 `.tmp` 再改名）。payload 之外不写解释文字；无法完成时 `payload.status="needs_materials"` 并在 issues 列明缺口。
4. 写完后再次 `pending-writes`，若还有下一 step 就继续，直到列表为空、且看板该 run 的 job 状态为 completed/failed（可用 `/api/state` 或 `agent_bridge.py list-editions` 辅助确认）。不要在中途停下等用户。
5. 全部完成后，向用户汇报：哪个 run/edition 完成、保存到 `exports/` 的路径、是否 completed、有哪些待核对项。稿件是"线索初稿"，仍需数据核实 + 合规复核 + 语言打磨。

选题、候选勾选和发布由用户决定。只有用户明确要求任务时才运行；刷新不能覆盖人工选材或编辑。模型无权改写公司固定尾部。保持历史截点，不把样例旧数字当作本期事实。

## 故障处置（踩坑速查）

- **页面上点按钮"秒失败 · TypeError"**：`TaskControl.deadline` 被当作整数参与算术时值为 `None`。检查 `fastnews/collection_task.py`、`fastnews/host_provider.py`、`fastnews/writing.py`、`fastnews/writing_newsroom.py` 里所有 `control.deadline - X`。取消 agent_direct 运行时时限，应把 deadline **推到极远**（`time.monotonic()+10**9`）而不是设 `None`，并置 `control.unbounded=True` 供 `agent_runner.file_runner` 识别（判断用 `getattr(control,'unbounded',False)`，不能用 `deadline is None`）。
- **页面上点按钮"无反应"（后端无任何新 run/job 记录、active_job=null）**：看板重启会重新生成 CSRF token（`server.py` 的 `self.token=secrets.token_urlsafe(32)`），浏览器旧页面里的 token 失效，POST 被 403 拒收。处置：让用户 `Ctrl+F5` 强刷页面；或用 `GET /api/state` 取新 token 后带 `X-Fastnews-CSRF` 头直接触发。
- **重启看板**：停止用 `runtime/python/python.exe -X utf8 tools/stop_dashboard.py`（要求 active_job 为空，否则先在页面停任务）；启动**必须后台托管**（`open_dashboard.py --serve`），前台 spawn 的 pythonw 子进程会随命令结束被沙箱回收（表现为日志 ready 但端口无监听）。重启后验证 `tools/open_dashboard.py --check` 返回 ready 且 `/api/state` 的 `server_now` 已更新。
- **用户说"找不到之前生成的初稿"**：这是"跨期次查看"需求，不是没保存。看板"选材与快讯"底部有**「历史稿件库」**（`#archive-panel`），列举**所有期次**的成稿、不必切期次，数据来自 `GET /api/writing-archive`（后端 `writing.archive()`）；查看/复制/下载复用 `POST /api/writing-export`。注意原有"历史版本与运行记录"面板**只列当前期次**的版本（`detail.writing.versions`），这是"找不到"的常见原因。导出文件也可落 `exports/历史初稿/`。
- **改前端 vs 改后端**：看板前端静态文件（index.html / *.js / *.css）是每次 GET 现读磁盘，改动**无需重启**；只有改 Python（server.py / writing.py 等）才需重启看板。

安装或高级维护见项目 `WORKBUDDY.md`。脚本 `scripts/dashboard.ps1` 提供启动与检查；它不执行自动新闻生产。样例、公司模板、数据库及导出稿保持本地，不上传技能市场。
