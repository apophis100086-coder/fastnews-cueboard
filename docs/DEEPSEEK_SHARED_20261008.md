# DeepSeek 共享交付包

2026-10-08，基于三入口交付 `aea0740`。用户指定 DeepSeek 并明确授权内置其共享密钥；本次只修改 API 预设、共享配置导入、对应说明和打包流程。默认页面入口仍是 WorkBuddy，Codex 与个人 API 配置继续保留。

默认 API 为 `https://api.deepseek.com`，模型 `deepseek-flash`，Chat Completions + JSON 对象。按官方协议关闭 thinking；不声称提供模型联网搜索。首次启动将共享种子另存为当前 Windows 用户加密的本机连接，接收者不用再次输入密钥。材料发送与费用授权由接收者首次选择 API 时确认。已有个人连接不被启动预设覆盖。

共享密钥只从被 Git 忽略的本地文件传入打包器，进入发行包 `data/setup/shared-api.json`。此文件为可迁移、可读取的共享凭据，包持有人能使用它；不能把 Windows 本机加密表述成发行包不含可读取密钥。manifest 显式记录 `account_credentials_included=true` 和 `shared_api_included=true`，根目录附“共享API说明.txt”。标准不含凭据的构建仍可使用。密钥不进入源码、Git、日志、页面返回或验收记录。

真实 API 检查：2026-10-08 09:55 北京时间，`deepseek-flash` 返回正确的 `{"ok":true}`，输入 122 token、输出 5 token，实际金额由服务商计费，未另行估计。只发送合成连接测试文本，没有发送本地范文或业务资料。此前首次尝试被本机非公开 DNS 地址拒绝，尚未发起模型请求；随后为固定官方 DeepSeek 域名沿用项目既有 public_fallback DNS，继续验证公开地址、固定 IP 和 TLS 主机名。新记录保留首次失败及最终成功，不把响应检查当作写作采用验收。

新增测试覆盖：共享预设首次导入、接收者授权不被预选、已有个人配置保护、替换密钥后的来源标签、服务地址限制、错误不回显、DeepSeek 参数、公共域名解析与 TLS。完整测试及最终解压结果见发行目录“发行核验.json”和“交付说明.txt”。

使用：完整解压，在 WorkBuddy 打开文件夹并使用项目技能启动。需要 DeepSeek 时，在页面“使用入口”选择“自接 API”，确认材料发送授权，保存 API 配置与入口，然后使用或检查响应。不要将项目内的共享配置导入 WorkBuddy 客户端本身；它是看板的 API 入口，与 WorkBuddy 账户模式独立。

官方依据：[首次调用 API](https://api-docs.deepseek.com/zh-cn/)、[Chat Completions 协议](https://api-docs.deepseek.com/api/create-chat-completion/)。历史三入口与存储验收见 THREE_ENTRY_ACCEPTANCE_20261008.md；其中“发行包不含凭据、未进行真实 API 调用”描述的是先前标准包，本次共享版以本记录和包内 manifest 为准。
