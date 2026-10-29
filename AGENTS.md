# AGENTS.md — 仓库写作与上传规则

本仓库是用户的学习计划 / 学习笔记仓库，远程地址：
`ssh://git@ssh.github.com:443/Aurora0517/note-agent-learning-note.git`（分支 `main`）。

> 本机网络封了 22 端口、HTTPS 到 GitHub 偶发断连，因此 remote 走 SSH + 443 端口。
> 仓库已配置 `core.sshCommand` 指向 `ssh.github.com:443`，直接 `git push` 即可，不要改回 HTTPS。

## 硬性规则

1. **新建或修改任何学习计划后，必须提交并推送到 `origin/main`**，不要只在本地留文件。
2. 推送前先 `git pull --rebase`，避免非快进冲突。
3. 提交信息使用中文短句 + 前缀：
   - 新增计划：`plan: 新增 <主题> 学习计划`
   - 更新计划：`plan: 更新 <主题> 进度`
   - 笔记：`note: <主题> 学习笔记`
   - 结构/文档：`docs: ...`
4. 学习计划文件路径：`plans/<YYYY>/<MM>/<YYYY-MM-DD>-<主题>.md`，以 `plans/_template.md` 为基线。
5. 新增计划后，同步在 `plans/README.md` 索引表中登记一行。
6. 不要把密钥、公司内部资料等敏感内容写进仓库。
7. 若推送失败（凭据/网络问题），**明确告知用户失败原因和需要他做的操作**，不要静默丢弃改动。

## 上传方式

```bash
./scripts/publish.sh "plan: 新增 某某 学习计划"
```
