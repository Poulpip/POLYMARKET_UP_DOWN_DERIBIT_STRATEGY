# Polymarket API Integration Guide

This document provides a comprehensive technical overview of how the trading bot interacts with the Polymarket APIs (Gamma API, CLOB API, and CLOB WebSocket). You can use this guide as a blueprint to build your next Polymarket trading bot.

---

## 1. Architecture & API Endpoints

Polymarket is split into two primary backend services:
1. **Gamma API:** Used for market discovery, retrieving events, filtering by timeframe, and fetching market metadata (like `conditionId` and `clobTokenIds`).
2. **CLOB (Central Limit Order Book) API:** Used for actual trading, retrieving orderbook states, querying account balances, and placing/cancelling orders.

### Base URLs
* **Gamma API:** `https://gamma-api.polymarket.com`
* **CLOB API:** `https://clob.polymarket.com`
* **CLOB WebSocket:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`

---

## 2. Environment Configurations & Credentials

To interact with the CLOB API, you must configure a Polygon wallet. If using a Gnosis Safe or direct wallet, the credentials are:
* `WALLET_ADDRESS`: Your Polygon public address.
* `WALLET_PRIVATE_KEY` / `POLY_PRIVATE_KEY`: Your Polygon private key.
* `FUNDER_ADDRESS`: Your Gnosis Safe proxy wallet address (if using smart contract wallet signature scheme).

---

## 3. Clob SDK Client Initialization

The bot uses the Python `py-clob-client` SDK (`py_clob_client_v2`) to authenticate and interact with the CLOB.

```python
from py_clob_client_v2.client import ClobClient

# Initialize the client
client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137, # Polygon Mainnet
    key=private_key,
    signature_type=2, # 2 indicates Gnosis Safe / EIP-1271 signatures
    funder=funder_address,
    use_server_time=True
)

# Derive API keys (first-time setup)
creds = client.derive_api_key() # or client.create_or_derive_api_key()
client.set_api_creds(creds)
```

---

## 4. Market Discovery (Gamma API)

Markets on Polymarket are structured under **Events**. An Event represents a topic (e.g. *"Bitcoin Up or Down on June 29?"*) and contains one or more **Markets** (e.g. outcome token pairs representing "Yes"/"Up" and "No"/"Down").

### Discovery Query
To query active BTC Up/Down events:
* **Endpoint:** `GET https://gamma-api.polymarket.com/events`
* **Query Parameters:**
  * `active`: `true`
  * `closed`: `false`
  * `limit`: `100`
  * `offset`: `0`
  * `end_date_min`: ISO Timestamp (e.g. `2026-06-29T16:00:00Z`)
  * `end_date_max`: ISO Timestamp (e.g. `2026-06-30T16:00:00Z`)

### Event Metadata Structure
The API response returns a list of events. Each event contains a list of `markets`:
```json
{
  "id": "334272",
  "title": "Bitcoin Up or Down - June 29, 9:50PM-9:55PM ET",
  "endDate": "2026-06-30T01:55:00Z",
  "markets": [
    {
      "id": "1822773",
      "conditionId": "0x29789033e9636c68c85f55bc4731d6ffbe8f41d37caf0df655a383b626e29c23",
      "outcomes": "[\"Up\", \"Down\"]",
      "outcomePrices": "[\"0.55\", \"0.45\"]",
      "clobTokenIds": "[\"4332761835...1558\", \"2391554306...3355\"]"
    }
  ]
}
```

* **`conditionId`:** The unique 32-byte hexadecimal representation of the market on-chain.
* **`clobTokenIds`:** The ERC-1155 token IDs representing the YES/NO (or UP/DOWN) contract shares.
  * Index `0` usually corresponds to the first outcome (e.g. "Up").
  * Index `1` corresponds to the second outcome (e.g. "Down").
* **`outcomePrices`:** Current mid-market probabilities (represented as a fraction of $1.00, e.g. `"0.55"` = 55%).

---

## 5. Order Book Queries (CLOB API)

To fetch current order book bids and asks for a specific outcome token without subscribing to WebSockets:

* **Endpoint:** `GET https://clob.polymarket.com/book?token_id=TOKEN_ID`
* **SDK equivalent:**
  ```python
  ob = client.get_order_book(token_id)
  ```

### Response Schema:
```json
{
  "bids": [
    {"price": "0.55", "size": "120.5"},
    {"price": "0.54", "size": "450.0"}
  ],
  "asks": [
    {"price": "0.56", "size": "80.0"},
    {"price": "0.57", "size": "310.2"}
  ]
}
```

---

## 6. Order Execution & Management (CLOB API)

All transactions on the CLOB require specifying the outcome token's `token_id`, transaction size, side (`BUY` or `SELL`), and a price cap.

### A. Placing Market Orders (Fill-and-Kill / FAK)
Polymarket's CLOB does not support native unbound market orders. Instead, they are sent as **FAK (Fill-and-Kill) Limit Orders** with a slippage buffer (e.g. a 1% price cushion).

```python
from py_clob_client_v2.clob_types import MarketOrderArgsV2, PartialCreateOrderOptions
from py_clob_client_v2.order_builder.constants import BUY

order_args = MarketOrderArgsV2(
    token_id="TOKEN_ID",
    amount=usdc_amount, # USDC amount for BUY, share amount for SELL
    side=BUY,
    price=safe_price_limit # Current orderbook price + 1% slippage cap
)

options = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)

# Place the FAK order
result = client.create_and_post_market_order(
    order_args,
    options=options,
    order_type="FAK"
)
```

### B. Placing Limit Orders (Maker/GTC)
Used for setting profit targets (Take Profit orders) or placing orders at a specific target price.

```python
from py_clob_client_v2.clob_types import OrderArgs, PartialCreateOrderOptions
from py_clob_client_v2.order_builder.constants import SELL

order_args = OrderArgs(
    token_id="TOKEN_ID",
    price=tp_price_limit, # e.g. 0.92
    size=shares_amount, # number of shares
    side=SELL
)

options = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)

# Place the Maker GTC order
result = client.create_and_post_order(
    order_args,
    options=options
)
# Returns the order ID (result.get("orderID")) to track or cancel later.
```

### C. Cancelling Orders
```python
# Accepts a list of order IDs
result = client.cancel_orders(["order_id_123"])
```

---

## 7. Real-Time Price Streaming (WebSocket API)

The WebSocket connection allows you to stream live bid/ask price updates for the specific contract token IDs you are trading.

* **WebSocket URL:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`

### A. Subscribing to Tokens
When opening the WebSocket connection, you must subscribe to a list of token IDs:
```json
{
  "assets_ids": ["TOKEN_ID_1", "TOKEN_ID_2"],
  "type": "market",
  "custom_feature_enabled": true
}
```

To add subscriptions on the fly:
```json
{
  "assets_ids": ["TOKEN_ID_3"],
  "operation": "subscribe",
  "custom_feature_enabled": true
}
```

### B. Handling WebSocket Messages
The WebSocket streams message updates in JSON format. The key event types are:

1. **`best_bid_ask`:**
   ```json
   {
     "event_type": "best_bid_ask",
     "asset_id": "TOKEN_ID_1",
     "best_bid": "0.54",
     "best_ask": "0.56",
     "timestamp": 1782564998
   }
   ```
2. **`price_change`:**
   ```json
   {
     "event_type": "price_change",
     "price_changes": [
       {
         "asset_id": "TOKEN_ID_1",
         "best_bid": "0.55",
         "best_ask": "0.56"
       }
     ]
   }
   ```

---

## 8. Account Balances (CLOB API)

To verify the balance of USDC or specific outcome tokens in the trading wallet:

### Fetching Conditional Token Shares
Polymarket shares are returned in micro-units (6 decimal places), so you must divide by `1,000,000` to get the actual shares.

```python
from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

params = BalanceAllowanceParams(
    asset_type=AssetType.CONDITIONAL,
    token_id="TOKEN_ID"
)
res = client.get_balance_allowance(params)
actual_shares = float(res.get("balance", 0.0)) / 1000000.0
```

---

## 9. Robust Market Resolution (Gamma API)

Relying on external spot index feeds (like Binance spot) to determine wins/losses at expiration creates discrepancies due to index feed offsets and pricing streams (Chainlink oracles vs. Binance spot). 

To ensure **100% resolution alignment with Polymarket**, query the resolved outcome directly via the Gamma API.

### Query Resolved Markets by Token ID
* **Endpoint:** `GET https://gamma-api.polymarket.com/markets`
* **Parameters:**
  * `clob_token_ids`: The trade's token ID (e.g. `4332761835...1558`)
  * `closed`: `true` (retrieve inactive/resolved markets)

### Parsing Response to Determine Win/Loss
```python
import requests
import json

def get_resolved_winner(token_id):
    url = "https://gamma-api.polymarket.com/markets"
    params = {
        "clob_token_ids": token_id,
        "closed": "true"
    }
    
    r = requests.get(url, params=params, timeout=10)
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            market_metadata = data[0]
            
            # The market must be resolved/closed
            if market_metadata.get("closed") is True:
                # Parse clobTokenIds and outcomePrices
                prices = market_metadata.get("outcomePrices", [])
                if isinstance(prices, str):
                    prices = json.loads(prices)
                    
                tokens = market_metadata.get("clobTokenIds", [])
                if isinstance(tokens, str):
                    tokens = json.loads(tokens)
                
                # The winner outcome will have a price payout of "1" or "1.0"
                winner_token_id = None
                for idx, price in enumerate(prices):
                    if str(price) in ("1", "1.0"):
                        if idx < len(tokens):
                            winner_token_id = tokens[idx]
                            break
                            
                if winner_token_id:
                    # Returns True if our token was the winner, False otherwise
                    return token_id == winner_token_id
    return None # Defer resolution if market is not resolved yet or API fails
```
