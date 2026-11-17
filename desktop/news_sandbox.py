#!/usr/bin/env python3
"""寰宇战情 · 世界新闻沙盘 — Linux 桌面应用（WebKitGTK 壳）"""
import os

# 强制 X11 后端，保证在 Wayland 桌面下窗口也能正常显示
os.environ.setdefault('GDK_BACKEND', 'x11')

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, WebKit2

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, '..', 'world-news-map.html')
ICON = os.path.join(BASE, 'icon.svg')


class NewsSandbox(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='cn.news.sandbox')
        self.connect('activate', self.on_activate)

    def on_activate(self, app):
        win = Gtk.ApplicationWindow(application=app, title='寰宇战情 · 世界新闻沙盘')
        win.set_default_size(1600, 1000)
        try:
            win.set_icon_from_file(ICON)
        except Exception:
            pass
        win.maximize()

        settings = WebKit2.Settings()
        settings.set_property('enable-javascript', True)
        settings.set_property('allow-file-access-from-file-urls', True)
        settings.set_property('enable-webgl', True)

        view = WebKit2.WebView(settings=settings)
        view.load_uri('file://' + HTML)
        win.add(view)

        win.connect('destroy', lambda *_: app.quit())
        win.show_all()
        win.present()


def main():
    if not os.path.exists(HTML):
        raise SystemExit(f'未找到地图文件: {HTML}')
    app = NewsSandbox()
    app.run(None)


if __name__ == '__main__':
    main()
