import requests
from datetime import datetime

session = requests.Session()

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# Establish NSE session
session.get(
    "https://www.nseindia.com/report-detail/fo_eq_security",
    headers=headers,
    timeout=30
)

# Test NIFTY 50 historical derivatives
params = {
    "from": "04-09-2026",
    "to": "04-09-2026",
    "instrumentType": "OPTIDX",
    "symbol": "NIFTY",
    "year": "2026",
    "expiryDate": "08-SEP-2026",
    "optionType": "CE",
    "strikePrice": "24000",
}

response = session.get(
    "https://www.nseindia.com/api/historicalOR/foCPV",
    params=params,
    headers=headers,
    timeout=30
)

print("HTTP Status:", response.status_code)
print(response.text)
import csv
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests


BASE = "https://www.nseindia.com"
HISTORICAL_API = BASE + "/api/historicalOR/foCPV"
PREOPEN_API = BASE + "/api/market-data-pre-open?key=NIFTY"
REPORT_PAGE = BASE + "/report-detail/eq_security"
OPTION_INFO_API = BASE + "/api/option-chain-contract-info?symbol=NIFTY"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/144.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Referer": BASE + "/",
    "X-Requested-With": "XMLHttpRequest",
}


def create_session():
    session = requests.Session()
    session.headers.update(HEADERS)

    response = session.get(REPORT_PAGE, timeout=30)

    if response.status_code != 200:
        raise RuntimeError(
            f"NSE session failed: HTTP {response.status_code}"
        )

    return session


def next_tuesday(d):
    days = (1 - d.weekday()) % 7
    return d + timedelta(days=days)


def format_date(d):
    return d.strftime("%d-%m-%Y")


def format_expiry(d):
    return d.strftime("%d-%b-%Y").upper()


def get_current_strikes(session, expiry):
    response = session.get(
        BASE + "/api/option-chain-v3",
        params={
            "type": "Indices",
            "symbol": "NIFTY",
            "expiry": format_expiry(expiry),
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Option-chain request failed: HTTP {response.status_code}"
        )

    data = response.json()

    rows = data.get("records", {}).get("data", [])

    strikes = []

    for row in rows:
        try:
            strike = float(row["strikePrice"])

            if strike % 100 == 0:
                strikes.append(int(strike))

        except Exception:
            pass

    strikes = sorted(set(strikes))

    if not strikes:
        raise RuntimeError("No NIFTY 100-point strikes were found.")

    return strikes


def get_historical_contract(
    session,
    trade_date,
    expiry,
    option_type,
    strike,
):
    params = {
        "from": format_date(trade_date),
        "to": format_date(trade_date),
        "instrumentType": "OPTIDX",
        "symbol": "NIFTY",
        "year": trade_date.year,
        "expiryDate": format_expiry(expiry),
        "optionType": option_type,
        "strikePrice": f"{strike:.2f}",
    }

    for attempt in range(3):
        try:
            response = session.get(
                HISTORICAL_API,
                params=params,
                timeout=30,
            )

            if response.status_code == 200:
                payload = response.json()
                rows = payload.get("data", [])

                if rows:
                    return rows[0]

        except Exception as error:
            if attempt == 2:
                print(
                    f"Failed {option_type} {strike}: {error}"
                )

        time.sleep(1)

    return None


def find_latest_trading_date(session):
    today = datetime.now().date()

    for days_back in range(0, 8):
        candidate = today - timedelta(days=days_back)

        if candidate.weekday() >= 5:
            continue

        expiry = next_tuesday(candidate)

        # Test one common 100-point strike.
        test_strikes = [24000, 23900, 23800, 24100]

        for strike in test_strikes:
            row = get_historical_contract(
                session,
                candidate,
                expiry,
                "CE",
                strike,
            )

            if row:
                return candidate, expiry

    raise RuntimeError(
        "Could not find the latest NIFTY trading date."
    )


def convert_row(row):
    def number(name):
        value = row.get(name)

        if value in (None, "", "-"):
            return ""

        try:
            return float(value)
        except Exception:
            return value

    traded_value = number("FH_TOT_TRADED_VAL")
    premium_value = number("CALCULATED_PREMIUM_VAL")

    if isinstance(traded_value, (int, float)):
        traded_value = traded_value / 100000

    if isinstance(premium_value, (int, float)):
        premium_value = premium_value / 100000

    return [
        row.get("FH_TIMESTAMP", ""),
        row.get("FH_EXPIRY_DT", ""),
        row.get("FH_OPTION_TYPE", ""),
        number("FH_STRIKE_PRICE"),
        number("FH_OPENING_PRICE"),
        number("FH_TRADE_HIGH_PRICE"),
        number("FH_TRADE_LOW_PRICE"),
        number("FH_CLOSING_PRICE"),
        number("FH_LAST_TRADED_PRICE"),
        number("FH_SETTLE_PRICE"),
        number("FH_TOT_TRADED_QTY"),
        traded_value,
        premium_value,
        number("FH_OPEN_INT"),
        number("FH_CHANGE_IN_OI"),
    ]


def download_nifty_data(session):
    trade_date, expiry = find_latest_trading_date(session)

    print(
        "Trading date:",
        trade_date.strftime("%d-%m-%Y"),
    )

    print(
        "Expiry:",
        expiry.strftime("%d-%m-%Y"),
    )

    strikes = get_current_strikes(session, expiry)

    print("100-point strikes found:", len(strikes))

    output = Path("nifty_latest.csv")

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
        "Change in OI",
    ]

    rows = []

    for strike in strikes:
        for option_type in ("CE", "PE"):

            row = get_historical_contract(
                session,
                trade_date,
                expiry,
                option_type,
                strike,
            )

            if row:
                rows.append(convert_row(row))

    rows.sort(
        key=lambda x: (
            x[3],
            x[2],
        )
    )

    with output.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:

        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)

    print("Rows written:", len(rows))
    print("Created:", output)


def download_premarket(session):
    now = datetime.now()

    # Only attempt the NSE pre-open request around 09:00-09:15 IST.
    if not (
        now.hour == 9
        and 0 <= now.minute <= 15
    ):
        result = {
            "value": None,
            "status": "outside_preopen_window",
        }

        Path("premarket.json").write_text(
            json.dumps(result),
            encoding="utf-8",
        )

        print("Pre-market skipped: outside 09:00-09:15 IST.")
        return

    response = session.get(
        PREOPEN_API,
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Pre-open request failed: HTTP {response.status_code}"
        )

    payload = response.json()

    nifty_value = None

    for item in payload.get("data", []):
        if item.get("index") == "NIFTY 50":
            nifty_value = item.get("last")
            break

    if nifty_value is None:
        raise RuntimeError(
            "NIFTY 50 indicative pre-market value was not found."
        )

    result = {
        "value": float(nifty_value),
        "status": "ok",
        "timestamp": now.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }

    Path("premarket.json").write_text(
        json.dumps(result),
        encoding="utf-8",
    )

    print("NIFTY pre-market:", nifty_value)


def main():
    session = create_session()

    download_nifty_data(session)
    download_premarket(session)


if __name__ == "__main__":
    main()
