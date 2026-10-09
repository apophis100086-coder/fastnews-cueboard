# 华泰柏瑞快讯 AI 线索看板

本地运行的新闻与写作工作台。人工选材后生成初稿，支持修改、来源追溯、Word 导出和历史数据备份；最终选题和采用由使用者决定，不自动发布。

## 当前版本在哪里

**统一仓库：** https://github.com/apophis100086-coder/fastnews-cueboard

**最新整合候选：** `codex/unify-delivery-20261010`。它合入最新三入口、DeepSeek、存储和交付成果，保留原 Codex 与改进版两条历史。原 Codex 对照基准是 `b08edbc`，导入的改进版是 `b9f7cdc`。原目录和业务数据保留。

本分支尚待 WorkBuddy 功能一致性验收，不能称为一比一迁移已完成。2026-10-10 的 `main` 仍是较早版本；首次获取请指定本分支，并记录实际提交号，后续正式发行必须固定具体提交。

- [整合范围、已知差异与验收清单](docs/REPOSITORY_INTEGRATION_20261010.md)
- [Git 维护和数据回退](docs/GIT_MAINTENANCE.md)
- [当前机器可读状态](PROJECT_STATUS.json)
- [完整发行包使用指南](使用指南.html)

## 从 GitHub 取得源码

在有仓库访问权限的 Windows 电脑运行：

```powershell
git clone --branch codex/unify-delivery-20261010 https://github.com/apophis100086-coder/fastnews-cueboard.git fastnews-cueboard
cd fastnews-cueboard
git rev-parse HEAD
```

把这个完整文件夹作为项目在 WorkBuddy 或 Codex 中打开。让客户端读取现有程序并运行，不要求它根据文档重做看板。开发与修复继续在这个仓库的功能分支进行。

### 源码首次安装

需要 Windows 10/11、Python 3.11+。在项目根目录运行：

```powershell
python tools/setup_workspace.py
```

安装器创建本项目 `.venv`，按 `requirements.lock` 安装依赖，检查公开配置并启用本地 Git 推送检查；不启动服务、不导入资料、不配置账户、不调用模型。若电脑使用 Python Launcher，可改为 `py -3.11 tools/setup_workspace.py`。维护和推送还需要 Node.js 20+。

安装完成后双击 **一键启动.cmd**。源码入口兼容本地 `.venv` 与发行包 `runtime`。本整合候选端口为 **8807**，打开后以实际显示地址为准；完整发行包由构建器单独设置端口。端口被其他副本占用时，启动器拒绝接管。

**源码不包含公司模板、13 篇样稿、新闻和旧稿数据库、API 密钥及运行环境。** 写作前需要从获授权的本地资料补齐样稿与模板；迁移旧业务数据应先备份，并在尚未启动的新副本中恢复。请先恢复再启动，恢复工具不会覆盖已有业务库。源码安装通过不表示材料完整或模型账户可用。

## 三种使用入口

| 入口 | 使用方式 | 已知边界 |
| --- | --- | --- |
| WorkBuddy | 页面选 WorkBuddy；在当前项目对话使用项目技能接手任务 | 账户、联网和实际写作需在该客户端验收 |
| Codex | 页面选 Codex；在当前项目对话使用项目技能接手任务 | 当前整合候选使用文件任务协议，与原版程序自动调用 CLI 有差异 |
| 自接 API | 页面填写 API 配置并确认材料发送与费用授权，由程序执行 | 普通 DeepSeek Chat Completions 处理已有材料，不提供本项目所需的模型联网补搜 |

WorkBuddy 技能位于 `.codebuddy/skills/fastnews-dashboard`，Codex 技能位于 `.agents/skills/fastnews-dashboard`。页面发起任务后，在对应项目对话说“处理看板任务”。切换入口不自动迁移其他文件夹的数据库，也不复制账户能力。

DeepSeek 预设及本地共享包构建工具已纳入源码；仓库没有共享密钥。凭据只在本地配置，含共享密钥的发行包按授权私下交付，不上传 GitHub。

## 备份、升级和带教交付

页面“数据与备份”创建一致性备份；在新副本首次启动前，用“恢复数据.cmd”恢复。程序与数据分别管理，不通过删库解决故障，也不直接覆盖正在使用的目录。

维护者在完成 WorkBuddy 一致性验收后，从固定提交生成带运行环境和获授权资料的完整包，并核对包内 `PACKAGE_MANIFEST.json`。带教使用完整包即可，无需学习 Git。

`tools/build_workbuddy_package.py` 依赖本机已有 `runtime`、写作样稿、模板和来源配置种子。只克隆源码不等于已经取得可交付运行包。历史发行验收见 [三入口记录](docs/THREE_ENTRY_ACCEPTANCE_20261008.md) 和 [DeepSeek 记录](docs/DEEPSEEK_SHARED_20261008.md)，不能当作新客户端已验收的证据。

## 开发检查

```powershell
.venv/Scripts/python.exe -B -X utf8 -m unittest discover -s tests -q
node --test --test-reporter=dot tests/ui_daily_test.cjs
```

提交后推送触发 `.githooks/pre-push`：扫描完整待推历史的敏感路径和常见密钥格式，在独立源码目录运行 Python 和前端测试。不得跳过检查。新克隆需运行安装器或执行 `git config --local core.hooksPath .githooks`。

原三份来源、主题和产品示例配置继续按固定字节校验。历史 `MANIFEST.json` 只记录最初交接资料，不重写为当前版本哈希。完整开发边界见 `AGENTS.md` 顶部；历史说明发生冲突时，以当前授权和整合清单为准。
