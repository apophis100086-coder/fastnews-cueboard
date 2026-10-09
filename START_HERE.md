# 快讯看板：从这里开始

先读 README.md，确认拿到的是 **Git 源码** 还是 **完整发行包**。当前整合分支为 `codex/unify-delivery-20261010`，尚待 WorkBuddy 功能一致性验收。

1. 从 Git 克隆的源码：安装 Python 3.11+，运行 `python tools/setup_workspace.py`。资料与账户另行配置，首次安装不代表写作已可用。
2. 完整发行包：完整解压。需要恢复旧数据时，在第一次启动前运行“恢复数据.cmd”。
3. 在 WorkBuddy 或 Codex 打开当前文件夹，使用本项目的 fastnews-dashboard 技能；也可双击“一键启动.cmd”。不要让客户端重新制作一套网页。
4. 页面选择 WorkBuddy、自接 API 或 Codex。客户端入口发起任务后，在对应项目对话说“处理看板任务”。
5. 迁移和升级前先做数据备份。保留旧项目，禁止通过删库解决问题。

开发维护读取 AGENTS.md、CODEX_MASTER_HANDOFF.md 和 docs/REPOSITORY_INTEGRATION_20261010.md。历史单入口限制、旧端口和早期框架确认要求不代表当前状态。
