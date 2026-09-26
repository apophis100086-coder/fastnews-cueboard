# 华泰柏瑞快讯 AI 线索看板

本地运行的新闻与写作工作台：晨报速览、产品专项检索、人工选材、框架反馈、确认后生成有来源的快讯初稿。用户决定采用哪些材料与最终框架；程序不自动选题、自动确认、发布或作投资决策。

本仓库保存应用源码、配置、数据库迁移、测试和开发文档。实际日常入口见 [使用说明](RUNNING.md)，开发状态及历史验收见 [实施状态](docs/IMPLEMENTATION_STATUS.md)。历史验收记录中的来源数和联网结果属于各次实测，不代表当前所有来源都可用。

最新维护已接通 [产品依据与已发稿回流](docs/PRODUCT_CONTEXT.md)：有日期的公开产品资料进入检索和框架，历史稿用于识别增量；私有稿件保留在本机。五产品真实检索及两篇样稿效果见 [第一轮验收](docs/ROUND1_ACCEPTANCE_20260926.md)。前一轮问题与修复见 [项目审查与修复](docs/PROJECT_AUDIT_20260926.md)。

产品池已增加 **科创50ETF华泰柏瑞（588090）**，与原四只产品共同支持专项检索和写作背景导出。身份依据、观察边界及后续扩充步骤见 [产品池维护](docs/PRODUCT_ONBOARDING.md)，本轮验证见 [科创50接入验收](docs/STAR50_ACCEPTANCE.md)。

产品定点补搜将公开来源的宿主搜索资格与程序晨扫能力分开，片段和待核对陈述随人工选择进入 MD/JSON。此前真实结果、用量和限制见 [定点补搜验收](docs/TARGETED_SEARCH_ACCEPTANCE.md)。

科创产品补搜现增加统计局、SEMI中国及五个产业观察方向，逐站短查询、缺失指标优先补查。近期消息与较早发布的统计/预测背景分别标明，实际查询覆盖可展开查看。改动与真实对照见 [产业数据召回验收](docs/INDUSTRY_RECALL_ACCEPTANCE.md)。

五只产品现共用 **近期线索＋产品背景**，按相关性与当前用途判读；“继续探查”进行不设固定网站白名单的增量搜索，沿用原截点、最多8分钟，期间可继续选材和导出。结果、版本化提示词、实际耗时与证据限制见 [双栏与探查升级验收](docs/EXPLORATION_ACCEPTANCE.md)。

第三步现为 **选材与快讯**。运行时显式读取仓库内的 [写作技能](skills/huatai-fastnews-writing/SKILL.md)；先核对材料并提出框架，用户粘贴、修改、确认后才写正文。复制稿逐段绑定来源，风险提示单列，旧背景交接包入口保留。操作与接口见 [快讯写作说明](docs/NEWS_WRITING.md)，真实案例与限制见 [写作升级验收](docs/NEWS_WRITING_ACCEPTANCE.md)。

## 日常使用

在原电脑上双击“华泰柏瑞快讯看板”桌面入口或项目中的 `打开看板.cmd`。本地页面地址为 `http://127.0.0.1:8765/`。

新电脑取得源码后，在项目根目录运行（Windows、Python 3.11+）：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m fastnews validate-config
.\.venv\Scripts\python.exe tools\open_dashboard.py
```

新电脑需自行配置运行环境及授权。仓库不携带原电脑的数据库、已有新闻、人工选择、登录状态或模型额度；复制源码不代表模型或来源接入已验证。需要原有业务数据时，使用单独备份恢复。

## 版本维护

私有仓库：[apophis100086-coder/fastnews-cueboard](https://github.com/apophis100086-coder/fastnews-cueboard)。首次版本记录始于 2026-09-17，此前的逐次修改无法从现有目录补成真实历史。

- `main`：通过测试的主线；每次改动使用独立分支，检查差异后合并。
- `baseline-20260917`：接入 Git 时的原应用基线，保留当时源码与文档。
- `v0.1.0`：加入仓库维护、可独立运行的测试和推送检查后的起点。

详见 [GitHub 长期维护与回退说明](docs/GIT_MAINTENANCE.md)。新克隆必须执行一次 `git config --local core.hooksPath .githooks`，并安装 Node.js 20+，才能启用本地推送前检查。

```powershell
# 开发中运行现有测试（离线，无模型调用）
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/ui_daily_test.cjs

# 提交后，在临时目录验证这一提交能否独立通过测试
.\.venv\Scripts\python.exe tools\check_repository.py
```

推送检查会检查敏感文件路径及常见密钥格式，并测试当前提交中的源码。它是本机检查，不是 GitHub 服务端强制分支保护。当前未配置 GitHub Actions 云端运行。

## 资料与数据边界

`.env*`（示例除外）、`data/`、`exports/`、`logs/`、`references/`、数据库、密钥文件和生成的压缩包不进入仓库，原电脑上的文件继续保留。

原三份配置 `sources.yaml`、`topic_taxonomy.yaml`、`products.example.yaml` 保持字节不变。测试通过从原 ZIP 核对得到的 SHA-256 验证它们，无需上传原资料；本机存在原 ZIP 时还会逐字比较。

`CODEX_MASTER_HANDOFF.md`、`START_HERE.md`、`MANIFEST.json` 和 `INPUT_MANIFEST.json` 是历史交接记录。`tools/validate_handoff.py` 验证原资料包的文件清单，不适用于持续修改后的 Git 仓库验收；原说明可在基线版本查看。当前开发从 `AGENTS.md`、总交接文档及相关模块文档进入。
