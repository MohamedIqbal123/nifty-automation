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
