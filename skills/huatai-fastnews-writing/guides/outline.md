# 框架任务

沿人工主线、材料排序和必写项整理，不写完整正文。mainline 一句话；title_directions 为1—3个有区别的标题方向。每段 purpose 和 points 先写准备新增的事实、细节或必要理解。analysis、product_connection 允许为空，不要求每段都有分析或产品承接，不强行铺设长传导链。自然段数量随独立材料决定。

origin=product_reference 是官方产品依据，不是当日新闻。只使用标明可写入正文的定位与覆盖特点。product_context 中内部指数资料、较早季度报告、历史发表记录不作为当前产品量化结论。无法支持关键关系时列 blocking_gaps，不靠名称或路由词推断持仓。

claims 记录拟写独立陈述，绑定 material_id、source_id。quote 必须逐字来自 sources.text，含主体、时间及数字口径所需上下文；不能从摘要、历史稿或记忆补事实。kind 区分 fact/reported/plan/forecast/analysis。published_at 来源可靠元数据或原文 publication_quote；data_period 是实际统计期，没有则留空，不机械复制发布日期。未知日期或原文缺失时 supported=false 并说明原因。

计划时间已过不能据此写成完成。涉及需求驱动、政策影响等归因，若拟用于正文也须列入 claims。作者解释建立在已列事实之上，不能新增隐含事实。数字的“约、以上、超过、不低于”等保留。

每段 material_ids、claim_ids 对应拟写事实；dispositions 覆盖全部材料，说明采用位置或不用原因。entities 覆盖涉及的具体公司，A/H归CN，纯港股HK，其他OTHER，无法确定UNKNOWN，列可定位依据。研究阶段召回被禁公司不等于可写入公开稿，不能改成行业事实规避限制。

suggestions 放编辑提醒；changes 写针对反馈的变化。blocking_gaps 只列支撑主线、关键事实和必写项尚缺的必要依据；gaps 列可选材料或已通过缩小范围避开的缺口。不把“不写当前权重”变成整篇必须补权重。材料不足以形成约1100字完整信息时，说明具体缺哪类独立事实，不用分析填篇幅。启用框架内补搜时按随任务加载的 supplement 指南主动补查；supplement_queries 最多3个仍需人工继续处理的公开问题，不含私有原稿或笔记。
