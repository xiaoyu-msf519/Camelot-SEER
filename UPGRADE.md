# v0.2：Arena 配置大表

## 入口变化

完整数据入口从 `api/v1` 改为 `api/v2`，旧完整入口从 Pages 删除，避免同时发布两套不一致的数据。兼容 `api/camelot-v1` 保留，现有 Camelot 事实同步配置无需修改。

客户端完整评分查询改用 `TableClient` 和 v2 Schema。每行是推理配置，不是整个模型；Manifest 的行数对应 `rows.length`。

## 不再公开

网页、`report.json`、`unmatched.json`、`policy.json`、Schema URL、原始响应、内部错误和诊断 artifact 不再进入 Pages。下载失败不返回内部文件；公开访问应得到 404。构建输出从 `dist/` 改为 `public/`。

## 评分语义

公开大表采用官方 Bradley–Terry rating 或 Agent IPS，不沿用旧的 0–100 综合维度归一化。不同分榜不能横向相加；Agent IPS 可为负，不是通过率。思考强度、预算、harness 精确匹配，未指定配置不继承 high/max。

AA 不接入和分发。默认评测源为 Arena 官方 CC-BY-4.0 数据集，模型事实保留 models.dev。旧测评解析器与 Seed 只作为源码回归基线保留。
