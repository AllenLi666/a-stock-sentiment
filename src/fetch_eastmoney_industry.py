#!/usr/bin/env python3
"""
东方财富行业板块抓取 — A股产业链热度数据源

Fetches A-share industry sector performance data from East Money API,
maps industry names to supply chain categories (半导体, 锂电池, etc.),
and outputs a CSV that can be consumed by supply_chain_report.py.

API: https://push2.eastmoney.com/api/qt/clist/get
Returns 496 industry sectors with BK code, name, change% etc.

Usage:
    python src/fetch_eastmoney_industry.py
    python src/fetch_eastmoney_industry.py --out data/eastmoney_industry.csv
    python src/fetch_eastmoney_industry.py --date 2026-05-24
"""

import argparse
import csv
import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# East Money industry sector API
EASTMONEY_API = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?cb=&pn=1&pz=500&po=1&np=1&fltt=2&invt=2&fid=f3"
    "&fs=m:90+t:2&fields=f12,f14,f2,f3,f4"
)

# ── Mapping: eastmoney industry name → supply chain industry category ──────────
# The key is a substring that we search for in the eastmoney industry name.
# If matched, we tag the article with the corresponding supply chain industry.
# Order matters: more specific matches first.
EASTMONEY_INDUSTRY_MAP = [
    # Semiconductor chain
    ("半导体材料", "半导体材料"),
    ("半导体设备", "半导体设备"),
    ("半导体", "半导体"),
    ("集成电路封测", "先进封装"),
    ("集成电路制造", "半导体"),
    ("集成电路设计", "AI芯片"),
    ("模拟芯片设计", "AI芯片"),
    ("数字芯片设计", "AI芯片"),
    ("分立器件", "功率半导体"),
    ("被动元件", "消费电子"),
    ("印制电路板", "消费电子"),

    # New Energy / EV
    ("锂电池", "锂电池"),
    ("锂电专用设备", "锂电池"),
    ("锂电", "锂电池"),
    ("光伏加工设备", "光伏储能"),
    ("光伏辅材", "光伏储能"),
    ("光伏主材", "光伏储能"),
    ("光伏", "光伏储能"),
    ("燃料电池", "新能源车"),
    ("新能源车", "新能源车"),
    ("新能源", "新能源"),
    ("电池", "新能源车"),
    ("汽车零部件", "新能源车"),
    ("汽车", "新能源车"),
    ("电机", "新能源车"),

    # Robotics
    ("机器人", "机器人"),
    ("工控", "机器人"),
    ("自动化设备", "机器人"),

    # Consumer electronics
    ("消费电子", "消费电子"),
    ("光学元件", "消费电子"),
    ("光学光电子", "消费电子"),
    ("元件", "消费电子"),
    ("通信设备", "消费电子"),

    # AI / Computing
    ("AI", "AI芯片"),
    ("人工智能", "人工智能"),
    ("算力", "AI芯片"),
    ("服务器", "AI芯片"),

    # Medical
    ("医药", "医药创新"),
    ("医美", "医药创新"),
    ("生物制品", "医药创新"),
    ("医疗器械", "医药创新"),
    ("医疗服务", "医药创新"),
    ("化学制药", "医药创新"),
    ("中药", "医药创新"),
    ("CXO", "医药创新"),
    ("CRO", "医药创新"),
    ("CDMO", "医药创新"),

    # Storage
    ("存储芯片", "存储芯片"),
    ("存储器", "存储芯片"),

    # Others - general coverage
    ("激光设备", "半导体设备"),
    ("电子化学品", "半导体材料"),
    ("氟化工", "半导体材料"),
    ("磁性材料", "消费电子"),
    ("玻璃", "光伏储能"),
    ("有机硅", "新能源"),
    ("新材料", "新能源"),
]


def fetch_industry_sectors(insecure=False):
    """Fetch all A-share industry sectors from eastmoney API."""
    ctx = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    req = urllib.request.Request(
        EASTMONEY_API,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if data.get("rc") != 0:
        raise RuntimeError("API error: {}".format(data.get("rtnm", "unknown")))
    return data["data"]["diff"], data["data"]["total"]


def map_to_supply_chain_industry(eastmoney_name):
    """Map an eastmoney industry name to our supply chain category."""
    for needle, category in EASTMONEY_INDUSTRY_MAP:
        if needle in eastmoney_name:
            return category
    return ""


def build_articles(sectors, report_date):
    """Convert eastmoney sector data to article format for supply_chain_report.py.

    Each sector becomes one 'article' with:
      - title: industry name + price action
      - summary: change % and performance note
      - content: structured data in JSON-like format
      - industry_tags: mapped supply chain category
    """
    articles = []
    for sector in sectors:
        name = sector.get("f14", "").strip()
        bk_code = sector.get("f12", "").strip()
        change_pct = sector.get("f3", 0)
        price = sector.get("f2", 0)
        volume = sector.get("f4", 0)

        if not name:
            continue

        # Map to supply chain industry
        mapped_industry = map_to_supply_chain_industry(name)

        # Build heat description
        if change_pct >= 5:
            heat = "🔥 强势大涨"
        elif change_pct >= 3:
            heat = "📈 涨幅领先"
        elif change_pct >= 1:
            heat = "📊 小幅上涨"
        elif change_pct >= -1:
            heat = "➡️ 横盘震荡"
        elif change_pct >= -3:
            heat = "📉 小幅下跌"
        else:
            heat = "🔥 大幅下跌"

        title = "【东方财富行业板块】{} {}（{}）".format(name, heat, bk_code)
        summary = "{}行业指数收于 {:.2f}，涨跌幅 {:.2f}%".format(name, price, change_pct)
        content = json.dumps(
            {
                "source": "eastmoney_industry",
                "bk_code": bk_code,
                "industry_name": name,
                "price": price,
                "change_pct": change_pct,
                "volume": volume,
                "date": report_date,
            },
            ensure_ascii=False,
        )

        articles.append({
            "date": report_date,
            "title": title,
            "summary": summary,
            "content": content,
            "industry_tags": mapped_industry,
            "url": "",  # No direct URL for each industry
        })

    return articles


def write_csv(articles, path):
    """Write articles to CSV in the same format as chinaventure_articles.csv."""
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
        description="Fetch A-share industry sector data from East Money"
    )
    parser.add_argument(
        "--out",
        default=os.path.join(BASE_DIR, "data", "eastmoney_industry.csv"),
        help="Output CSV path",
    )
    parser.add_argument(
        "--date",
        default="",
        help="Report date (YYYY-MM-DD), defaults to today",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip SSL verification (use in CI/headless env)",
    )
    args = parser.parse_args()

    report_date = args.date or datetime.now().strftime("%Y-%m-%d")
    print("[eastmoney] Fetching industry sectors from East Money API...")

    sectors, total = fetch_industry_sectors(insecure=args.insecure)
    print("[eastmoney] Total sectors: {}, fetched: {}".format(total, len(sectors)))

    articles = build_articles(sectors, report_date)

    # Count mapped vs unmapped
    mapped_count = sum(1 for a in articles if a["industry_tags"])
    print("[eastmoney] Mapped to supply chain: {}/{} sectors".format(mapped_count, len(articles)))

    # Show which supply chain categories were found
    categories = {}
    for a in articles:
        if a["industry_tags"]:
            categories[a["industry_tags"]] = categories.get(a["industry_tags"], 0) + 1
    if categories:
        print("[eastmoney] Supply chain categories matched:")
        for cat, cnt in sorted(categories.items(), key=lambda x: -x[1]):
            print("  {}: {} sectors".format(cat, cnt))

    write_csv(articles, args.out)


if __name__ == "__main__":
    main()
