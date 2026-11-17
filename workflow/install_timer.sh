#!/usr/bin/env bash
# 安装国际新闻独立更新 workflow 的 systemd 用户定时器（Persistent=true，错过开机自动补跑）
set -e
BASE="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/news-updater.service" <<EOF
[Unit]
Description=International news workflow updater

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $BASE/workflow/news_updater.py
WorkingDirectory=$BASE
EOF

cat > "$UNIT_DIR/news-updater.timer" <<EOF
[Unit]
Description=Daily international news update (catch up missed runs)

[Timer]
OnCalendar=*-*-* 07:00:00
OnCalendar=*-*-* 21:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now news-updater.timer
echo "已安装并启用 news-updater.timer（每天 07:00 / 21:00，Persistent=true 开机补跑）"
systemctl --user list-timers news-updater.timer --no-pager
