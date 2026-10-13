# Camelot-SEER 部署与 Camelot 链路验收

历史记录：本文描述 v0.1 首次部署。当前 Arena v2 JSON 发布、无网页和内部端点删除以 [UPGRADE.md](UPGRADE.md) 及 [ARENA_ACCEPTANCE.md](ARENA_ACCEPTANCE.md) 为准。

日期：2026-10-13。

## 已部署

- 仓库：https://github.com/xiaoyu-msf519/Camelot-SEER
- Pages：https://xiaoyu-msf519.github.io/Camelot-SEER/
- 完整数据：https://xiaoyu-msf519.github.io/Camelot-SEER/api/v1/manifest.json
- Camelot 兼容数据：https://xiaoyu-msf519.github.io/Camelot-SEER/api/camelot-v1/manifest.json
- 线上报告：https://xiaoyu-msf519.github.io/Camelot-SEER/report.json

Pages 已通过 GitHub API 启用 `build_type=workflow`，HTTPS 强制开启。首次推送提交为 `f4288db`；首次 Actions [38382982630](https://github.com/xiaoyu-msf519/Camelot-SEER/actions/runs/38382982630) 的 build 和 deploy 都为 success。

每 6 小时运行一次（UTC `17 */6 * * *`），另支持 push 与手动 `workflow_dispatch`。部署后在线 smoke 验证两种 snapshot 的完整性、模型匹配和投影一致性。Actions 调度由 GitHub 管理，具体执行时间可能排队延迟。

`.gitignore` 已验证忽略 `.venv/`、`.cache/`、`dist/`、`.env*`、SSH 私钥与常见证书密钥格式。SSH 密钥只通过本机 Git 配置引用，GitHub API 凭据从本机凭据管理器读取，没有写入项目或日志。仓库与 Pages 不需要模型 API Key。

## 首次线上数据结果

线上版本：`v1-0995e96fa6d7eb94bca0f4e5`，生成时间 `2026-10-13T10:23:23Z`。

- 模型：255；评测观测：1,246；已匹配：288。
- 兼容导出：234 个模型，包含 `architect / coder / reviewer` 参考 Profile。
- 全部 6 个来源实际抓取成功，没有网络回退。
- 仍隔离 models.dev 的两条无效规格，具体模型见线上 report。
- 完整快照 hash：`691633d0c0c2391113a195f65c3f8c98d1131cc689c0c8d1cfcc1ca5d671ac97`。
- 兼容快照 hash：`6819cf4b7114e6f3ce004d85b939cb68d2b497bb910b78a5c973a36641c18ed3`。

每次构建会生成新版本，此处记录首次线上实测，最新值以 manifest 与 report 为准。

## Camelot 链路实测

在现有 `D:/Camelot-DSH/src` 中运行：

```powershell
node --import tsx/esm ../model-observatory/scripts/test-camelot-link.mjs .
node --import tsx/esm ../model-observatory/scripts/test-camelot-boot.mjs .
```

两条命令均 Exit 0；一键 `Test-Camelot-Link.bat` 同样 Exit 0。

第一条使用 Camelot **现有** ModelRegistry、dsh Route Adapter 和 ProviderHub：

- 实际 HTTP(S) 请求线上兼容 manifest 与 snapshot，并通过 SHA-256、Schema 和关系校验。
- ModelRegistry 从 `seed` 更新到 `remote`，注册表包含 234 个模型。
- 本地模拟目录的 `openai/gpt-4o` 与 `openai/gpt-4.1` 精确匹配，coder 解析选到 `gpt-4.1`。
- 保留本地 Seed 的 coder 下限：`minContext=8192`、`requireTools=true`。
- 本地 Adapter 未确认 tools 时，即使远端声明支持工具，仍拒绝路由。
- 无本地凭据时拒绝；本地上下文 64k 不能被远端 128k 声明提高。
- 重开 Registry 从已验证 cache 加载；取消同步时保留原有数据。

第二条通过实际 Cordis `boot()` 加载 Camelot Core，全部存储位于 SEER 的忽略目录 `.cache/core-boot/`：

- 启动时 Core 已可用，首次观察为 Seed 且 `syncing=true`。
- 后台数据同步成功，状态转为 `remote`，三种 Profile 可用。
- 原有 Project Runtime 仍正常注册。
- 空本地路由仍拒绝执行，不因注册表数据增加而授予模型执行权限。
- 测试最后销毁 Cordis fiber，终止刷新 timer 并清理在途操作。

本次解析使用**明确的本地 fixture 模型目录**，没有读取模型 API Key、没有实际调用 LLM，也没有派发 Worker。结论是数据获取、校验、模型对应、路由安全门禁和 Core 后台同步链路已打通，不能扩大为“真实模型生成任务已经验收”。

完整数据只读客户端也已实际请求线上地址，成功匹配 `openai/gpt-4o`：Context=62.07，Price=16.67；无证据的其他维度保持未知。

```powershell
python -m examples.poll https://xiaoyu-msf519.github.io/Camelot-SEER/api/v1/manifest.json openai gpt-4o --once
python -m scripts.check-pages
```

两条均 Exit 0。后者确认完整 Feed 与兼容投影逐字段一致。

## 当前客户端配置与限制

提供 `examples/camelot-seer.patch.yml`，已安装 Camelot Core 的 Web profile 可显式加载：

```powershell
pnpm dsh web --patch ../model-observatory/examples/camelot-seer.patch.yml
```

补丁配置 registryUrl、4 秒网络截止时间和 6 小时后台更新周期。本次**没有改动 Camelot src 的默认配置或源文件**。

自定义本地 `catalogPath` 优先，现有 Core 会因此关闭远程同步；需要启用远程时应由本地部署明确决定是否移除该设置。不能直接替用户删除本地工具探测 Catalog。

兼容 Profile 是参考排序，basis 为 bootstrap，硬要求为空；本地 Seed 下限仍由 Camelot 保留。未知或陈旧维度不参与分数排序，退回稳定身份顺序。这避免空 Profile 同步后出现 `UNKNOWN_PROFILE`，但不将历史榜成绩冒充近期角色能力。

尚未完成：Web 招募视图读取完整多维 Feed、对真实凭据/模型的任务级验收。这些属于下一次 Camelot 客户端接入。

## 本次检查

| 命令 / 检查 | Exit Code |
| --- | --- |
| 本地 25 项单测 | 0 |
| 本地 offline build | 0 |
| Camelot 原有 validateCatalog 对新 Profile 投影校验 | 0 |
| GitHub Actions build / deploy | success |
| Pages 首页与 report HTTP | 200 |
| `test-camelot-link.mjs` | 0 |
| `test-camelot-boot.mjs` | 0 |
| `Test-Camelot-Link.bat` | 0 |
| `examples.poll --once` | 0 |
| `scripts.check-pages` | 0 |
| `git check-ignore` 秘密与输出文件 | 0 |
| `git diff --check` | 0 |

以上是实际已执行的记录；并未用日志观察替代可重复断言。
