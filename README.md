# Camelot-SEER

Arena 官方分榜与模型规格的独立数据模块。每个 **模型 × 思考强度 × token budget × harness** 配置单独保存，用于 Camelot 招募时查询证据。没有唯一综合模型分，没有执行 Adapter 或权限。

[JSON 大表 Manifest](https://xiaoyu-msf519.github.io/Camelot-SEER/api/v2/manifest.json) · [Arena 官方开放数据](https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset)

## 数据与许可

评测来自 Arena 官方 `leaderboard-dataset` 的 `latest` Parquet 导出，许可 **CC-BY-4.0**，每条保留原始 rating / IPS、置信区间、票数或观测数、排名和发布日期。模型上下文、能力声明与报价来自 models.dev，属于聚合声明。Artificial Analysis 不接入、不采集、不二级分发，不需要任何 API Key。

| 分榜 | 含义 |
| --- | --- |
| Chat、Coding、Math、指令遵循、多轮、创意写作 | Text Arena，采用 style control，原始 Bradley–Terry rating |
| WebDev | 独立 Code Arena 的 Web 开发偏好，不等于所有代码工程能力 |
| Vision | Vision Arena，采用 style control |
| Agent | 官方 Agent Arena IPS，保留整体及任务反馈、可引导性、工具行为、终端可靠性子项 |

不同分榜的 rating 只在同榜比较；IPS 是有符号估计量，可以为负，不能当作通过率或百分数。官方 Agent 聚合项标识为 `agent`，原始子项仍保留，SEER 不另算综合模型分。

## 思考配置与模型对应

一行一个 `variantId`，模型 API 身份与 `inference.effort / budget / harness` 分开。支持 `unspecified / none / low / medium / high / xhigh / max / thinking / unknown`。`thinking-32k` 保存预算标签 `32k`，不伪造精确 token 数；`codex-harness` 与普通配置分开。

`modelStatus` 保留本地事实的 lifecycle 状态；身份可对应不代表模型处于 active。状态未知或 context 未知的行仍有原始评测，但不会伪装成可被旧 v1 兼容 Catalog 接收的模型。

客户端未指定强度，只对应 `unspecified`。它表示来源未注明额外强度，不是证明厂商的默认参数。不会取 high/max 最高分来填默认成绩，也不会自动启用思考模式。

日期版本保持分开：`gpt-4.1-2025-04-14` 不自动映射到浮动 `gpt-4.1`。来源与 API ID 的大小写/空格展示形式仅在同厂商且唯一时对应；已审阅的版本标点差异见 `config/arena-aliases.json`。无法确认的记录仍显示真实榜单成绩，但 `binding=unmatched`、`apiModelId=null`，不借用别的模型规格。

相同明确身份与配置的自然语言/slug 标签可合并，逐分榜保留原始 `modelName`。同一日期、同一分榜有冲突记录时不挑最高分，该项保持未知。

客户端必须先在本地确认 Adapter 支持该模型与推理设置，再选择对应 `variantId`。远端表无法注册 Provider、修改 Endpoint 或授予 tools / filesystem 权限。

## 最小公开发布

GitHub Pages 仅上传 `public/`，没有网页界面；文件白名单为：

```text
api/v2/manifest.json
api/v2/snapshots/<sha256>.json
api/camelot-v1/manifest.json
api/camelot-v1/snapshots/<sha256>.json
```

大表 Schema 为 v2。Manifest 的 `modelCount` 在 v2 入口表示配置行数。JSON Schema 留在源码 `schema/seer-table.schema.json`，不额外公开诊断接口。源码中旧 Seed 和旧 Schema 用于回归测试，不会随 Pages 上传。

内部错误、源校验报告、隔离记录、缓存、未匹配明细、原始下载和报价历史保留在 `.cache/`，不上传为公开 artifact。公开表只包含必要证据、绑定状态、当前报价及署名；不包含源请求错误文本。超过白名单的文件或符号链接会使发布失败。

`api/camelot-v1` 保留已有 Camelot Registry 接口，只输出它能表达的事实和 bootstrap Profile。排序只参考新鲜、至少 100 票、未指定额外思考设置且无特殊 harness 的 Coding / Math 记录。不会把 high 变体当作默认路由，也不伪造评测运行日期。本地 Profile 能力下限、Adapter 和凭据要求仍由 Camelot 执行。

## 本地运行

需要 Python 3.11+；Windows 可双击 `Validate.bat` 创建独立 `.venv`、安装依赖、单测与离线验收。`Collect.bat` 实际联网采集。

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m observatory build --offline
python -m observatory validate public/api/v2
python -m observatory build
python -m observatory match openai gpt-5 --effort high
```

当前内部报告为 `.cache/build-report.json`。`--write-seed` 要求所有选中源在线成功，用于明确更新离线基线。源码 Seed 已保存实际 Arena 数据，不依赖网络启动。

所有源网络操作总截止时间最多 5 秒，包含 HF 的有限 HTTPS CDN 重定向；失败回到已校验缓存或 Seed。来源故障不更新其数据获取时间，评分标为陈旧。collector 尊重本机代理配置；发布者和源地址仍由本地配置决定。

固定观测、别名和 `--as-of` 可重复生成相同内容与 hash。新源发布日期、配置或数据变化才应产生不同结果；抓取时间也是明确的版本输入。最新 7 个不可变快照保留给客户端请求，原始缓存保留最近 20 份及当前源引用。

## 客户端与部署

```powershell
python -m examples.poll https://xiaoyu-msf519.github.io/Camelot-SEER/api/v2/manifest.json openai gpt-5 --effort high --once
```

Python `TableClient` 和 Node `clients/camelot-reader.mjs` 验证同源路径、4MiB 上限、SHA-256、封闭 Schema、来源引用和版本数量；拒绝回滚。一次 manifest + snapshot 请求总截止时间为 4 秒。离线重开可恢复绑定发布者的缓存，返回陈旧证据而非阻塞 Camelot 本地启动。Node 读取器由 Camelot 本地 Ajv 与 manifest validator 校验，Schema 从本地源码读取，不能信任远端任意 Schema。

`match(provider, api_id, effort=..., budget=..., harness=...)` 精确对应配置；缺失返回 `None`，歧义报错。`variants(provider, api_id)` 返回该本地模型可对应的证据配置列表，用于 UI 选择，不能代替本地能力授权。

Actions 每 6 小时采集、执行测试、验证 Schema 与公开文件白名单并部署 Pages；部署后实际下载大表和兼容表进行 smoke 检查。工作流不使用 AA 密钥，也不需要模型 API Key。

现有 Camelot Core 可显式加载 `examples/camelot-seer.patch.yml`，事实更新入口不变。双击 `Test-Camelot-Link.bat` 验证真实 Camelot ModelRegistry / ProviderHub / Core 数据同步；模型目录是本地 fixture，不执行付费 LLM 请求。

Camelot Web 招募页面自动读取 v2 配置大表仍需单独接入；本模块提供 JSON 数据协议、Python/Node 只读客户端和定时发布。`scripts/test-camelot-table.mjs` 实际通过 Camelot 本地校验器验证读取、推理配置对应与缓存重开；`--local` 可验证本地生成文件。升级细节见 [UPGRADE.md](UPGRADE.md)。旧阶段实测记录保留在 [ACCEPTANCE.md](ACCEPTANCE.md) 和 [DEPLOYMENT.md](DEPLOYMENT.md)。
