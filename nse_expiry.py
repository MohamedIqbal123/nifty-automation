"""
nse_expiry.py - pick the correct NIFTY expiry and strikes, holiday-proof.

Idea: never trust a hand-calculated expiry date. Ask NSE which expiries exist
(records.expiryDates), choose the nearest valid one, and use a holiday-based
calculation only as a cross-check.
"""
import time
from datetime import date, datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import requests

BASE = "https://www.nseindia.com"
IST = ZoneInfo("Asia/Kolkata")
WEEKLY_EXPIRY_WEEKDAY = 1          # Monday=0 ... Tuesday=1 (Nifty weekly expiry)
MARKET_CLOSE = dtime(15, 30)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE}/option-chain",
}

# Backup list, used only if NSE's holiday API fails. Add dates as "DD-Mon-YYYY".
EXTRA_HOLIDAYS = {
    "20-Oct-2026",   # Dussehra (check against NSE's official list)
}


# ---------------------------------------------------------------- helpers
def parse_d(s: str) -> date:
    """'19-Oct-2026' or '19-OCT-2026' -> date(2026, 10, 19)"""
    return datetime.strptime(s.strip(), "%d-%b-%Y").date()


def now_ist() -> datetime:
    # GitHub runners use UTC. Always use India time for market dates.
    return datetime.now(IST)


def open_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get(BASE, timeout=20)                    # sets cookies
    s.get(f"{BASE}/option-chain", timeout=20)  # sets more cookies
    return s


def get_json(session, url, ok=lambda d: bool(d), retries=4):
    """GET with retries. Retries when NSE returns 200 but empty data."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=20)
            print(f"[{attempt}] {url} -> HTTP {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                if ok(data):
                    return data
                last = "empty data"
            else:
                last = f"HTTP {r.status_code}"
        except Exception as e:                  # network / JSON error
            last = repr(e)
        time.sleep(2 * attempt)
        session = open_session()               # fresh cookies for next try
    raise RuntimeError(f"NSE request failed after {retries} tries: {last}")


# ---------------------------------------------------------------- holidays
def get_holidays(session) -> set:
    """F&O trading holidays as a set of dates. Falls back to EXTRA_HOLIDAYS."""
    days = {parse_d(h) for h in EXTRA_HOLIDAYS}
    try:
        data = get_json(session, f"{BASE}/api/holiday-master?type=trading",
                        retries=2)
        for item in data.get("FO", []):
            days.add(parse_d(item["tradingDate"]))
    except Exception as e:
        print("Holiday API failed, using backup list only:", e)
    return days


def calc_expected_expiry(today: date, holidays: set) -> date:
    """Next weekly expiry weekday; if weekend/holiday, move to previous day."""
    d = today
    while d.weekday() != WEEKLY_EXPIRY_WEEKDAY:
        d += timedelta(days=1)
    while d.weekday() >= 5 or d in holidays:
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------- expiry
def pick_expiry(expiry_strings, now=None) -> date:
    """Nearest expiry from NSE's own list. If today is expiry day and the
    market has closed (after 3:30 PM IST), move to the next one."""
    now = now or now_ist()
    today, closed = now.date(), now.time() >= MARKET_CLOSE
    for d in sorted(parse_d(e) for e in expiry_strings):
        if d > today or (d == today and not closed):
            return d
    raise RuntimeError(f"No upcoming expiry in {list(expiry_strings)[:6]}")


# ---------------------------------------------------------------- strikes
def get_rows(chain: dict, expiry: date, step: int = 100):
    records = chain["records"]
    rows = [
        r for r in records["data"]
        if parse_d(r["expiryDate"]) == expiry and r["strikePrice"] % step == 0
    ]
    if not rows:
        raise RuntimeError(
            f"No {step}-point strikes for {expiry}. "
            f"NSE expiries: {records.get('expiryDates', [])[:6]}"
        )
    return rows


# ---------------------------------------------------------------- main
def get_nifty_rows(step: int = 100):
    """Returns (expiry_date, rows, underlying_value)."""
    session = open_session()
    chain = get_json(
        session,
        f"{BASE}/api/option-chain-indices?symbol=NIFTY",
        ok=lambda d: bool(d.get("records", {}).get("data")),
    )
    holidays = get_holidays(session)

    expiry = pick_expiry(chain["records"]["expiryDates"])
    expected = calc_expected_expiry(now_ist().date(), holidays)
    print("Today (IST):", now_ist().date())
    print("NSE expiry chosen:", expiry.strftime("%d-%b-%Y"))
    print("Holiday-calc expiry:", expected.strftime("%d-%b-%Y"))
    if expected != expiry:
        print("NOTE: they differ (e.g. monthly expiry or special change). "
              "Using NSE's list.")

    rows = get_rows(chain, expiry, step)
    print(f"{step}-point strikes found: {len(rows)}")
    return expiry, rows, chain["records"].get("underlyingValue")


if __name__ == "__main__":
    exp, rows, spot = get_nifty_rows()
    print("Spot:", spot, "| Expiry:", exp, "| Rows:", len(rows))
