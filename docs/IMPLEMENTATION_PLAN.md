# 分阶段开发任务与入口

这是待实现计划；所有CLI示例均为接口要求，不代表本资料包已经有fastnews代码。

## 1. 第一轮就做这些

1. 查看现有仓库、Python环境、依赖和数据库；不要覆盖用户已有代码。
2. 阅读根AGENTS与主文档，输出`docs/IMPLEMENTATION_STATUS.md`，分清保留/新增/未配置。
3. 对26源配置做离线校验；创建来源实测任务，不执行未授权付费服务。
4. 实现M0/M1与离线测试。使用本包的验收情境自行补充fixture解析器；fixture与生产隔离。
5. 交付命令输出、测试结果、数据表、已接通与未接通项。之后再推进真实采集和UI。

用户已确认定位，无需每一步重新询问“是否做这个看板”。只有新付费服务、私有数据外发、破坏性迁移、增加整站抓取范围等权限变化需要确认。

## 2. 任务拆分

| ID | 任务 | 前置 | 必交付/验收 |
|---|---|---|---|
| M0.1 | 审仓库与配置加载 | 无 | 26源/9主题/4路由不丢，引用存在，未知字段报清楚 |
| M0.2 | 来源能力注册 | M0.1 | needs_probe初始状态，明示首页≠采集器 |
| M1.1 | SQLite与版本迁移框架 | M0 | 初始化可重跑，不删既有数据 |
| M1.2 | 采集接口与mock adapter | M1.1 | 标准记录title/time/url/source/lead、批次及单源状态 |
| M1.3 | 规范化与基础聚类 | M1.2 | URL规范化、事件保留来源、阶段不同不误并 |
| M1.4 | CLI与路由 | M1.3 | 原5条命令可执行，4产品可加载，默认零LLM |
| M1.5 | 时间与幂等测试 | M1.4 | 冷启动、截点分离、重跑、失败隔离、游标未完不越位 |
| M2.1 | 探测原26源 | M0.2 | 逐项记录访问方式、robots/条款、元数据字段、实测日期 |
| M2.2 | 首批5源真实接通 | M1/M2.1 | 合规增量、解析自测、限速重试、coverage报告 |
| M3.1 | Evidence/Fact与画像快照 | M1 | 来源定位、单位/时期、结构as_of均可追溯 |
| M3.2 | 历史稿件导入与增量比较 | M3.1 | 正文与标题粒度分开，缺历史不判不撞题 |
| M3.3 | 编辑评分与schema校验 | M3.1/2 | 六分项、null、固定公式、假引用拒绝 |
| M3.4 | 人工选择与导出 | M3.3 | 未选不导出已选包；refresh不丢选择；受限值不入正文事实区 |
| M4.1 | provider接口与预算 | M3 | none可运行，真实服务由配置启用，未知费用不显示0 |
| M4.2 | 二轮研究闭环 | M2/M4.1 | 固定池优先、定向补漏、有界抽取评估、失败部分返回 |
| M5.1 | 单页看板 | M3.4/M4 | 候选并列、分项折叠、来源/时间一眼可见、可手工改序 |
| M5.2 | 易用性与错误状态 | M5.1 | 三个日常动作、不强制填表、不把失败显示为无新闻 |
| M6 | 三产品试跑与复盘 | M5 | 固定版本，记录10个交易日，多合理答案，不自动学坏偏好 |

优先不强制部署REST服务。CLI和轻量UI共享业务函数即可；不让UI自身负责计费、判时间和持久化选择。

## 3. 原CLI兼容及建议扩展

保留：
```bash
python -m fastnews init-db
python -m fastnews sync-sources
python -m fastnews scan-morning --since-hours 18
python -m fastnews list-events --today
python -m fastnews route-product 513130
```

建议新增（由Codex实现，flag可等价调整但记录文档）：
```bash
python -m fastnews source-status
python -m fastnews scan-morning --bootstrap-hours 72
python -m fastnews route-product 513530 --edition-date 2026-09-15 --news-cutoff "2026-09-14T23:59:59+08:00"
python -m fastnews research-product 513130 --edition-id <ID>
python -m fastnews import-history <FILE> --product-code 513130
python -m fastnews import-product-data <FILE> --declared-source Wind
python -m fastnews export-handoff --edition-id <ID> --decision-revision <REV>
```

`--declared-source`只是用户资料声明，系统仍保留实际输入路径/版本，不能把第三方正文重标源。研究与抓取中的机密字段不进入命令日志。

## 4. 真实来源探测表

每项填写`source_id / 检查时间 / 公开入口 / 允许范围 / 元数据字段 / 全文能力 / 分页 / 时区 / 缺陷 / adapter / fixture / last_success`。

先检验原包优先源；被挡住的标manual/search，不绕过。允许首批选择实际可读的5源，必须报告哪些产品覆盖仍偏弱。发现新必要来源时加入backlog，不擅自启用。

## 5. Definition of Done

每阶段交付需包含：改动路径、运行方法、离线测试数和结果、真实联网成功来源、失败/缺失清单、实际token与耗时（未调用就区分原因），以及下一阶段计划。

测试尚未执行、真实源尚未访问、模型未接入时直说状态。不要用“已实现整体系统”掩盖stub或fixture。最后UI验收必须演示用户选择而非模型自动选好的结果。
