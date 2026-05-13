# feishu_hub — agent ↔ 飞书办公中台

让本机 AI agent（CC / Codex / GA / fsapp ...）跟飞书生态打通的胶水层。**不是飞书 SDK 的包装**——飞书侧的事情交给官方 [`lark-cli`](https://github.com/larksuite/cli)，本模块只做"飞书外的事"：本机审计、安装、配置、hook 桥接、本机执行流编排。

---

## 一句话心智模型

> **飞书是共享状态机，`lark-cli` 是 agent 对飞书的系统调用；`feishu_hub` 只保留执行桥和审计缓存。**

详细心智模型与红线见 `docs/LARK_CLI_CAPABILITIES.md` 以及 memory `feishu_collaboration_model.md`。**违反这条模型的代码会被持续删除**——见下方 M3.A 变更说明。

### 状态归处

| 状态 | 归谁 |
|---|---|
| 工作项生命周期、agent 执行步骤流 | 飞书 Task（`lark-cli task +create / agent_task_step_info append_task_steps`） |
| 跨 agent 即时上下文 | 飞书 IM thread（`lark-cli im +messages-reply --thread`） |
| 协作痕迹、评论 | 飞书 Docs 评论 / 群消息 |
| 索引、统计、看板视图 | 飞书 Base（不是事实源，是索引层） |
| 云侧路由（@mention / 关键词 / 定时） | 飞书 Base Workflow（`LarkMessageTrigger` / `TimerTrigger`） |
| 本机进程、模型调用、重试预算 | 本地（dispatcher.runners / budget） |
| 审计、排错重放 | 本地 journal jsonl |

---

## 模块结构（M3.A 之后）

```
feishu_hub/
├── lark_cli.py         # 稳定封装 lark-cli 调用（subprocess + JSON 解析 + 异常归一化）
├── shim.py             # ~/.local/bin/lark-cli shim：透传 + 脱敏 + 审计
├── journal.py          # 本机 jsonl 审计落盘（不是协作事实源）
├── redact.py           # 敏感字段脱敏
├── remoterefs.py       # 从 stdout 抽取飞书远端 token（doc_token / record_id 等）
├── config.py           # ~/.feishu_hub/config.yaml 读写
├── hooks_merge.py      # CC/Codex hook 配置合并
├── smoke.py            # 升级后回归探针（im +messages-send / docs +create 等已验证命令）
├── lark_cli.py         # 同上（高层包裹）
├── git_log.py          # 多仓库 git log 聚合（日报数据源）
├── llm_summary.py      # 日报摘要（调 GA llmcore；唯一允许 import ga 的文件）
├── daily_report.py     # 本机复盘日报（与飞书 lark-workflow-standup-report 互补，不冲突）
├── bitable_demo.py     # 一键建 agent_tasks 表的参考脚本（M3.A 后表降级为索引层）
├── templates/          # CC/Codex 配置片段 + Stop hook 脚本
└── dispatcher/         # 本机执行桥（M3.A 后 thin bridge）
    ├── cli.py          # fire（hook 单次）+ replay（调试）+ test-rule
    ├── loop.py         # event → match rules → trace/budget → run runner → emit
    ├── rules.py        # 本机 hook → runner 匹配
    ├── runners.py      # cc_headless / codex_exec / gemini_headless / noop
    ├── trace.py        # trace_id / depth / parent_event_id 链路保护
    └── budget.py       # 调用次数 + 成本预算
```

---

## 快速上手

### 安装与配置

```bash
# 1. 装 lark-cli + 它的 skill 包（飞书原生命令面）
npm install -g @larksuite/cli
npx skills add larksuite/cli -y -g

# 2. 登录飞书（按需）
lark-cli auth login --domain im,task,docs,base,drive,vc

# 3. 初始化 feishu_hub
python -m feishu_hub init
```

`init` 会：
- 建 `~/.feishu_hub/` 目录树（journal / state / bin）
- 部署 `agent-stop-notify.sh` 到 `~/.feishu_hub/bin/`
- 把 CC/Codex Stop hook 配置 merge 进 `~/.claude/settings.json` / `~/.codex/hooks.json`
- 在 PATH 前部署 lark-cli shim（透传真实 lark-cli + 落 journal）
- 引导填 `notify_receive_id`、`daily_report.root_folder_token` 等

### 升级后回归

```bash
python -m feishu_hub smoke
```

跑全部已验证的 lark-cli 命令矩阵（`im +messages-send` / `docs +create v2` / `docs +update overwrite` / `drive files list` / `drive +create-folder` / `drive move`），任一失败时 init 与 daily_report 拒绝执行，错误信息指向具体命令。

---

## 变更历史

### M3.A — 清理与降维（2026-05-13）

把违反"飞书=共享状态机"协同模型的本地代码删干净，dispatcher 瘦身成"事件→本机 runner"的薄桥。

**它解决了什么问题**：

| 之前的痛 | 根因 | M3.A 做了什么 |
|---|---|---|
| dispatcher 把所有 agent 事件落到本地 bitable_writer，再写飞书 Base 表 | 把"共享状态"回退成"本机派生" — 跨 agent 看不到本地文件，飞书侧总落后一步 | **删 `dispatcher/bitable_writer.py`**；状态写飞书由 hook/runner 直接调 lark-cli（M3.B 落地）|
| dispatcher 长进程 `tail -F` 本地 journal jsonl 当事件源 | 本地 journal 是审计缓存，不是协作事件源；事件源应该是 `lark-cli event consume <key>` | **删 dispatcher `tail` 子命令** + 相关 checkpoint 文件 + launchd 集成 |
| `dispatcher.emit` 被 `bitable_writer.wrap_emit` 强制包一层 | dispatcher 越界写飞书 | **解开 wrap_emit**；emit 回调只用于本机 journal 审计 |
| 设计文档跟代码现状脱节，"3 种触发模式" / "tail by launchd" 等过时描述持续误导 | 历史包袱 | **5 处文档同步**：`loop.py` / `rules.py` docstring + `DISPATCHER_DESIGN.md` + `M2_COVERAGE.md` + `CC_HEADLESS_AUTH.md` + `M3_DESIGN.md` v2 标作废 |

**它没做什么**（留给 M3.B/C）：

- 没建立 task 域主路径（CC/Codex Stop hook 现在仍写本地 journal + 发 IM 文本；不写飞书 Task）
- 没启用 bot@bot 协作（M3.C）
- M2 bitable `agent_tasks` 表数据不动——保留但**降级为索引层**，M3.B 后由独立 indexer 从飞书 Task 列表反向刷出

**数据**：

- 1 文件删除（166 行）+ 2 文件大幅收缩（cli.py 316→194 / 老 cli 测试 208→153）
- 新增 1 个契约测试文件（`tests/test_feishu_hub_dispatcher_cli_m3a.py`）固化"不再 import bitable_writer / 不再有 tail 子命令"
- 7 个 commit，349/349 测试 PASS，smoke 6/6 PASS
- 实施计划：`docs/superpowers/plans/2026-05-13-feishu-hub-m3a-cleanup.md`

**对下游的影响**：

- 用过 `python -m feishu_hub.dispatcher tail` 的 launchd plist / cron / shell 脚本：**会断**。改用 `python -m feishu_hub.dispatcher fire` 单次直触（接 CC/Codex Stop hook 调用），或 `replay` 重放本机 journal 调试。
- 直接 import `feishu_hub.dispatcher.bitable_writer` 的代码：**会断**。M3.B 完成后通过 `lark-cli task +create` 路径替代。

---

## 进一步阅读

- **协同模型**（必读）：`docs/LARK_CLI_CAPABILITIES.md`
- **dispatcher 设计**：`docs/FEISHU_HUB_DISPATCHER_DESIGN.md`（含 M3.A 后变更节）
- **M2 历史覆盖**：`docs/FEISHU_HUB_M2_COVERAGE.md`
- **M3 v2 设计稿**（部分作废）：`docs/FEISHU_HUB_M3_DESIGN.md`
- **场景清单**：`docs/FEISHU_HUB_SCENARIOS.md`
- **CC headless 认证**：`docs/CC_HEADLESS_AUTH.md`
- **lark-cli 官方 skill 仓库**：https://github.com/larksuite/cli/tree/main/skills

---

## 贡献指引

写 / 改 `feishu_hub/*` 任何代码前，先答这两题：

1. **这个状态归飞书拿还是本地拿？** 答案在飞书侧才动飞书代码；答案在本地侧才动本地代码。
2. **本地要做的事，是不是 `lark-cli` 子进程契约（NDJSON 流 / ready marker / stdin EOF / PreConsume 反订阅）已经做了？** 是 → 删掉重写。

违反这两条的 PR 会被 review 退回。
