#!/usr/bin/env bash
# 网络恢复/连上 NAS 后执行本脚本，把仓库推送到双远程（origin=GitHub, nas=内网NAS）
# 用法: bash git_push.sh
set -e
cd "$(dirname "$0")"

echo "[1/2] 推送 GitHub (origin)..."
git push origin main

echo "[2/2] 推送内网 NAS (nas)..."
git push nas main

echo "✅ 推送完成"
git status -sb | head -1
