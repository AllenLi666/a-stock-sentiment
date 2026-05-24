import argparse
import csv
import json
import os
import ssl
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen


X_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


def load_env_token(base_dir):
    env_path = os.path.join(base_dir, ".env")
    if not os.path.exists(env_path):
        return ""
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "X_BEARER_TOKEN":
                return value.strip().strip("'").strip('"')
    return ""


def fetch_recent(query, bearer_token, max_results, insecure=False):
    params = {
        "query": query,
        "max_results": str(max_results),
        "tweet.fields": "created_at,author_id,lang",
    }
    req = Request("{}?{}".format(X_SEARCH_URL, urlencode(params)), headers={
        "Authorization": "Bearer {}".format(bearer_token),
        "User-Agent": "a-stock-sentiment-mvp",
    })
    context = ssl._create_unverified_context() if insecure else None
    try:
        with urlopen(req, timeout=30, context=context) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit("X API request failed: HTTP {} {}\n{}".format(exc.code, exc.reason, body))


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Fetch recent X posts with X API v2")
    parser.add_argument("--query", required=True)
    parser.add_argument("--out", default=os.path.join(base_dir, "data", "real_tweets.csv"))
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--bearer-token", default=os.environ.get("X_BEARER_TOKEN", "") or load_env_token(base_dir))
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for old local Python CA stores")
    parser.add_argument("--authors", default="", help="Comma-separated author_ids to keep (post-fetch filter, e.g. '1518874924718653441,20659155')")
    parser.add_argument("--author-names", default="", help="Comma-separated @usernames to filter (adds from:username to query)")
    args = parser.parse_args()

    if not args.bearer_token:
        raise SystemExit("Missing X bearer token. Set X_BEARER_TOKEN or pass --bearer-token.")

    # If author-names provided, add from: filter to query
    query = args.query
    if args.author_names:
        names = [n.strip().lstrip("@") for n in args.author_names.split(",") if n.strip()]
        if names:
            from_clause = "(" + " OR ".join("from:{}".format(n) for n in names) + ")"
            query = "{} {}".format(from_clause, query)

    payload = fetch_recent(query, args.bearer_token, args.max_results, args.insecure)
    rows = []
    for item in payload.get("data") or []:
        created_at = item.get("created_at", "")
        author_id = item.get("author_id", "")
        rows.append({
            "date": created_at[:10],
            "tweet_id": item.get("id", ""),
            "author": author_id,
            "text": item.get("text", "").replace("\n", " ").strip(),
        })

    # Post-fetch filter by author_ids if --authors is set
    if args.authors:
        whitelist = set(a.strip() for a in args.authors.split(",") if a.strip())
        before = len(rows)
        rows = [r for r in rows if r["author"] in whitelist]
        print("Filtered by authors: {} -> {} rows (kept {} of {} author_ids)".format(before, len(rows), len(whitelist), before))

    directory = os.path.dirname(args.out)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "tweet_id", "author", "text"])
        writer.writeheader()
        writer.writerows(rows)
    print("Wrote {} rows to {}".format(len(rows), args.out))


if __name__ == "__main__":
    main()
