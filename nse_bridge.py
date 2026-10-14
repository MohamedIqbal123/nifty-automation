import csv
import json
import time
import requests
import hashlib
from datetime import datetime, timedelta, timezone


BASE = "https://www.nseindia.com"


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/",
}


# India time (GitHub servers run on UTC, so we convert)
IST = timezone(timedelta(hours=5, minutes=30))

# Nifty weekly expiry day: Monday=0, Tuesday=1
EXPIRY_WEEKDAY = 1

# Backup holiday list, used only if NSE's holiday list cannot be loaded.
# Format: "DD-Mon-YYYY". Add new holidays here if you ever need to.
EXTRA_HOLIDAYS = [
    "20-Oct-2026",
]


session = requests.Session()
session.headers.update(HEADERS)


def nse_get(url, params=None, retries=1):

    last_error = None

    for attempt in range(1, retries + 1):

        try:

            r = session.get(
                url,
                params=params,
                timeout=30
            )

            r.raise_for_status()

            return r.json()

        except Exception as e:

            last_error = e

            if attempt < retries:
                time.sleep(2 * attempt)

    raise last_error


def parse_d(text):
    return datetime.strptime(text.strip(), "%d-%b-%Y").date()


def num(row, key, divisor=1):
    return float(row.get(key, 0) or 0) / divisor


# --------------------------------------------------
# 1. Establish NSE session
# --------------------------------------------------

print("Opening NSE session...")


session.get(
    BASE + "/report-detail/fo_eq_security",
    timeout=30
)


print("NSE session established.")


# --------------------------------------------------
# 2. Find the correct expiry (holiday-proof)
# --------------------------------------------------

today = datetime.now(IST).date()

print("Today (India):", today.strftime("%d-%b-%Y"))


def get_holidays():

    days = set()

    for text in EXTRA_HOLIDAYS:
        days.add(parse_d(text))

    try:

        data = nse_get(
            BASE + "/api/holiday-master",
            {"type": "trading"},
            retries=2
        )

        for item in data.get("FO", []):

            try:
                days.add(parse_d(item["tradingDate"]))
            except Exception:
                pass

    except Exception as e:

        print("Holiday list error (using backup list):", e)

    return days


def calc_expected_expiry(start, holidays):
    """Next Tuesday on/after start. If that day is a weekend or
    holiday, move back to the previous trading day."""

    d = start

    for _ in range(10):

        while d.weekday() != EXPIRY_WEEKDAY:
            d += timedelta(days=1)

        e = d

        while e.weekday() >= 5 or e in holidays:
            e -= timedelta(days=1)

        if e >= start:
            return e

        d += timedelta(days=1)

    return start


def get_nse_expiries():
    """Expiry dates that NSE itself says exist."""

    try:

        info = nse_get(
            BASE + "/api/option-chain-contract-info",
            {"symbol": "NIFTY"},
            retries=3
        )

        return sorted(
            parse_d(x) for x in info.get("expiryDates", [])
        )

    except Exception as e:

        print("NSE expiry list error:", e)

        return []


holidays = get_holidays()

calc_first = calc_expected_expiry(today, holidays)

calc_second = calc_expected_expiry(
    calc_first + timedelta(days=1),
    holidays
)

candidates = []

for d in get_nse_expiries():
    if d >= today and d not in candidates:
        candidates.append(d)

for d in [calc_first, calc_second]:
    if d not in candidates:
        candidates.append(d)

candidates = candidates[:4]

print(
    "Expiry candidates:",
    ", ".join(d.strftime("%d-%b-%Y") for d in candidates)
)


# --------------------------------------------------
# 3. Get NIFTY option-chain strikes
# --------------------------------------------------

print(
    "Getting NIFTY option-chain..."
)


strikes = []

expiry = None

expiry_text = None


for candidate in candidates:

    expiry = candidate

    expiry_text = expiry.strftime("%d-%b-%Y").upper()

    print("Trying expiry:", expiry_text)

    try:

        option_chain = nse_get(
            BASE + "/api/option-chain-v3",
            {
                "type": "equity",
                "symbol": "NIFTY",
                "expiry": expiry_text
            },
            retries=3
        )

    except Exception as e:

        print("Option-chain error:", e)

        continue

    records = option_chain.get("records", {})

    data = records.get("data", [])

    found = set()

    for item in data:

        strike = item.get("strikePrice")

        if strike is not None:

            strike = float(strike)

            if strike % 100 == 0:

                found.add(strike)

    print(
        "100-point strikes found:",
        len(found)
    )

    if found:

        strikes = sorted(found)

        break


if not strikes:

    raise Exception(
        "No NIFTY 100-point strikes found for: "
        + ", ".join(d.strftime("%d-%b-%Y") for d in candidates)
    )


print("Using expiry:", expiry_text)


# --------------------------------------------------
# 4. Find latest trading date
# --------------------------------------------------

def historical_rows(
    date_obj,
    option_type,
    strike
):

    date_text = date_obj.strftime(
        "%d-%m-%Y"
    )


    params = {
        "from": date_text,
        "to": date_text,
        "instrumentType": "OPTIDX",
        "symbol": "NIFTY",
        "year": str(date_obj.year),
        "expiryDate": expiry_text,
        "optionType": option_type,
        "strikePrice": str(int(strike)),
    }


    result = nse_get(
        BASE + "/api/historicalOR/foCPV",
        params
    )


    return result.get(
        "data",
        []
    )


print(
    "Finding latest trading date..."
)


# Strikes from the middle of the real list (near the current price),
# plus your original four as backup.
mid = len(strikes) // 2

test_strikes = [
    int(s) for s in strikes[max(0, mid - 2): mid + 2]
] + [24000, 23900, 23800, 24100]


latest_date = None


for back in range(0, 8):

    test_date = today - timedelta(
        days=back
    )


    for strike in test_strikes:

        try:

            rows = historical_rows(
                test_date,
                "CE",
                strike
            )


            if rows:

                latest_date = test_date

                break


        except Exception:

            pass


    if latest_date:

        break


if latest_date is None:

    raise Exception(
        "Could not find latest NIFTY trading date."
    )


print(
    "Trading date:",
    latest_date.strftime(
        "%d-%m-%Y"
    )
)


# --------------------------------------------------
# 5. Download CE + PE historical data
# --------------------------------------------------

output_rows = []


for strike in strikes:

    for option_type in [
        "CE",
        "PE"
    ]:

        print(
            "Downloading",
            option_type,
            int(strike)
        )


        try:

            rows = historical_rows(
                latest_date,
                option_type,
                strike
            )


            if not rows:

                continue


            row = rows[0]


            output_rows.append([

                latest_date.strftime("%d-%b-%Y"),

                expiry.strftime("%d-%b-%Y"),

                option_type,

                float(strike),

                num(row, "FH_OPENING_PRICE"),

                num(row, "FH_TRADE_HIGH_PRICE"),

                num(row, "FH_TRADE_LOW_PRICE"),

                num(row, "FH_CLOSING_PRICE"),

                num(row, "FH_LAST_TRADED_PRICE"),

                num(row, "FH_SETTLE_PRICE"),

                num(row, "FH_TOT_TRADED_QTY"),

                num(row, "FH_TOT_TRADED_VAL", 100000),

                num(row, "CALCULATED_PREMIUM_VAL", 100000),

                num(row, "FH_OPEN_INT"),

                num(row, "FH_CHANGE_IN_OI"),

            ])


        except Exception as e:

            print(
                "Skipped",
                option_type,
                int(strike),
                ":",
                e
            )


# --------------------------------------------------
# 6. Write Raw Data CSV
# --------------------------------------------------

headers = [

    "Date",
    "Expiry Date",
    "Option Type",
    "Strike Price",
    "Open Price",
    "High Price",
    "Low Price",
    "Close Price",
    "Last Price",
    "Settlement Price",
    "Volume",
    "Value (₹ Lakhs)",
    "Premium Value (₹ Lakhs)",
    "Open Interest",
    "Change in OI"

]


with open(
    "nifty_latest.csv",
    "w",
    newline="",
    encoding="utf-8"
) as f:

    writer = csv.writer(f)

    writer.writerow(
        headers
    )

    for row in output_rows:

        writer.writerow(
            row
        )


print(
    "Rows written:",
    len(output_rows)
)


print(
    "Created: nifty_latest.csv"
)


# --------------------------------------------------
# 7. Pre-market NIFTY value (via headless browser)
# --------------------------------------------------

premarket = {
    "value": None,
    "status": "outside_preopen_window"
}

now = datetime.now()

utc_minutes = (
    now.hour * 60
    + now.minute
)

if 205 <= utc_minutes <= 235:

    print(
        "Pre-market window detected."
    )

    try:

        from playwright.sync_api import sync_playwright
        import re

        nifty_value = None

        with sync_playwright() as p:

            browser = p.chromium.launch(headless=True, args=["--disable-http2"])

            page = browser.new_page()

            page.goto(
                "https://www.nseindia.com/market-data/pre-open-market-cm-and-emerge-market",
                timeout=60000
            )

            page.wait_for_timeout(6000)

            body_text = page.inner_text("body")

            browser.close()

        match = re.search(
            r"Nifty\s*50\s*[\r\n]*\s*([\d,]+\.\d+)",
            body_text
        )

        if match:

            nifty_value = float(
                match.group(1).replace(",", "")
            )

        if nifty_value is not None:

            premarket = {
                "value": nifty_value,
                "status": "success"
            }

            print(
                "Pre-market NIFTY:",
                nifty_value
            )

        else:

            premarket = {
                "value": None,
                "status": "nifty_value_not_found"
            }

            print(
                "NIFTY pre-market value not found."
            )

            print(
                "Page text snippet:",
                body_text[:500]
            )

    except Exception as e:

        premarket = {
            "value": None,
            "status": "error",
            "message": str(e)
        }

        print(
            "Pre-market error:",
            e
        )

if 205 <= utc_minutes <= 235:

    with open(
        "premarket.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            premarket,
            f,
            indent=2
        )

    print(
        "Updated premarket.json"
    )

else:

    print(
        "Outside pre-market window; "
        "keeping existing premarket.json"
    )

# --------------------------------------------------
# 8. Verify whether CSV content changed
# --------------------------------------------------

with open(
    "nifty_latest.csv",
    "rb"
) as f:

    new_csv_bytes = f.read()


new_hash = hashlib.sha256(
    new_csv_bytes
).hexdigest()


old_hash = None


try:

    with open(
        "nse_status.json",
        "r",
        encoding="utf-8"
    ) as f:

        old_status = json.load(
            f
        )


        old_hash = old_status.get(
            "csv_hash"
        )


except Exception:

    pass


status = {

    "source_trading_date":
        latest_date.strftime(
            "%d-%b-%Y"
        ),

    "expiry_date":
        expiry.strftime(
            "%d-%b-%Y"
        ),

    "rows":
        len(output_rows),

    "generated_at_utc":
        datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

    "csv_hash":
        new_hash,

    "data_changed":
        old_hash != new_hash

}


# --------------------------------------------------
# 9. Get NIFTY 50 closing price
# --------------------------------------------------

nifty_close = None


try:

    response = nse_get(
        BASE + "/api/allIndices"
    )


    indices = response.get(
        "data",
        []
    )


    for item in indices:

        index_name = (
            item.get("index")
            or item.get("indexName")
        )


        if index_name == "NIFTY 50":

            nifty_close = float(
                item.get("last")
            )

            break


except Exception as e:

    print(
        "NIFTY close error:",
        e
    )


print(
    "NIFTY 50 close:",
    nifty_close
)


# --------------------------------------------------
# 10. Save final NSE status
# --------------------------------------------------

status["nifty_close"] = nifty_close


with open(
    "nse_status.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        status,
        f,
        indent=2
    )


print(
    "CSV data changed:",
    status["data_changed"]
)


print(
    "Status saved: nse_status.json"
)


print(
    "Finished."
)
