# 华泰柏瑞快讯 AI 看板

用于晨间阅读、产品线索检索、人工选材、快讯写作与 Word 导出。WorkBuddy、Codex 和自接 API 共用这一套程序与页面。继续使用本仓库维护，当前主线为 `main`。

## 下载后直接使用

**[点击下载 Windows 完整项目](https://github.com/apophis100086-coder/fastnews-cueboard/releases/latest/download/FastNews-Windows.zip)** · [查看版本和下载说明](https://github.com/apophis100086-coder/fastnews-cueboard/releases/latest)

仓库为私有，需要先登录有访问权限的 GitHub 账户。完整包包含程序、Windows x64 运行环境、13 篇写作样稿、3 类公司模板和项目技能，无需另装 Python。若带教没有仓库权限，可由维护者下载完整包后直接转交。

1. 下载 **FastNews-Windows.zip**，解压到一个固定文件夹。
2. 在 WorkBuddy 选择“打开本地文件夹”，打开解压出来的 **快讯AI看板 带教版**。
3. 输入：**“读取本项目的 CODEBUDDY.md，打开现有看板，按项目技能处理任务。”** 也可直接双击文件夹里的 **一键启动.cmd**。

打开后可直接在本项目对话说 **“检索半导体设备的最新线索，保存到看板”** 或 **“生成今天的晨报”**。项目技能建立任务并回写结果，看板自动呈现进度和已保存内容。也可先点网页按钮，再说“处理看板任务”。使用入口、数据备份和使用说明集中在页面右上角“设置与帮助”。实际联网与成稿质量仍需在首次使用中分别核对。

只运行现有程序，不要让客户端依据文档重新开发。GitHub 自动生成的 “Source code” 是开发源码；日常使用请下载上面的完整项目包。每份包的 `PACKAGE_MANIFEST.json` 记录代码版本及逐文件摘要，确保换电脑仍拿到同一版本。

## 三种使用入口

| 入口 | 使用方法 |
| --- | --- |
| WorkBuddy | 在该客户端打开项目文件夹，页面选 WorkBuddy，用项目技能处理任务。 |
| Codex | 在该客户端打开同一项目，页面选 Codex，用项目技能处理任务。 |
| 自接 API | 页面选择 DeepSeek 预设，填写自己的密钥，确认材料发送和费用授权后保存。普通 DeepSeek 接口使用已有材料，不提供模型联网补搜。 |

密钥不放在 GitHub 下载包中。先前获授权的共享密钥只保留在本地交付资料里。新电脑需登录自己的 WorkBuddy/Codex 账户，或者配置 API；程序不会复制其他电脑的账户。

## 稿件与升级

人工选材后依次补充同题资料、起草、编辑、独立核验，再由程序组装固定公司模板。每步保留结果，核验不会改写已保存正文；不自动选材、不自动发布。API 写作共用 15 分钟，智能体任务可由使用者停止。

工作数据在 `data`，导出稿件在 `exports`。页面“数据与备份”可生成一致性备份；升级时解压新版本，**先运行“恢复数据.cmd”，再启动**。不要覆盖旧目录或删除数据库。完整包从空业务库开始，不包含其他人的历史稿件。

[详细使用指南](使用指南.html) · [交付验收](docs/READY_TO_USE_20261010.md) · [维护与回退](docs/GIT_MAINTENANCE.md)

## 开发者

`git clone https://github.com/apophis100086-coder/fastnews-cueboard.git` 获取主线源码，修改使用独立分支。源码不带运行环境和私有资料，维护者从本地发行环境补齐；也可先取得完整包使用，再开展开发。

源码环境使用 Python 3.11+ 执行 `python tools/setup_workspace.py`。该命令建立 `.venv`、安装锁定依赖、配置 `.githooks`；不调用模型。启动器同时支持 `.venv` 与完整包的 `runtime`。检查命令：

```powershell
.venv/Scripts/python.exe -B -X utf8 -m unittest discover -s tests -q
node --test --test-reporter=dot tests/ui_daily_test.cjs
```

推送前自动扫描待推历史并在独立源码目录运行测试，禁止跳过。发布从干净提交构建，验证逐文件摘要后上传到此私有仓库的 Releases。运行环境及获授权样稿模板只进入完整发行附件；密钥和业务数据库不上传。原三份配置保持逐字校验，历史 `MANIFEST.json` 不重写。
