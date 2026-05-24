"""
行业关键词提取 + 产业链股票匹配

Reads articles (from 投中网 or any source), extracts industry tags,
and maps them to A-share stocks using the supply chain knowledge base.

Usage:
    from industry_extract import extract_industries, match_supply_chain_stocks

"""

import csv
import os
import re


def load_supply_chain(path=None):
    """Load supply chain knowledge base from CSV."""
    if path is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base_dir, "data", "supply_chain.csv")

    industries = {}  # industry -> list of stocks
    stock_map = {}   # code -> stock info
    industry_keywords = {}  # industry -> list of keywords

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            industry = row["industry"].strip()
            code = row["code"].strip()
            stock = {
                "code": code,
                "name": row["name"].strip(),
                "industry": industry,
                "role": row["role"].strip(),
                "aliases": [a.strip() for a in row["aliases"].split("|") if a.strip()],
                "industry_group": row["industry_group"].strip(),
            }
            industries.setdefault(industry, []).append(stock)
            stock_map[code] = stock

            # Parse keywords
            kw_str = row.get("keywords", "").strip()
            if kw_str:
                keywords = [k.strip() for k in kw_str.split("|") if k.strip()]
                industry_keywords.setdefault(industry, []).extend(keywords)

    return industries, stock_map, industry_keywords


def extract_industries_from_text(text, industry_keywords):
    """Extract industry tags from article text using keyword matching."""
    if not text:
        return []

    lower_text = text.lower()
    matched = []

    for industry, keywords in industry_keywords.items():
        for kw in keywords:
            if kw.lower() in lower_text:
                matched.append(industry)
                break  # One match per industry is enough

    return matched


def match_supply_chain_stocks(industries, supply_chain):
    """Map a list of industry tags to their corresponding supply chain stocks."""
    matched_stocks = []
    seen_codes = set()

    for industry in industries:
        stocks = supply_chain.get(industry, [])
        for stock in stocks:
            if stock["code"] not in seen_codes:
                seen_codes.add(stock["code"])
                matched_stocks.append({
                    **stock,
                    "matched_industry": industry,
                })

    return matched_stocks


def load_articles(path):
    """Load articles CSV."""
    articles = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            articles.append(row)
    return articles


def process_articles(articles_path, supply_chain_path=None):
    """Full pipeline: load articles -> extract industries -> match stocks."""
    industries_db, stock_map, industry_keywords = load_supply_chain(supply_chain_path)
    articles = load_articles(articles_path)

    result = {
        "article_count": len(articles),
        "industries_found": {},  # industry -> count
        "matched_stocks": {},    # code -> stock info
        "articles_by_industry": {},  # industry -> list of article titles
        "stocks_by_industry": {},    # industry -> list of stocks
    }

    for article in articles:
        text = "{} {} {}".format(
            article.get("title", ""),
            article.get("summary", ""),
            article.get("content", "")
        )
        tags_str = article.get("industry_tags", "")
        # Also parse tags from CSV if available
        existing_tags = [t.strip() for t in tags_str.split("|") if t.strip()]

        # Combine existing tags with fresh extraction
        extracted = extract_industries_from_text(text, industry_keywords)
        all_tags = list(set(existing_tags + extracted))

        for tag in all_tags:
            result["industries_found"][tag] = result["industries_found"].get(tag, 0) + 1
            result["articles_by_industry"].setdefault(tag, []).append(article.get("title", ""))

            # Match stocks for this industry
            stocks = industries_db.get(tag, [])
            if tag not in result["stocks_by_industry"]:
                result["stocks_by_industry"][tag] = []
            for stock in stocks:
                if stock["code"] not in result["matched_stocks"]:
                    enriched = {**stock, "matched_industry": tag}
                    result["matched_stocks"][stock["code"]] = enriched
                    result["stocks_by_industry"][tag].append(enriched)

    return result


if __name__ == "__main__":
    # Quick test
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    articles_path = os.path.join(base_dir, "data", "chinaventure_articles.csv")
    if os.path.exists(articles_path):
        result = process_articles(articles_path)
        print("Industries found: {}".format(list(result["industries_found"].keys())))
        print("Matched stocks: {}".format([s["name"] for s in result["matched_stocks"].values()]))
    else:
        print("No articles file found at {}. Run fetch_chinaventure.py first.".format(articles_path))
