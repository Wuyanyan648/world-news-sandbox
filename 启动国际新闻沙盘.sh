#!/usr/bin/env bash
# 国际新闻沙盘 · 一键启动（Linux 桌面应用）
set -e
cd "$(dirname "$0")"

if ! python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('WebKit','6.0')" 2>/dev/null; then
  echo "缺少 GTK4 / WebKitGTK 6.0 运行库，请先安装：sudo apt install gir1.2-gtk-4.0 gir1.2-webkit-6.0 python3-gi" >&2
  exit 1
fi

exec python3 desktop/news_sandbox.py "$@"
