import argparse
import csv
import os


DATE_FIELDS = ["date", "created_at", "time", "timestamp"]
ID_FIELDS = ["tweet_id", "id", "id_str"]
AUTHOR_FIELDS = ["author", "username", "screen_name", "user"]
TEXT_FIELDS = ["text", "full_text", "tweet", "content"]


def first_value(row, candidates, default=""):
    lower_map = {key.lower(): value for key, value in row.items()}
    for name in candidates:
        if name in lower_map and lower_map[name]:
            return lower_map[name]
    return default


def normalize_date(value):
    value = (value or "").strip()
    if not value:
        return ""
    if "T" in value:
        return value.split("T", 1)[0]
    if " " in value:
        return value.split(" ", 1)[0]
    return value[:10]


def normalize(input_path, output_path):
    rows = []
    with open(input_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for index, row in enumerate(reader, 1):
            text = first_value(row, TEXT_FIELDS).strip()
            if not text:
                continue
            tweet_id = first_value(row, ID_FIELDS, "row{}".format(index)).strip()
            author = first_value(row, AUTHOR_FIELDS, "unknown").strip()
            date = normalize_date(first_value(row, DATE_FIELDS))
            if not date:
                continue
            rows.append({
                "date": date,
                "tweet_id": tweet_id,
                "author": author,
                "text": text.replace("\n", " ").strip(),
            })

    directory = os.path.dirname(output_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "tweet_id", "author", "text"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Normalize exported X/Twitter CSV for backtest")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", default=os.path.join(base_dir, "data", "real_tweets.csv"))
    args = parser.parse_args()
    count = normalize(args.input, args.out)
    print("Wrote {} rows to {}".format(count, args.out))


if __name__ == "__main__":
    main()
