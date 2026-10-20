#!/usr/bin/env bash
# 本地一键部署到阿里云轻量 47.109.203.241（Git Bash 下运行）
# 用法：SSHPASS='服务器root密码' bash deploy/deploy-remote.sh
# 说明：本机没有 sshpass/expect，用 SSH_ASKPASS 免密通道；密码只在临时脚本里存在，脚本退出即删。
set -euo pipefail

: "${SSHPASS:?缺少密码，用法：SSHPASS=xxx bash deploy/deploy-remote.sh}"

HOST_IP=47.109.203.241
HOST="root@$HOST_IP"
REMOTE_APP=/opt/kuafu-agent
STAGE="${STAGE:-$TEMP/kuafu-deploy}"

# 免密通道：askpass 脚本 + 强制走它（无需 tty / DISPLAY）
ASKPASS="$(mktemp)"
trap 'rm -f "$ASKPASS" >/dev/null 2>&1 || true' EXIT
cat > "$ASKPASS" <<EOF
#!/bin/sh
echo '$SSHPASS'
EOF
chmod 700 "$ASKPASS"
export SSH_ASKPASS="$ASKPASS"
export SSH_ASKPASS_REQUIRE=force
SSH_OPTS='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15'

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

step '探测连通性'
ssh $SSH_OPTS "$HOST" 'uname -sr; free -m | head -2; echo "python3: $(python3 -V 2>&1)"; echo "nginx: $(nginx -v 2>&1)"'

# WIPE=1 时先彻底清空旧部署（服务、nginx 站点、目录、数据库），再全新安装
if [ "${WIPE:-0}" = '1' ]; then
  step "清空旧部署 $REMOTE_APP"
  ssh $SSH_OPTS "$HOST" "set -e
systemctl stop kuafu-agent-backend 2>/dev/null || true
systemctl disable kuafu-agent-backend 2>/dev/null || true
rm -f /etc/systemd/system/kuafu-agent-backend.service
rm -f /etc/nginx/conf.d/kuafu-agent.conf
systemctl daemon-reload
rm -rf '$REMOTE_APP'
echo '  旧部署已清除（含数据库）'
ls -d '$REMOTE_APP' 2>/dev/null || echo '  目录已不存在'"
fi

step "上传代码到 $REMOTE_APP"
ssh $SSH_OPTS "$HOST" "mkdir -p $REMOTE_APP"
scp $SSH_OPTS -r "$STAGE/." "$HOST:$REMOTE_APP/"

step '服务器端安装（venv + 依赖 + systemd + nginx）'
ssh $SSH_OPTS "$HOST" "chmod 600 $REMOTE_APP/kuafu-agent.env && bash $REMOTE_APP/deploy/setup-server.sh"

step '线上自检'
ssh $SSH_OPTS "$HOST" "curl -s -o /dev/null -w '静态页 HTTP %{http_code}\n' http://127.0.0.1:3001/; \
curl -fsS -m 5 http://127.0.0.1:3001/api/health; echo"

printf '\n\033[1;32m部署流程结束\033[0m —— 访问 http://%s:3001 （记得在阿里云控制台放行 TCP 3001）\n' "$HOST_IP"
