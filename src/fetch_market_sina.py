#!/usr/bin/env python3
"""
Fetch A-share daily bars from Sina Finance API (works from outside China).
Output CSV: date,code,name,open,high,low,close,volume

Usage:
    PYTHONPATH=src python3 src/fetch_market_sina.py \
        --aliases data/stock_aliases.csv \
        --start 2026-04-01 \
        --end 2026-05-22 \
        --out data/real_market_full.csv \
        --sleep 0.2
"""

import argparse
import csv
import json
import os
import time
from urllib.request import Request, urlopen

from entities import load_stock_aliases


SINA_KLINE_URL = (
    "http://money.finance.sina.com.cn/quotes_service/"
    "api/json_v2.php/CN_MarketData.getKLineData"
)


def sina_symbol(code):
    """Convert A-share code to Sina symbol prefix."""
    if code.startswith("6"):
        return "sh{}".format(code)
    elif code.startswith("0") or code.startswith("3"):
        return "sz{}".format(code)
    elif code.startswith("8"):
        return "bj{}".format(code)
    return code


def parse_sina_date(day_str):
    """Sina returns '2026-04-01' or '2026-04-01 00:00:00'."""
    return day_str.split(" ")[0]


def fetch_daily_klines(code, start_date, end_date, datalen=60, retries=3):
    """Fetch daily kline data from Sina Finance for the given code.

    Sina's API returns up to datalen days. We'll fetch a generous window
    and filter client-side.
    """
    symbol = sina_symbol(code)
    params = "symbol={}&scale=240&ma=no&datalen={}".format(symbol, datalen)
    url = "{}?{}".format(SINA_KLINE_URL, params)

    last_error = None
    for attempt in range(retries):
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "http://finance.sina.com.cn/",
        })
        try:
            with urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("gbk")  # Sina uses GBK encoding
            data = json.loads(raw)
            rows = []
            for item in data:
                d = parse_sina_date(item.get("day", ""))
                if d < start_date or d > end_date:
                    continue
                row = {
                    "date": d,
                    "open": float(item.get("open", 0)),
                    "high": float(item.get("high", 0)),
                    "low": float(item.get("low", 0)),
                    "close": float(item.get("close", 0)),
                    "volume": int(float(item.get("volume", 0))),
                }
                rows.append(row)
            # Sort by date ascending
            rows.sort(key=lambda r: r["date"])
            return rows
        except Exception as exc:
            last_error = exc
            time.sleep(1.0 + attempt * 0.5)

    print("  WARN: Failed to fetch {} after {} retries: {}".format(
        code, retries, last_error))
    return []


def write_rows(path, rows):
    """Write rows to CSV with header."""
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date", "code", "name", "open", "high", "low", "close", "volume"
        ])
        writer.writeheader()
        writer.writerows(rows)


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(
        description="Fetch A-share daily bars from Sina Finance")
    parser.add_argument(
        "--aliases",
        default=os.path.join(base_dir, "data", "stock_aliases.csv"))
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--out",
        default=os.path.join(base_dir, "data", "real_market.csv"))
    parser.add_argument("--sleep", type=float, default=0.25,
                        help="Seconds between requests (default: 0.25)")
    parser.add_argument("--datalen", type=int, default=90,
                        help="Number of trading days to request (default: 90)")
    args = parser.parse_args()

    all_rows = []
    stocks = load_stock_aliases(args.aliases)
    total = len(stocks)

    for idx, stock in enumerate(stocks, 1):
        code = stock["code"]
        name = stock["name"]
        print("[{}/{}] Fetching {} {}...".format(idx, total, code, name))

        klines = fetch_daily_klines(
            code, args.start, args.end,
            datalen=args.datalen, retries=3)

        for k in klines:
            k["code"] = code
            k["name"] = name
            all_rows.append(k)

        print("  -> {} rows".format(len(klines)))
        time.sleep(args.sleep)

    # Sort by code then date
    all_rows.sort(key=lambda item: (item["code"], item["date"]))
    write_rows(args.out, all_rows)
    print("\nDone. Wrote {} rows for {} stocks to {}".format(
        len(all_rows), total, args.out))


if __name__ == "__main__":
    main()
