# note-agent-learning-note

Agent 学习笔记与学习计划仓库。

## 目录结构

```
plans/            学习计划（按年月归档）
  YYYY/MM/        例如 plans/2026/10/2026-10-29-xxx.md
notes/            学习笔记、实验记录、踩坑总结
scripts/          辅助脚本
```

## 约定

- 每份学习计划一个文件，命名：`YYYY-MM-DD-主题.md`，放在 `plans/<年>/<月>/` 下。
- 基线模板见 `plans/_template.md`，新计划从模板复制。
- `plans/README.md` 维护计划索引表，新增计划后同步更新。
- 学习笔记放 `notes/`，命名 `YYYY-MM-DD-主题.md`，可引用对应的计划文件。
- 提交信息格式：`plan: 新增 <主题> 学习计划` / `note: 更新 <主题> 笔记` / `docs: ...`。

## 日常上传流程

```bash
# 拉取最新
git pull --rebase

# 新增/修改文件后
./scripts/publish.sh "plan: 新增 xxx 学习计划"
```

`scripts/publish.sh` 等价于 `git add -A && git commit -m "<msg>" && git push`。

> 所有学习计划创建或更新后，一律提交并推送到 `origin/main`，本地不长期留存未推送内容。

## 本机环境说明

本机 SSH 22 端口被封、HTTPS 到 GitHub 偶发 `Empty reply from server`，因此：

- remote 使用 `ssh://git@ssh.github.com:443/Aurora0517/note-agent-learning-note.git`
- 仓库内已设置 `core.sshCommand`（走 443 端口，known_hosts 存放在 `.git/ssh_known_hosts`）
- 换机器时需重新添加 SSH 公钥到 GitHub（仓库 Settings → Deploy keys，需勾选 Allow write access）
