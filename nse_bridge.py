import csv
import json
import requests
from datetime import datetime, timedelta

BASE = "https://www.nseindia.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/",
}

session = requests.Session()
session.headers.update(HEADERS)


def next_tuesday(d):
    days = (1 - d.weekday()) % 7
    if days == 0:
        days = 7
    return d + timedelta(days=days)


def nse_get(url, params=None):
    r = session.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


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
# 2. Find next Tuesday expiry
# --------------------------------------------------

today = datetime.now().date()
expiry = next_tuesday(today)

expiry_text = expiry.strftime("%d-%b-%Y").upper()
print("Target expiry:", expiry_text)


# --------------------------------------------------
# 3. Get NIFTY option-chain strikes
# --------------------------------------------------

print("Getting NIFTY option-chain...")

option_chain = nse_get(
    BASE + "/api/option-chain-v3",
    {
        "type": "equity",
        "symbol": "NIFTY",
        "expiry": expiry_text
    }
)

records = option_chain.get("records", {})
data = records.get("data", [])

strikes = set()

for item in data:
    strike = item.get("strikePrice")

    if strike is not None:
        strike = float(strike)

        if strike % 100 == 0:
            strikes.add(strike)

strikes = sorted(strikes)

print("100-point strikes found:", len(strikes))

if not strikes:
    raise Exception("No NIFTY 100-point strikes found.")


# --------------------------------------------------
# 4. Find latest trading date
# --------------------------------------------------

def historical_rows(date_obj, option_type, strike):
    date_text = date_obj.strftime("%d-%m-%Y")

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

    return result.get("data", [])


print("Finding latest trading date...")

latest_date = None

for back in range(0, 8):

    test_date = today - timedelta(days=back)

    for strike in [24000, 23900, 23800, 24100]:

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
    raise Exception("Could not find latest NIFTY trading date.")


print(
    "Trading date:",
    latest_date.strftime("%d-%m-%Y")
)


# --------------------------------------------------
# 5. Download CE + PE historical data
# --------------------------------------------------

output_rows = []

for strike in strikes:

    for option_type in ["CE", "PE"]:

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

                float(row.get("FH_OPENING_PRICE", 0) or 0),
                float(row.get("FH_TRADE_HIGH_PRICE", 0) or 0),
                float(row.get("FH_TRADE_LOW_PRICE", 0) or 0),
                float(row.get("FH_CLOSING_PRICE", 0) or 0),
                float(row.get("FH_LAST_TRADED_PRICE", 0) or 0),
                float(row.get("FH_SETTLE_PRICE", 0) or 0),

                float(row.get("FH_TOT_TRADED_QTY", 0) or 0),

                float(row.get("FH_TOT_TRADED_VAL", 0) or 0) / 100000,

                float(row.get("CALCULATED_PREMIUM_VAL", 0) or 0) / 100000,

                float(row.get("FH_OPEN_INT", 0) or 0),

                float(row.get("FH_CHANGE_IN_OI", 0) or 0),
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
# 6. Write exact Raw Data CSV
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

    writer.writerow(headers)

    for row in output_rows:
        writer.writerow(row)


print(
    "Rows written:",
    len(output_rows)
)

print("Created: nifty_latest.csv")


# --------------------------------------------------
# 7. Pre-market NIFTY value
# --------------------------------------------------

premarket = {
    "value": None,
    "status": "outside_preopen_window"
}

now = datetime.now()

# GitHub runner time is UTC.
# 09:00-09:15 IST = 03:30-03:45 UTC.

utc_minutes = now.hour * 60 + now.minute

if 210 <= utc_minutes <= 225:

    print("Pre-market window detected.")

    try:

        response = nse_get(
            BASE + "/api/market-data-pre-open",
            {
                "key": "NIFTY"
            }
        )

        nifty_value = None

        for item in response.get("data", []):

            if item.get("index") == "NIFTY 50":

                nifty_value = item.get("last")
                break

        if nifty_value is not None:

            premarket = {
                "value": float(nifty_value),
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

            print("NIFTY pre-market value not found.")

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


if 210 <= utc_minutes <= 225:
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

    print("Updated premarket.json")
else:
    print("Outside pre-market window; keeping existing premarket.json")


print("Created: premarket.json")
print("Finished.")
