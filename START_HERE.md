> **2026-10-10 完整交付更新（优先于下方历史说明）：** 用户已授权将完整项目及现有 13 篇样稿、公司模板上传到原私有 GitHub 仓库的发行附件；密钥与历史业务数据仍不上传。桌面只保留“快讯AI看板 项目”为正式目录，旧副本经完整备份与校验后清理。交付维护主线为 main，下载使用 Releases 的 FastNews-Windows.zip。当前版本恢复编辑与独立核验分步，保留三入口、手工选材、版本历史和备份恢复；旧 v3/v4 任务按其原记录执行。账户回传、联网和稿件采用分别核验，不能相互代替。详见 [README](README.md) 与 [本轮验收](docs/READY_TO_USE_20261010.md)。

# 快讯看板：从这里开始

先读 README.md，确认拿到的是 **Git 源码** 还是 **完整发行包**。当前整合分支为 `codex/unify-delivery-20261010`，尚待 WorkBuddy 功能一致性验收。

1. 从 Git 克隆的源码：安装 Python 3.11+，运行 `python tools/setup_workspace.py`。资料与账户另行配置，首次安装不代表写作已可用。
2. 完整发行包：完整解压。需要恢复旧数据时，在第一次启动前运行“恢复数据.cmd”。
3. 在 WorkBuddy 或 Codex 打开当前文件夹，使用本项目的 fastnews-dashboard 技能；也可双击“一键启动.cmd”。不要让客户端重新制作一套网页。
4. 页面选择 WorkBuddy、自接 API 或 Codex。客户端入口发起任务后，在对应项目对话说“处理看板任务”。
5. 迁移和升级前先做数据备份。保留旧项目，禁止通过删库解决问题。

开发维护读取 AGENTS.md、CODEX_MASTER_HANDOFF.md 和 docs/REPOSITORY_INTEGRATION_20261010.md。历史单入口限制、旧端口和早期框架确认要求不代表当前状态。
