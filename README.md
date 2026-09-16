# 华泰柏瑞快讯 AI 线索看板｜Codex总交接包 V2

**定位：辅助整理和比较，让使用者决定。**

保留来源窗口的固定源晨扫、Morning Event Pool和产品后二轮检索，接上产品画像、事实级增量比较、可解释编辑评分、人工素材篮和写作交接。

从`START_HERE.md`开始。总文档为`CODEX_MASTER_HANDOFF.md`，可单独阅读；机器配置与专项规格在子目录按需读取。

| 路径 | 用途 |
|---|---|
| AGENTS.md / START_HERE.md | Codex入口与开发边界 |
| CODEX_MASTER_HANDOFF.md | 统一总规格 |
| docs/INTEGRATION_DECISIONS.md | 原方案冲突如何处理、哪些是新增建议 |
| docs/SOURCE_AUDIT.md | 原11文件审阅、26源逐项表、尚未实测的能力 |
| docs/SCORING_SPEC.md | 六分项、null规则、产品差异与试跑评分 |
| docs/DATA_CONTRACTS.md / db/ | 事实/事件/候选/人工选择与旧schema演进 |
| docs/IMPLEMENTATION_PLAN.md | CLI到5源、研究、UI的分阶段计划 |
| docs/ACCEPTANCE.md | 采集、时间、重复、人工权利、导出等行为验收 |
| config/ | 原来源/主题/路由＋V2试跑覆盖层 |
| prompts/ | 抽取、评估、历史比较、交接模板 |
| contracts/ / examples/ | 线索卡与人工选择JSON契约、显式合成示例 |
| references/ | 原ZIP、配套说明、前序计划与V5历史参照 |
| tools/validate_handoff.py | 离线完整性自检，不是应用测试 |

## 重要状态

本包没有fastnews运行实现；没有重新联网核查站点；没有可用模型/搜索/Wind凭证。原配置不等于已经接通。合成例子不是当日事实。评分、预算、首批产品和时间目标是可调整的试跑方案。

原`config/sources.yaml`、`topic_taxonomy.yaml`和`products.example.yaml`逐字保留；来源清单26项、9主题、4产品，没有擅自删减。根目录新规则与整合表明确了业务决定和工程建议，不要求用户再次整理同样材料。
