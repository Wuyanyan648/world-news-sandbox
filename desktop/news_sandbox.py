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
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, WebKit2, GLib, Gdk

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, '..', 'world-news-map.html')
ICON = os.path.join(BASE, 'icon.svg')
ICON_NAME = 'world-news-sandbox'
APP_ID = 'cn.news.sandbox'
WM_CLASS = 'WorldNewsSandbox'
DATA_DIR = Path(BASE).parent / 'data'
ARCHIVE_DIR = Path(BASE).parent / 'archive'


def setup_identity():
    """设置程序名与 WM_CLASS，使桌面环境能把窗口匹配到 .desktop（图标/名称正确）。"""
    GLib.set_prgname('world-news-sandbox')
    GLib.set_application_name('国际新闻沙盘')
    try:
        Gdk.set_program_class(WM_CLASS)
    except Exception:
        pass
    # 图标优先使用已安装到图标主题的同名图标，缺失时回退到本地 SVG
    try:
        Gtk.Window.set_default_icon_from_file(ICON)
    except Exception:
        pass


class DataManager:
    """通过 JS 桥接管理本地周数据（列表/删除/生成），浏览器端无此能力。"""

    def __init__(self):
        self._generating = set()
        self.view = None

    def list_weeks(self):
        weeks = []
        if DATA_DIR.exists():
            for f in sorted(DATA_DIR.glob('news-*.js')):
                m = re.fullmatch(r'news-(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.js', f.name)
                if not m:
                    continue
                text = f.read_text(encoding='utf-8')
                # 兼容两种历史格式：JSON 生成的 "id": 1 与早期手写的 id: 1
                count = len(re.findall(r'(?:"id"|\bid)\s*:\s*\d+', text))
                weeks.append({
                    'week': f'{m.group(1)}_{m.group(2)}',
                    'events': count,
                    'size': f.stat().st_size,
                    'hasArchive': (ARCHIVE_DIR / f'{m.group(1)}_{m.group(2)}').exists(),
                })
        return {'ok': True, 'weeks': weeks}

    def delete_week(self, week):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}', week or ''):
            return {'ok': False, 'error': '非法周标识'}
        if week in self._generating:
            return {'ok': False, 'error': '该周正在生成，请稍后再删除'}
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
                output = (proc.stdout + proc.stderr).strip()
                if proc.returncode == 0 and '已有更新任务在运行' in output:
                    # workflow 被单实例锁跳过时也返回 0，不能当成生成成功
                    out = {'type': 'error', 'ok': False,
                           'error': '已有更新任务在运行，请稍后再试。'}
                elif proc.returncode == 0:
                    out = {'type': 'generated', 'ok': True, 'week': week}
                else:
                    out = {'type': 'error', 'ok': False,
                           'error': output[-1500:] or 'workflow 执行失败（可能缺少 API key）'}
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
        super().__init__(application_id=APP_ID)
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
        win.set_icon_name(ICON_NAME)
        try:
            win.set_icon_from_file(ICON)
        except Exception:
            pass
        win.set_title('寰宇战情 · 世界新闻沙盘')
        win.maximize()

        settings = WebKit2.Settings()
        settings.set_property('enable-javascript', True)
        settings.set_property('allow-file-access-from-file-urls', True)
        settings.set_property('enable-webgl', True)

        self.view = WebKit2.WebView(settings=settings)
        self.manager.view = self.view          # 生成完成回调需要用它推消息给页面
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
    setup_identity()
    app = NewsSandbox()
    app.run(None)


if __name__ == '__main__':
    main()
