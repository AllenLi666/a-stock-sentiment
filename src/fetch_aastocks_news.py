#!/usr/bin/env python3
"""
AASTOCKS 财经新闻抓取 — A股新闻 (AAFN) — Playwright 版

Scrapes AASTOCKS A-share financial news section (aafn) for latest news.
The page loads news items dynamically via AJAX, so Playwright is needed.

URL: http://www.aastocks.com/tc/stocks/news/aafn/latest-news

Usage:
    python src/fetch_aastocks_news.py
    python src/fetch_aastocks_news.py --date 2026-05-24 --out data/aastocks_news.csv
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

BASE_URL = "http://www.aastocks.com/tc/stocks/news/aafn/latest-news"

# ── Industry keyword mapping ──
# AASTOCKS covers broad A-share market news; match against supply chain
AASTOCKS_KEYWORD_MAP = [
    ("AI芯片", ["AI芯片", "AI 芯片", "人工智能芯片", "NPU", "算力", "GPU", "寒武纪", "海光信息"]),
    ("人工智能", ["人工智能", "AI", "大模型", "生成式AI", "ChatGPT", "DeepSeek", "文心一言", "通义千问"]),
    ("半导体", ["半导体", "芯片", "晶圆", "台积电", "中芯国际", "华虹", "光刻", "集成电路"]),
    ("先进封装", ["先进封装", "Chiplet", "CoWoS", "封测", "SiP", "晶圆级封装"]),
    ("半导体设备", ["半导体设备", "刻蚀", "CVD", "PVD", "清洗设备", "涂胶", "显影"]),
    ("半导体材料", ["半导体材料", "硅片", "光刻胶", "靶材", "CMP", "抛光", "电子特气", "特种气体"]),
    ("存储芯片", ["存储芯片", "DRAM", "NAND", "HBM", "DDR5", "内存", "闪存", "存储器"]),
    ("功率半导体", ["功率半导体", "IGBT", "SiC", "碳化硅", "MOSFET", "功率器件"]),
    ("新能源车", ["新能源车", "新能源汽车", "电动汽车", "电动车", "比亚迪", "特斯拉", "蔚来", "小鹏", "理想"]),
    ("锂电池", ["锂电池", "锂电", "动力电池", "储能电池", "磷酸铁锂", "锂矿", "碳酸锂"]),
    ("光伏储能", ["光伏", "太阳能", "储能", "逆变器", "组件", "硅料", "TOPCon", "HJT", "BC电池"]),
    ("新能源", ["新能源", "绿电", "风电", "氢能", "可再生能源"]),
    ("消费电子", ["消费电子", "手机", "折叠屏", "智能手机", "可穿戴", "VR", "AR", "MR", "AI PC"]),
    ("机器人", ["机器人", "人形机器人", "具身智能", "减速器", "伺服", "工业机器人"]),
    ("医药创新", ["创新药", "医药", "CXO", "CRO", "CDMO", "PD-1", "ADC", "CAR-T", "生物药", "疫苗"]),
]


def extract_industries(text):
    """Match text to supply chain industries."""
    if not text:
        return []
    lower_text = text.lower()
    matched = []
    for industry, keywords in AASTOCKS_KEYWORD_MAP:
        for kw in keywords:
            if kw.lower() in lower_text:
                matched.append(industry)
                break
    return matched


def scrape_aastocks(page, max_items=30):
    """Scrape news items from aaStocks page after JS rendering."""
    items = []

    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print("[aastocks] Page load warning: {}".format(e))

    # Wait for dynamic content to load
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # Wait additional time for AJAX-loaded news
    time.sleep(3)

    # Try to find news items by various selectors
    # The news items are loaded into a container with class containing "AAFN"
    found_items = []

    # Approach 1: Look for AAFN news content divs
    for selector in [
        '[class*="AAFNNewsContent"]',
        '[class*="aafn"] a',
        '[class*="news_list"] a',
        '[class*="newsList"] a',
        'div[class*="content"] a[href*="aafn"]',
    ]:
        try:
            elements = page.query_selector_all(selector)
            if elements:
                for el in elements[:max_items]:
                    try:
                        text = (el.inner_text() or "").strip()
                        href = (el.get_attribute("href") or "")
                        if text and len(text) > 15 and href:
                            found_items.append((text, href))
                    except Exception:
                        continue
                if found_items:
                    break
        except Exception:
            continue

    # Approach 2: Get all visible text and find news-like patterns
    if not found_items:
        all_text = page.inner_text("body")
        lines = all_text.split("\n")
        for line in lines:
            line = line.strip()
            if not line or len(line) < 20:
                continue
            # AASTOCKS news items often have date-like prefixes
            # e.g., "24/05/2026 某公司公告..."
            if re.match(r"\d{2}/\d{2}/\d{4}", line) or re.match(r"\d{4}-\d{2}-\d{2}", line):
                found_items.append((line, ""))
            # Or stock codes like (600XXX)
            elif re.search(r"\(\d{6}\)", line) and len(line) > 30:
                found_items.append((line, ""))

    # Approach 3: Get all links that look like news articles
    if not found_items:
        links = page.query_selector_all("a[href*='aafn'], a[href*='news']")
        for link in links[:max_items]:
            try:
                text = (link.inner_text() or "").strip()
                href = (link.get_attribute("href") or "")
                if text and len(text) > 15:
                    found_items.append((text, href))
            except Exception:
                continue

    # Approach 4: Get all substantial text blocks
    if not found_items:
        paragraphs = page.query_selector_all("p, div.text, span.desc, li")
        for p in paragraphs[:max_items]:
            try:
                text = (p.inner_text() or "").strip()
                if len(text) > 30:
                    found_items.append((text, ""))
            except Exception:
                continue

    # Deduplicate
    seen = set()
    for text, href in found_items:
        key = text[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        items.append((text, href))

    return items


def build_articles(raw_items, report_date):
    """Convert raw news items to article format."""
    articles = []
    seen_titles = set()

    for text, href in raw_items:
        # Clean up
        title = re.sub(r"\s+", " ", text).strip()
        if not title or len(title) < 15:
            continue

        # Dedup
        title_key = title[:80].lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        # Skip navigation/UI text
        skip_patterns = [
            r"^首頁", r"^登入", r"^註冊", r"^搜尋", r"^更多",
            r"免責聲明", r"版權", r"客戶服務", r"關於我們",
            r"報價", r"分析", r"評論", r"牛熊", r"認股證",
            r"ETF", r"MPF", r"財經視頻", r"市場動態",
            "menu", "header", "footer", "nav",
        ]
        if any(re.search(p, title) for p in skip_patterns):
            continue

        # Extract date if present (dd/mm/yyyy or yyyy-mm-dd at start)
        date_match = re.match(r"(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})\s+(.*)", title)
        if date_match:
            content = date_match.group(2)
        else:
            content = title

        # Summary
        summary = content[:150] if len(content) > 150 else content

        # Detect industry tags
        matched = extract_industries(title)
        industry_tags = "|".join(set(matched))

        articles.append({
            "date": report_date,
            "title": "【AASTOCKS】{}".format(content[:120]),
            "summary": summary,
            "content": title,
            "industry_tags": industry_tags,
            "url": href if href and href.startswith("http") else "",
        })

    return articles


def write_csv(articles, path):
    """Write articles to CSV."""
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)

    fieldnames = ["date", "title", "summary", "content", "industry_tags", "url"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for article in articles:
            writer.writerow(article)

    print("Wrote {} articles to {}".format(len(articles), path))


def main():
    parser = argparse.ArgumentParser(
        description="Fetch A-share news from AASTOCKS (Playwright)"
    )
    parser.add_argument(
        "--out",
        default=os.path.join(BASE_DIR, "data", "aastocks_news.csv"),
        help="Output CSV path",
    )
    parser.add_argument(
        "--date",
        default="",
        help="Report date (YYYY-MM-DD), defaults to today",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=30,
        help="Maximum number of news items to extract",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run Playwright in headless mode (default: True)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_false",
        dest="headless",
        help="Run Playwright in visible mode (debugging)",
    )
    args = parser.parse_args()

    report_date = args.date or datetime.now().strftime("%Y-%m-%d")
    print("[aastocks] Fetching AASTOCKS A-share news...")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        raw_items = scrape_aastocks(page, max_items=args.max_items)
        print("[aastocks] Raw news items found: {}".format(len(raw_items)))

        browser.close()

    articles = build_articles(raw_items, report_date)
    print("[aastocks] Articles built: {}".format(len(articles)))

    tagged = sum(1 for a in articles if a["industry_tags"])
    print("[aastocks] Industry-tagged: {}/{}".format(tagged, len(articles)))
    if tagged:
        cats = {}
        for a in articles:
            if a["industry_tags"]:
                for tag in a["industry_tags"].split("|"):
                    cats[tag] = cats.get(tag, 0) + 1
        print("[aastocks] Categories found:")
        for cat, cnt in sorted(cats.items(), key=lambda x: -x[1]):
            print("  {}: {} articles".format(cat, cnt))

    write_csv(articles, args.out)


if __name__ == "__main__":
    main()
