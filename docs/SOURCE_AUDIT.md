# 原始来源与适配能力审阅

依据：用户提供的来源交接ZIP中的config/sources.yaml。本次只读取和结构检查，不重新联网验证。

共26项，21项配置晨扫；9主题、4产品路由。T1 11项、T2 8项、T3 7项。只有TrendForce显式设置discovery_url；部分其他home_url本身是统计或披露入口，仍需按各自页面实测。

**全部初始能力为needs_probe，不代表访问失败，也不代表已接通。** 原URL、名称、来源等级、晨扫标记和备注在sources.yaml中原样保留。

| ID | 原名称 | T级 | 晨扫 | 原发现模式 | 原入口/明确栏目 |
|---|---|---|---|---|---|
| `cls_morning` | 财联社｜早间新闻精选 | T3_FINANCIAL_MEDIA | 是 | `site_search_or_listing` | `https://www.cls.cn/` |
| `stcn_egs_morning` | 证券时报·e公司｜早知道 | T3_FINANCIAL_MEDIA | 是 | `site_search_or_listing` | `https://egs.stcn.com/` |
| `yicai_overnight` | 第一财经｜昨夜今晨 | T3_FINANCIAL_MEDIA | 是 | `site_search_or_listing` | `https://www.yicai.com/` |
| `wallstreetcn_breakfast` | 华尔街见闻｜财经早餐 | T3_FINANCIAL_MEDIA | 是 | `site_search_or_listing` | `https://wallstreetcn.com/` |
| `cls_hk_morning` | 财联社｜港股早报 | T3_FINANCIAL_MEDIA | 是 | `site_search_or_listing` | `https://www.cls.cn/` |
| `zhitong_hk_morning` | 智通财经｜港股早知道 | T3_FINANCIAL_MEDIA | 是 | `site_search_or_listing` | `https://www.zhitongcaijing.com/` |
| `hkex_stock_connect_daily` | 港交所｜Stock Connect Historical Daily | T1_PRIMARY | 是 | `official_stats` | `https://www.hkex.com.hk/Mutual-Market/Stock-Connect/Statistics/Historical-Daily` |
| `hkex_news` | 港交所｜披露易 HKEXnews | T1_PRIMARY | 是 | `official_disclosure` | `https://www1.hkexnews.hk/index_c.htm` |
| `ubs_southbound` | UBS｜港股通资金流 | T2_PROFESSIONAL | 是 | `structured_page` | `https://warrants.ubs.com/sc/underlying/southbound-moneyflow-turnover` |
| `eastmoney_hsgt` | 东方财富｜沪深港通数据 | T3_FINANCIAL_MEDIA | 否 | `structured_page` | `https://data.eastmoney.com/hsgt/hsgtV2.html` |
| `trendforce` | TrendForce 集邦咨询 | T2_PROFESSIONAL | 是 | `listing` | `https://www.trendforce.cn/presscenter/news` |
| `miit` | 工业和信息化部 | T1_PRIMARY | 是 | `official_listing` | `https://www.miit.gov.cn/` |
| `nda` | 国家数据局 | T1_PRIMARY | 是 | `official_listing` | `https://www.nda.gov.cn/` |
| `csia` | 中国半导体行业协会 | T2_PROFESSIONAL | 是 | `listing` | `https://web.csia.net.cn/` |
| `semi` | SEMI | T2_PROFESSIONAL | 否 | `listing` | `https://www.semi.org/` |
| `nmpa` | 国家药品监督管理局 | T1_PRIMARY | 是 | `official_listing` | `https://www.nmpa.gov.cn/` |
| `cde` | 国家药监局药审中心 CDE | T1_PRIMARY | 是 | `official_listing` | `https://www.cde.org.cn/` |
| `medcube` | 医药魔方 | T2_PROFESSIONAL | 是 | `listing_or_search` | `https://www.medcube.cn/` |
| `dxy_insight` | 丁香园 Insight | T2_PROFESSIONAL | 否 | `optional_manual_or_search` | `https://db.dxy.cn/` |
| `fda` | U.S. FDA | T1_PRIMARY | 否 | `official_listing` | `https://www.fda.gov/` |
| `clinicaltrials` | ClinicalTrials.gov | T1_PRIMARY | 否 | `official_database` | `https://clinicaltrials.gov/` |
| `moa` | 农业农村部 | T1_PRIMARY | 是 | `official_listing` | `https://www.moa.gov.cn/` |
| `moa_agri_market` | 重点农产品市场信息平台 | T1_PRIMARY | 是 | `official_stats` | `https://ncpscxx.moa.gov.cn/` |
| `mysteel` | Mysteel | T2_PROFESSIONAL | 是 | `listing_or_search` | `https://www.mysteel.com/` |
| `smm` | 上海有色网 SMM | T2_PROFESSIONAL | 是 | `listing_or_search` | `https://www.smm.cn/` |
| `gov_policy_library` | 国务院政策文件库 | T1_PRIMARY | 是 | `official_search` | `https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary` |

## 与当前项目直接相关的提醒

来源V1优先接通港交所/披露易、政府部委、TrendForce、财经媒体，再接其他专业来源。优先级是开发探测顺序，不等于每站都能立刻实现。

清单同时保留农业、有色等通用来源，晨扫阶段尚不知道当天产品，因此不因首批3产品暂时不用就删掉。第4只创新药产品和相关专业源也保留。

正式的近期快讯正文库、当前指数/基金结构资料、Wind接口并未由该清单自动提供。先用授权导入补足；新来源作为待核实项，不在此交接擅自扩展网址。

## 源文件审阅明细

| 原包文件 | 已读范围/提取内容 |
|---|---|
| `AGENTS.md` | 完整：开发边界、零LLM、幂等、访问限制、来源链、规则可配、测试 |
| `HANDOFF.md` | 完整：先CLI和5条命令 |
| `README.md` | 完整：业务两阶段、26源方案说明、预算、技术栈、输出与验收 |
| `.env.example` | 完整：两个空API key、DB路径、时区、日志，无可用凭证 |
| `docs/ARCHITECTURE.md` | 完整：采集、去重阈值、事件、路由公式、二轮与失败处理 |
| `docs/ACCEPTANCE.md` | 完整：M1—M6及总体验收 |
| `config/sources.yaml` | 完整：26来源；逐项保留原配置 |
| `config/topic_taxonomy.yaml` | 完整：9主题，关键词及少量实体示例 |
| `config/products.example.yaml` | 完整：4路由，主题权重、偏好源及扩展词 |
| `prompts/second_round_research.md` | 完整：旧0—5评估维度及TOP3输出 |
| `db/schema.sql` | 完整：10表、来源关联、原候选和usage字段 |

其他输入：配套交接说明MD全文和当前窗口项目计划V0.1全文均已读取；V5仅作为历史规范保存。

本包审阅不构成网站实测报告。Codex必须另输出last_probe时间、允许访问范围、实际适配器和成功/失败证据。
