import yfinance as yf
import pandas as pd

ticker = yf.Ticker("TSCO.L")

# See ALL available fields
info = ticker.info
for key, value in info.items():
    print(f"{key}: {value}")

    import yfinance as yf
import pandas as pd

tickers = ["TSCO.L", "VOD.L", "MKS.L", "BARC.L", "BP.L"]

records = []
for t in tickers:
    ticker = yf.Ticker(t)
    info   = ticker.info

    record = {
        "company":              info.get("longName"),
        "ticker":               t,
        "sector":               info.get("sector"),
        "industry":             info.get("industry"),
        "employees":            info.get("fullTimeEmployees"),

        # --- LOAN SIGNALS ---
        "total_debt":           info.get("totalDebt"),
        "debt_to_equity":       info.get("debtToEquity"),
        "current_ratio":        info.get("currentRatio"),     # below 1 = cash squeeze
        "quick_ratio":          info.get("quickRatio"),       # liquidity stress
        "interest_coverage":    info.get("ebitda"),           # can they service debt

        # --- GROWTH SIGNALS ---
        "total_revenue":        info.get("totalRevenue"),
        "revenue_growth":       info.get("revenueGrowth"),    # YoY growth
        "earnings_growth":      info.get("earningsGrowth"),
        "earnings_quarterly":   info.get("earningsQuarterlyGrowth"),

        # --- FINANCIAL HEALTH ---
        "total_cash":           info.get("totalCash"),
        "free_cashflow":        info.get("freeCashflow"),
        "operating_cashflow":   info.get("operatingCashflow"),
        "profit_margin":        info.get("profitMargins"),
        "gross_margin":         info.get("grossMargins"),
        "ebitda_margin":        info.get("ebitdaMargins"),

        # --- SIZE ---
        "market_cap":           info.get("marketCap"),
        "enterprise_value":     info.get("enterpriseValue"),
        "book_value":           info.get("bookValue"),
        "total_assets":         info.get("totalAssets") if info.get("totalAssets") else None,

        # --- MARKET SIGNALS ---
        "beta":                 info.get("beta"),             # volatility
        "52w_high":             info.get("fiftyTwoWeekHigh"),
        "52w_low":              info.get("fiftyTwoWeekLow"),
        "price_to_book":        info.get("priceToBook"),
    }
    records.append(record)

df = pd.DataFrame(records)
print(df.to_string())
