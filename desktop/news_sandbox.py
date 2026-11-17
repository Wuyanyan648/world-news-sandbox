#!/usr/bin/env python3
"""寰宇战情 · 世界新闻沙盘 — Linux 桌面应用（WebKitGTK 壳）"""
import os

# 强制 X11 后端，保证在 Wayland 桌面下窗口也能正常显示
os.environ.setdefault('GDK_BACKEND', 'x11')

import json
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, WebKit2, GLib

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, '..', 'world-news-map.html')
ICON = os.path.join(BASE, 'icon.svg')
DATA_DIR = Path(BASE).parent / 'data'
ARCHIVE_DIR = Path(BASE).parent / 'archive'


class DataManager:
    """通过 JS 桥接管理本地周数据（列表/删除/生成），浏览器端无此能力。"""

    def __init__(self):
        self._generating = set()

    def list_weeks(self):
        weeks = []
        if DATA_DIR.exists():
            for f in sorted(DATA_DIR.glob('news-*.js')):
                m = re.fullmatch(r'news-(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.js', f.name)
                if not m:
                    continue
                text = f.read_text(encoding='utf-8')
                weeks.append({
                    'week': f'{m.group(1)}_{m.group(2)}',
                    'events': text.count('"id":'),
                    'size': f.stat().st_size,
                    'hasArchive': (ARCHIVE_DIR / f'{m.group(1)}_{m.group(2)}').exists(),
                })
        return {'ok': True, 'weeks': weeks}

    def delete_week(self, week):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}', week or ''):
            return {'ok': False, 'error': '非法周标识'}
        removed = []
        f = DATA_DIR / f'news-{week}.js'
        if f.exists():
            f.unlink()
            removed.append(f.name)
        d = ARCHIVE_DIR / week
        if d.exists():
            shutil.rmtree(d)
            removed.append(f'archive/{week}')
        return {'ok': True, 'removed': removed, 'weeks': self.list_weeks()['weeks']}

    def generate_week(self, week, end_date):
        """后台线程调用独立 workflow 生成指定周数据，完成后通过 JS 回调通知。"""
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}', week or ''):
            return {'type': 'error', 'ok': False, 'error': '非法周标识'}
        try:
            datetime.strptime(end_date, '%Y-%m-%d')
        except (TypeError, ValueError):
            return {'type': 'error', 'ok': False, 'error': '非法截止日期'}
        if week in self._generating:
            return {'type': 'generating', 'ok': True, 'week': week}
        self._generating.add(week)
        updater = Path(BASE).parent / 'workflow' / 'news_updater.py'

        def run():
            try:
                proc = subprocess.run(
                    [sys.executable, str(updater), '--date', end_date],
                    capture_output=True, text=True, timeout=900)
                if proc.returncode == 0:
                    out = {'type': 'generated', 'ok': True, 'week': week}
                else:
                    tail = (proc.stdout + proc.stderr).strip()[-1500:]
                    out = {'type': 'error', 'ok': False,
                           'error': tail or 'workflow 执行失败（可能缺少 API key）'}
            except Exception as e:
                out = {'type': 'error', 'ok': False, 'error': str(e)}
            finally:
                self._generating.discard(week)
            GLib.idle_add(lambda: self.view.run_javascript(
                f'window.onManagerResult({json.dumps(out, ensure_ascii=False)})', None, None, None))

        threading.Thread(target=run, daemon=True).start()
        return {'type': 'generating', 'ok': True, 'week': week}


class NewsSandbox(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='cn.news.sandbox')
        self.manager = DataManager()
        self.view = None
        self.win = None
        self.connect('activate', self.on_activate)

    def on_manager_message(self, ucm, result):
        try:
            req = json.loads(result.get_js_value().to_string())
            action = req.get('action')
            if action == 'list':
                out = self.manager.list_weeks()
            elif action == 'deleteWeek':
                out = self.manager.delete_week(req.get('week', ''))
            elif action == 'generateWeek':
                out = self.manager.generate_week(req.get('week', ''), req.get('endDate', ''))
            else:
                out = {'ok': False, 'error': '未知操作'}
        except Exception as e:
            out = {'ok': False, 'error': str(e)}
        self.view.run_javascript(
            f'window.onManagerResult({json.dumps(out, ensure_ascii=False)})', None, None, None)

    def on_activate(self, app):
        # 已存在窗口时直接置前，避免重复激活产生多个窗口
        if self.win is not None:
            self.win.present()
            return
        win = Gtk.ApplicationWindow(application=app, title='寰宇战情 · 世界新闻沙盘')
        self.win = win
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

        self.view = WebKit2.WebView(settings=settings)
        ucm = self.view.get_user_content_manager()
        ucm.register_script_message_handler('newsManager')
        ucm.connect('script-message-received', self.on_manager_message)
        self.view.load_uri('file://' + HTML)
        win.add(self.view)

        win.connect('destroy', lambda *_: app.quit())
        win.show_all()
        win.present()
        if '--open-manager' in sys.argv:
            def on_load_changed(v, ev):
                if ev == WebKit2.LoadEvent.FINISHED:
                    v.run_javascript('openManager && openManager();', None, None, None)
            self.view.connect('load-changed', on_load_changed)


def main():
    if not os.path.exists(HTML):
        raise SystemExit(f'未找到地图文件: {HTML}')
    app = NewsSandbox()
    app.run(None)


if __name__ == '__main__':
    main()
