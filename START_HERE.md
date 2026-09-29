# WorkBuddy 独立版入口

当前启动、登录、使用和交付状态请先读 [WORKBUDDY.md](WORKBUDDY.md)。下文为源项目保留的历史说明；其中 Codex 模型、8765端口和原 GitHub 仓库不适用于此版。

# 从这里交给Codex

这是一套开发交接资料，不是已开发的看板。请将整个目录放入本地项目工作区。

## 最短阅读顺序

1. `AGENTS.md`：开发边界。
2. `CODEX_MASTER_HANDOFF.md`：业务、整合架构、时间、评分、人工选择和实施总规格。
3. `docs/IMPLEMENTATION_PLAN.md`：先执行M0/M1。

实现采集时读取`config/sources.yaml`、`docs/SOURCE_AUDIT.md`；实现评估时读取`docs/SCORING_SPEC.md`、契约与相关prompt；实现数据库时读取`docs/DATA_CONTRACTS.md`和旧schema参照。不要每次把references所有历史稿同时读入。

## 第一条消息，可直接发给Codex

在当前仓库开发“华泰柏瑞快讯AI线索看板”。先读根目录AGENTS.md、CODEX_MASTER_HANDOFF.md和docs/IMPLEMENTATION_PLAN.md，检查现有代码后写一份简短实施计划，再完成M0/M1与对应离线测试。保留原26来源、9主题、4产品路由；新增配置只是试跑预设，不代表来源已接通或持仓已核实。先CLI、不先UI；默认零LLM晨扫；采集与写稿截点分开；保留事实级来源链、人工选题和预算控制。不自动决定、不自动写快讯、不伪造联网结果。缺搜索/模型key时用明确none或隔离fixture。每阶段说明改动、运行命令、测试结果及未接通项。

## 资料包自检

```bash
python tools/validate_handoff.py
```

该命令只检查交接文件完整性，不采集新闻、不调用模型，不代表应用验收通过。
