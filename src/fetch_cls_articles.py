#!/usr/bin/env python3
"""
财联社 (cls.cn) 电报快讯抓取 — Playwright 版

CLS telegraph API requires signing (签名错误), so we use Playwright
headless Chromium to render the page and extract the telegraph items.

The telegraph page (cls.cn/telegraph) is a Next.js SSR app that loads
real-time news items. We scrape the rendered DOM.

Usage:
    python src/fetch_cls_articles.py
    python src/fetch_cls_articles.py --date 2026-05-24 --out data/cls_articles.csv
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

BASE_URL = "https://www.cls.cn/telegraph"

# ── Industry keyword mapping: CLS article text → supply chain categories ──
# These are matched against article title + summary to auto-tag industries
CLS_KEYWORD_MAP = [
    # AI / Semiconductor
    ("AI芯片", ["AI芯片", "人工智能芯片", "NPU", "算力芯片", "GPU", "寒武纪", "海光信息"]),
    ("人工智能", ["人工智能", "AI大模型", "大模型", "ChatGPT", "生成式AI", "DeepSeek", "文心一言"]),
    ("半导体", ["半导体", "晶圆", "芯片", "台积电", "中芯国际", "晶圆代工", "光刻"]),
    ("先进封装", ["先进封装", "封装", "Chiplet", "CoWoS", "FC-BGA", "SiP", "晶圆级封装", "封测"]),
    ("半导体设备", ["半导体设备", "刻蚀", "薄膜沉积", "CVD", "PVD", "清洗设备", "涂胶显影"]),
    ("半导体材料", ["半导体材料", "硅片", "光刻胶", "靶材", "CMP", "抛光液", "电子特气"]),
    ("存储芯片", ["存储芯片", "DRAM", "NAND", "HBM", "DDR5", "内存", "闪存", "存储器"]),
    ("功率半导体", ["功率半导体", "IGBT", "SiC", "碳化硅", "MOSFET", "功率器件"]),

    # New Energy
    ("新能源车", ["新能源车", "电动汽车", "电动车", "新能源汽车", "比亚迪", "特斯拉", "蔚来", "小鹏", "理想", "刀片电池", "DM-i"]),
    ("锂电池", ["锂电池", "锂电", "动力电池", "储能电池", "磷酸铁锂", "三元锂", "锂矿"]),
    ("光伏储能", ["光伏", "太阳能", "储能", "逆变器", "组件", "硅料", "硅片", "TOPCon", "HJT", "BC电池"]),
    ("新能源", ["新能源", "绿电", "风电", "氢能", "新能源发电"]),

    # Consumer / Robotics
    ("消费电子", ["消费电子", "手机", "折叠屏", "智能手机", "可穿戴", "VR", "AR", "MR", "AI PC", "PC"]),
    ("机器人", ["机器人", "人形机器人", "具身智能", "减速器", "伺服电机", "工业机器人"]),

    # Medical
    ("医药创新", ["创新药", "医药", "CXO", "CRO", "CDMO", "PD-1", "ADC", "CAR-T", "医保", "集采"]),
]


def extract_industries_from_text(text):
    """Match article text to supply chain industries using keyword map."""
    if not text:
        return []
    lower_text = text.lower()
    matched = []
    for industry, keywords in CLS_KEYWORD_MAP:
        for kw in keywords:
            if kw.lower() in lower_text:
                matched.append(industry)
                break
    return matched


def extract_telegraph_items(page, max_items=50):
    """Extract telegraph news items from the rendered page."""
    items = []

    # Wait for telegraph list to load
    try:
        # Try to wait for the telegraph item container
        page.wait_for_selector(
            '[class*="telegraph"]',
            timeout=10000,
        )
    except PwTimeout:
        pass

    # Give a little more time for React to render
    time.sleep(2)

    # Get all text content that looks like telegraph items
    # The cls.cn telegraph page renders items with timestamps and content divs
    all_text = page.inner_text("body")

    # Try to find structured data
    # Approach 1: Look for item-level containers
    containers = page.query_selector_all('[class*="telegraphList"], [class*="telegraph_list"], [class*="roll-list"], [class*="rollList"]')
    if containers:
        container = containers[0]
        items_html = container.inner_html()
        # Parse items from the container
        article_elements = container.query_selector_all('[class*="item"], [class*="Item"], li, [class*="card"]')
        for el in article_elements[:max_items]:
            try:
                title = (el.inner_text() or "").strip()
                if title and len(title) > 10:
                    items.append(title)
            except Exception:
                continue

    # Approach 2: Fall back to scanning for time-stamped entries
    if not items:
        # Telegraph items typically have timestamps like "HH:MM" followed by content
        lines = all_text.split("\n")
        current_item = []
        for line in lines:
            line = line.strip()
            if not line:
                if current_item:
                    items.append(" ".join(current_item))
                    current_item = []
                continue
            # Check if line starts with a time pattern like "09:30" or "2026-05-24 09:30"
            if re.match(r"^\d{1,2}:\d{2}", line) or re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}", line):
                if current_item:
                    items.append(" ".join(current_item))
                    if len(items) >= max_items:
                        break
                current_item = [line]
            else:
                current_item.append(line)

        if current_item:
            items.append(" ".join(current_item))

    # Approach 3: Get all paragraphs with substantial content
    if len(items) < 3:
        paragraphs = page.query_selector_all("p, div.text, [class*='content'], [class*='desc']")
        seen = set()
        for p in paragraphs[:max_items]:
            try:
                text = (p.inner_text() or "").strip()
                if len(text) > 20 and text not in seen:
                    seen.add(text)
                    items.append(text)
            except Exception:
                continue

    return items


def build_articles(raw_items, report_date):
    """Convert raw telegraph text items to article format."""
    articles = []
    seen_titles = set()

    for item in raw_items:
        # Clean up and deduplicate
        title = re.sub(r"\s+", " ", item).strip()
        if not title or len(title) < 15:
            continue

        # Deduplicate near-duplicates
        title_key = title[:60].lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        # Extract time if present
        time_match = re.match(r"(\d{1,2}:\d{2})\s+(.*)", title)
        if time_match:
            time_str = time_match.group(1)
            content = time_match.group(2)
        else:
            time_str = ""
            content = title

        # Extract summary (first meaningful sentence or first 120 chars)
        summary = content[:120] if len(content) > 120 else content

        # Skip navigation/UI text
        skip_patterns = [
            r"^首页", r"^登录", r"^注册", r"^搜索", r"^更多",
            r"^\d+\s*条评论", r"^加载", r"^没有更多",
            "客服", "举报", "反馈",
        ]
        if any(re.search(p, title) for p in skip_patterns):
            continue

        # Detect industry tags
        matched_industries = extract_industries_from_text(title + " " + summary)
        industry_tags = "|".join(set(matched_industries))

        articles.append({
            "date": report_date,
            "title": "【财联社】{}".format(content[:100]),
            "summary": summary,
            "content": title,
            "industry_tags": industry_tags,
            "url": "",
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
        description="Fetch telegraph articles from 财联社 (Playwright)"
    )
    parser.add_argument(
        "--out",
        default=os.path.join(BASE_DIR, "data", "cls_articles.csv"),
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
        default=50,
        help="Maximum number of telegraph items to extract",
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
    print("[cls] Fetching 财联社 telegraph articles...")
    print("[cls] URL: {}".format(BASE_URL))

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

        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print("[cls] Page load warning: {}".format(e))

        # Wait for content to render
        page.wait_for_load_state("networkidle", timeout=15000)

        raw_items = extract_telegraph_items(page, max_items=args.max_items)
        print("[cls] Raw telegraph items found: {}".format(len(raw_items)))

        browser.close()

    articles = build_articles(raw_items, report_date)
    print("[cls] Articles built: {}".format(len(articles)))

    # Count industry-tagged
    tagged = sum(1 for a in articles if a["industry_tags"])
    print("[cls] Industry-tagged: {}/{}".format(tagged, len(articles)))
    if tagged:
        cats = {}
        for a in articles:
            if a["industry_tags"]:
                for tag in a["industry_tags"].split("|"):
                    cats[tag] = cats.get(tag, 0) + 1
        print("[cls] Categories found:")
        for cat, cnt in sorted(cats.items(), key=lambda x: -x[1]):
            print("  {}: {} articles".format(cat, cnt))

    write_csv(articles, args.out)


if __name__ == "__main__":
    main()
