# M0 来源能力探测任务表

编制日期：2026-09-15。**本表仅为离线配置审阅和后续探测任务；26 个来源均为 needs_probe，真实联网探测 0 个，真实适配器 0 个。** 原始来源、主题、产品配置原样保留。首页、发现模式和发现提示不能证明已存在可用列表、RSS、API 或解析器。

## 逐源任务

表中的入口来自原配置，未经访问核验。只有 TrendForce 明确提供 discovery_url；其他行为以实际公开页面及访问许可为准。优先级仅表示建议检查顺序，不能据此预先声称首批 5 源可接通。

每行当前共有状态：检查时间、允许范围、实测元数据字段、全文能力、分页、时区、缺陷、adapter、fixture、last_success 均为 **null（未探测）**；access_review 为 not_checked。离线测试材料来自显著标注的合成 fixture，不计入任何来源的实测 fixture 或 last_success。

| source_id | 原公开入口 | 晨扫 | 检查任务 | 优先组 | 当前状态 |
|---|---|---|---|---|---|
| cls_morning | https://www.cls.cn/ | 是 | 定位早间新闻精选栏目，检查列表时间及导语，确认与港股栏目共享限速 | 1 | needs_probe |
| stcn_egs_morning | https://egs.stcn.com/ | 是 | 定位早知道栏目，检查文章链接、发布时间及翻页 | 1 | needs_probe |
| yicai_overnight | https://www.yicai.com/ | 是 | 定位昨夜今晨栏目，区分摘要日期、转载时间与首次公开时间 | 1 | needs_probe |
| wallstreetcn_breakfast | https://wallstreetcn.com/ | 是 | 定位财经早餐公开栏目，核查允许范围及付费内容边界 | 1 | needs_probe |
| cls_hk_morning | https://www.cls.cn/ | 是 | 定位港股早报，复用同域缓存/限速并保留来源栏目身份 | 1 | needs_probe |
| zhitong_hk_morning | https://www.zhitongcaijing.com/ | 是 | 定位港股早知道栏目，核查列表完整性与发布时间 | 1 | needs_probe |
| hkex_stock_connect_daily | https://www.hkex.com.hk/Mutual-Market/Stock-Connect/Statistics/Historical-Daily | 是 | 检查公开历史统计入口、日期范围、币种、通道及净买额口径 | 1 | needs_probe |
| hkex_news | https://www1.hkexnews.hk/index_c.htm | 是 | 检查公开披露检索、公告元数据及分页，区分公告/实施/修订阶段 | 1 | needs_probe |
| ubs_southbound | https://warrants.ubs.com/sc/underlying/southbound-moneyflow-turnover | 是 | 检查结构化页面与数据日期，保留 UBS 取得路径并回溯港交所核验 | 2 | needs_probe |
| eastmoney_hsgt | https://data.eastmoney.com/hsgt/hsgtV2.html | 否 | 检查公开历史辅助序列，区分资金流入、成交净买额和额度使用 | 2 | needs_probe |
| trendforce | https://www.trendforce.cn/presscenter/news | 是 | 检查显式新闻列表的时间、导语、原始统计归属及分页 | 1 | needs_probe |
| miit | https://www.miit.gov.cn/ | 是 | 从公开首页定位相关政策/电子信息列表，保存公告及实施日期 | 1 | needs_probe |
| nda | https://www.nda.gov.cn/ | 是 | 定位公开新闻和政策列表，区分计划、公告、实施 | 1 | needs_probe |
| csia | https://web.csia.net.cn/ | 是 | 定位公开行业列表，核查首发/转载归属及文章元数据 | 2 | needs_probe |
| semi | https://www.semi.org/ | 否 | 定位允许访问的公开发布栏目，检查语言、时区和付费边界 | 2 | needs_probe |
| nmpa | https://www.nmpa.gov.cn/ | 是 | 定位药品审批/政策列表，保存批准范围及公开时间 | 1 | needs_probe |
| cde | https://www.cde.org.cn/ | 是 | 检查公开审评信息入口，区分申报、受理、临床及批准阶段 | 1 | needs_probe |
| medcube | https://www.medcube.cn/ | 是 | 仅检查公开行业列表及登录边界；受限后转 search/manual | 2 | needs_probe |
| dxy_insight | https://db.dxy.cn/ | 否 | 仅确认公开范围和人工/搜索路径；不进入需登录数据库 | 2 | needs_probe |
| fda | https://www.fda.gov/ | 否 | 定位公开审批/安全性栏目，确认发布时间时区及批准范围 | 1 | needs_probe |
| clinicaltrials | https://clinicaltrials.gov/ | 否 | 检查公开研究记录的首次发布、更新日期和研究阶段 | 2 | needs_probe |
| moa | https://www.moa.gov.cn/ | 是 | 定位农业公开政策/市场栏目，区分统计期与公告时间 | 1 | needs_probe |
| moa_agri_market | https://ncpscxx.moa.gov.cn/ | 是 | 检查公开价格统计、单位、统计日期和发布频率 | 1 | needs_probe |
| mysteel | https://www.mysteel.com/ | 是 | 定位允许访问的公开产业栏目，辨别登录/付费正文边界 | 2 | needs_probe |
| smm | https://www.smm.cn/ | 是 | 检查公开新闻/统计入口，区分报价统计与媒体转载及付费边界 | 2 | needs_probe |
| gov_policy_library | https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary | 是 | 检查公开政策检索和分页，保留发文、公开、实施及修订时间 | 1 | needs_probe |

## 每项探测的验收记录

后续 M2 为每源单独记录以下字段，未知项保持 null：

- source_id、检查时间、原入口和最终 URL；允许访问范围、robots/条款检查结论。
- 实际元数据字段：标题、原文链接、来源、导语、报道发布时间、首次公开时间及精度；如不存在明确字段，记录缺口。
- 全文能力与限制、分页/游标、页面时区、统计期、修订方式、缺陷和覆盖截断原因。
- adapter 名称、经过脱敏且许可使用的解析 fixture 路径、测试命令和结果、last_success。

只有完成访问检查及实际解析验收，才把独立能力覆盖层改为 ready_metadata / ready_fulltext。search_only、manual_only、blocked、degraded 分别记录真实原因。网络失败、零新增、解析失败和未接通不得互换；单源失败不阻塞其他源。

## 边界与缺口

本阶段没有发出 HTTP、搜索、模型或 Wind 请求；M0/M1 的 CLI 和离线合成数据不能证明网站覆盖。后续允许在原 26 源范围按实际可读性先完成 5 源，再明确报告其他 21 源及产品覆盖缺口。

遵守 robots、站点条款、限速、登录、验证码和付费墙，不绕过限制。财联社两个栏目共享域名限速及网络缓存。任意 URL 与重定向在运行时做私网/危险目标校验；配置 URL 语法校验不替代网络安全校验。

已发稿正文库、当前产品/指数结构、Wind 授权仍未接通；使用用户授权资料导入路径或保留 source_backlog。路由关键词不是成份股证据。农业、有色主题与创新药第四产品均保留，不能因首批产品覆盖需求而删除。

## M0 离线检查命令

在仓库根目录执行：

    python -m unittest discover -s tests -p test_config.py -v
    python -m fastnews validate-config

配置加载合并 defaults 及独立能力层，检查原 26 来源 / 21 晨扫标记 / 9 主题 / 4 路由和交叉引用；未知字段、重复 ID/YAML key、错误类型、布尔值冒充数字和未经支持的 provider 均报错。原 3 份配置按字节与 V1 ZIP 比对。

路由来源权重和分段时效沿用 V1 ARCHITECTURE 第 4 节，去重主题求和按 V2 第 6.4 节。可选指数衰减及 preferred_source_multiplier 是可配置工程试跑项，默认不额外抬高偏好来源；未知发布时间保持 null。所有路由分只用于线索排序解释，不触发人工采用。
