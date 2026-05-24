import csv


def load_stock_aliases(path):
    stocks = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            aliases = [item.strip() for item in row["aliases"].split("|") if item.strip()]
            stocks.append({
                "code": row["code"].strip(),
                "name": row["name"].strip(),
                "aliases": aliases,
                "industry": row["industry"].strip(),
            })
    return stocks


def find_stocks(text, stocks):
    matches = []
    lower_text = text.lower()
    for stock in stocks:
        hit_aliases = []
        for alias in stock["aliases"]:
            if alias.lower() in lower_text:
                hit_aliases.append(alias)
        if hit_aliases:
            matches.append({
                "code": stock["code"],
                "name": stock["name"],
                "industry": stock["industry"],
                "aliases": hit_aliases,
            })
    return matches
