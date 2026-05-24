"""
投中网 (ChinaVenture) 每日文章爬虫 — Playwright 版

Uses Playwright headless Chromium to bypass JSL anti-bot challenge.
Scrapes www.chinaventure.com.cn homepage for article links, then
fetches individual article details.

URL pattern: /news/{category}-{date}-{id}.html
  - category: numeric category ID (80=market news, 78=funding, etc.)
  - date: YYYYMMDD format
  - id: numeric article ID

Usage:
    /usr/local/opt/python@3.11/bin/python3.11 src/fetch_chinaventure.py
    /usr/local/opt/python@3.11/bin/python3.11 src/fetch_chinaventure.py --date 2026-05-21
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime, date as date_type
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

BASE_URL = "https://www.chinaventure.com.cn"

# Category IDs that are relevant for industry/news analysis
# 80 = market/industry news, 78 = investment/funding,
# Other categories may also contain relevant content
RELEVANT_CATEGORIES = {
    "80": "行业新闻",
    "78": "投融资",
    "108": "深度分析",
    "110": "行业观察",
    "111": "市场动态",
    "112": "研究报告",
    "113": "企业动态",
    "114": "产业趋势",
    "116": "科技创新",
}

# Industry keyword mapping
INDUSTRY_KEYWORDS = {
    "先进封装": ["先进封装", "chiplet", "封装测试", "封测", "fc-bga", "sip", "晶圆级封装", "cowos"],
    "半导体设备": ["半导体设备", "刻蚀设备", "薄膜沉积", "光刻机", "cvd", "pvd", "清洗设备"],
    "半导体材料": ["半导体材料", "硅片", "光刻胶", "电子特气", "靶材", "cmp", "抛光垫"],
    "存储芯片": ["存储芯片", "dram", "nand", "hbm", "闪存", "内存", "存算一体"],
    "AI芯片": ["ai芯片", "人工智能芯片", "gpu", "npu", "算力芯片", "ai加速"],
    "功率半导体": ["功率半导体", "igbt", "碳化硅", "sic", "mosfet", "功率器件"],
    "消费电子": ["消费电子", "智能手机", "vr", "ar", "可穿戴", "智能终端"],
    "新能源车": ["新能源车", "电动汽车", "智能驾驶", "自动驾驶", "锂电", "电驱"],
    "锂电池": ["锂电池", "动力电池", "储能电池", "锂电", "正极材料", "负极材料"],
    "光伏储能": ["光伏", "太阳能", "逆变器", "储能", "组件", "硅片", "topcon", "hjt"],
    "人工智能": ["人工智能", "大模型", "ai", "自然语言", "计算机视觉", "深度学习", "gpt"],
    "机器人": ["机器人", "人形机器人", "协作机器人", "减速器", "伺服", "自动化"],
    "医药创新": ["创新药", "生物医药", "cxo", "cro", "cdmo", "肿瘤药", "临床试验"],
    "半导体": ["半导体", "芯片", "集成电路", "晶圆", "foundry", "制程"],
    "新能源": ["新能源", "碳中和", "清洁能源", "可再生能源", "风电"],
}


def extract_date_from_url(url):
    """Extract date from URL pattern: /news/80-20260522-391497.html"""
    m = re.search(r'-(\d{8})-', url)
    if m:
        raw = m.group(1)
        return "{}-{}-{}".format(raw[:4], raw[4:6], raw[6:8])
    return ""


def extract_category_from_url(url):
    """Extract category ID from URL pattern: /news/80-20260522-391497.html"""
    m = re.search(r'/news/(\d+)-', url)
    return m.group(1) if m else ""


def extract_industry_tags(title, summary="", content=""):
    """Extract industry tags from article text."""
    text = (title + " " + summary + " " + content).lower()
    tags = []
    for industry, keywords in INDUSTRY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                tags.append(industry)
                break
    return tags


def scrape_article_links_from_homepage(page, max_pages=1):
    """
    Scrape article links from the homepage, clicking "加载更多" to load
    older articles. The homepage dynamically loads more articles via
    a 'click_getmore_pc' button.

    Args:
        page: Playwright page object
        max_pages: Number of times to click "加载更多" (default: 1 = initial only).
                   Use higher values (10-20) to reach older articles.
    """
    print("  [Playwright] Loading homepage for article links...")
    page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(3000)

    # Click "加载更多" repeatedly to load historical articles
    if max_pages > 1:
        print("    Clicking '加载更多' up to {} times...".format(max_pages - 1))
        for i in range(max_pages - 1):
            try:
                btn = page.query_selector("button.click_getmore_pc")
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(2000)
                    if (i + 1) % 5 == 0:
                        print("      ... clicked {} times".format(i + 1))
                else:
                    print("      No more '加载更多' button at click #{}".format(i + 1))
                    break
            except Exception as exc:
                print("      Error clicking load more #{}: {}".format(i + 1, exc))
                break

    html = page.content()

    # Extract all news article links from the homepage
    # Pattern: /news/{category}-{date}-{id}.html
    pattern = re.compile(r'href=["\'](/news/[\w-]+\.html)["\']')
    all_links = set()
    for m in pattern.finditer(html):
        all_links.add(urljoin(BASE_URL, m.group(1)))

    # Deduplicate and build article data
    article_data = {}
    for url in sorted(all_links):
        article_data[url] = {"url": url, "date": extract_date_from_url(url)}

    print("    Found {} unique article links (after {} load-more clicks)".format(
        len(article_data), max_pages - 1))
    return article_data


def fetch_article_detail(page, article):
    """Fetch full article content from its URL."""
    url = article["url"]
    print("    Fetching: {}...".format(url.rsplit('/', 1)[-1]), end=" ")

    try:
        page.goto(url, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(2000)
    except PwTimeout:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
        except Exception as exc:
            print("FAILED: {}".format(exc))
            return article

    # Extract title from page title
    title = page.title()
    # Clean title: remove " | 投中网" suffix
    title = re.sub(r'\s*[|]\s*投中网.*$', '', title).strip()
    if title:
        article["title"] = title

    # Get meta description as summary
    meta_desc = page.evaluate("""() => {
        const m = document.querySelector('meta[name="description"]');
        return m ? m.getAttribute('content') : '';
    }""")
    if meta_desc:
        article["summary"] = meta_desc.strip()

    # Get article content from the article wrapper
    content_text = page.evaluate("""() => {
        // Try several selectors for article content
        const selectors = [
            '.article_slice_pc',
            '.article_warpper_pc',
            '.describe_text',
            '#articleId',
            '.page_content',
        ];
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el) {
                // Get text, exclude script/style
                const clone = el.cloneNode(true);
                const scripts = clone.querySelectorAll('script, style, .articledetail_ercode_pc, .copyright_text');
                scripts.forEach(s => s.remove());
                return clone.innerText.trim();
            }
        }
        return '';
    }""")
    if content_text:
        # Clean up excessive whitespace
        content_text = re.sub(r'\s+', ' ', content_text).strip()
        article["content"] = content_text[:2000]

    print("OK ({} chars)".format(len(article.get("content", ""))))
    return article


def write_csv(articles, path):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "title", "url", "summary", "content", "industry_tags"])
        writer.writeheader()
        for article in articles:
            writer.writerow({
                "date": article.get("date", ""),
                "title": article.get("title", ""),
                "url": article.get("url", ""),
                "summary": article.get("summary", ""),
                "content": article.get("content", ""),
                "industry_tags": "|".join(article.get("tags", [])),
            })
    print("Wrote {} articles to {}".format(len(articles), path))


def fetch_article_by_url(page, url):
    """Fetch a single article by its direct URL."""
    article = {"url": url, "date": extract_date_from_url(url)}
    return fetch_article_detail(page, article)


def main():
    parser = argparse.ArgumentParser(description="Fetch daily articles from 投中网 (Playwright)")
    parser.add_argument("--date", default="",
                        help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--out", default=os.path.join(BASE_DIR, "data", "chinaventure_articles.csv"),
                        help="Output CSV path")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="Run headless browser")
    parser.add_argument("--visible", action="store_true",
                        help="Run with visible browser (debug)")
    parser.add_argument("--max-pages", type=int, default=1,
                        help="Number of 'load more' clicks to fetch older articles (default: 1 = homepage only, use 10-20 for history)")
    parser.add_argument("--urls", nargs="*", default=[],
                        help="Fetch specific article URLs directly (bypasses homepage scraping)")
    args = parser.parse_args()

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")
    headless = not args.visible

    print("=" * 60)
    print("  投中网 Playwright 爬虫")
    print("=" * 60)
    if args.urls:
        print("Mode: direct URL fetch ({} URLs)".format(len(args.urls)))
    else:
        print("Target date: {}".format(target_date))
        print("Max load-more pages: {}".format(args.max_pages))
    print()

    with sync_playwright() as pw:
        print("[1/3] Launching Chromium...")
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        page = context.new_page()

        if args.urls:
            # Direct URL mode: fetch specified articles one by one
            print("[2/3] Fetching specified article URLs...")
            completed = []
            for i, url in enumerate(args.urls):
                article = fetch_article_by_url(page, url)
                article["tags"] = extract_industry_tags(
                    article.get("title", ""),
                    article.get("summary", ""),
                    article.get("content", ""),
                )
                cat = extract_category_from_url(url)
                cat_name = RELEVANT_CATEGORIES.get(cat, "其他")
                print("    [{}/{}] [{}] {} | tags: {}".format(
                    i + 1, len(args.urls),
                    cat_name,
                    article.get("title", "?")[:40],
                    "|".join(article["tags"]),
                ))
                completed.append(article)
                time.sleep(1.5)
        else:
            # Homepage scraping mode
            print("[2/3] Scraping article links from homepage...")
            articles_dict = scrape_article_links_from_homepage(page, max_pages=args.max_pages)

            if not articles_dict:
                print("  No articles found!")
                browser.close()
                return

            # Filter by target date if specified
            filtered = {}
            for url, art in articles_dict.items():
                art_date = art.get("date", "")
                if not target_date or art_date == target_date:
                    filtered[url] = art

            print("  Articles matching date {}: {}".format(target_date, len(filtered)))

            # Fetch details for matching articles
            print("\n[3/3] Fetching article details...")
            completed = []
            for i, (url, article) in enumerate(sorted(filtered.items())):
                article = fetch_article_detail(page, article)
                article["tags"] = extract_industry_tags(
                    article.get("title", ""),
                    article.get("summary", ""),
                    article.get("content", ""),
                )
                cat = extract_category_from_url(url)
                cat_name = RELEVANT_CATEGORIES.get(cat, "其他")
                print("    [{}/{}] [{}] {} | tags: {}".format(
                    i + 1, len(filtered),
                    cat_name,
                    article.get("title", "?")[:40],
                    "|".join(article["tags"]),
                ))
                completed.append(article)
                time.sleep(1.5)

        browser.close()

    # Write CSV
    write_csv(completed, args.out)
    print("\nDone! {} articles for date {}".format(len(completed), target_date))


if __name__ == "__main__":
    main()
