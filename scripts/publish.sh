#!/usr/bin/env bash
# 提交并推送当前仓库改动到 origin/main
# 用法: ./scripts/publish.sh "plan: 新增 xxx 学习计划"
set -euo pipefail

cd "$(dirname "$0")/.."

MSG="${1:-}"
if [ -z "$MSG" ]; then
  echo "用法: ./scripts/publish.sh \"<提交信息>\"" >&2
  exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "[warn] 当前分支为 $BRANCH，切换回 main" >&2
  git checkout main
fi

# 1. 先本地提交，保证工作区干净，rebase 才能执行
git add -A
if git diff --cached --quiet; then
  NEED_COMMIT=0
else
  git commit -m "$MSG"
  NEED_COMMIT=1
fi

# 2. 同步远端，把本地提交叠加到最新 main 之上
git fetch origin main
if ! git rebase origin/main; then
  echo "[error] rebase 失败：请解决冲突后执行 git rebase --continue，或 git rebase --abort 回退" >&2
  exit 1
fi

# 3. 推送
if ! git push origin main; then
  echo "[error] 推送失败：检查 SSH 密钥是否仍对 GitHub 有效（443 端口）" >&2
  exit 1
fi

if [ "$NEED_COMMIT" = "1" ]; then
  echo "[ok] 已推送: $MSG"
else
  echo "[ok] 已与远端同步"
fi
