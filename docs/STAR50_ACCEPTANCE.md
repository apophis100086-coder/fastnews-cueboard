# 科创50ETF华泰柏瑞接入验收｜2026-09-17

## 已完成

- 产品池由4只扩至5只，新增588090科创50ETF华泰柏瑞。基金身份与跟踪指数经上交所产品资料概要核对，依据与后续扩充方法见 [产品池维护](PRODUCT_ONBOARDING.md)。
- 新增 `products.additional.yaml` 追加层；原 `sources.yaml`、`topic_taxonomy.yaml`、`products.example.yaml` 字节保持不变。追加层不能覆盖原代码，登记、发现、画像和检索规则按实际产品集合校验。配置测试还用合成第六只产品确认无需修改程序硬编码。
- 新画像区分科创50整体与半导体设备主题，覆盖样本调整、企业经营／研发、政策、估值和独立资金口径。科创板／科创50明确命中进入专项召回，纯行业消息先作为背景，未导入当前成份股、权重和持仓。
- 产品选择、领域网站与关键词查询、冻结期次、人工候选、选材、Markdown／JSON导出贯通。切换产品后隐藏上一产品报告并给出提示；切回可恢复原报告，原选材和草稿仍保留。
- 修复实际联调暴露的Windows打包应用目录重定向问题：短期宿主任务改用系统临时目录，继续保留路径归属检查、进程隔离和只读沙箱。没有放宽目录检查。

## 验证结果

| 验证 | 结果 |
|---|---|
| Python全量离线测试 | 445项通过，99.354秒 |
| 前端交互测试 | 35项通过 |
| M0/M1隔离CLI | 17个命令通过，包括全部5只产品路由 |
| 系统隔离CLI | 22个命令通过，包括全部5只产品路由和原有导出闭环 |
| 原三份配置校验 | 固定SHA-256校验通过，本机原ZIP逐字比较通过 |
| 正式页面 | `/api/state` 返回5产品及588090的8条计划查询；真实浏览器下拉框显示名称代码，切换后显示新计划、隐藏旧报告 |
| 正式库保护 | 34张表中33张内容指纹相同，只有 `fn_products` 从4行增至5行；原4行仍保持一致 |

命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test --test-reporter=dot tests/ui_daily_test.cjs
.\.venv\Scripts\python.exe tools/run_m1_acceptance.py
.\.venv\Scripts\python.exe tools/run_system_acceptance.py
.\.venv\Scripts\python.exe tools/validate_targeted_search_live.py --live --product 588090
```

原有104篇文章／104个版本、114个事件、10个决定版本、5份导出、10份阅读报告、16个候选池记录保持原样。没有重建数据库；一致性备份通过SQLite `integrity_check`。

## 真实联网验收

在新建隔离库中，新闻截点为2026-09-16 23:59:59北京时间。修复后通过已登录Codex宿主生成5条有来源的研究线索，状态为 `partial`；独立完成加入候选、选择与Markdown／JSON导出。正式库没有导入这些测试报告或测试选择。

本次结果覆盖指数调样、本基金交易与份额线索、科创企业经营和并购等方向。输出保留成份身份、原始公告、交易数据口径和结构快照缺口，没有把未知持仓写成事实，没有自动生成Fact。2份材料为 `host_report_pending`，其余为搜索摘要；没有材料具有CLI独立返回正文的证明。

- 首次尝试在宿主启动前因目录归属检查失败，记录为失败；保留该记录，未冒充成功。
- 修复后的真实宿主任务1次、170.42秒；输入164265 token（其中缓存输入116736）、输出4178 token。使用宿主订阅额度，未使用API Key；内部请求数和费用不由本地程序测量。
- 计划最多8条查询、3个重点补证事件、每事件最多2页。CLI可观察联网动作4个；模型自报的查询与阅读次数不是独立审计证据。
- 程序来源对照中工信部、国家数据局、集邦返回列表元数据；政府政策库、行业协会等未启用的采集路径保持未请求。此结果不代表全部来源或原文已接通。

## 本地证据

以下运行文件和数据库均被Git忽略，仅保留在本机：

- 备份：`data/backups/before-star50-20260917T032034Z.db`
- 数据校验：`data/workflow_acceptance/preservation.before-star50-20260917.json`、`data/star50-preservation-result.json`
- 全量测试：`data/star50-final-python-tests.log`
- CLI验收：`data/acceptance/20260917T033355897914Z/report.json`、`data/system_acceptance/20260917T033419019853Z/report.json`
- 首次失败记录：`data/targeted_acceptance/20260917T032440616562Z/summary.json`
- 真实报告、公开输入、宿主动作及导出：`data/targeted_acceptance/20260917T032936811350Z/`

正式服务已重启加载本轮代码。Git推送继续执行仓库自带的pre-push检查，不跳过测试。历史Edition和已导出文件保留原画像与版本，新增产品不改写过去的选材。
