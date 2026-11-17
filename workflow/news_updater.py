#!/usr/bin/env python3
"""国际新闻独立更新 workflow（不依赖 TRAE）。

流程：读取 news-data.js 的 latestDate → 与当前北京时间比较，若已最新则跳过；
否则调用带联网搜索的 LLM API 检索近一周国际新闻，结构化校验后原子写入
news-data.js 与 archive/ 归档。由 systemd --user timer（Persistent=true）调度。
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_FILE = BASE / 'news-data.js'
ARCHIVE_DIR = BASE / 'archive'
LOG_FILE = BASE / 'workflow' / 'updater.log'
CONFIG_FILE = BASE / 'workflow' / 'config.json'
BJT = timezone(timedelta(hours=8))

CATS = {'war', 'diplomacy', 'economy', 'tech', 'climate', 'politics', 'disaster'}

PROMPT = """请检索最近 7 天（截至 {today}）的国际重大新闻，覆盖地缘政治、军事冲突、外交、经济金融、科技、气候灾害等领域，使用联网搜索获取真实信息，重要事件多信源交叉核实。

严格只输出一个 JSON 对象（不要任何解释、不要 markdown 代码围栏），结构如下：
{{
  "latestDate": "最新新闻的日期，格式 YYYY-MM-DD",
  "events": [
    {{
      "id": 1,
      "d": "MM-DD",
      "cat": "war/diplomacy/economy/tech/climate/politics/disaster 之一",
      "title": "中文标题",
      "locs": [{{"n": "地点中文名", "lat": 纬度数字, "lng": 经度数字}}],
      "countries": ["英文国名，用于地图高亮，如 Russia/China/Iran/Ukraine/United States of America"],
      "sum": "中文摘要，60-120字，含关键事实",
      "links": [{{"t": "信源名称", "u": "https://原始链接"}}],
      "rel": [相关事件的 id 数字，无则空数组]
    }}
  ]
}}

要求：15-25 条事件，优先影响大的新闻；locs 至少一个地点且经纬度为数字；链接必须真实可访问；single-source 消息在摘要中标注"未获独立证实"。当前日期：{today}。"""


def log(msg):
    line = f"[{datetime.now(BJT).isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(exist_ok=True)
    with LOG_FILE.open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))


PRIVATE_CONFIG = Path.home() / '.config' / 'news-sandbox' / 'config.json'


def get_api_key(cfg):
    """按安全优先级读取 API key：环境变量 → 用户级私有配置 → 项目配置 → 交互式输入。"""
    for var in ('NEWS_API_KEY', 'PERPLEXITY_API_KEY', 'MOONSHOT_API_KEY'):
        if os.environ.get(var):
            return os.environ[var]
    if PRIVATE_CONFIG.exists():
        key = json.loads(PRIVATE_CONFIG.read_text(encoding='utf-8')).get('api_key')
        if key:
            return key
    key = cfg.get('api_key')
    if key and not key.startswith('在此填入'):
        return key
    import getpass
    key = getpass.getpass('请输入新闻 API key（输入不回显，默认不保存）：').strip()
    if not key:
        raise SystemExit('未提供 API key，更新中止。')
    ans = input(f'是否保存到 {PRIVATE_CONFIG}（权限600，下次免输入）？[y/N] ').strip().lower()
    if ans == 'y':
        PRIVATE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        PRIVATE_CONFIG.write_text(json.dumps({'api_key': key}), encoding='utf-8')
        PRIVATE_CONFIG.chmod(0o600)
        log(f'API key 已保存到 {PRIVATE_CONFIG}（600 权限）。')
    return key


def current_latest():
    if not DATA_FILE.exists():
        return None
    m = re.search(r'latestDate:\s*"(\d{4}-\d{2}-\d{2})"', DATA_FILE.read_text(encoding='utf-8'))
    return m.group(1) if m else None


def call_llm(cfg, today):
    payload = {
        'model': cfg.get('model', 'sonar-pro'),
        'messages': [
            {'role': 'system', 'content': '你是国际新闻检索与结构化助手，使用联网检索获取真实最新新闻，严格输出 JSON。'},
            {'role': 'user', 'content': PROMPT.format(today=today)},
        ],
        'temperature': 0.2,
    }
    req = urllib.request.Request(
        cfg.get('base_url', 'https://api.perplexity.ai/chat/completions'),
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': f"Bearer {get_api_key(cfg)}"},
        method='POST',
    )
    proxy = cfg.get('proxy') or os.environ.get('https_proxy')
    if proxy:
        urllib.request.install_opener(urllib.request.build_opener(
            urllib.request.ProxyHandler({'http': proxy, 'https': proxy})))
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode('utf-8'))
    return body['choices'][0]['message']['content']


def parse_json(text):
    text = text.strip()
    text = re.sub(r'^```(?:json)?|```$', '', text, flags=re.M).strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < 0:
        raise ValueError('API 返回中未找到 JSON 对象')
    return json.loads(text[start:end + 1])


def validate(data):
    if not isinstance(data.get('events'), list) or not data['events']:
        raise ValueError('events 为空')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', data.get('latestDate', '')):
        raise ValueError('latestDate 格式错误')
    for ev in data['events']:
        if ev.get('cat') not in CATS:
            raise ValueError(f"非法分类: {ev.get('cat')}")
        if not ev.get('locs'):
            raise ValueError('缺少 locs')
        for loc in ev['locs']:
            float(loc['lat']); float(loc['lng'])
        if not ev.get('links'):
            raise ValueError('缺少信源链接')
    return data


def write_outputs(data, target, is_current):
    latest = data['latestDate']
    events = data['events']
    generated = datetime.now(BJT).isoformat(timespec='seconds')
    payload = ('window.NEWS_DATA = {\n'
               f'  latestDate: "{latest}",\n'
               f'  generatedAt: "{generated}",\n'
               '  events: ' + json.dumps(events, ensure_ascii=False, indent=2) + '\n};\n')

    start = (datetime.strptime(target, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    # 每周数据独立保存，供页面按指定日期热切换
    data_dir = BASE / 'data'
    data_dir.mkdir(exist_ok=True)
    (data_dir / f'news-{start}_{target}.js').write_text(payload, encoding='utf-8')
    # 仅当前周更新主数据文件（latestDate 驱动页面自动更新）
    if is_current:
        tmp = DATA_FILE.with_suffix('.tmp')
        tmp.write_text(payload, encoding='utf-8')
        tmp.replace(DATA_FILE)

    week_dir = ARCHIVE_DIR / f'{start}_{target}'
    week_dir.mkdir(parents=True, exist_ok=True)
    by_day = {}
    for ev in events:
        by_day.setdefault(ev['d'], []).append(ev)
    for day, items in by_day.items():
        y, m, d = target.split('-')[0], *day.split('-')
        lines = [f'# {y}-{m}-{d} 国际新闻\n']
        for ev in items:
            lines.append(f"## {ev['title']}\n")
            lines.append(ev['sum'] + '\n')
            lines.append('来源：' + '、'.join(f"[{l['t']}]({l['u']})" for l in ev['links']) + '\n')
        (week_dir / f'{y}-{m}-{d}.md').write_text('\n'.join(lines), encoding='utf-8')
    log(f'数据已写入 data/news-{start}_{target}.js 与 archive/{start}_{target}/')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='国际新闻独立更新 workflow')
    parser.add_argument('--date', help='指定截止日期 YYYY-MM-DD，检索该日期及其前7天的新闻（用于历史数据或测试）')
    args = parser.parse_args()

    today = datetime.now(BJT).strftime('%Y-%m-%d')
    target = args.date or today
    datetime.strptime(target, '%Y-%m-%d')
    is_current = (target == today)

    if not args.date:
        latest = current_latest()
        if latest and latest >= today:
            log(f'数据已最新（{latest}），跳过更新。')
            return 0
        log(f'数据过期（latestDate={latest}，今天={today}），开始 workflow 更新。')
    else:
        log(f'指定日期更新：检索截至 {target} 及其前7天的新闻。')
    cfg = load_config()
    try:
        raw = call_llm(cfg, target)
        data = validate(parse_json(raw))
        write_outputs(data, target, is_current)
        log(f"更新完成：latestDate={data['latestDate']}，事件 {len(data['events'])} 条。")
        return 0
    except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError) as e:
        log(f'更新失败，保留旧数据：{e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
