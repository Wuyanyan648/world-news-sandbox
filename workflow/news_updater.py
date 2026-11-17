#!/usr/bin/env python3
"""国际新闻独立更新 workflow（不依赖 TRAE）。

流程：读取 news-data.js 的 latestDate → 与当前北京时间比较，若已最新则跳过；
否则调用带联网搜索的 LLM API 检索近一周国际新闻，结构化校验后原子写入
news-data.js 与 archive/ 归档。由 systemd --user timer（Persistent=true）调度。
"""
import fcntl
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feeds import fetch_feeds, build_leads_block

BASE = Path(__file__).resolve().parent.parent
DATA_FILE = BASE / 'news-data.js'
ARCHIVE_DIR = BASE / 'archive'
LOG_FILE = BASE / 'workflow' / 'updater.log'
CONFIG_FILE = BASE / 'workflow' / 'config.json'
LOCK_FILE = BASE / 'workflow' / '.lock'
BJT = timezone(timedelta(hours=8))

CATS = {'war', 'diplomacy', 'economy', 'tech', 'climate', 'politics', 'disaster'}

PROMPT = """请检索最近 7 天（截至 {today}）的国际重大新闻，覆盖地缘政治、军事冲突、外交、经济金融、科技、气候灾害等领域，使用联网搜索获取真实信息，重要事件多信源交叉核实。

以下是刚从 X / YouTube 等平台抓取的最新线索（标题可能简略、可能有噪音）：
{leads}

要求：
- 这些线索只作为“更快的发现渠道”，必须用联网检索核实细节后再收录，无法核实的不要写成确定事实。
- 若某条线索来自 X/YouTube 且核实成立，链接优先给原始权威报道；确实只有平台信息时，可标注“据社交平台消息”。
- 优先收录近 48 小时内发生、传统媒体尚未充分报道的事件。

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
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2 * 1024 * 1024:
            tail = LOG_FILE.read_text(encoding='utf-8')[-500_000:]
            LOG_FILE.write_text(tail, encoding='utf-8')
    except OSError:
        pass
    with LOG_FILE.open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))


PRIVATE_CONFIG = Path.home() / '.config' / 'news-sandbox' / 'config.json'


def get_api_key(cfg):
    """按安全优先级读取 API key：环境变量 → 用户级私有配置 → 项目配置 → 交互式输入。"""
    for var in ('NEWS_API_KEY', 'DEEPSEEK_API_KEY', 'PERPLEXITY_API_KEY', 'MOONSHOT_API_KEY'):
        val = (os.environ.get(var) or '').strip()
        if val:
            return val
    if PRIVATE_CONFIG.exists():
        key = (json.loads(PRIVATE_CONFIG.read_text(encoding='utf-8')).get('api_key') or '').strip()
        if key:
            return key
    key = (cfg.get('api_key') or '').strip()
    if key and not key.startswith('在此填入'):
        return key
    if not sys.stdin.isatty():
        raise SystemExit('未提供 API key（非交互环境请使用环境变量 NEWS_API_KEY 或 ~/.config/news-sandbox/config.json）。')
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


def call_llm(cfg, today, leads=''):
    payload = {
        'model': cfg.get('model', 'deepseek-flash'),
        'messages': [
            {'role': 'system', 'content': '你是国际新闻检索与结构化助手，使用联网检索获取真实最新新闻，严格输出 JSON。'},
            {'role': 'user', 'content': PROMPT.format(today=today, leads=leads)},
        ],
        'temperature': 0.2,
    }
    if cfg.get('provider', 'deepseek') == 'deepseek':
        # DeepSeek 服务端联网搜索工具（非思考模式，客户端无需处理工具循环）
        payload['tools'] = [{'type': 'deepseek_web_search', 'name': 'deepseek_web_search'}]
        payload['thinking'] = {'type': 'disabled'}

    # API 默认直连：只有显式配置 api_proxy 时才走代理。
    # 不能复用 feeds 的代理，否则国内可直连的 API 会因代理未启动而全部失败。
    proxy = cfg.get('api_proxy') or ''
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    opener = urllib.request.build_opener(*handlers)

    last_err = None
    for attempt in range(3):
        req = urllib.request.Request(
            cfg.get('base_url', 'https://api.deepseek.com/chat/completions'),
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Authorization': f"Bearer {get_api_key(cfg)}"},
            method='POST',
        )
        try:
            with opener.open(req, timeout=300) as resp:
                body = json.loads(resp.read().decode('utf-8'))
            return body['choices'][0]['message']['content']
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < 2:
                log(f'第 {attempt + 1} 次请求失败（{e}），5 秒后重试。')
                time.sleep(5)
    raise urllib.error.URLError(f'重试 3 次仍失败: {last_err}')


def parse_json(text):
    text = text.strip()
    text = re.sub(r'^```(?:json)?|```$', '', text, flags=re.M).strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < 0:
        raise ValueError('API 返回中未找到 JSON 对象')
    return json.loads(text[start:end + 1])


def normalize(data, target):
    """校验并清洗模型输出：日期必须在检索窗口内、坐标合法、id 唯一、rel 指向有效事件。"""
    target_dt = datetime.strptime(target, '%Y-%m-%d')
    start_dt = target_dt - timedelta(days=7)

    events, id_map, dropped = [], {}, 0
    for raw in data.get('events') or []:
        try:
            d = str(raw.get('d', '')).strip()
            if not re.fullmatch(r'\d{2}-\d{2}', d):
                raise ValueError('d 格式')
            month, dom = map(int, d.split('-'))
            day_dt = datetime(target_dt.year, month, dom)
            if day_dt > target_dt:
                day_dt = day_dt.replace(year=target_dt.year - 1)
            if not (start_dt <= day_dt <= target_dt):
                raise ValueError('事件日期不在检索窗口内')

            cat = raw.get('cat')
            if cat not in CATS:
                raise ValueError('分类非法')

            title = str(raw.get('title') or '').strip()
            if not title:
                raise ValueError('标题为空')

            locs = []
            for loc in raw.get('locs') or []:
                lat, lng = float(loc['lat']), float(loc['lng'])
                if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    raise ValueError('坐标越界')
                locs.append({'n': str(loc.get('n') or '事发地'), 'lat': lat, 'lng': lng})
            if not locs:
                raise ValueError('缺少地点')

            links = [{'t': str(l.get('t') or '来源'), 'u': str(l.get('u'))}
                     for l in (raw.get('links') or [])
                     if str(l.get('u', '')).startswith('http')]
            if not links:
                raise ValueError('缺少有效信源链接')

            try:
                old_id = int(raw.get('id'))
            except (TypeError, ValueError):
                old_id = 0

            events.append({
                'old_id': old_id,
                'd': d, 'cat': cat, 'title': title, 'locs': locs,
                'countries': [str(c) for c in (raw.get('countries') or []) if c],
                'sum': str(raw.get('sum') or '').strip(),
                'links': links,
                'rel': [int(x) for x in (raw.get('rel') or [])
                        if isinstance(x, int) or str(x).isdigit()],
            })
        except Exception:
            dropped += 1

    if not events:
        raise ValueError(f'没有通过校验的事件（丢弃 {dropped} 条），保留旧数据')

    # 重排为连续唯一 id，同时保留旧 id -> 新 id 映射，保证 rel 语义正确
    for i, ev in enumerate(events, 1):
        if ev['old_id'] > 0 and ev['old_id'] not in id_map:
            id_map[ev['old_id']] = i
        ev['id'] = i
    valid_ids = {e['id'] for e in events}
    for ev in events:
        ev['rel'] = sorted({id_map[r] for r in ev['rel']
                            if r in id_map and id_map[r] in valid_ids and id_map[r] != ev['id']})
        ev.pop('old_id')

    if dropped:
        log(f'已丢弃 {dropped} 条不合规事件。')
    return {'latestDate': target, 'events': events}


def write_outputs(data, target, is_current):
    latest = data['latestDate']
    events = data['events']
    generated = datetime.now(BJT).isoformat(timespec='seconds')
    payload = ('window.NEWS_DATA = {\n'
               f'  latestDate: "{latest}",\n'
               f'  generatedAt: "{generated}",\n'
               '  events: ' + json.dumps(events, ensure_ascii=False, indent=2) + '\n};\n')

    target_dt = datetime.strptime(target, '%Y-%m-%d')
    start = (target_dt - timedelta(days=7)).strftime('%Y-%m-%d')
    # 每周数据独立保存，供页面按指定日期热切换
    data_dir = BASE / 'data'
    data_dir.mkdir(exist_ok=True)
    week_file = data_dir / f'news-{start}_{target}.js'
    tmp = week_file.with_suffix('.tmp')
    tmp.write_text(payload, encoding='utf-8')
    tmp.replace(week_file)
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
        month, dom = map(int, day.split('-'))
        try:
            day_dt = datetime(target_dt.year, month, dom)
            if day_dt > target_dt:
                day_dt = day_dt.replace(year=target_dt.year - 1)
        except ValueError:
            log(f'跳过非法日期 {day}。')
            continue
        lines = [f'# {day_dt:%Y-%m-%d} 国际新闻\n']
        for ev in items:
            lines.append(f"## {ev['title']}\n")
            lines.append(ev['sum'] + '\n')
            lines.append('来源：' + '、'.join(f"[{l['t']}]({l['u']})" for l in ev['links']) + '\n')
        (week_dir / f'{day_dt:%Y-%m-%d}.md').write_text('\n'.join(lines), encoding='utf-8')
    log(f'数据已写入 data/news-{start}_{target}.js 与 archive/{start}_{target}/')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='国际新闻独立更新 workflow')
    parser.add_argument('--date', help='指定截止日期 YYYY-MM-DD，检索该日期及其前7天的新闻（用于历史数据或测试）')
    args = parser.parse_args()

    # 单实例锁：避免定时任务与手动/界面触发同时运行，造成重复计费与写冲突
    LOCK_FILE.parent.mkdir(exist_ok=True)
    lock_handle = LOCK_FILE.open('w')
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log('已有更新任务在运行，本次跳过。')
        return 0

    today = datetime.now(BJT).strftime('%Y-%m-%d')
    target = args.date or today
    try:
        datetime.strptime(target, '%Y-%m-%d')
    except ValueError:
        log(f'日期格式错误：{target}，应为 YYYY-MM-DD。')
        return 2
    if target > today:
        log(f'不能检索未来日期：{target}（今天 {today}）。')
        return 2
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
        leads_items = fetch_feeds(cfg, target, log)
        leads = build_leads_block(leads_items)
        if leads_items:
            log(f'外部情报源共获取 {len(leads_items)} 条线索。')
        raw = call_llm(cfg, target, leads)
        data = normalize(parse_json(raw), target)
        write_outputs(data, target, is_current)
        log(f"更新完成：latestDate={data['latestDate']}，事件 {len(data['events'])} 条。")
        return 0
    except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError) as e:
        log(f'更新失败，保留旧数据：{e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
