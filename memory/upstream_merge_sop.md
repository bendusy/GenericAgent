# Upstream Merge SOP

同步 `lsdefine/GenericAgent` (upstream) 到本 fork (`bendusy/GenericAgent`，origin) 的标准流程。

## 仓库拓扑

- `upstream` = `https://github.com/lsdefine/GenericAgent.git`（只 fetch，不 push）
- `origin`   = `git@github.com:bendusy/GenericAgent.git`（fork，可能被多机推送）
- 本地 `main` 同时领先 upstream 与 origin，三方需手动调和。

## 完整步骤

### 1. 暂存本地脏改动
```bash
git status
git stash push -u -m "pre-upstream-merge"   # -u 包含未跟踪文件
```

### 2. 合并 upstream
```bash
git fetch upstream
git log --oneline HEAD..upstream/main        # 预览将要引入的提交
git merge upstream/main --no-edit
```

冲突常见位置：
- `.gitignore`：fork 的 reflect 白名单段（保留两边的 `!reflect/*.py`）
- `hub.pyw`：fork 的 BBS/proxy infra vs upstream 的 EXCLUDES（合并而非二选一）

### 3. 恢复脏改动
```bash
git stash pop
```
若 stash pop 再次冲突（通常还在 `hub.pyw`），手动合并 → `git add` → `git commit` 单独成 commit。

### 4. 同步 origin（其它机器可能已推新提交）
```bash
git fetch origin
git log --oneline HEAD..origin/main          # 看 origin 多了什么
git merge origin/main --no-edit              # 用 merge，**不要用 rebase**
```

> 为什么不 rebase：本地 `main` 包含 upstream 的合并提交，rebase 会把上游 27+ 个提交当成本地工作逐个重放，每个都可能冲突。merge 一次解决。

### 5. 推送
```bash
git push origin main
```

### 6. 重启 launchd 服务（macOS）
推送后需要重启已加载的 plist 让新代码生效：
```bash
for label in $(launchctl list | awk '/com\.genericagent\./ {print $3}'); do
  launchctl kickstart -k "gui/$(id -u)/$label"
done
```
- `kickstart -k` = 强制 kill 当前进程并立即重启，比 `bootout`+`bootstrap` 简洁
- 当前已安装的 label 通常是 `com.genericagent.fsapp`（可能还有 `wechatapp`）
- 验证：`launchctl list | grep genericagent`，PID 应已变化

## 冲突解决参考

### `.gitignore` reflect 段
```
!reflect/agent_team_worker.py
!reflect/agent_team_worker_robust.py    # fork 私有
!reflect/goal_mode.py                   # upstream 新增
```

### `hub.pyw`（2026-06-17 起：私有逻辑已外置，hub.pyw 跟随上游）
**不再内联私有块**。hub.pyw 用上游原样 + 3 处最小 hook：`discover_services()` 末尾调
`_apply_extra_services()`、`start()` 加可选 `env_overrides`/`cwd`、`_toggle` 传参。
私有 proxy/bbs/fsapp 注入全在 **`hub_extra_services.py`**（fork-only，入库）。
- 合并时 hub.pyw 若冲突：直接 `git checkout upstream/main -- hub.pyw`，再补回那 3 处 hook（diff 仅 +24/-3）。
- `hub_extra_services.py` 是 fork 独有文件，上游永远不会动它，**零冲突**。
- 缺失外置文件时 hub.pyw = 纯上游行为（静默退化）。

### 关键事实：生产链路不走 hub.pyw
飞书 fsapp 生产路径是 **launchd → `start_fsapp_with_proxy.sh` → `frontends/fsapp.py`**，
不经 hub.pyw（GUI launcher 仅手动维护用）。改 hub.pyw 不影响生产。
代理注入两处对齐（PORT=5678 / BBS_PORT=58800）：`start_fsapp_with_proxy.sh`（生产）
和 `hub_extra_services.py`（维护启动器）。改其一记得同步另一处。

## 常见雷区

| 现象 | 原因 | 处理 |
|------|------|------|
| `push --rejected fetch first` | origin 被另一台机器推过 | step 4 merge origin |
| rebase 一直冲突 | 把上游提交也当本地 | abort，改用 merge |
| stash pop 冲突 | 本地脏改动撞到 upstream 同区域 | 手动合并 → 单独 commit |
| `assets/agent_bbs.db` 未跟踪 | BBS 运行时产物 | 加 `.gitignore`，**不要提交** |
| `.lto/` / `.codegraph/` 未跟踪 | LTO run state / CodeGraph 索引 | 已在 `.gitignore`，本地产物不提交 |
| `feishu_hub/` 空目录 | 已迁到 `bendusy/roostery` 独立仓 | 正常，本仓不再含 feishu_hub 源码 |

## Commit message 约定

- 合并提交：默认 `Merge remote-tracking branch 'upstream/main'`
- 冲突解决补丁：`fix(fork): merge upstream <feature> into <fork-feature>`

## 一键脚本（可选）

```bash
git stash push -u -m "pre-upstream-merge" && \
git fetch upstream && git merge upstream/main --no-edit && \
git stash pop ; \
git fetch origin && git merge origin/main --no-edit && \
git push origin main && \
for label in $(launchctl list | awk '/com\.genericagent\./ {print $3}'); do \
  launchctl kickstart -k "gui/$(id -u)/$label"; \
done
```
冲突时脚本会停在 merge 步骤，手动解完再继续后续命令。
