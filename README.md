# **StockApp: Advanced Trading & Analysis Suite**

**StockApp** is a comprehensive Python-based desktop application designed for Indian stock market traders using the Zerodha Kite Connect ecosystem. It integrates real-time portfolio management, automated stop-loss placement, fundamental scraping, and an **Automatic Intraday Trading System (AITS)** into a single, user-friendly interface.

## ---

**🚀 Key Features**

* **Real-time Dashboard:** Monitor Profile, Orders, Positions, Holdings, and Margins directly from the app.  
* **Automated Risk Management:** Bulk-place GTT (Good-Till-Triggered) stop-loss orders using technical indicators like 20-day Moving Averages or trailing percentages.  
* **Intraday Automation (AITS):** A rule-based engine that executes trades from an Excel plan, featuring a "Kill Switch" for daily loss protection and WebSocket-based live price tracking.  
* **Fundamental Analysis:** Scrapes deep financial metrics and growth data from Screener.in and calculates a proprietary "Fundamental Score."  
* **Trade Plan Generator:** Automatically calculates Entry, Target, and Stop-Loss levels for a list of stocks based on Range Breakouts or Moving Average pullbacks.  
* **Data Archiving:** One-click "Save to Excel" functionality for almost every data view.

## ---

**🛠 Prerequisites & Setup**

1. **Authorization:** You must provide an enctoken (obtained from your Kite web session) in the top input bar to initialize the connection.  
2. **Dependencies:** Ensure you have the following installed:  
   * PyQt6, pandas, requests, beautifulsoup4, xlsxwriter, kiteconnect, openpyxl.  
3. **Token Mapping:** Many tabs rely on e.xlsx. Go to the **Historical Data** tab and click **Generate e.xlsx** to map symbols to their internal Kite tokens.

## ---

**📂 Tab-by-Tab Documentation**

### **1\. Account Management (Profile, Orders, Positions, Holdings, Margins)**

These tabs provide a mirror of your Zerodha dashboard.

* **Feature:** Refresh live data and export it to Excel for auditing.  
* **Use Case:** Quickly check your daily PnL or verify which orders were filled without leaving the analysis environment.

### **2\. Instruments & Historical Data**

* **Instruments:** View all tradable symbols on NSE/BSE. Filters out "junk" symbols (RE, BE, etc.) automatically.  
* **Historical Data:** Downloads OHLCV (Open, High, Low, Close, Volume) and OI (Open Interest) data to .txt files compatible with charting software like Amibroker or MetaTrader.

### **3\. Stop Loss (GTT Management)**

* **Feature:** Automatically calculates stop-loss levels.  
* **Strategies:** \* *Generic:* Uses a mix of 20-day MAs and Recent Lows.  
  * *Fixed/Trailing %:* Sets SL based on a user-defined percentage from your Average Price or LTP.  
* **Action:** Deletes existing SELL GTTs and replaces them with fresh ones to ensure your capital is always protected.

### **4\. Target (Bulk GTT Buy)**

* **Feature:** Place bulk "Buy" orders for a list of stocks.  
* **Logic:** Trigger orders based on the "Max High of Past X Days" (Range Breakout) or a simple percentage above the current price.

### **5\. Fundamental Scraper**

* **Feature:** Enter a list of symbols; the app crawls Screener.in for:  
  * Ratios (P/E, ROCE, ROE, Debt to Equity).  
  * Growth (10yr/5yr/3yr Sales and Profit CAGR).  
  * Scores (Piotroski F-Score, G-Factor, and a custom weighted Fundamental Score).  
* **Use Case:** Ideal for long-term investors filtering for high-growth, low-debt companies.

### **6\. TradePlan (Strategy Builder)**

* **Feature:** The "Brain" of the automation.  
* **Input:** A list of symbols.  
* **Output:** A trade\_plan.xlsx file containing calculated Entry, SL, and Target prices based on chosen strategies (e.g., 15-day Range Breakout or 9 EMA Pullback).

### **7\. AITS (Automatic Intraday Trading System)**

The most advanced module of the application.

* **Paper Trading:** Test strategies with live prices without risking real capital.  
* **Live Mode:** Uses **Bracket Orders (BO)** to lock in Targets and Stop-Losses immediately upon entry.  
* **Risk Control:** \* *Kill Switch:* Automatically squares off all positions if the daily loss limit (e.g., \-₹2500) is hit.  
  * *Manual Adoption:* Can detect trades you took manually on your phone and start managing them (applying SL/Target) automatically.  
* **Dashboard:** A live-updating table showing Symbol, Status (Pending/Active/Closed), and Live PnL.

## ---

**⚠️ Important Usage Notes**

* **Market Hours:** The AITS worker respects the start/end times defined in the UI and will initiate a "Square Off All" at the end of the session.  
* **Rate Limits:** The app includes built-in time.sleep() intervals to prevent your IP from being blocked by Kite or Screener.in.  
* **Disclaimer:** This is an algorithmic trading tool. Always test new strategies in **Paper Trading Mode** before deploying live capital.

# **Kite Trade Python**

An unofficial, lightweight Python API client for interacting with Zerodha's Kite trading platform. This application allows you to automate trading, fetch historical data, manage orders, and handle GTTs (Good Till Triggered) without requiring the official Kite Connect API subscription. It authenticates using user credentials to generate an enctoken.

⚠️ **Disclaimer**: *This is an unofficial wrapper. Using enctoken directly bypasses the official Kite Connect API flow and may violate Zerodha's Terms of Service. Use this application strictly at your own risk. The authors are not responsible for any blocked accounts or financial losses.*

## ---

**Features**

* **Auto-Dependency Management:** Automatically installs required packages (requests, python-dateutil) if not found.  
* **Authentication:** Generate an enctoken directly using your Zerodha User ID, Password, and 2FA app code.  
* **User Data:** Fetch profile information, fund margins, current positions, and holdings.  
* **Market Data:** Retrieve the complete master list of tradable instruments and fetch historical candle data (with Open Interest support).  
* **Order Management:** Place, modify, and cancel standard orders (Market, Limit, SL, SL-M) across different product types (MIS, CNC, NRML).  
* **GTT Management:** Complete support for placing, modifying, deleting, and retrieving Good Till Triggered (GTT) orders (Single and OCO/Two-leg).

## ---

**Installation & Prerequisites**

Ensure you have Python 3 installed. The script uses requests and python-dateutil. While the script attempts to install them automatically, you can manually install them via pip:

Bash

pip install requests python-dateutil

Place the kite\_trade.py file in your project directory.

## ---

**Getting Started**

### **1\. Generating the enctoken**

To use the KiteApp, you first need to authenticate and generate an enctoken. You can do this using the helper function get\_enctoken.

Python

from kite\_trade import get\_enctoken, KiteApp

user\_id \= "YOUR\_USER\_ID"  
password \= "YOUR\_PASSWORD"  
twofa \= "YOUR\_2FA\_CODE" \# 6-digit TOTP from your authenticator app

\# Generate Token  
enctoken \= get\_enctoken(user\_id, password, twofa)  
print(f"Your enctoken is: {enctoken}")

### **2\. Initializing the Client**

Once you have the token, initialize the KiteApp instance:

Python

kite \= KiteApp(enctoken=enctoken)

## ---

**Usage Examples**

### **Fetching User & Account Data**

Python

\# Get user profile  
profile \= kite.profile()

\# Get available margins and funds  
margins \= kite.margins()  
print(margins\['equity'\]\['available'\]\['live\_balance'\])

\# Get current holdings and open positions  
holdings \= kite.holdings()  
positions \= kite.positions()

### **Fetching Market Data**

Python

\# Get the complete list of instruments for a specific exchange (e.g., NSE)  
nse\_instruments \= kite.instruments(exchange=kite.EXCHANGE\_NSE)

\# Fetch historical data (Candles)  
\# Requires an instrument\_token (which you can get from the instruments list)  
historical\_data \= kite.historical\_data(  
    instrument\_token=256265, \# Example token for NIFTY 50  
    from\_date="2023-01-01",  
    to\_date="2023-01-10",  
    interval="5minute",  
    continuous=False,  
    oi=True  
)  
print(historical\_data)

### **Managing Orders**

Python

\# Place a standard Market Buy Order (Intraday \- MIS)  
order\_id \= kite.place\_order(  
    variety=kite.VARIETY\_REGULAR,  
    exchange=kite.EXCHANGE\_NSE,  
    tradingsymbol="INFY",  
    transaction\_type=kite.TRANSACTION\_TYPE\_BUY,  
    quantity=1,  
    product=kite.PRODUCT\_MIS,  
    order\_type=kite.ORDER\_TYPE\_MARKET  
)  
print(f"Order placed with ID: {order\_id}")

\# Modify an existing order  
kite.modify\_order(  
    variety=kite.VARIETY\_REGULAR,  
    order\_id=order\_id,  
    quantity=2  
)

\# Cancel an order  
kite.cancel\_order(  
    variety=kite.VARIETY\_REGULAR,  
    order\_id=order\_id  
)

### **Managing GTT Orders**

Python

\# Get all active GTTs  
gtts \= kite.get\_gtts()

\# Place a Single GTT Order  
gtt\_response \= kite.place\_gtt(  
    trigger\_type=kite.GTT\_TYPE\_SINGLE,  
    tradingsymbol="RELIANCE",  
    exchange=kite.EXCHANGE\_NSE,  
    trigger\_values=\[2500.0\], \# Trigger at 2500  
    last\_price=2600.0,  
    orders=\[{  
        "transaction\_type": kite.TRANSACTION\_TYPE\_SELL,  
        "quantity": 10,  
        "order\_type": kite.ORDER\_TYPE\_LIMIT,  
        "product": kite.PRODUCT\_CNC,  
        "price": 2500.0  
    }\]  
)

\# Delete a GTT order  
kite.delete\_gtt(trigger\_id="YOUR\_GTT\_TRIGGER\_ID")

## ---

**API Reference**

### **Order Constants Available**

The KiteApp class contains pre-defined constants for safe order placement:

4. **Exchanges**: EXCHANGE\_NSE, EXCHANGE\_BSE, EXCHANGE\_NFO, EXCHANGE\_MCX, etc.  
5. **Products**: PRODUCT\_MIS (Intraday), PRODUCT\_CNC (Delivery), PRODUCT\_NRML (F\&O).  
6. **Order Types**: ORDER\_TYPE\_MARKET, ORDER\_TYPE\_LIMIT, ORDER\_TYPE\_SL, ORDER\_TYPE\_SLM.  
7. **Transaction Types**: TRANSACTION\_TYPE\_BUY, TRANSACTION\_TYPE\_SELL.  
8. **Varieties**: VARIETY\_REGULAR, VARIETY\_AMO, VARIETY\_CO.

### **Key Methods**

| Method | Description |
| :---- | :---- |
| instruments(exchange) | Fetches the latest master list of trading instruments. |
| historical\_data(...) | Fetches historical candle/kline data, optionally including Open Interest (OI). |
| orders(), positions(), holdings() | Returns JSON lists of order history, intraday positions, and delivery holdings respectively. |
| place\_order(...) | Executes an order. Requires variety, exchange, symbol, transaction type, quantity, product, and order type. Returns order\_id. |
| place\_gtt(...) | Sets up a new GTT. Accepts GTT\_TYPE\_SINGLE or GTT\_TYPE\_OCO (Two-leg). |

### 

### **Files to Club Together**

You should place the following files in a single project folder:

* **kite\_trade.py**: The core library handling the Zerodha Kite API calls.  
* **stock\_app.py**: The main PyQt6 application file that contains the GUI and worker logic.  
* **config.json**: (Optional, created automatically) Stores your session token and file paths for persistence.  
* **e.xlsx**: (Generated via the app) This is the master mapping file for instrument tokens.  
* **Data/ folder**: A directory where historical .txt files will be saved.

