#!/usr/bin/env bash
# 一键推送 main 到 GitHub
#
# 背景（2026-11 实测）：
#   1. 直连 github.com 被网络策略阻断（TLS 握手直接失败）；
#   2. 必须走本机 HTTP 代理（默认 127.0.0.1:2788）；
#   3. 走代理时 git 默认的 schannel 后端会在握手阶段报
#      "schannel: failed to receive handshake"，需切 OpenSSL 后端 + HTTP/1.1。
#
# 用法：bash scripts/push.sh
# 退出码：0 成功 / 1 全部通道失败（本地提交与 bundle 仍在，稍后重试即可）
set -u

cd "$(dirname "$0")/.." || exit 1

PROXIES="${GIT_PROXIES:-http://127.0.0.1:2788 http://127.0.0.1:7890 http://127.0.0.1:10809}"

try_push() {
  local label="$1"
  shift
  printf '[push] %s ... ' "$label"
  if git "$@" push origin main >/tmp/push_last.log 2>&1; then
    echo "OK"
    tail -3 /tmp/push_last.log
    return 0
  fi
  echo "失败：$(tail -1 /tmp/push_last.log | cut -c1-100)"
  return 1
}

# 1) 直连（部分环境不需要代理）
git config --get http.proxy >/dev/null 2>&1 && echo "[push] 检测到已配置 http.proxy，跳过直连" || {
  try_push "直连" -c http.sslBackend=openssl && exit 0
}

# 2) 依次尝试本机常见代理端口
for p in $PROXIES; do
  try_push "经代理 $p" \
    -c "http.proxy=$p" -c "https.proxy=$p" \
    -c http.version=HTTP/1.1 -c http.sslBackend=openssl && exit 0
done

echo "[push] 所有通道均失败：多为网络/代理不可达。本地提交未丢，可稍后重试本脚本。"
exit 1
