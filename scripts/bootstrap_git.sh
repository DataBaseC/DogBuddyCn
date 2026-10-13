#!/usr/bin/env bash
# DogBuddyCn — 把当前部署目录变成 git 检出并推送到 GitHub（一次性操作）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
#
# 之后 `manage.py update` 会自动走 git 路径：
#   git fetch origin  →  git checkout <sha>  →  装依赖  →  重启  →  健康检查断言版本  →  失败回滚
#
# 关键点：**拉取用匿名 HTTPS，不需要任何凭据**（仓库是公开的）；
# 只有这一次推送需要写权限，用 deploy key（推荐）或 PAT。
#
# 用法：
#   bash scripts/bootstrap_git.sh /opt/dogbuddy
#   DOGBUDDY_GIT_SSH=git@github.com:you/repo.git bash scripts/bootstrap_git.sh
set -euo pipefail

APP_DIR="${1:-/opt/dogbuddy}"
REPO_HTTPS="${DOGBUDDY_GIT_HTTPS:-https://github.com/DataBaseC/DogBuddyCn.git}"
REPO_PUSH="${DOGBUDDY_GIT_SSH:-git@github.com:DataBaseC/DogBuddyCn.git}"
BRANCH="${DOGBUDDY_GIT_BRANCH:-main}"

cd "$APP_DIR"

VER="$(./.venv/bin/python -c 'import dogbuddy; print(dogbuddy.__version__)' 2>/dev/null || echo unknown)"
echo "== DogBuddyCn git bootstrap =="
echo "   app_dir : $APP_DIR"
echo "   version : $VER"
echo "   fetch   : $REPO_HTTPS"
echo "   push    : $REPO_PUSH"

if [ -d .git ]; then
  echo "-- 已经是 git 检出，跳过 init"
else
  echo "-- git init ($BRANCH)"
  git init -q -b "$BRANCH"
fi

# 身份（只写本仓库，不动全局配置）
git config user.email "dogbuddy@localhost"
git config user.name "DogBuddyCn"

echo "-- 暂存工作树（.gitignore 已排除 .venv/ wheels/ __pycache__/ VERSION/ *.db）"
git add -A

if git diff --cached --quiet; then
  echo "-- 没有需要提交的改动"
else
  git commit -q -m "DogBuddyCn v${VER} — 初始导入（部署目录）"
  echo "-- 已提交: $(git log -1 --format='%h %s')"
fi

echo "-- 配置 remote：拉取走匿名 HTTPS，推送走 SSH"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REPO_HTTPS"
else
  git remote add origin "$REPO_HTTPS"
fi
git remote set-url --push origin "$REPO_PUSH"

echo "-- 验证匿名拉取（不需要凭据）"
if timeout 45 git ls-remote origin >/dev/null 2>&1; then
  echo "   ok：服务器可以匿名访问远端"
else
  echo "   ! 拉取失败——检查网络；bootstrap 仍会尝试推送" >&2
fi

echo "-- 推送"
git push -u origin "$BRANCH"

echo "-- 校验：远端 HEAD 与本地是否一致"
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git ls-remote origin "refs/heads/$BRANCH" | cut -f1)"
echo "   local : $LOCAL"
echo "   remote: $REMOTE"
[ "$LOCAL" = "$REMOTE" ] && echo "== 完成：远端已同步 ==" || { echo "! 远端与本地不一致" >&2; exit 1; }

echo
echo "下一步（自更新就不需要任何凭据了）："
echo "  ./.venv/bin/python manage.py update --check     # 只看更新源"
echo "  ./.venv/bin/python manage.py update             # 真正的自升级"
echo "或者直接对管理员说一句「升级你自己」，它会调用 self_update 工具。"
