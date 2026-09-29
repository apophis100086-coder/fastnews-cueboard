# M2 实际来源接入与验收

## 结论

2026-09-15 已完成原 **26 个来源的真实公开访问探测**。最终 **4 个来源可读取公开列表元数据**：TrendForce、工信部、国家数据局、FDA。原配置 FDA 的 `morning_scan=false` 保留，因此默认晨扫实际读取其中 **3 个来源**。

**M2.2 的首批 5 源目标部分通过，目前为 4 源。** 未用额外站点或受限内容凑数。来源清单、主题表和四产品路由未改写。前期“5 个技术解析器可返回条目”的探测记录已被条款复核结果取代：e 公司要求书面授权，已禁用自动读取；SMM 也因明确的自动抓取限制而保留人工路径。

这里的“可读取”只表示标题、链接及页面实际提供的日期等字段能够取得，不代表事件已核实、首次公开时间已确定或可以直接用于写稿。所有元数据的 `evidence_state=pending`，`first_published_at=null`。正式晨扫没有请求文章全文，也没有调用搜索、模型或 Wind；研发接口探测曾收到附带完整农业日报的列表响应，因此没有将该接口启用。

## 代码与命令

- `fastnews/network.py`：标准库安全 HTTP 客户端。校验 URL、DNS 公网地址、固定实际连接 IP、TLS 主机名和每跳重定向；拒绝私网、凭证、非 HTTP(S)、危险端口、HTTPS 降级和越出来源范围的重定向。按域名限速、缓存、有限重试、响应大小与超时上限。robots 通配符、最长规则及特定 user-agent 规则生效；403 保守阻止，404 明确记录无 robots 文件，不视为条款许可。
- `fastnews/live_sources.py`：四个启用的列表读取器、一个因条款禁用的 e 公司解析器、26 源独立探测和能力报告。页面结构变化返回 `parse_error`；时间未知保留 null；标题、URL 路径和抓取时间都不用于补造发布日期。
- `config/live_sources.yaml`：独立覆盖层，记录验证入口、解析器、范围、时间缺项、`metadata_allowed`、`fulltext_allowed` 和条款限制。来源期望配置与本次实际结果分开。
- `tools/probe_sources.py`：实际探测工具；已知条款禁止的来源也会在 `--raw` 检查中跳过，不能用该开关绕过限制。

在仓库根目录执行：

```powershell
python tools/probe_sources.py --output data/probes/source_probe.json
python -m unittest discover -s tests -p test_live_sources.py -v
python -m fastnews probe-sources --summary
python -m fastnews scan-morning --bootstrap-hours 72 --summary
```

第三、四条通过主程序接入探测及数据库扫描，详细命令以 `python -m fastnews --help` 为准。真实扫描不使用 `--offline`；离线测试内的 HTML 为显式合成样例，不计入联网成果。

## 四个实际读取器

| 来源 | 验证入口 | 全列表解析条目 | 时间与覆盖限制 |
|---|---|---:|---|
| TrendForce | https://www.trendforce.cn/presscenter/news | 5 | 有页面报道日期和列表导语；首次公开未核验；第一页范围；仅本地个人元数据索引，保留来源，未经许可不转载正文 |
| 工信部 | https://www.miit.gov.cn/ | 76 | 仅本站首页可见条目，页面日期保留日精度；首次公开未核验，不据此推定完整历史覆盖 |
| 国家数据局 | https://www.nda.gov.cn/sjj/swdt/list/index_pc_1.html | 25 | 新闻动态五组各第一页；保留日精度；媒体声音仍为媒体转引，不变成一手事实 |
| FDA | https://www.fda.gov/news-events/fda-newsroom/press-announcements | 10 | 可见日精度与机器零时刻不同，原值保存在来源链；时区精度未核实时不填报道时刻；原始晨扫开关为 false |

所有列表都返回 `complete=false` 和明确的 `listing_scope_incomplete`。条目上限触发时另外保存基于稳定内容指纹的续扫 token；列表发生变化就重新回扫。不会以“请求成功”推进完整覆盖游标。缺少完整分页/历史范围属于可见缺口。

FDA 条款允许使用一般公共领域内容，但有例外。该来源的 `fulltext_allowed=true` 是访问政策判断；**M2 没有把它标为 `ready_fulltext`**。二轮全文功能及其单独验收状态见 M4 集成报告。

## 26 源定稿探测结果

机器报告探测时间为 **2026-09-15T11:50:09.299045+00:00**。它是实际 UTC 时间，不修改网页自己的发布日期。结果为：4 ready_metadata、12 degraded、7 blocked、3 manual_only；48 次网络请求中包含 24 次公共 DNS 查询，20 次缓存命中，7 个来源失败。网络和站点状态后续可能变化，以每次运行结果为准。

| source_id | 能力结果 | 当前限制 |
|---|---|---|
| cls_morning | degraded | 首页可访问，早间栏目解析器未接通 |
| stcn_egs_morning | manual_only | 实际解析过首页，但书面授权要求使自动采集禁用 |
| yicai_overnight | degraded | 首页可访问，指定栏目解析器未接通 |
| wallstreetcn_breakfast | degraded | 首页可访问，财经早餐解析器未接通 |
| cls_hk_morning | degraded | 首页可访问，港股早报解析器未接通 |
| zhitong_hk_morning | degraded | 首页可访问，港股早知道解析器未接通 |
| hkex_stock_connect_daily | degraded | 统计页可访问，日期/口径/分页解析未完成 |
| hkex_news | degraded | 入口可访问，披露检索元数据未接通 |
| ubs_southbound | degraded | 页面可访问，日期和统计口径解析未接通 |
| eastmoney_hsgt | degraded | 页面可访问，公开数据接口解析未接通 |
| trendforce | ready_metadata | 私人本地元数据索引；第一页、首次公开和全文缺口 |
| miit | ready_metadata | 首页局部范围；首次公开和全文缺口 |
| nda | ready_metadata | 新闻栏目局部范围；首次公开和全文缺口 |
| csia | degraded | 返回脚本外壳，正文列表元数据未接通 |
| semi | blocked | 本次 HTTP 403 |
| nmpa | blocked | 本次 HTTP 412，未绕过 |
| cde | blocked | robots HTTP 403，未访问目标列表 |
| medcube | blocked | TLS 验证失败，未关闭证书校验 |
| dxy_insight | manual_only | 公开入口可读，数据库授权及解析未接通 |
| fda | ready_metadata | 日期精度待核实；默认不参加晨扫；全文状态见 M4 单独验收 |
| clinicaltrials | blocked | robots 重定向至 NCBI misuse/abuse 页面，未继续或改变访问身份 |
| moa | blocked | robots disallow |
| moa_agri_market | degraded | 公开接口已探明，但列表附带整篇报告，不启用零全文晨扫 |
| mysteel | degraded | 首页可访问，元数据解析及商业用途授权未接通 |
| smm | manual_only | 法律声明明确禁止未书面授权的机器人抓取/监视等 |
| gov_policy_library | blocked | robots disallow |

## 农业公开接口探索结果

为了继续在原 26 源内寻找第 5 源，实际读取了农业市场平台首页及它直接引用的两个公开脚本。脚本明确调用只读查询 `POST /product/FarmDaily/selectDailyList`，参数为 `pageSize` 与 `currentPage`。程序以安全客户端得到 HTTP 200 和 6 行公开数据。

该列表响应包含 `counclesion`、`daylyDate`、`createDate`、`source`，还附带 `countent` 整篇日报正文。`daylyDate` 是统计日，`createDate` 还出现次日回补；两者均不能直接当首次公开时间。已观察接口没有字段投影能力，因此不接入默认“零全文晨扫”。这次研发探测文件不会冒充晨扫数据或已完成采集器。

端点及请求形状来自官网静态脚本，未猜测 API，未调用详情端点。`SafeHTTPClient.post_json` 仅允许该精确只读查询 URL 与受限参数；不会开放任意 POST。后续如需使用，应归入二轮有预算的资料读取并核对统计期、正文差异及首次公开时间。

## 离线与真实验收

**31 项 M2 离线测试全部通过。** 覆盖 URL/DNS/重定向安全、robots、来源条款禁用、参数预算、结构异常、元数据来源链、日精度保留、窗口过滤、上限与续扫。测试本身不进行联网。

实际读取验收另存 `data/probes/discovery_acceptance.json`。首次强制联网读取中，工信部 23 条、国家数据局 8 条、TrendForce 1 条落入最近 72 小时的报道日期区间；这三源重复读取内容指纹变化均为 0。FDA 首轮发生临时访问失败，后续读取恢复 10 条；这些记录时间未知，不能称为最近 72 小时内新消息。其恢复条目不冒充重复运行零新增。

数据库入库去重、CLI 完整批次状态与人工选择持久化的验收由主程序集成报告记录。本报告不以接口级重复指纹检查代替数据库测试。

## 运行环境与未接通项

本机系统 DNS 将公开域名映射为 `198.18.0.x` / `fdfe:dcba:9876::` 非公网地址。客户端最初正确拒绝；没有为它们加白名单。覆盖层显式选择 `dns_mode=cloudflare_public`，以固定公网 `1.1.1.1` 和 TLS 主机名校验访问 Cloudflare 官方 DoH，取得真实公网地址后固定 IP 建立目标连接。DNS 查询也计入网络使用量。未读取用户浏览器 cookie、未使用代理凭证、未更改系统网络设置。

仍未接通：其余 22 源的自动元数据采集、完整历史覆盖/分页、搜索和模型接口、Wind、产品当前结构数据。日精度/首次公开未知的线索保留待核实状态；不自动勾选、不自动写稿、不自动发布。

条款依据：

- [TrendForce 使用条款](https://www.trendforce.cn/about/terms)与[产业洞察引用指南](https://www.trendforce.cn/presscenter/attribution.html)：已实际检查，限制记录在覆盖层。
- [e 公司版权声明](https://egs.stcn.com/public/copyright.html)：已实际检查，改为人工授权路径。
- [SMM 法律声明](https://www.smm.cn/legal)：已实际检查，自动抓取禁用。
- [FDA Website Policies](https://www.fda.gov/about-fda/about-website/website-policies)：已实际检查，公共领域的一般规则保留例外。
- [Cloudflare 官方 DoH JSON 文档](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)：仅用于 DNS 基础设施，未新增新闻来源。
