# CLI 与本地数据闭环

当前阶段汇总见 [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)。本页保留原命令与基础数据结构，并在末尾列出新增命令。

## 1. 运行前提

从项目根目录运行 Python 3.11+。运行依赖锁定于 `requirements.lock`：PyYAML 6.0.3、tzdata 2025.3。自动化测试使用标准库 unittest。该版本以源码目录运行，配置、迁移与 fixture 文件位于仓库中；尚未制作独立安装包。

原有来源、主题和产品文件没有修改。新增 `config/routing.defaults.yaml` 提供版本化规则路由参数；此分数只用于整理检索顺序，不是编辑评分、事实可信概率、持仓证据或投资建议。

## 2. 原五条命令

```powershell
python -m fastnews init-db
python -m fastnews sync-sources
python -m fastnews scan-morning --since-hours 18
python -m fastnews list-events --today
python -m fastnews route-product 513130
```

原命令均可执行。当前默认 live 晨扫尝试原21个晨扫来源，其中3个已接通，其余返回独立缺口，批次为 `partial`。FDA也有元数据读取器，但原晨扫标记为false。缺失能力是结构化业务结果，退出码为0；无效参数、配置或数据库模式冲突退出码为2。退出码0不能代替检查coverage。

每条命令都支持 `--offline`、`--db`、`--config-dir`、`--summary`、`--json`，公共选项可放子命令前后。默认输出 UTF-8 JSON，`--summary` 收起逐条来源和事件，便于检查运行结果；省略它可查看完整来源链和时间字段。

## 3. 可复现的离线闭环

```powershell
python -m fastnews validate-config --summary
python -m fastnews init-db --offline --summary
python -m fastnews sync-sources --offline --summary
python -m fastnews scan-morning --offline --bootstrap-hours 72 --summary
python -m fastnews list-events --offline --today --summary
python -m fastnews route-product 513130 --offline --edition-date 2026-09-15 --news-cutoff "2026-09-14T23:59:59+08:00" --summary
python -m fastnews route-product 513530 --offline --summary
python -m fastnews route-product 588710 --offline --summary
python -m fastnews route-product 517120 --offline --summary
python -m fastnews source-status --offline
```

默认 live 文件为 `data/fastnews.db`，fixture 文件为 `data/fastnews.fixture.db`。数据库内部永久记录模式；即使通过 `--db` 指向同一文件，也拒绝跨模式读写。fixture 只允许带 `is_fixture: true` 的本地合成 JSON，不能把任意真实资料直接混入这一路径。

默认 fixture 是 `tests/fixtures/morning.synthetic.json`，含固定逻辑时钟 `2026-09-15T08:15:00+08:00`。离线扫描、后续事件列表和默认产品视图使用此时钟，便于跨日期复现；数据库批次开始、结束及元数据实际读取时间仍记录真正执行时间。需要其他合成时间可显式传 `--now`，时间必须含时区。

```powershell
python -m fastnews scan-morning --offline --fixture tests/fixtures/morning.synthetic.json --now "2026-09-15T08:15:00+08:00" --summary
```

每次输出含 `data_mode`、`is_fixture`、模式标记及实际能力状态。fixture 来源扫描成功不改变 `needs_probe`，也不写入真实来源的 `last_success_at`。真实搜索/LLM/Wind接口仍未接通。

## 4. 时间与续扫

- 冷启动默认回填最近 72 小时元数据；后续按每源完整覆盖游标向前重叠 2 小时。
- `--since-hours 18` 保留兼容，但会标记显式窗口之前覆盖未知；它不是自动证明昨日白天已扫全。
- 冷启动短窗及其截断续扫结束后不会建立全覆盖游标；下一次默认扫描仍回填 72 小时，补回先前遗漏的白天区间。
- `--bootstrap-hours` 可显式回填更长区间；与 `--since-hours` 互斥。
- `scan-morning --date 2026-09-15` 按北京时间指定日期采集，今天截至实际扫描时刻；与 `--since-hours`、`--bootstrap-hours` 互斥。按日期运行不改日常增量扫描游标与续跑进度。历史补取只覆盖来源当前公开列表，不能保证全日历史完整。页面“获取当天信息”使用同一入口。
- 每源最多 50 条，可用 `--max-items-per-source` 调低，不能通过该参数突破配置上限。
- 触顶返回 `truncated`，保存固定窗口和续扫 token，完整覆盖游标不前进。下次优先完成原窗口；失败不会丢失 token。fixture 内容改变时重扫原窗口，靠幂等约束避免漏项或重复入库。
- 扫描优先按版本修订时间、其次按本篇报道时间发现元数据；选题截点按首次公开时间判断。转载不会刷新旧事实时效。
- `--today` 是跨日工作池：保留近期报道、时间未知线索和已知未来计划，不按首次入库日期等于今天筛选。

保存首次公开、报道、事件发生、实施、统计区间、修订、实际读取时间。缺失为 null；只有日期的消息保存整日不确定区间，同日盘中截点标 `pending_time`。秒级默认截点 23:59:59 覆盖当天完整日期。公告后未来实施保留 `plan`，不会变成已完成。

产品路由返回独立的 M1 只读信息视图及稳定 `view_` ID；默认新闻截点为 Edition 日期前一自然日 23:59:59。这个只读视图还不是 M3 的持久化 Edition。显式行情日期与来源可单独传入：

```powershell
python -m fastnews route-product 513530 --offline --edition-date 2026-09-15 --news-cutoff "2026-09-14T23:59:59+08:00" --market-cutoff "HK=2026-09-14@user_supplied_fixture" --summary
```

不提供行情截点时显示缺项；程序没有交易日历，也不把自然日减一当上一交易日。

## 5. 事件、来源链与产品路由

文章以保留语义参数的 canonical URL 识别，只去明确追踪参数及 fragment。原始 URL 另存。内容或时间元数据更正产生新版本；旧版本与来源关系继续保留。

事件采用保守的显式事件键、结构字段或精确标题规则归并，并检查阶段、日期与数字冲突；同主题不等于同事件。公告、实施、修订、取消分别保留，以相关事件键关联。M1 不做语义模糊去重，不保证换写法的转载都能识别。

来源链逐版本保存来源 ID、实际发现 URL、归属声明、读取时间及内容 hash。转引链只作为未核验声明，既不增加“已独立核实来源数”，也不重标原始出处。元数据标题/导语不等于已读原文，`facts` 为空、`evidence_state=pending`，不会导出可发布事实。

四产品都使用原路由配置；主题命中去重计权，来源和时效权重见新增 YAML。M1 默认隐藏规则得分，用 `--show-scores` 查看；缺时间分数为 null，未命中主题的事件也仍然可见。没有编辑分、成份股权重或历史稿件增量推断。南向总量可进入红利/科技的市场环境线索，但不能推断资金专门买入其中某风格。

路由输出默认 `selected=false`、`default_selection=null`，只读数据库，不修改人工选择。人工决定另存 `fn_decisions`，应通过 `show-edition` 或页面查看；不能拿路由默认值判断已保存选择。M3已实现人工素材篮、选择版本和交接包；没有自动成稿或发布命令。

## 6. SQLite 表与迁移边界

| 表 | M1 用途 |
|---|---|
| `fn_schema_migrations`、`fn_meta` | 迁移版本/hash、数据库模式、旧表保留状态 |
| `fn_sources`、`fn_topics`、`fn_products` | 原配置注册、实际能力、游标和续扫点 |
| `fn_scan_batches`、`fn_source_runs` | 批次与逐源结果，成功空结果/失败/未接通/截断分开 |
| `fn_articles`、`fn_article_versions` | URL身份、不可变元数据版本和 hash |
| `fn_article_sources`、`fn_article_tags` | 逐版本来源发现链、规则命中词 |
| `fn_events`、`fn_event_articles` | 保守事件关系、阶段及多文章关联 |
| `fn_usage` | 每次扫描请求数、token、成本状态和实测耗时 |

迁移只增加 `fn_` 表。发现旧 V1 十表时，先使用 SQLite backup 生成带时间戳的副本，事务建表，保留旧数据并记录 `preserved_not_converted`；旧候选/旧分数没有自动转成 V2 事实。数据库结构冲突或迁移 checksum 不一致时明确拒绝修改。初始化与 registry 同步可反复运行，且不重置已存游标。

## 7. 预算与未接通能力

晨扫每源最多50条，真实网络请求与缓存计数单列；模型和搜索调用为0，token为0。`cost_status=not_called`表示未调用付费provider，不表示没有HTTP请求。离线模式没有网络。

二轮研究已实现12事件 / 20全文 / 每事件4篇、8搜索、4模型请求、24000输入 / 6000输出token的预算预留、结算、失败重试与缓存。默认provider为none；真实搜索与模型未实现连接。公开正文仅由显式 `--fetch-public-text` 开启，当前仅FDA政策允许。失败保留真实用量或unknown，不能填假零。

真实HTTP已实现DNS公网校验、固定连接IP、TLS、逐跳重定向、robots和条款边界、域名限速。页面结构失败与空列表分开；当前有限列表没有完整分页覆盖，详见M2报告。

## 8. 测试

```powershell
python -m unittest discover -s tests -v
python tools/validate_handoff.py
python tools/run_m1_acceptance.py
```

第一条是应用离线测试，第二条只验证交接包原文件完整性。第三条保留M1历史验收流程，其中live扫描显式使用 `--no-network`，不作为真实采集证据。完整合成联调执行 `python tools/run_system_acceptance.py`；真实联调加 `--live`。当前首批5源目标仅4源通过，详见阶段状态。

## 9. 新增命令

```powershell
python -m fastnews probe-sources --summary
python -m fastnews scan-morning --source fda --summary
python -m fastnews scan-morning --no-network --summary
python -m fastnews create-edition 513130
python -m fastnews list-editions
python -m fastnews show-edition <EditionID>
python -m fastnews import-evidence <文件> --edition-id <EditionID>
python -m fastnews import-product-data <JSON文件> --product-code 513130 --declared-source <用户资料声明>
python -m fastnews import-history <文件> --product-code 513130
python -m fastnews assess-events --edition-id <EditionID>
python -m fastnews research-product 513130 --edition-id <EditionID>
python -m fastnews save-decision --edition-id <EditionID> --selected-event-id <EventID> --fact-id <FactID> --actor human --expected-revision 0
python -m fastnews export-handoff --edition-id <EditionID> --decision-revision 1
python -m fastnews record-pilot-feedback <人工反馈JSON>
python -m fastnews pilot-report 513130
python -m fastnews serve --port 8765
```

尖括号为占位符，须替换为实际值。显式指定文件即授权本地读取，不触发私文外发。`--source` 单次明确读取注册源，不改变原晨扫开关；其他来源最近失败状态仍保留。正文人工摘录可以在页面完成。

`create-edition` 支持 `--edition-date`、`--news-cutoff`、`--market-cutoff HK=YYYY-MM-DD@来源`。行情日期可以缺失，相关事实会保留缺口。先导入产品结构/历史正文，再新建一期冻结快照。每次人工保存须传当前revision，支持多次 `--fact-id` 按输入顺序选材。

新增表：002保存Edition、文档、Evidence、Fact、结构、历史、评估、Decision和Export；003保存研究run、provider调用预留及缓存；004保存不可变人工试跑反馈修订。所有迁移追加、已有数据保留，失败整体回滚。

格式、字段和完整示例分别见 [M3材料指南](M3_EDITORIAL_GUIDE.md)、[M4研究预算](M4_RESEARCH_GUIDE.md)、[M5页面指南](M5_UI_GUIDE.md)、[M6试跑指南](M6_PILOT_GUIDE.md)。
