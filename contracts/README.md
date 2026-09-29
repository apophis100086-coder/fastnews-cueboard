# 契约的责任边界

JSON Schema只检查字段形状，不能替代以下业务校验：fact/evidence/profile引用实际存在；来源片段在原文可定位；time precision和截点合法；分项齐全时总分符合固定公式；partial/null不伪造分数；私有资料可用权限；fixture隔离；真实人工操作授权。

candidate.schema.json是存储/显示候选的规范形状，不要求把系统管理的ID、最终状态和总分交给模型生成。模型只生成事实提议和评估部分，服务器校验后填充。

editorial_selection.schema.json用于用户保存选择；它不能由模型工具调用或抓取材料直接创建。明确人工操作与服务端校验才构成选择，actor_type=human字符串不是身份认证。
