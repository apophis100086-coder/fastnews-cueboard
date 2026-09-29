# M3：事实材料、人工选题与写作交接

## 实际完成的能力

本模块把已取得的材料做成可以逐条追溯的事实记录，保存人工选择，并导出材料包。它不会生成快讯正文、发布稿件、自动选题或把关键词变成产品持仓。

- Edition 冻结产品、新闻截点、行情截点、四产品观察画像、评分规则、输出规则及当时已导入的产品/历史快照。重新运行相同参数可复用；改变截点、画像或历史快照需要新建一期。
- 明确授权后可读取本地 UTF-8 JSON、Markdown、文本。JSON 可包含人工明确提供的事实；纯正文只存为已读材料，不会伪称自动抽取完成。
- 运行时公开全文入口 `record_public_evidence` 接受采集器已经取得的正文，保存本地版本/hash，并标 `public_http / fulltext_read`。它自身不访问网络、不判断站点是否允许抓取，也不宣称该页面就是每项事实的原始出处。
- 人工选中原文片段后，`add_fact_from_evidence` 可把原文中的准确表述存为事实，保留实际读取来源、版本、定位。未知首次公开时间继续待补证；不会用抓取时间代替。
- 来源片段逐字定位、值/单位/币种/统计期、首次公开和后来修订时间均单独保存。非逐字支持的人工改写可浏览，但保持 pending，不进入正文事实区。
- 只有标题的历史稿件只支持主题层比较。无语义服务时，程序只做正文逐字重合比较；没有重合仍为“尚不能确认不撞题”。
- 六分项用 0–4 或 null 表示；总分由固定公式计算，缺项不补 0。`[4,3,4,3,4,3]` 的纯数学测试结果为 87.5，显示为 88。没有自动采用阈值。
- 人工选择、顺序和主线说明独立于评估。刷新不会改变选择；乐观锁拒绝用旧 revision 覆盖新决定。可选择任意已登记事件、跨事件选补充事实，或撤销主事件。
- 导出为不可变 Markdown/JSON 材料快照。没有保存主事件时拒绝导出；只有 pending 主事件时，可导出“选题意向”包，其可用事实为空，并提示不能据此成稿。

## 无模型时如何使用

程序不依赖模型密钥即可浏览真实采集器取得的标题和公开材料。语义评估、核查产品具体作用机制、语义撞题不是 `none` 模式已经完成的能力。

最短人工链路是：创建一期 → 查看正文 → 选中真实原文片段 → 保存事实/素材顺序 → 保存主事件 → 导出材料包。事实有证据和时间缺口时，程序保留缺口而不替人补齐。

`market_cutoffs={}` 表示行情日期未知，可以研究政策等非行情线索。带市场统计的事实仍须有明确行情日期、统计期及币种；程序不推算节假日或交易日历。

## Python 业务接口

CLI 和本地看板共用以下接口。各接口位于 `fastnews/editorial.py`；CLI 的最终参数以 `python -m fastnews --help` 为准。

```python
create_edition(conn, config, product_code="513530", edition_date="2026-09-15",
               news_cutoff="2026-09-14T23:59:59+08:00",
               market_cutoffs={"HK": {"date": "2026-09-14", "source": "user_explicit"}})

import_evidence(conn, path, authorized=True, edition_id=edition_id)
import_product_data(conn, path, authorized=True, product_code="513530")
import_history(conn, path, authorized=True, product_code="513530")

assess_events(conn, config, edition_id=edition_id)  # 0 LLM，未知评分为 null
edition_detail(conn, edition_id)  # 原文、来源、事实、评估、选择与更新提醒

add_fact_from_evidence(conn, edition_id=edition_id, event_id=event_id,
                      evidence_id=evidence_id, statement="原文中的准确片段",
                      kind="reported", actor_type="human")

save_decision(conn, edition_id=edition_id, selected_event_id=event_id,
              selected_fact_ids=[fact_id], expected_revision=0,
              actor_type="human", mainline_note=None)
export_handoff(conn, config, edition_id=edition_id,
               decision_revision=1, output_dir="data/exports")
```

`expected_revision=0` 只用于首次保存。后续保存必须传刚读到的版本号。产品和历史材料要先导入再创建新 Edition，才能冻结到本期里。

`record_public_evidence` 由有预算的 HTTP 研究路径调用；不能用它把搜索摘要或没有真正取得的内容标成读过原文。`validate_assessment_proposal` 只接受契约内评估字段，拒绝模型提交 human/selected/verified 等权限字段、虚构引用及不属于本期的材料。

## 本地材料格式

三个 `examples/editorial_*.synthetic.json` 仅供离线验证，具有 `is_fixture: true`，只能导入独立 fixture 数据库。这些资料不是联网成果或真实业务资料。

真实 JSON 材料使用 `is_fixture: false`；来源名称和文件原件要如实记录，不要把合成样本去掉标记充当真实资料。Markdown/文本默认按真实本地材料处理，仅存正文，不自动造 claims。

事实 JSON 的关键字段：`statement / kind / excerpt / observed_value / metric / unit / currency / metric_scope / period_start / period_end / first_published_at / effective_at`。未知值为 null。

`statement` 与 `excerpt` 必须是该 `content` 中支持同一事实的表述。数字必须在片段中出现。若用户改写句子不能逐字定位，则系统保存为 pending，后续可用原文摘录建立事实。

结构快照必须有 `snapshot_type / data_as_of / published_at / excerpt`。对象分为指数成份、基金持仓、所投基金持仓、业务结构和产品身份。未来生效结构及截点后的快照不会作为本期当前结构。成员和权重必须能定位到源片段，产品词表不能作为结构来源。

历史材料必须有真实发表时间及 `title_only / partial_text / full_text` 完整程度。上传日期不代表发表日期，标题不能标成正文。

## 导出控制

- `kind` 保留计划、预测、公司声明、报道等身份，正文写作需保留限定和归属。
- 待补证、冲突、截点外及受限精确产品规模/份额/流量只进入“不可直接用于正文”附录，不输出其句子和量值。不会连带过滤南向市场总量。
- 可用事实的引用片段收窄到实际选中表述，避免把同一段里受限产品数字夹带出去。
- 媒体转引 Wind 的实际取得源仍为媒体；`declared_source=Wind` 单独保留，不取得虚假的直接 Wind 权限或 primary 状态。
- 公开/本地文件身份、hash、定位、日期和人工作出决定的 revision 可追溯。导出进入 `output_dir/live/…` 或 `output_dir/fixture/…`；文件已存在且内容不同则拒绝覆盖。
- 原文后来更正，新增事实和冲突提醒会出现；原选择和旧导出保持原样。

## 验收与尚缺条件

离线执行命令：

```powershell
python -m unittest tests.test_editorial -v
```

M3 专项测试覆盖 44 项行为：冻结/幂等、四产品观察差异、授权和 mode 隔离、假引用/不相关改写、片段与数值定位、时间精度、UTC 日界线、未来实施计划、后修订、冲突保留、历史粒度、固定评分、模型权限、人工锁与顺序、受限值过滤、不可变导出、公开正文来源和人工摘录等。新增检查数值指标在尚未填写类型/范围/单位时保持待补证，不能通过直接摘录绕过产品数字过滤。运行结果以本轮测试输出和总验收报告为准。

目前仓库示例未提供真实的产品法律/结构资料、Wind 数据或正式历史稿件。这些资料的格式入口已实现，实际资料未接入前仍显示缺项。外部模型语义能力由 M4 provider 配置与授权决定；没有接入时不宣称已自动核验或判断新旧事件。
