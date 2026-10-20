#!/usr/bin/env bash
# 服务器端一键安装脚本（root 执行）
# 用法：bash /opt/kuafu-agent/deploy/setup-server.sh
# 前置：代码已上传到 /opt/kuafu-agent/{backend,frontend}，密钥已写入 /opt/kuafu-agent/kuafu-agent.env
set -euo pipefail

APP_DIR=/opt/kuafu-agent
BE_DIR="$APP_DIR/backend"
FE_DIR="$APP_DIR/frontend"
SERVICE_USER=www-data
CONF_SRC="$APP_DIR/deploy/kuafu-agent.conf"
SERVICE_SRC="$APP_DIR/deploy/kuafu-agent.service"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

log '1/6 目录与权限'
install -d -m 755 "$BE_DIR" "$FE_DIR" "$BE_DIR/data"
# 数据库要被后端进程读写
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
chown -R "$SERVICE_USER":"$SERVICE_USER" "$BE_DIR"
# 静态页 nginx(www-data) 只读即可
chown -R root:root "$FE_DIR"
chmod -R a+rX "$FE_DIR"

log '2/6 Python 虚拟环境'
# 判断标准不只是"目录在不在"——ensurepip 缺失时会留下一个能启动但没 pip 的残废 venv
if [ ! -x "$BE_DIR/.venv/bin/python" ] || ! "$BE_DIR/.venv/bin/python" -c 'import ensurepip' >/dev/null 2>&1; then
  rm -rf "$BE_DIR/.venv"
  if ! python3 -m venv "$BE_DIR/.venv" 2>/dev/null; then
    echo '  缺少 python3-venv，正在 apt 安装…'
    apt-get update -qq
    apt-get install -y -qq python3.10-venv
    python3 -m venv "$BE_DIR/.venv"
  fi
fi
"$BE_DIR/.venv/bin/python" -m pip install --upgrade pip -q

log '3/6 安装依赖（可能耗时 1-3 分钟）'
# 阿里云轻量在国内，优先走清华源，失败再回退官方
if ! "$BE_DIR/.venv/bin/pip" install -q -r "$BE_DIR/requirements.txt" \
     -i https://pypi.tuna.tsinghua.edu.cn/simple 2>/dev/null; then
  echo '  清华源失败，回退官方源…'
  "$BE_DIR/.venv/bin/pip" install -q -r "$BE_DIR/requirements.txt"
fi
chown -R "$SERVICE_USER":"$SERVICE_USER" "$BE_DIR"

log '4/6 密钥文件'
if [ ! -f "$APP_DIR/kuafu-agent.env" ]; then
  echo '  ！缺少 /opt/kuafu-agent/kuafu-agent.env，请先上传'
  exit 1
fi
chmod 600 "$APP_DIR/kuafu-agent.env"
chown root:root "$APP_DIR/kuafu-agent.env"

log '5/6 systemd 服务'
cp "$SERVICE_SRC" /etc/systemd/system/kuafu-agent-backend.service
systemctl daemon-reload
systemctl enable kuafu-agent-backend
systemctl restart kuafu-agent-backend

log '6/6 nginx 站点（3001）'
cp "$CONF_SRC" /etc/nginx/conf.d/kuafu-agent.conf
nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl restart nginx

log '等待后端就绪'
for i in $(seq 1 40); do
  if curl -fsS -m 3 http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
    echo '  后端已就绪'
    break
  fi
  sleep 1
done

echo
echo '──────── 结果 ────────'
systemctl is-active kuafu-agent-backend nginx | sed 's/^/  /'
curl -fsS -m 5 http://127.0.0.1:8080/api/health || echo '  （后端健康检查未通过）'
echo
curl -s -o /dev/null -w '  静态页 HTTP %{http_code}\n' http://127.0.0.1:3001/
echo '别忘了：阿里云控制台防火墙放行 TCP 3001'
