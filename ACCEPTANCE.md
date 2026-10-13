# 首版验收记录

日期：2026-10-13。代码基线：`dc6462b`。独立工程位于 `model-observatory/`，Camelot `src/` 在开发前后均为干净工作区，没有任何 upstream 文件修改。

交付收尾另补 Pages 根目录的静态 JSON 入口页；补充后实际执行构建、校验及入口链接检查通过，不改变快照 Schema 或评分策略。

## 实际结果

- 初次在线采集：6 个来源全部通过，并提交其规范化离线 Seed。
- 模型记录：255；原始评测观测：1,246；已对应身份的观测：288；未对应：958。
- Camelot v1 兼容导出：234 个模型。严格遵守其非空 context、明确评测日期、封闭 Schema 和引用关系要求。
- 最新在线 BAT 验收版本：`v1-19b5068bb222ae0068e4ce10`，时间 `2026-10-13T04:28:16Z`。
- 该次 Aider、BFCL、models.dev 为 fresh；Arena-Hard、EvalPlus、SWE-bench 在 5 秒总截止时间后使用缓存。构建与校验仍以 Exit 0 完成，没有标成“所有源在线成功”。
- 当前多维快照：1,706,497 字节，低于 4MiB 客户端上限。
- 最新在线快照 SHA-256：`affb0939472d13cdfa3478672c92c0881ced6ae69a0e392390c976fd40d6499c`。

`fresh` 来源状态表示这次成功核对来源，不代表每条评测近期发生；benchmark 的独立新鲜度仍按日期计算。

## 测试与 Exit Code

以下命令在项目目录运行；虚拟环境使用独立 `.venv`。干净克隆在 `.cache/fresh-check/`，没有父工程的源缓存或 dist。

| 命令 / 验收 | Exit Code | 结果 |
| --- | --- | --- |
| `python -m venv .venv` | 0 | 独立新环境 |
| `.venv/Scripts/python -m pip install -r requirements.txt` | 0 | 在新环境实际安装 |
| `.venv/Scripts/python -m pip install --no-deps -e .` | 0 | 可安装的 CLI 入口 |
| `.venv/Scripts/python -m unittest discover -s tests -v` | 0 | 25 项通过 |
| `python -m observatory build --write-seed` | 0 | 初次 6 源在线成功，冻结 Seed |
| `git clone --local --no-hardlinks . .cache/fresh-check` | 0 | 无缓存干净源码副本 |
| 干净克隆中执行 25 项单测 | 0 | 不依赖原工作区资源 |
| 干净克隆 `python -m observatory build --offline --as-of 2026-10-13T06:00:00Z` | 0 | 6 个来源都从 Seed 加载 |
| 干净克隆 `python -m observatory validate dist/api/v1` | 0 | hash、Schema、引用关系通过 |
| 干净克隆 `node scripts/verify-camelot.mjs D:/Camelot-DSH/src` | 0 | 现有 Camelot `validateCatalog` 通过，234 模型 |
| `.venv/Scripts/model-observatory.exe validate dist/api/v1` | 0 | 安装后 console script 可用 |
| `python -m observatory schema` 与 `git diff --exit-code -- schema` | 0 | 生成 Schema 无漂移 |
| 两次相同 `--offline --as-of` 构建与字节比较 | 0 | 快照与 manifest 逐字节相同 |
| `cmd.exe /d /c "Validate.bat <NUL"` | 0 | 安装检查、25 单测、离线构建、完整校验 |
| `cmd.exe /d /c "Collect.bat <NUL"` | 0 | 实际在线请求、超时缓存降级、完整校验 |
| Seed 结构遍历检查 | 0 | 无凭证、执行配置或权限字段 |
| Camelot `git status --short` | 0 | 无输出，无修改 |

重放比较的快照 hash 为 `19a55ac3259f74307b3eeda9869216430cff324e5ea16379e1099a33e2dfe8a6`。干净 Seed 副本的固定时间快照 hash 为 `e49f45870b4174f642d208b1f276b3804ce601bb128d6f9e28d98c18e4ca8f24`。两者输入来源时间不同，因此不要求跨输入 hash 相同。

## 覆盖的故障与安全边界

- 真正的持续少量响应请求仍受总截止时间约束，不会靠保持连接逃过超时。
- HTTP 故障、Schema 漂移和无效数据不会覆盖已验证来源；损坏源缓存可回到 Seed。
- 没有有效缓存或 Seed 时拒绝发布空 Registry。
- 客户端验证同源相对路径、响应字节数、精确 SHA-256、Schema、版本/数量与引用。
- 客户端重新打开后可从绑定发布者的缓存恢复；篡改数据与 HTTP 503 保留此前有效状态。
- 远程执行/权限字段被 Schema 拒绝；Python YAML 对象执行标签被 safe loader 拒绝。
- 模型日期版本保持分开，provider 相同才可进行客户端精确匹配；歧义不分配。
- high/thinking 变体和不同 Agent harness 不会借最高分覆盖默认成绩。
- 没价格时为未知，不是免费；明确零报价和未知报价区别处理。
- 价格变化保存观测历史，缺少官方生效日期时保持未知。
- 过期快照、未知策略或废弃身份不能继续标成可用于新鲜招募的证据。

## 已发现问题与边界

1. models.dev 中 `google/gemini-3.5-live-translate-preview` 和 `mistral/ministral-3b-2512` 存在无法满足本地规格约束的记录。两条均隔离，保留原始响应 hash 和报告记录；没有补造或截断数值。
2. 958 条证据暂未匹配。大部分为其他托管服务、开源权重名称、旧版本或多模型系统。继续扩展需要审阅明确映射，不能使用模糊匹配凑覆盖率。
3. 当前 chat 适配器采用历史 Arena-Hard v0.1 机器裁判榜，不能宣称已经接入当前人类 Chatbot Arena 排名。
4. EvalPlus、BFCL 部分条目没有公开评测日期/完整环境；这些字段保持未知，相关评分不能宣称具有已确认的新鲜度。
5. reasoning 和 longContext 暂无真实测评适配器，保持 `null`。Context 只评分声明容量，latency 只解释 BFCL 环境。
6. SWE-bench 网站数据是 CC-BY-NC-4.0；商业用途需排除该源或解决其授权，代码 MIT 不覆盖来源数据权利。
7. 兼容投影的 `profiles` 为空，只供事实/证据交换。不能直接替换现有依赖 Seed Profile 的整套路由 Catalog；完整评分应作为招募数据旁路接入。
8. GitHub Actions/Pages 工作流已写好，但本次未创建外部仓库、未执行 GitHub 平台部署。需将独立项目放入目标仓库并启用 Pages 的 Actions 发布方式。
9. Camelot 客户端未修改。提供的是完整静态数据 API 与只读轮询示例，实际客户端同步与招募 UI 是后续接入工作。

## 后续顺序

1. 把本项目发布到独立 GitHub 仓库并验收首次 Pages workflow。
2. 扩充有依据的别名映射，再接安全的当前人类偏好、reasoning 和长上下文数据源。
3. Camelot 新增后台数据同步与发布者绑定缓存，保留本地 Role/Profile/Adapter 执行政策。
4. 在招募界面展示分维度得分、覆盖度、预算、时效和逐条来源；任务需求决定权重，不引入唯一综合模型分。
