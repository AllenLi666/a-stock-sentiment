#!/usr/bin/env python3
"""
Telegram Bot — send A-stock report to Telegram chat.

Usage:
    python3 src/telegram_bot.py <report.md>                         # Summary (all sections)
    python3 src/telegram_bot.py <report.md> --title "Title"         # Custom title
    python3 src/telegram_bot.py <report.md> --candidates            # Only 短线+中线 tables
    python3 src/telegram_bot.py <report.md> --full                  # Full report as file

Requires in .env:
    TELEGRAM_BOT_TOKEN=your_bot_token
    TELEGRAM_CHAT_ID=your_chat_id
    TELEGRAM_CHAT_ID=id1,id2,id3    # Multiple recipients
"""

import os
import sys
import json
import ssl
import urllib.request
import urllib.error

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CONTEXT = ssl.create_default_context()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config():
    """Load Telegram config from .env or environment.

    Returns:
        token (str): Bot token
        chat_ids (list[str]): List of chat IDs (single or comma-separated)
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID", "")

    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("TELEGRAM_CHAT_ID="):
                    chat_id_raw = line.split("=", 1)[1].strip().strip('"').strip("'")

    # Parse comma-separated chat IDs, strip whitespace, filter empties
    chat_ids = [cid.strip() for cid in chat_id_raw.split(",") if cid.strip()]
    return token, chat_ids


def read_markdown(path):
    """Read markdown report file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_report_text(md_text, title="📊 A股产业链早盘报告", candidates_only=False):
    """Extract key info from markdown report and format as Telegram message.

    Args:
        md_text: Raw markdown content
        title: Message title
        candidates_only: If True, only include 短线+中线 tables (sections 2+3)
    """
    lines = md_text.split("\n")

    # Extract sections
    short_lines = []
    mid_lines = []
    detail_lines = []
    in_short = False
    in_mid = False
    in_detail = False
    relay_lines = []

    for line in lines:
        if "短线推荐候选" in line:
            in_short = True
            in_mid = False
            in_detail = False
            continue
        elif "中线推荐候选" in line:
            in_short = False
            in_mid = True
            in_detail = False
            continue
        elif "逐股逻辑" in line:
            in_short = False
            in_mid = False
            in_detail = True
            continue
        elif "风险提示" in line:
            in_detail = False
            continue

        if in_short and line.strip().startswith("|") and "代码" not in line:
            short_lines.append(line.strip())
        if in_mid and line.strip().startswith("|") and "代码" not in line:
            mid_lines.append(line.strip())
        if in_detail and "舆论接力信号" in line:
            relay_lines.append(line.strip())

    # Build message
    msg = "<b>{}</b>\n\n".format(title)

    # Short-term (section 2)
    msg += "<b>🔥 短线候选（持有1-5天）</b>\n"
    for row in short_lines[:5]:
        parts = [p.strip() for p in row.split("|") if p.strip()]
        if len(parts) >= 5:
            msg += "  {}  <b>{}</b> {}（{}） — {}分\n".format(
                parts[0], parts[2], parts[1], parts[3], parts[4]
            )

    # Mid-term (section 3)
    msg += "\n<b>📈 中线候选（持有1-4周）</b>\n"
    for row in mid_lines[:5]:
        parts = [p.strip() for p in row.split("|") if p.strip()]
        if len(parts) >= 5:
            msg += "  {}  <b>{}</b> {}（{}） — {}分\n".format(
                parts[0], parts[2], parts[1], parts[3], parts[4]
            )

    if not candidates_only:
        # Relay signals
        if relay_lines:
            msg += "\n<b>📡 舆论接力信号</b>\n"
            for rl in relay_lines[:3]:
                msg += "  {}\n".format(rl.replace("|", "").strip())

        msg += "\n<i>⚠️ 入市有风险，投资需谨慎</i>"

    return msg


def send_telegram_message(token, chat_id, text):
    """Send text message via Telegram Bot API to a single chat."""
    url = "https://api.telegram.org/bot{}/sendMessage".format(token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                return True, "OK"
            else:
                return False, result.get("description", "Unknown error")
    except urllib.error.HTTPError as e:
        return False, "HTTP {}: {}".format(e.code, e.reason)
    except urllib.error.URLError as e:
        return False, "Network error: {}".format(e.reason)
    except Exception as e:
        return False, str(e)


def send_telegram_document(token, chat_id, file_path, caption=""):
    """Send a file as document via Telegram Bot API to a single chat."""
    url = "https://api.telegram.org/bot{}/sendDocument".format(token)

    boundary = "----FormBoundary7MA4YWxkTrZu0gW"
    file_name = os.path.basename(file_path)

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    # Build multipart form-data body manually (no external deps)
    body_parts = []
    body_parts.append("--" + boundary)
    body_parts.append('Content-Disposition: form-data; name="chat_id"')
    body_parts.append("")
    body_parts.append(str(chat_id))

    if caption:
        body_parts.append("--" + boundary)
        body_parts.append('Content-Disposition: form-data; name="caption"')
        body_parts.append("")
        body_parts.append(caption)

    body_parts.append("--" + boundary)
    body_parts.append(
        'Content-Disposition: form-data; name="document"; filename="{}"'.format(file_name)
    )
    body_parts.append("Content-Type: text/markdown")
    body_parts.append("")
    body_parts.append("")  # Separator before binary data

    # Encode header parts
    header_bytes = ("\r\n".join(body_parts) + "\r\n").encode("utf-8")
    footer_bytes = ("\r\n--" + boundary + "--\r\n").encode("utf-8")

    data = header_bytes + file_bytes + footer_bytes

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                return True, "OK"
            else:
                return False, result.get("description", "Unknown error")
    except urllib.error.HTTPError as e:
        return False, "HTTP {}: {}".format(e.code, e.reason)
    except urllib.error.URLError as e:
        return False, "Network error: {}".format(e.reason)
    except Exception as e:
        return False, str(e)


def broadcast_message(token, chat_ids, text):
    """Send text message to multiple chat IDs.

    Returns:
        success_count (int), failures (list of (chat_id, error))
    """
    success_count = 0
    failures = []
    for cid in chat_ids:
        ok, msg = send_telegram_message(token, cid, text)
        if ok:
            success_count += 1
            print("  ✅ Sent to chat_id={}".format(cid))
        else:
            failures.append((cid, msg))
            print("  ❌ Failed chat_id={}: {}".format(cid, msg))
    return success_count, failures


def broadcast_document(token, chat_ids, file_path, caption=""):
    """Send a file as document to multiple chat IDs.

    Returns:
        success_count (int), failures (list of (chat_id, error))
    """
    success_count = 0
    failures = []
    for cid in chat_ids:
        ok, msg = send_telegram_document(token, cid, file_path, caption)
        if ok:
            success_count += 1
            print("  ✅ Document sent to chat_id={}".format(cid))
        else:
            failures.append((cid, msg))
            print("  ❌ Failed chat_id={}: {}".format(cid, msg))
    return success_count, failures


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 src/telegram_bot.py <report.md>")
        print("   or: python3 src/telegram_bot.py <report.md> --title 'Custom Title'")
        print("   or: python3 src/telegram_bot.py <report.md> --candidates")
        print("   or: python3 src/telegram_bot.py <report.md> --full")
        sys.exit(1)

    report_path = sys.argv[1]
    title = "📊 A股产业链早盘报告"
    full_mode = "--full" in sys.argv
    candidates_mode = "--candidates" in sys.argv

    if "--title" in sys.argv:
        idx = sys.argv.index("--title")
        if idx + 1 < len(sys.argv):
            title = sys.argv[idx + 1]

    if not os.path.exists(report_path):
        print("Error: report file not found: {}".format(report_path))
        sys.exit(1)

    token, chat_ids = load_config()
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env")
        sys.exit(1)
    if not chat_ids:
        print("Error: TELEGRAM_CHAT_ID not found in .env")
        print("After chatting with your bot, visit:")
        print("  https://api.telegram.org/bot{}/getUpdates".format(token))
        print("Look for 'chat':{'id': <NUMBER>} and add TELEGRAM_CHAT_ID=<NUMBER> to .env")
        print("For multiple recipients, separate with commas:")
        print("  TELEGRAM_CHAT_ID=570734222,5366557242,7767662780")
        sys.exit(1)

    print("Broadcasting to {} recipient(s)...".format(len(chat_ids)))

    if full_mode:
        # Send full report as a document (.md file)
        success_count, failures = broadcast_document(
            token, chat_ids, report_path, caption=title
        )
    else:
        # Send summary as text message
        md_text = read_markdown(report_path)
        message = build_report_text(md_text, title, candidates_only=candidates_mode)
        # Telegram has 4096 char limit, truncate if needed
        if len(message) > 4000:
            message = message[:4000] + "\n\n<i>...报告过长，已截断</i>"
        success_count, failures = broadcast_message(token, chat_ids, message)

    if failures:
        print("⚠️  Sent to {}/{} recipients. Failures:".format(
            success_count, len(chat_ids)
        ))
        for cid, err in failures:
            print("    {}: {}".format(cid, err))
        if success_count == 0:
            sys.exit(1)
    else:
        print("✅ Report sent to all {} recipient(s) successfully!".format(success_count))


if __name__ == "__main__":
    main()
