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

## 冲突解决参考

### `.gitignore` reflect 段
```
!reflect/agent_team_worker.py
!reflect/agent_team_worker_robust.py    # fork 私有
!reflect/goal_mode.py                   # upstream 新增
```

### `hub.pyw` discover_services
保留两边：fork 的 proxy_sh / bbs_py / fsapp_env 启动块 **且** 上游的 `EXCLUDES = {'goal_mode.py', 'chatapp_common.py', 'tuiapp.py'}`。

## 常见雷区

| 现象 | 原因 | 处理 |
|------|------|------|
| `push --rejected fetch first` | origin 被另一台机器推过 | step 4 merge origin |
| rebase 一直冲突 | 把上游提交也当本地 | abort，改用 merge |
| stash pop 冲突 | 本地脏改动撞到 upstream 同区域 | 手动合并 → 单独 commit |
| `assets/agent_bbs.db` 未跟踪 | BBS 运行时产物 | 加 `.gitignore`，**不要提交** |

## Commit message 约定

- 合并提交：默认 `Merge remote-tracking branch 'upstream/main'`
- 冲突解决补丁：`fix(fork): merge upstream <feature> into <fork-feature>`

## 一键脚本（可选）

```bash
git stash push -u -m "pre-upstream-merge" && \
git fetch upstream && git merge upstream/main --no-edit && \
git stash pop ; \
git fetch origin && git merge origin/main --no-edit && \
git push origin main
```
冲突时脚本会停在 merge 步骤，手动解完再继续后续命令。
