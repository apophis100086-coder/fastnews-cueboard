# 智能体直连模型 · 产品检索与初稿生成

看板在 WorkBuddy 5.6.2 环境里，无法通过自带 CLI 调模型（CLI 认证是沙箱绑定的，
看板这个独立进程读不到）。因此模型能力改由 **WorkBuddy 智能体**（对话里的 agent）承担：
智能体负责联网检索、提炼线索、起草初稿，结果回写进看板库，看板继续负责展示与选材。

## 为什么这样接

- 看板自带 CLI 调模型：`Authentication required`，且安装包无交互登录组件，无法 `/login`。
- WorkBuddy 桌面端模型能力：沙箱 IPC 绑定，只有智能体进程能拿到。
- 结论：模型这一环改由智能体做，看板做"扫描/路由/选材/导出"这些本地环节（这些本来就正常）。

## 桥接脚本

`tools/agent_bridge.py`（用包内 Python 运行，只追加、不改人工选择与历史稿）：

```bash
runtime/python/python.exe -X utf8 tools/agent_bridge.py list-editions
runtime/python/python.exe -X utf8 tools/agent_bridge.py route <edition_id>          # 看扫描路由出的线索
runtime/python/python.exe -X utf8 tools/agent_bridge.py pool <edition_id>           # 看当前候选池
runtime/python/python.exe -X utf8 tools/agent_bridge.py add-candidate <edition_id> <event_id> --snapshot <json>   # 回写检索提炼
runtime/python/python.exe -X utf8 tools/agent_bridge.py save-draft <edition_id> <markdown>   # 保存初稿到 exports/
```

## 典型流程（用户视角）

1. 用 `fastnews-dashboard` 打开看板，选产品建期（扫描/路由照常）。
2. 对智能体说"帮我检索 XX 的产品线索"——智能体联网搜索，把有用线索提炼成 snapshot，
   通过 `add-candidate` 回写候选池。
3. 在看板里勾选线索、填重点（人工选材不受影响）。
4. 对智能体说"按选中的材料生成快讯初稿"——智能体按公司口径起草，
   通过 `save-draft` 存到 `exports/`，再交给用户走 `huatai-etf-news-polish` 打磨。

## 关键约定

- snapshot 里 `origin` 写 `agent_research`，`fact_verified: false`，数据必须注来源 URL。
- 初稿是"线索初稿"，不是可直接发布的稿件；正式稿仍需数据核实 + 合规复核 + 语言打磨。
- 不复制、不打印任何认证凭据；不切换到 Codex 或付费 API。

## 写作流水线直连（2026-09-29 起）

看板的"生成快讯"按钮不再调用本机 CLI（必挂），改为**文件交换式智能体直连**：

1. 用户在看板勾选材料、点击"生成快讯"，写作流水线照常启动（15 分钟时限）。
2. 每个模型步骤（补搜→起草→编辑→核验）由 `fastnews/agent_runner.py` 落成
   `data/agent-exchange/<run_id>/step-NN.request.json`（含完整 prompt 与 JSON schema）。
3. 智能体执行 `agent_bridge.py pending-writes` 查看待办，读取 request 文件，
   用自身模型能力完成该步骤，把 `{"payload": <符合schema的对象>}` **原子写入**
   同目录 `step-NN.response.json`（先写 `.tmp` 再改名）。
4. 看板进程自动拾取响应，走原有校验（事实引用、数字、必写项、口径）与版本保存；
   页面实时显示"智能体直连 · 当前阶段 · 等待智能体响应"。
5. 校验不过会按原有失败路径保留中间稿并在页面列出待核对项，绝不静默放行。

要点：
- 一个 run 通常有 3–4 个步骤，须按 step 顺序逐个响应；全部共用 15 分钟时限，响应要果断。
- `payload` 必须严格符合 request 中的 schema；除此之外不要写解释文字。
- 检索类步骤（research）可用联网检索；成稿类步骤只用 request 中给定材料与范文。
- 读者方案需开启 `agent_direct`（默认方案已开启）；关闭则回退原 CLI 路径（本机不可用）。
