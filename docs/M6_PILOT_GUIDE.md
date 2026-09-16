# M6：试跑记录与人工复盘

## 交付状态

本阶段实现的是**试跑记录和复盘工具**。十个真实交易日需要用户实际使用和反馈，无法由开发期间的单元测试、历史回放或合成材料代替。程序不会自动认定看板效果合格，也不会根据一次采用或弃用改变评分、产品画像或全局规则。

首批重点产品为 513130、513530、588710；517120 保留相同记录能力。各产品单独累计，不能把三只产品同一天的记录合成“三天”。

## 一次有效记录需要什么

- 已保存的 Edition 和属于该 Edition 的已完成研究 run（`complete` 或 `partial` 都可以）。来源失败、模型缺失属于真实试用情况，不应删掉不理想的样本。
- 人工明确反馈。认可事件只能引用该 run 实际显示的研究事件 ID；反馈不会替代素材篮的人工选择，也不会触发写稿。
- 操作前基线耗时、实际耗时、纠错次数、遗漏线索及原因，可按用户已知情况填写。未知项为 null，不填零。
- 交易日声明及来源。程序没有交易所日历接口，不能把周一至周五直接判为交易日。未确认的日期可以记工作记录，但不会计入已确认试跑交易日。

`accepted_event_ids=null` 表示尚未判断；`[]` 表示用户已判断、没有认可的候选。这两种状态在统计中有不同含义。

## 何时计作真实试跑日

必须同时满足：

1. 数据库为 `live`，不是 `fixture`；
2. 研究实际执行日期与 Edition 日期相同；
3. 用户明确确认该日期为交易日，并记录日历来源。

今天用旧日期 Edition 做回放，会记为历史回放，不能补成过去的实际使用天数。未来日期和早于研究执行的反馈日期均被拒绝。

同一产品、同一 Edition 日期只算一次。完全相同的重复保存直接复用已有记录；不同内容须提供最新 `expected_revision`，追加人工修订。旧版本保留在审计表里，复盘只取当天最新版本，不把修订次数当作样本量。

## Python 接口

先在 JSON 文件中写入实际 Edition/run ID 和用户反馈，再由统一 CLI 提交：

```powershell
python -m fastnews record-pilot-feedback data/my-pilot-feedback.json --summary
python -m fastnews pilot-report 513130 --summary
python -m fastnews pilot-report --offline --summary
```

最后一条只查看合成测试数据库，不能用它报告真实试跑效果。反馈文件可从以下结构开始；未知值保留 null，ID 必须替换为实际已保存的引用：

```json
{
  "edition_id": "实际EditionID",
  "research_run_id": "实际research_run_id",
  "accepted_event_ids": null,
  "baseline_minutes": null,
  "actual_minutes": null,
  "correction_count": null,
  "missed_clues": null,
  "reason": null,
  "trading_day_confirmed": false,
  "trading_day_source": null
}
```

以下为共享业务接口，可供本地看板直接调用：

```python
from fastnews.pilot import record_pilot_feedback, pilot_report

record = record_pilot_feedback(
    conn,
    config,
    edition_id="已保存的EditionID",
    research_run_id="属于该Edition的research_run_id",
    actor_type="human",
    accepted_event_ids=None,
    baseline_minutes=None,
    actual_minutes=None,
    correction_count=None,
    missed_clues=None,
    reason=None,
    trading_day_confirmed=False,
    trading_day_source=None,
)

summary = pilot_report(conn, product_code="513130")
```

用户日后补充耗时时，重新提交完整记录，并传 `expected_revision=record["revision"]`。不能用模型补写“用户认可”，也不能预填用户尚未测量的时间。

测试命令：

```powershell
python -m unittest tests.test_pilot -v
```

## 报告怎样解释

报告按产品、冻结版本分别分组：画像及评分规则、路由规则、提示词、模型/provider 版本等变化后形成新组。不会拼接不同规则下的五天与另五天宣称“同一版跑了十天”。每日产品结构和稿件输入快照仍可正常更新。

| 字段 | 含义 |
|---|---|
| `record_count` | 已记录的独立产品日期数，包含待补和fixture |
| `audit_revision_count` | 人工保存版本数，不等于试跑天数 |
| `real_trading_session_count` | 满足真实模式、当天执行、交易日声明的日期数 |
| `acceptance_judgment_count` | 上述日期中用户实际判断过候选的次数 |
| `first_three_acceptance_rate` | 原研究顺序前三项中至少一项被用户认可的比例；未判断项不当作失败或成功 |
| `timing_pair_count` | 同时提供基线和实际耗时的有效样本数 |
| `observed_time_reduction_fraction` | 配对样本总基线耗时与总实际耗时的降幅；基线为零或缺项时为 null |
| `remaining_to_ten` | 该固定版本组还差的真实试跑日期数 |

“前三项”指被引用的研究 run 当时保存的前三项，不能事后改顺序来提高认可率。该指标不评价未来涨跌，也不保证选题唯一正确。

未满十个日期，或认可反馈/耗时配对不足，状态为 `not_accepted_insufficient_trial_data`。数据充足后仅变为 `ready_for_human_review`，仍须人工判断。初始目标“前三候选认可率不低于 80%”“人工耗时下降至少 20%”只是复盘参考，程序不会把样本充足等同于目标达成。

## 持久化与边界

`004_pilot.sql` 新增 `fn_pilot_feedback`，引用原有 Edition、研究 run 和产品，保留人工修订链、版本签名和未知值。迁移只新增结构，不删旧库。数据库本身约束 mode 和人工 actor，禁止改写既有反馈行。

当前没有自动定时使用、自动收集用户认可、交易所日历接入或自动学习权重。十交易日效果验收仍处于待实测阶段；fixture 中构造再多日期也不能完成它。
