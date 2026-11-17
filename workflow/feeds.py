#!/usr/bin/env python3
"""外部情报源抓取（YouTube / X / 自定义 RSS）。

只依赖标准库；网络失败、代理不可用、单个源异常都不会中断主流程，
统一返回已成功获取的条目。用于给 workflow 提供比传统媒体更快的线索。
"""
import html
import json
import os
import re
import socket
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

BJT = timezone(timedelta(hours=8))
ATOM = {'a': 'http://www.w3.org/2005/Atom'}
DEFAULT_TTL_DAYS = 7


def _proxy(cfg):
    feeds = cfg.get('feeds') or {}
    return feeds.get('proxy') or cfg.get('proxy') or os.environ.get('https_proxy') or ''


def _open(url, proxy, timeout=12):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36'
    })
    with opener.open(req, timeout=timeout) as r:
        return r.read()


def _clean(text):
    text = html.unescape(text or '')
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def parse_feed(data, source):
    """解析 Atom / RSS 两种常见结构，返回 [{source,title,published,url}]。"""
    root = ET.fromstring(data)
    items = []

    # Atom（YouTube 使用）
    for e in root.findall('a:entry', ATOM):
        title = e.findtext('a:title', default='', namespaces=ATOM)
        published = e.findtext('a:published', default='', namespaces=ATOM)
        link = ''
        for l in e.findall('a:link', ATOM):
            if l.get('rel') in (None, 'alternate'):
                link = l.get('href', '')
                break
        if title:
            items.append({'source': source, 'title': _clean(title),
                          'published': published, 'url': link})

    # RSS 2.0
    for e in root.findall('.//item'):
        title = e.findtext('title', default='')
        link = e.findtext('link', default='')
        published = e.findtext('pubDate', default='')
        if title:
            items.append({'source': source, 'title': _clean(title),
                          'published': published, 'url': link})
    return items


def _parse_time(value):
    if not value:
        return None
    value = value.strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%a, %d %b %Y %H:%M:%S %z',
                '%a, %d %b %Y %H:%M:%S %Z'):
        try:
            dt = datetime.strptime(value.replace('Z', '+0000'), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fetch_feeds(cfg, since_date, log):
    """抓取所有配置源，返回最近若干天内的线索，按标题去重。"""
    feeds = cfg.get('feeds') or {}
    if not feeds.get('enabled'):
        return []
    proxy = _proxy(cfg)
    if proxy:
        p = urlparse(proxy)
        try:
            socket.create_connection((p.hostname or '127.0.0.1', p.port or 7897), timeout=1).close()
        except OSError:
            log('代理不可用，本轮跳过所有外部情报源。')
            return []
    ttl_days = int(feeds.get('ttl_days') or DEFAULT_TTL_DAYS)
    per_source = int(feeds.get('per_source') or 6)
    since = datetime.strptime(since_date, '%Y-%m-%d').replace(tzinfo=BJT) - timedelta(days=ttl_days)

    targets = []
    for ch in feeds.get('youtube_channels') or []:
        if isinstance(ch, dict):
            targets.append((ch.get('name') or 'YouTube', 
                            f"https://www.youtube.com/feeds/videos.xml?channel_id={ch.get('channel_id','')}"))
        elif ch:
            targets.append(('YouTube', f'https://www.youtube.com/feeds/videos.xml?channel_id={ch}'))

    rsshub = (feeds.get('rsshub_base') or '').rstrip('/')
    if rsshub:
        for acc in feeds.get('x_accounts') or []:
            handle = acc.get('handle') if isinstance(acc, dict) else acc
            name = acc.get('name') if isinstance(acc, dict) else handle
            if handle:
                targets.append((f'X @{handle}', f'{rsshub}/twitter/user/{handle.lstrip("@")}'))

    for f in feeds.get('custom_feeds') or []:
        if isinstance(f, dict) and f.get('url'):
            targets.append((f.get('name') or 'RSS', f['url']))

    collected, seen = [], set()
    for name, url in targets:
        try:
            items = parse_feed(_open(url, proxy), name)
        except Exception as e:
            log(f'情报源跳过 {name}: {type(e).__name__}')
            continue
        kept = 0
        for it in items:
            dt = _parse_time(it['published'])
            if dt and dt < since:
                continue
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            collected.append(it)
            kept += 1
            if kept >= per_source:
                break
        if kept:
            log(f'情报源 {name}: 获取 {kept} 条')
    return collected


def build_leads_block(items, limit=24):
    """把情报源条目压缩成提示词片段。"""
    if not items:
        return '（本轮未获取到外部平台线索，请仅依靠联网检索。）'
    lines = []
    for it in items[:limit]:
        dt = _parse_time(it.get('published') or '')
        when = dt.astimezone(BJT).strftime('%Y-%m-%d') if dt else ''
        lines.append(f"- [{it['source']}]{(' ' + when) if when else ''} {it['title']}")
    return '\n'.join(lines)
