import argparse
import csv
import json
import os
import ssl
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from entities import load_stock_aliases


EASTMONEY_KLINE_URLS = [
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "http://push2his.eastmoney.com/api/qt/stock/kline/get",
]


def secid_for_code(code):
    if code.startswith("6"):
        return "1.{}".format(code)
    return "0.{}".format(code)


def normalize_date(value):
    return value.replace("-", "")


def fetch_klines(code, start_date, end_date, insecure=False, retries=3):
    params = {
        "secid": secid_for_code(code),
        "klt": "101",
        "fqt": "1",
        "beg": normalize_date(start_date),
        "end": normalize_date(end_date),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    context = ssl._create_unverified_context() if insecure else None
    last_error = None
    for base_url in EASTMONEY_KLINE_URLS:
        url = "{}?{}".format(base_url, urlencode(params))
        for attempt in range(retries):
            req = Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://quote.eastmoney.com/",
                "Connection": "close",
            })
            try:
                with urlopen(req, timeout=30, context=context) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                data = payload.get("data") or {}
                return data.get("klines") or []
            except Exception as exc:
                last_error = exc
                time.sleep(1.0 + attempt)
    raise last_error


def parse_kline(code, name, line):
    parts = line.split(",")
    return {
        "date": parts[0],
        "code": code,
        "name": name,
        "open": parts[1],
        "high": parts[3],
        "low": parts[4],
        "close": parts[2],
        "volume": parts[5],
    }


def write_rows(path, rows):
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
    parser = argparse.ArgumentParser(description="Fetch A-share daily bars from Eastmoney")
    parser.add_argument("--aliases", default=os.path.join(base_dir, "data", "stock_aliases.csv"))
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", default=os.path.join(base_dir, "data", "real_market.csv"))
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for old local Python CA stores")
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    rows = []
    for stock in load_stock_aliases(args.aliases):
        code = stock["code"]
        name = stock["name"]
        print("Fetching {} {}".format(code, name))
        for line in fetch_klines(code, args.start, args.end, args.insecure, args.retries):
            rows.append(parse_kline(code, name, line))
        time.sleep(args.sleep)

    rows.sort(key=lambda item: (item["code"], item["date"]))
    write_rows(args.out, rows)
    print("Wrote {} rows to {}".format(len(rows), args.out))


if __name__ == "__main__":
    main()
