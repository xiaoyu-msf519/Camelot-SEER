# Model Observatory

独立的公开模型评测数据项目。采集公开结构化榜单，保存原始证据，计算分维度的参考分数，再通过 GitHub Actions 定时生成 GitHub Pages 静态快照。

目录与 Camelot 的 `src` 完全独立，没有 dsh、Cordis 或 Camelot SDK 依赖，不读取 API Key、SSH 密钥或客户端配置。输出是招募参考数据；执行 Adapter、Worker 准入、权限和本地硬性要求仍由 Camelot 控制。

## 本地运行

需要 Python 3.11+。在本项目目录运行：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m observatory build --offline
python -m observatory validate dist/api/v1
python -m observatory match openai gpt-4o
python -m observatory build
```

Windows 可双击 `Validate.bat`，在本项目 `.venv` 中安装依赖并离线验收；`Collect.bat` 使用同一独立环境实际采集网络数据。BAT 使用 ASCII 文本，避免控制台中文编码差异。输出位于 `dist/`，源故障、降级和隔离记录位于 `dist/report.json`。终端使用者也建议先创建/激活项目虚拟环境。

`--offline` 不发起任何网络请求，从校验过的缓存或随项目提交的 `seed/` 读取。安装 Python 依赖本身需要网络或本地 wheel 缓存。

## 评分维度

所有分数为 0 到 100，高分表示该维度更好。**没有通用综合模型分数**。每个维度同时输出 `coverage`、`freshness`、组件权重、证据 ID 和来源 ID。

| 维度 | 第一版计算依据 | 解释 |
| --- | --- | --- |
| `coding` | Aider Polyglot 二次尝试通过率 50%，HumanEval+ 25%，MBPP+ 25% | 编辑能力与函数级代码能力；缺失组件按已有权重重归一，同时降低覆盖度 |
| `chat` | Arena-Hard-Auto v0.1 胜率 | 历史机器裁判代理指标，**不是 Chatbot Arena 人类 Elo** |
| `agent` | mini-SWE-agent / SWE-bench Verified 70%，BFCL 多轮任务 30% | 明确依赖 harness；其他 Agent 的结果仅保存证据 |
| `tools` | BFCL V4 Non-Live AST 50%，Live 50% | 默认采用标准 FC 模式，Prompt 模式与 thinking 变体保留为证据 |
| `context` | 声明容量的固定对数刻度，4,096=0，1,048,576=100 | 容量不是长上下文实测质量 |
| `price` | 1M 输入 + 250k 输出的美元成本 C；`100/(1+C)` | 越便宜分数越高，保留原始单价；不含缓存优惠、附加 reasoning、地区及 tier 计费 |
| `latency` | `100/(1+BFCL 平均秒数/5)` | 只表示 BFCL 测试环境中的延迟，不代表线上 API 普遍延迟 |
| `reasoning` | 暂无已核验适配器 | `null`，后续接入 LiveBench 等真实证据 |
| `longContext` | 暂无已核验适配器 | `null`，后续接入 RULER / LongBench |

完整策略在 `observatory/scoring.py` 和生成的 `dist/policy.json`，版本为 `dimensions-1.0.0`。更新计算规则应提升策略版本。

选择同一组件时采用最新报告日期，同日以稳定证据 ID 决定，**不挑最高分**。明确的 high / medium / thinking / 多模型组合不能默默替代默认变体。每条变体的成绩、框架版本、推理预算和调用模式保留在 `evidence` 中。

超过 180 天的 benchmark 标为陈旧；事实/价格核对窗口为 30 天。未知评测日期保持 `null`，不伪造评测时间。缺少日期的来源不能标为新鲜招募证据。`recruitmentReady` 仅为建议标记，要求覆盖度至少 50%、已知新鲜证据、非废弃的模型状态。示例客户端还重新检查当前时间、快照 24 小时有效期和本地认可的策略版本。

## 数据源与来源权利

| 数据源 | 数据格式 | 当前范围 | 来源许可说明 |
| --- | --- | --- | --- |
| [models.dev](https://models.dev) | JSON | 十个本地配置的厂商；规格、工具/视觉能力与美元价格 | 仓库 MIT，厂商事实是聚合声明，不代表本项目实测 |
| [Aider](https://aider.chat/docs/leaderboards/) | 官方仓库 YAML | Polyglot 225 题、两次通过率 | Apache-2.0 |
| [EvalPlus](https://evalplus.github.io/leaderboard.html) | 官方仓库 JSON | HumanEval+、MBPP+ | Apache-2.0 |
| [Arena-Hard-Auto](https://github.com/lmarena/arena-hard-auto) | 官方仓库 CSV | v0.1，2024-07-31 历史快照 | Apache-2.0，明确为历史代理指标 |
| [BFCL](https://gorilla.cs.berkeley.edu/leaderboard.html) | 官网 CSV | V4 工具调用、多轮任务、测试延迟 | gorilla 仓库 Apache-2.0，保留榜单出处 |
| [SWE-bench](https://www.swebench.com/) | 官方网站仓库 JSON | Verified；保留框架、提交日期、核验标记 | 网站仓库 **CC-BY-NC-4.0** |

代码采用 MIT；**来源数据不统一转成 MIT**，快照保留每个来源的许可与署名。SWE-bench 网站数据的非商业限制需要单独处理，商业部署可通过源配置或以下命令排除该源，并确认其他来源条款：

```powershell
python -m observatory build --sources models-dev,aider-polyglot,evalplus,arena-hard,bfcl
```

没有抓取需要付费认证的 Artificial Analysis API，没有绕过登录、验证码或访问限制。当前不反序列化 Chatbot Arena 的 Python pickle，也不将普通模型资料 CSV 冒充人类偏好榜。未接入的榜单不会产生虚构分数。

## 数据契约

`schema/registry.schema.json` 是封闭的 JSON Schema（draft 2020-12）；`Feed` 的运行时校验另外检查证据、别名和来源引用。调用 `python -m observatory schema` 可重复生成。

```json
{
  "schemaVersion": 1,
  "catalogVersion": "v1-<input-digest>",
  "generatedAt": "2026-10-13T04:08:55Z",
  "scoringPolicyVersion": "dimensions-1.0.0",
  "mappingVersion": "aliases-1",
  "sources": [],
  "models": {
    "openai/gpt-4o": {
      "identity": {"canonicalId": "openai/gpt-4o", "provider": "openai", "apiModelId": "gpt-4o", "aliases": [], "displayName": "GPT-4o", "status": "active"},
      "facts": "contextWindow / maxOutputTokens / capabilities / dated pricing / provenance",
      "evidenceIds": [],
      "dimensions": "individual scores, coverage, freshness and components",
      "provenance": "source IDs and fetch time"
    }
  },
  "evidence": "benchmark / version / original metric / environment / reportedAt / evaluatedAt / source label / mapping status"
}
```

上面是结构说明，不是可校验实例；实际完整实例由 `build` 生成，且 `seed/` 保存本次真实采集的规范化源观测。原始下载字节存入 `.cache/raw/<sha256>.bin`，快照的 `contentSha256` 可对应核验原始响应。Seed 是规范化离线基线，不包含原始脚本、模型执行配置或凭证。

`reportedAt` 表示榜单报告/提交日期，`evaluatedAt` 只在来源明确给出运行日期时填写。不能把抓取时间当作模型评测时间。models.dev 当前列表默认 `active` 表示数据源当前收录，并不保证厂商仍接受该 API ID。

价格保留 `priceVersion`、`observedAt`、`effectiveFrom`、`effectiveUntil` 和来源。官方生效时间未知时保持 `null`；连续观测到价格变化时保留最近 64 次不同报价，不伪造官方有效区间。

## 模型对应与执行防火墙

客户端以 **provider + API model ID** 精确匹配。`gpt-4o` 与 `gpt-4o-2024-08-06` 分开，不能继承彼此的成绩。跨服务商托管的同名模型也不能自动共享规格、价目或成绩。

特殊来源名称在 `config/aliases.json` 审阅后登记。BFCL 适配器允许去掉公开调用模式后缀并精确忽略展示大小写；不会删掉日期或模糊合并版本。冲突返回 `ambiguous`。未匹配的观测仍保留在主快照，同时汇总于 `dist/unmatched.json`，方便逐条补充映射。

来源只给出榜单成绩、又缺规格的旧版本可以拥有身份与证据，其 Facts 仍为未知。不能借用同系列新模型的上下文或价格。

远程事实中 `tools=true` 不提供可执行 Adapter，也不授予任何权限。Schema 拒绝 API Key、Endpoint override、代码、Tool 权限和文件系统权限。SHA-256 用于完整性核验，**不等于数字签名或对榜单真实性的背书**；发布者地址必须由本地可信配置确定。

## GitHub Pages 发布

将**本项目目录的内容**放入独立仓库。在仓库 Settings → Pages 中选择 **GitHub Actions**，默认分支建议 `main`。`.github/workflows/publish.yml` 支持提交、手动触发和每 6 小时调度。Pages 只托管静态文件；定时任务运行在 Actions 中。

第一次部署不需要模型 API Key。工作流先执行测试，再恢复缓存、并行采集、校验快照和已提交的 Schema，最后上传 Pages artifact。网络抓取单源总超时最多 5 秒，含持续少量发送数据的情况。错误、超时、格式漂移会回退到同源缓存/Seed；没有任何可校验数据时明确失败，拒绝发布空表。

发布路径示例（替换 OWNER/REPO）：

```text
https://OWNER.github.io/REPO/api/v1/manifest.json
https://OWNER.github.io/REPO/api/v1/snapshots/<sha256>.json
https://OWNER.github.io/REPO/api/camelot-v1/manifest.json
https://OWNER.github.io/REPO/report.json
https://OWNER.github.io/REPO/unmatched.json
https://OWNER.github.io/REPO/policy.json
```

`api/v1` 是完整多维证据契约；`api/camelot-v1` 是现有 Camelot Registry Schema 的受限投影，适配其 4MiB、manifest 和 hash 约束。旧契约要求非空 context 与评测日期，因此无法表达的模型/证据会被省略，**不伪造字段**。投影 `profiles` 为空，不能直接替换依赖 Seed Profile 的现有招募配置；后续客户端接入应将评分作为旁路依据，保留本地 Role/Profile/Adapter 政策。

本项目没有修改或开启 Camelot 的定时同步。示例只读客户端可验证完整接收与匹配流程：

```powershell
python -m examples.poll https://OWNER.github.io/REPO/api/v1/manifest.json openai gpt-4o --once
```

去掉 `--once` 后默认每 6 小时请求一次，加入 0–10% 随机抖动；整个 manifest + snapshot 请求总截止时间为 4 秒。失败保留先前已验证的缓存并提供错误信息；首次无缓存失败返回未知，不阻塞客户端本地启动。实际招募还必须执行客户端自己的能力、预算和权限门禁。

## 维护与复现

```powershell
python -m observatory build --offline --as-of 2026-10-13T06:00:00Z --output dist/replay-a
python -m observatory build --offline --as-of 2026-10-13T06:00:00Z --output dist/replay-b
```

相同源观测、映射、策略和 `--as-of` 产生逐字节相同的快照。不能期待不同抓取时间或不同实际报价生成同一版本。

有 Camelot 源码和 Node 24 的开发环境可额外运行只读集成校验：

```powershell
node scripts/verify-camelot.mjs ../src
```

`python -m observatory build --write-seed` 用于明确更新离线基线，要求全部选中来源通过在线校验。定时 CI 不自动提交 Seed；更新 Seed、映射和策略需走代码审阅。每次发布最多保留最近 7 个不可变快照；客户端请求过期版本遇到 404 时，应重新获取 manifest。原始响应缓存保留最近 20 份及当前源引用，规范化价格保留最近 64 次变化；这不是永久原始数据档案。

下一步按数据价值推进：扩大已审阅的别名映射，接入安全的当前人类偏好 CSV/JSON 和 reasoning/longContext 源，再在 Camelot 加入仅供招募参考的同步与多维证据视图。
