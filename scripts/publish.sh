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
  echo "[warn] 当前分支为 $BRANCH，将切换到 main" >&2
  git checkout main
fi

# 先同步远端，避免非快进
git fetch origin main
git rebase origin/main || {
  echo "[error] rebase 失败，请手动解决冲突后再执行 publish" >&2
  exit 1
}

git add -A
if git diff --cached --quiet; then
  echo "[info] 没有需要提交的改动"
  exit 0
fi

git commit -m "$MSG"
git push origin main
echo "[ok] 已推送: $MSG"
