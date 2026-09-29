---
name: fastnews-dashboard
display_name: 华泰柏瑞快讯看板
description: Open the local Windows fast-news dashboard, inspect the official WorkBuddy connection, and locate stage-specific failures.
description_zh: 打开本地 Windows 华泰柏瑞快讯看板，检查 WorkBuddy 官方连接，定位晨报、检索及成稿失败。
description_en: Open the Windows news dashboard, check its official WorkBuddy connection, and diagnose failed stages.
version: 1.0.1
author: Fastnews Cueboard project
---

# 华泰柏瑞快讯看板

用户要求打开看板、检查连接或定位看板失败时使用本技能。实际应用是独立的本地网页，不用聊天内容模拟已经生成的晨报或稿件。

1. 默认项目在用户桌面的“快讯AI看板 WorkBuddy版”。若本技能位于项目内，向上查找 `WORKBUDDY_ORIGIN.json`。其他路径由用户指定，确认该标识后再操作。
2. 打开看板：运行项目内 `runtime/python/pythonw.exe tools/open_dashboard.py`，启动时不自动采集或调用模型。已有正确实例会复用，端口8767被其他程序占用时明确报告，不能结束不明进程。
3. 只读检查：`runtime/python/python.exe -X utf8 tools/workbuddy_connect.py`。用户要求实际检查连接时，加 `--probe`；这会使用官方账户入口调用默认模型并检查公开网页检索。WorkBuddy安全中心可能对官方组件读取自身登录状态弹出确认，由用户决定；不要改安全规则或绕过确认。后台未取得登录状态时，可用页面“设置→打开 WorkBuddy”确认客户端登录；只有安装包含交互组件时才提供“首次连接”。客户端已登录而后台失败，要报告连接尚未接通，不能继续重复要求登录。不要读取、复制或打印凭据，不转回 Codex 或付费 API。
4. 定位失败：先读取最新安全状态和 `data/logs/dashboard.log`，区分补资料、起草、编辑、核验、模板和导出。编辑或核验失败时已有正文仍然保留；不能笼统报告整篇未生成。技术检查通过不等于用户已经采用稿件。

选题、候选勾选和发布由用户决定。只有用户明确要求任务时才运行；刷新不能覆盖人工选材或编辑。模型无权改写公司固定尾部。保持历史截点，不把样例旧数字当作本期事实。

安装或高级维护见项目 `WORKBUDDY.md`。脚本 `scripts/dashboard.ps1` 提供启动与检查；它不执行自动新闻生产。样例、公司模板、数据库及导出稿保持本地，不上传技能市场。
