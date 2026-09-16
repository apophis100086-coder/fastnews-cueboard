# GitHub 长期维护与回退

私有仓库：https://github.com/apophis100086-coder/fastnews-cueboard

版本历史始于 2026-09-17。`baseline-20260917` 记录接入 Git 时的现有应用；`v0.1.0` 记录维护配置完成后的起点。Git 只保存已提交文件，推送后 GitHub 才有相应版本。此前每一次微调没有可恢复的 Git 历史。

## 每次迭代

1. 查看 `git status` 和近期提交；先保护未提交改动，再从主线建立本次分支。
2. 修改一项明确需求，核对差异，执行相应测试。涉及数据库迁移，先创建一致性备份并验证迁移保留用户数据。
3. 提交本次改动，说明修改原因和效果。推送分支后在 GitHub 查看比较结果或 Pull Request。
4. 确认测试及人工验收结果后合并主线；重要版本加标签。后续有问题，按提交定位和回退。

例如，为调整来源显示建立分支：

```powershell
git status
git switch main
git pull --ff-only
git switch -c fix/source-display

# 修改完成后，先看差异并运行测试
git diff
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/ui_daily_test.cjs
git add <本次改动文件>
git diff --cached
git commit -m "fix: explain source availability in the dashboard"
git push -u origin fix/source-display
```

示例中的分支名应随任务变化；`<本次改动文件>` 替换为实际路径。不要把用户未完成的其他工作一起提交。无需每次微调都新建仓库，继续使用这个仓库即可。

## 推送前检查

当前电脑在此仓库启用了：

```powershell
git config --local core.hooksPath .githooks
```

Git hook 每次推送会调用 `tools/check_repository.py --pre-push`：

- 确认工作区已提交，推送的分支指向当前检查的提交。
- 将该提交的源码取到临时目录；目录中没有本机数据库、私有参考资料或授权文件。
- 检查禁止上传的路径及常见密钥格式；命中时只显示文件名及类型，不输出密钥内容。
- 运行 Python 和前端离线测试，失败就停止此次推送。测试结束删除临时副本。

新克隆需要创建 `.venv`、安装 `requirements.lock` 和 Node.js 20+，然后重新配置 hook。Git 不会自动启用从远程下载的 hook。检查不调用真实采集或模型，也不启动云端 GitHub Actions。

这是本地检查，可以被有权限的人修改；目前不能把它称为 GitHub 服务端强制分支保护。服务端规则是否可用取决于仓库与账号计划，未开通付费升级。常见密钥格式检查也不能代替人工审查新上传的资料。

## 定位与回退

先查看历史和改动：

```powershell
git log --oneline --decorate -20
git show <提交号>
git diff v0.1.0..main
```

需要撤销已提交变更时，从主线建立修复分支，使用 `git revert <提交号>` 生成一条有记录的反向改动，重新测试后提交修复。合并提交的回退需要单独检查父提交，不直接照搬普通提交命令。不用 `git reset --hard` 或强制推送去覆盖共享历史。

如需比较旧版本，优先在独立工作区打开标签，避免改动正在运行的项目目录。旧代码可能不支持新版数据库，必须使用对应的备份副本验证，不能直接用旧代码操作正式数据库。

## 数据单独备份

`.gitignore` 排除 `.env*`（示例除外）、`.venv/`、`data/`、`exports/`、`logs/`、`references/`、数据库、私钥和生成压缩包。本轮只设置版本管理，没有删除这些本地文件。

GitHub 可恢复代码，不包含新闻数据、人工选择、导出稿件、用户偏好或模型登录状态。它们需要单独备份。SQLite 运行时可能使用 WAL，不应只复制主 `.db` 文件；使用 SQLite backup API 创建一致性快照，或停服后备份完整数据库。备份文件仍不上传此仓库。

## 初始验证记录

接入前：416 项 Python 测试、29 项前端测试通过，M0/M1 离线 CLI 验收 16 个命令通过；原资料包 40 项完整性检查在本轮维护修改前通过。验收使用隔离数据库，没有重新采集新闻或调用模型。

本轮维护会修改 README、AGENTS 和忽略规则，因此历史 `MANIFEST.json` 不再匹配当前工作目录；它保留原交接包意义，不更新其原件哈希。当前源码验证用单元测试及 `tools/check_repository.py`，原三份配置另有固定 SHA-256 和可选原 ZIP 比对。
