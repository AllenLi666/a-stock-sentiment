import argparse
import csv
import os
import re
from collections import Counter, defaultdict

from entities import find_stocks, load_stock_aliases
from report import write_report, write_trade_report
from signals import build_trade_candidates, load_mention_history, load_prices, load_technical_indicators
from sentiment import score_text


STOPWORDS = {
    "今天", "讨论", "继续", "出现", "很多", "明显", "同时", "有人", "某只",
    "小票", "数据", "内容", "应该", "整体", "今日", "大家", "还是", "主要",
    "被提到", "关键词", "情绪", "热度", "关注",
}

KEYWORD_LEXICON = [
    "新能源", "新能源车", "储能", "出海", "订单", "半导体", "先进封装",
    "消费复苏", "估值分歧", "地产链", "债务", "销售数据", "智能驾驶",
    "价格战", "银行股", "防御属性", "息差", "分红", "光伏", "产能过剩",
    "AI", "教育", "办公", "语音模型", "复苏", "震荡", "反弹", "风险",
    "刷屏", "数据支撑", "成交量", "公告", "财报",
]


def read_tweets(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def extract_keywords(text):
    # Light-weight keyword extraction for MVP use. The lexicon avoids poor Chinese
    # character-window splits while keeping the implementation dependency-free.
    tokens = [word for word in KEYWORD_LEXICON if word in text]
    tokens.extend(re.findall(r"[A-Za-z$]{2,}[A-Za-z0-9$]*", text))
    cleaned = []
    for token in tokens:
        if token in STOPWORDS:
            continue
        if re.fullmatch(r"\d{4,}", token):
            continue
        cleaned.append(token)
    return cleaned


def analyze(tweets_path, aliases_path):
    stocks = load_stock_aliases(aliases_path)
    tweets = read_tweets(tweets_path)
    stock_stats = {}
    industry_stats = defaultdict(lambda: {"mentions": 0, "score_sum": 0})
    market_keywords = Counter()

    for tweet in tweets:
        text = tweet.get("text", "")
        sentiment = score_text(text)
        keywords = extract_keywords(text)
        market_keywords.update(keywords)
        matches = find_stocks(text, stocks)

        for match in matches:
            key = match["code"]
            if key not in stock_stats:
                stock_stats[key] = {
                    "code": match["code"],
                    "name": match["name"],
                    "industry": match["industry"],
                    "mentions": 0,
                    "score_sum": 0,
                    "keyword_counter": Counter(),
                    "authors": set(),
                }
            stock_stats[key]["mentions"] += 1
            stock_stats[key]["score_sum"] += sentiment["score"]
            stock_stats[key]["keyword_counter"].update(keywords)
            author = tweet.get("author", "").strip()
            if author:
                stock_stats[key]["authors"].add(author)

            industry_stats[match["industry"]]["mentions"] += 1
            industry_stats[match["industry"]]["score_sum"] += sentiment["score"]

    for item in stock_stats.values():
        item["keywords"] = item["keyword_counter"].most_common(10)
        item["unique_authors"] = len(item["authors"])
        del item["authors"]

    return stock_stats, industry_stats, market_keywords.most_common(30), len(tweets)


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="A股社交媒体舆情 MVP 分析器")
    parser.add_argument("--tweets", default=os.path.join(base_dir, "data", "sample_tweets.csv"))
    parser.add_argument("--aliases", default=os.path.join(base_dir, "data", "stock_aliases.csv"))
    parser.add_argument("--prices", default=os.path.join(base_dir, "data", "market_prices.csv"))
    parser.add_argument("--history", default=os.path.join(base_dir, "data", "historical_mentions.csv"))
    parser.add_argument("--technicals", default=os.path.join(base_dir, "data", "technical_indicators.csv"))
    parser.add_argument("--out", default=os.path.join(base_dir, "reports", "daily_report.md"))
    parser.add_argument("--trade-out", default=os.path.join(base_dir, "reports", "trade_candidates.md"))
    parser.add_argument("--max-candidates", type=int, default=5)
    args = parser.parse_args()

    stats, industry_stats, keyword_counts, tweet_count = analyze(args.tweets, args.aliases)
    prices = load_prices(args.prices)
    history = load_mention_history(args.history)
    technicals = load_technical_indicators(args.technicals)
    trade_candidates = build_trade_candidates(stats, prices, history, technicals, args.max_candidates)
    write_report(args.out, stats, industry_stats, keyword_counts, tweet_count, trade_candidates)
    write_trade_report(args.trade_out, trade_candidates)
    print("Report written to {}".format(args.out))
    print("Trade report written to {}".format(args.trade_out))


if __name__ == "__main__":
    main()
