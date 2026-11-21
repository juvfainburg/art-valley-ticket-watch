#!/usr/bin/env python3
"""Art Valley New Years 26-27: resale price watcher.

Checks Triniq (the festival's own ticket seller and its official resale
marketplace) and alerts (phone push via ntfy, plus a Mac notification when run
on a Mac) when an entry ticket is listed below the target price. Each listing
alerts once, not every 10 minutes.

  python3 art_valley_watch.py            one check (GitHub every 5 min, the Mac every 2 min)
  python3 art_valley_watch.py --show     print every listing now, no alert
  python3 art_valley_watch.py --test     fire a test alert (phone + Mac)
  python3 art_valley_watch.py --dry N    list what WOULD alert at limit N

Price compared = the ticket price Triniq shows, before its booking fee
(about A$10-11), the wider net. Every alert shows the total with fee.
Under URGENT_BELOW the phone alert is marked urgent. Set INCLUDE_FEES = True
to compare the total instead.
Phone channel: env NTFY_TOPIC, else the file ntfy_topic next to this script.
Car and vehicle passes are add-ons, not entry, so they never alert.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

TARGET = 190.0            # alert when an entry ticket is BELOW this (A$)
URGENT_BELOW = 180.0      # below this the phone alert is marked urgent
INCLUDE_FEES = False      # True = compare price + booking fee
EVENT = "artvalley26"
RESALE_URL = "https://triniq.com/resale/event/" + EVENT
SALE_URL = "https://triniq.com/" + EVENT + "/tickets"
ADDON_WORDS = ("pass",)   # "Car Pass", "Large Vehicle Pass"

HERE = Path(os.environ.get("WATCH_DIR") or Path(__file__).resolve().parent)
HERE.mkdir(parents=True, exist_ok=True)
ON_MAC = sys.platform == "darwin"
STATE = HERE / "state.json"
LOG = HERE / "watch.log"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130 Safari/537.36")

ROW = re.compile(
    r'\{"key":"(?P<key>[^"]+)","href":"(?P<href>/resale/[^"]+)",'
    r'"title":"(?P<title>[^"]*)","description":(?:null|"[^"]*"),'
    r'"price":\{"price":"A\$(?P<price>[\d,.]+)[^"]*","fee":"(?P<fee>[^"]*)"')
LISTED = re.compile(r'(\d+) resale tickets? listed')
TIER = re.compile(
    r'"title":"(?P<title>[^"]*)","description":"[^"]*","price":(?P<price>[\d.]+),'
    r'"bookingFee":(?P<fee>[\d.]+).{0,400}?"isSoldOut":(?P<sold>true|false),'
    r'"isHidden":(?P<hidden>true|false)')


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line)
    with LOG.open("a") as f:
        f.write(line + "\n")
    # keep the log short
    lines = LOG.read_text().splitlines()
    if len(lines) > 3000:
        LOG.write_text("\n".join(lines[-2000:]) + "\n")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace").replace('\\"', '"')


def money(s):
    m = re.search(r"[\d,.]+", s or "")
    return float(m.group(0).replace(",", "")) if m else 0.0


def is_addon(title):
    return any(w in title.lower() for w in ADDON_WORDS)


def resale_listings():
    page = fetch(RESALE_URL)
    rows, seen = [], set()
    for m in ROW.finditer(page):
        if m["key"] in seen:
            continue
        seen.add(m["key"])
        price, fee = money(m["price"]), money(m["fee"])
        rows.append({"key": m["key"], "title": m["title"], "price": price,
                     "fee": fee, "url": "https://triniq.com" + m["href"],
                     "source": "resale"})
    listed = LISTED.search(page)
    return rows, (int(listed.group(1)) if listed else None)


def general_sale():
    """Tiers still on sale from the festival itself (resale cap = these)."""
    page = fetch(SALE_URL)
    out, seen = [], set()
    for m in TIER.finditer(page):
        if m["sold"] == "true" or m["hidden"] == "true" or m["title"] in seen:
            continue
        seen.add(m["title"])
        out.append({"key": "sale:" + m["title"] + ":" + m["price"],
                    "title": m["title"], "price": float(m["price"]),
                    "fee": float(m["fee"]), "url": SALE_URL,
                    "source": "general sale"})
    return out


def compare_price(row):
    return row["price"] + (row["fee"] if INCLUDE_FEES else 0)


def ntfy_topic():
    t = os.environ.get("NTFY_TOPIC", "").strip()
    if not t:
        f = Path(__file__).resolve().parent / "ntfy_topic"
        t = f.read_text().strip() if f.exists() else ""
    return t


def push(title, body, url=None, priority="high"):
    topic = ntfy_topic()
    if not topic or os.environ.get("NO_PUSH"):
        return False
    headers = {"Title": title.encode("utf-8"), "Priority": priority, "Tags": "ticket"}
    if url:
        headers["Click"] = url
    req = urllib.request.Request("https://ntfy.sh/" + topic, data=body.encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:
        log("PUSH FAILED: %s" % e)
        return False


def notify(title, body, url=None, priority="high"):
    push(title, body, url, priority)
    if not ON_MAC:
        return

    def q(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')
    script = 'display notification "%s" with title "%s" sound name "Glass"' % (
        q(body), q(title))
    subprocess.run(["osascript", "-e", script], check=False)
    if url:
        # open the listing so it can be bought before someone else gets it
        subprocess.run(["open", url], check=False)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"alerted": [], "fails": 0, "broken_alerted": False}


def save_state(st):
    STATE.write_text(json.dumps(st, indent=1))


def main():
    args = sys.argv[1:]
    if "--test" in args:
        ok = push("Art Valley ticket watch", "Test alert: phone alerts work.",
                  RESALE_URL, "default")
        if ON_MAC:
            subprocess.run(["osascript", "-e", 'display notification "Test alert: '
                            'Mac alerts work." with title "Art Valley ticket watch"'])
        print("phone push:", "sent" if ok else "NOT sent (no topic or network)")
        return

    st = load_state()
    try:
        rows, listed = resale_listings()
        rows += general_sale()
    except Exception as e:  # network down, site changed, etc.
        st["fails"] = st.get("fails", 0) + 1
        log("CHECK FAILED (%d in a row): %s" % (st["fails"], e))
        if st["fails"] == 6 and not st.get("broken_alerted"):
            notify("Art Valley ticket watch", "Checks failing for 1 hour: " + str(e)[:80])
            st["broken_alerted"] = True
        save_state(st)
        return

    resale = [r for r in rows if r["source"] == "resale"]
    # page says N listed but we read none: Triniq changed its page layout
    broken = bool(listed) and not resale
    st["fails"] = 0
    if broken and not st.get("broken_alerted"):
        notify("Art Valley ticket watch",
               "Triniq page changed, the watcher cannot read prices. Fix needed.",
               RESALE_URL)
        st["broken_alerted"] = True
    elif not broken:
        st["broken_alerted"] = False

    entry = sorted((r for r in rows if not is_addon(r["title"])), key=compare_price)
    cheapest = entry[0] if entry else None
    summary = ("cheapest entry A$%.0f (+A$%.0f fee, %s, %s)" % (
        cheapest["price"], cheapest["fee"], cheapest["title"], cheapest["source"])
        if cheapest else "no entry tickets listed")
    log("%d resale listed, read %d | %s" % (listed or 0, len(resale), summary))

    if "--show" in args:
        for r in sorted(rows, key=compare_price):
            print("  A$%7.2f + A$%5.2f  %-12s %-40s %s" % (
                r["price"], r["fee"], r["source"], r["title"], r["url"]))
        save_state(st)
        return

    if "--dry" in args:  # --dry N: list what WOULD alert at target N, change nothing
        t = float(args[args.index("--dry") + 1])
        for r in entry:
            if compare_price(r) < t:
                print("  would alert: %s A$%.0f %s" % (r["title"], r["price"], r["url"]))
        return

    hits = [r for r in entry if compare_price(r) < TARGET
            and r["key"] not in st["alerted"]]
    for r in hits:
        urgent = compare_price(r) < URGENT_BELOW
        msg = "%s: A$%.0f + A$%.0f fee = A$%.0f total (%s). Tap to buy." % (
            r["title"], r["price"], r["fee"], r["price"] + r["fee"], r["source"])
        title = "Art Valley ticket A$%.0f (under A$%.0f)" % (
            r["price"], URGENT_BELOW if urgent else TARGET)
        log("ALERT " + msg + " " + r["url"])
        notify(title, msg, r["url"], "urgent" if urgent else "high")
        st["alerted"].append(r["key"])
    save_state(st)


if __name__ == "__main__":
    main()
