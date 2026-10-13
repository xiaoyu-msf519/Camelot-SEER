# Arena v2 JSON 数据模块验收

日期：2026-10-13。此次按要求不接入或二级分发 Artificial Analysis，并取消网页交付。

## 数据结果

实际从 Arena 官方 CC-BY-4.0 Parquet 取得：Text（style control）、Vision（style control）、WebDev、Agent 及四项 Agent 行为指标；另从 models.dev 取得公开规格与当前报价。

共 496 个独立评测配置，120 个配置可精确对应当前本地模型事实。覆盖：Chat 414、Coding 409、Math 400、指令遵循 414、多轮 412、创意写作 412、Vision 165、WebDev 139、Agent 52。

未绑定记录仍保留真实评测，不能借用其他模型的 context、报价或权限。120 是精确绑定的配置数，不是模型数量，也不表示所有客户端已经注册这些路由。

大表约 1.1 MB。每个 score 保留原始值、计量口径、置信区间、排名、票数或观测/会话数、发布日期、来源和时效。不同分榜没有混成一个综合分。IPS 允许为负，没有伪造为百分比。

## 推理设置

- 模型身份与思考强度、预算标签、harness 分开。
- 未指定强度不继承 high/max；未知修饰标签不当作默认配置。
- `thinking-32k` 保存标签而非臆造精确 token 数。
- 日期不被删除；浮动 API 名称不自动继承日期快照成绩。
- 同模型、同设置、同 harness 的明确展示名可合并；每个分榜仍保留原始名称。
- 同一分榜、同一日期存在冲突行时，不选择最高分，该项留空。
- 调用者必须在本地验证 Adapter 支持对应设置；远端声明不启用工具或模型参数。

## 最小发布

Pages 只上传白名单内两种 JSON 表的 manifest 与哈希快照，无 `index.html`。

主表：`api/v2/manifest.json`。旧 Camelot 事实消费者：`api/camelot-v1/manifest.json`。

`report.json`、`unmatched.json`、`policy.json`、Schema URL、旧完整 `api/v1`、原始响应、源错误和诊断 artifact 不再公开。构建报告在忽略目录 `.cache/build-report.json`。兼容表仅对未指定推理设置的充分样本提供 bootstrap 排序，本地 Profile 下限不变。

## 实际执行

| 命令 | Exit Code / 结果 |
| --- | --- |
| `python -m pip install pyarrow==21.0.0` | 0 |
| 原始 Arena 首次在线采集 | 9 源 fresh，0 故障 |
| `python -m unittest discover -s tests -v` | 0，41 项通过 |
| `node --test clients/tests/reader.test.mjs` | 0，3 项通过 |
| `python -m observatory build --offline` | 0，496 行 |
| `python -m observatory validate public/api/v2` | 0 |
| `node --import tsx/esm ../model-observatory/scripts/test-camelot-table.mjs . --local` | 0；Camelot 本地 Ajv 和 manifest validator 通过 |
| `node --import tsx/esm ../model-observatory/scripts/verify-camelot.mjs .` | 0；现有 Camelot validateCatalog 通过，234 模型 |
| `git diff --check` | 0 |
| `python -m scripts.check-pages`（线上） | 0；大表和兼容表版本/身份一致，内部端点 404 |
| `Test-Camelot-Link.bat`（线上） | 0；ModelRegistry、Core 后台同步、v2 Node reader 均通过 |

Node 读取验收实际选择 `gpt-5 / high`，Coding 原始 rating 为 `1470.9189152600536`；默认模式不能借用这一行，错误预算不匹配，缓存重开正常，执行配置字段被 Schema 拒绝。测试未读取模型 API Key、未执行 LLM 请求。

网络采集严格保持每源 5 秒总截止时间，含 HF CDN 重定向。在后续一次在线检查中 Text 超时并回到已验证缓存，其余源在线通过；陈旧标记如实保留，未把降级声明为全在线成功。

## 线上交付结果

代码修复基线 `2129cfa`。Actions [38402419765](https://github.com/xiaoyu-msf519/Camelot-SEER/actions/runs/38402419765) 和 [38402418063](https://github.com/xiaoyu-msf519/Camelot-SEER/actions/runs/38402418063) 的 build、deploy、线上 smoke 均 success。

线上实测版本 `v2-b15d70dc6be37d79217e9df2`；496 个独立配置和 234 个 v1 兼容模型。`test-camelot-link.mjs`、`test-camelot-boot.mjs`、`test-camelot-table.mjs` 均通过一键 BAT 实际访问 Pages，Exit 0。三条运行可能遇到新的定时发布，所以不同独立运行允许读取不同递增版本。

首次 v2 部署后 smoke 曾误判 `mistral-large-4` 在兼容表缺失。其来源 lifecycle 状态为 unknown，本来就应从严格 v1 Catalog 省略。已在完整表增加 `modelStatus`，修正 smoke 区分该有意省略与错误绑定，并添加回归测试；没有放宽兼容表约束。

主入口：[v2 manifest](https://xiaoyu-msf519.github.io/Camelot-SEER/api/v2/manifest.json)。兼容入口：[Camelot v1 manifest](https://xiaoyu-msf519.github.io/Camelot-SEER/api/camelot-v1/manifest.json)。Schema 留在源码，不单独发布。`index.html`、`report.json`、`unmatched.json`、`policy.json`、`schema/registry.schema.json`、`api/v1/manifest.json` 已逐项实测 HTTP 404。

Camelot `src` 工作区和 `packages/core / packages/subagent` 均无修改。部署只变更独立 SEER 项目；AA 数据与密钥未加入源码、Seed 或公开快照。

## 限制

当前原生 Camelot 的 v1 Registry 自动同步事实和 Profile；完整 v2 配置证据由交付的 Python/Node reader 读取。将 v2 reader 挂到 Camelot 招募流程属于客户端消费工作，不会由静态注册表自行授予执行能力。

Arena 覆盖的模型和当前 API 事实并不完全一致。未核验的旧版本、服务商和不明推理配置不会为了提高绑定数而模糊合并。
