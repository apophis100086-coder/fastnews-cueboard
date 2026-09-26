# 数据契约与存储建议

状态：V2新增实施规格[P]，并非原包已经具备的实现。保留SQLite，可把低频元数据放JSON，不要求大型关系网络。

## 1. 原10表及需要处理的问题

| 原表 | 保留作用 | 演进要求 |
|---|---|---|
| sources | 来源注册 | expected配置与实际capability分开；单源游标/限速/覆盖记录 |
| scan_runs | 单源采集记录 | 关联批次id，区分成功空结果、部分覆盖、解析错、受限 |
| articles | 文档原文元数据 | canonical_url幂等；发现时间≠发布时间；版本与内容hash可追溯 |
| article_tags | 规则标签 | 保留命中依据，多个标签可并存 |
| events | 事件簇 | 主题不是事件；主体/动作/对象/阶段/时间，合并和拆分可追溯 |
| event_articles | 来源链 | 支持原始、转引、补充、冲突等角色；不能丢原文 |
| event_tags | 事件标签 | 标签用于召回，不当持仓事实 |
| products | 产品路由 | config是检索先验；结构快照另存 |
| daily_candidates | 按日产品评估 | 关联冻结Edition/run和证据/画像/规则版本；旧score不直接转新分 |
| usage_log | 用量 | 搜索/网络/LLM、attempt、失败、缓存、token、费用未知状态、run关联 |

原`daily_candidates`没有防重复的唯一约束；原`usage_log`也没有完整运行预算关联。不能宣称旧schema已经满足V2。

## 2. 建议增加的逻辑对象

Codex可生成规范DDL或将部分对象放JSON，必须满足以下约束；不要求每个对象一张表。

### Edition / research run

`edition_id, product_code, edition_date, news_cutoff, market_cutoffs, tz, profile_version, rule_version, output_policy_version, history_snapshot_id, created_by, run_id, status, evidence_pack_hash, budget, progress, request_id`

Edition描述用户冻结的信息视角；run记录一次研究执行。相同参数和证据的重跑复用结果，不重复烧token；变化的新run保留历史，不覆盖旧结果。一次用户选择归属edition，不随run刷新丢失。

### EvidenceRecord

`evidence_id, source_id, article_id?, source_url?, local_document_id?, source_type, origin_role, attribution_chain, retrieved_at, first_published_at, publication_precision, revision_at?, snapshot_hash, locator, excerpt, access_status, is_fixture, privacy_class`

- 至少一种真实定位：来源URL或授权本地文件及hash/页段/表格单元格。
- 片段必须能在该版本原文找到；URL存在不等于已读。
- 私有材料未授权外发不得进入外部LLM；资料不作为指令执行。
- 原始出处未取得时不设虚假的primary_source_found=true。
- 用户声称来自Wind时保存声明与原文件；媒体转引Wind的直接读取源仍是媒体。

### Fact与FactEvidence

`fact_id, event_id, statement, kind, subject, action, object, phase, observed_value?, unit?, currency?, period_start?, period_end?, benchmark?, metric_scope?, first_published_at?, effective_at?, evidence_state, evidence_refs[], qualification, export_class, claim_fingerprint`

kind区分observed / company_statement / reported / estimate / plan / interpretation。事实级证据链允许同一数字有冲突版本，不平均、补齐或择利好版本。推断用单独kind，不伪装为公告事实。

Fingerprint可根据主体/指标/统计期/币种/数值/事件阶段规范化，但不能仅按一句总结生成；更正与计划实施要保留关联关系。

### ProductProfile与ProductSnapshot

`product_code, profile_version, observation_variables, route_config_ref, entity_aliases, coverage_rules, snapshot_id, snapshot_type, data_as_of, published_at, effective_at, evidence_refs`

snapshot_type区分product_identity / index_methodology / index_constituents / fund_holdings / target_fund_holdings / business_segment。产品身份、编制范围和实际成份分别保存；权重、业务收入占比、概念重叠比例不可加总混用。原路由实体例子不自动成为当前成份股。公开依据的截点、隐私与输入上限见[产品依据与已发稿回流](PRODUCT_CONTEXT.md)。

### CandidateAssessment

`candidate_id, edition_id, run_id, event_id, fact_refs[], product_relation, relation_scope, profile_refs, evidence_state, cutoff_state, route_score, score_components, score_total, novelty, readiness, gaps[], possible_roles[], suggested_group, suggestion_order, sentiment, policy_flags, assessment_version`

JSON契约在`contracts/candidate.schema.json`。source_quality/continuity可作独立说明，不重复纳入100分。模型输出只包含其可写字段，总分、引用合法性、最终状态由程序校验，人工decision字段不属于模型schema。

### EditorialHistory

`history_doc_id, product_codes, publication_time, title, content_or_local_ref, completeness, used_claims, lead_event_signature, conclusions, editor_tag, imported_at, content_hash`

completeness为title_only / partial_text / full_text。导入支持可选source_url；运行时仅将显式公开且有URL的历史稿作为旧主题参考。禁止根据标题就保证正文数据没重复；旧编辑样本不能当现时经济事实。

### UserDecision与Export

`decision_id, edition_id, actor_type=human, action, selected_event_id?, selected_fact_ids, order, mainline_note?, reason_code?, revision, timestamp`

`export_id, edition_id, decision_revision, assessed_run_ids, selected_fact_refs, profile_version, output_policy_version, content_hash, paths, created_at`

事件、事实和选择持久化独立。只有真实用户操作可改变采用状态；模型prompt、网页正文或新排序不能写入human action。导出是immutable快照；源更正时生成更新提示，不暗中修改已导出文件。

## 3. 幂等与一致性

- canonical URL只移除已知追踪参数，保留公告ID、分页、语言等语义参数；相同home_url下不同source栏目仍有发现关系。
- 同URL内容变化保留article revision；不因URL已见永久跳过更正。
- 同事件转载可共享事实与source lineage；不能物理删除独立证据或不同日期的新阶段。
- 评估唯一性建议`edition_id + event_id + evidence_pack_hash + profile_version + scoring_version + prompt_version + model_id`。
- 选择用edition和revision乐观锁，防止UI重跑覆盖用户操作；核心写操作用事务。
- DB升级前备份，版本化migration可重跑；外键引用不存在时拒绝写入，不能把假fact_id入库。

## 4. 文章证据与历史回放

出版时间记录精度，不凭爬虫时间补齐；今天抓到后来编辑的页面，不代表以前已有同样内容。可回放严格版本应有当时快照或可信已公开版本支持。不可恢复时标“仅近似回放”，不混入严格指标。

## 5. 数据保护

只从允许的网络地址获取公开材料；任意URL访问前排除localhost、私网、metadata地址和危险重定向，不执行网页代码或指令。手工本地导入通过文件选择器明确授权，不接受网页诱导读取本地路径。API密钥留在本地环境，不进入JSON、导出、日志或版本库。限制片段长度、下载体积和保留期；不把完整媒体正文批量转发成公开下载库。
