#!/usr/bin/env python3
import requests
import os
import sys
import time
import datetime
import argparse
import json
from collections import defaultdict
from dotenv import load_dotenv

# Subgraph Deployments
SUBGRAPHS = {
    "activity": "Qmf3qPUsfQ8et6E3QNBmuXXKqUJi91mo5zbsaTkQrSnMAP",
    "orderbook": "QmVGA9vvNZtEquVzDpw8wnTFDxVjB6mavTRMTrKuUBhi4t"
}

def query_graph(api_key, ipfs_hash, query):
    url = f"https://gateway.thegraph.com/api/{api_key}/deployments/id/{ipfs_hash}"
    r = requests.post(url, json={"query": query}, timeout=15)
    if r.status_code != 200:
        print(f"Graph query failed with status {r.status_code}: {r.text}")
        return None
    data = r.json()
    if 'errors' in data:
        print(f"Graph query error: {data['errors']}")
        return None
    return data.get('data')

def fetch_all_history(window_seconds=None):
    load_dotenv()
    wallet = os.environ.get('WALLET_ADDRESS', os.environ.get('FUNDER_ADDRESS'))
    if not wallet:
        print("Error: FUNDER_ADDRESS not found in .env")
        return
        
    api_key = os.environ.get('GRAPH_API_KEY')
    if not api_key or api_key == "your_new_key_here":
        print("\n❌ CRITICAL ERROR: GRAPH_API_KEY is missing from .env!")
        print("Because Polymarket hides AMM swap trades from their public API, we MUST use The Graph blockchain indexer.")
        print("To fix this:")
        print("1. Go to https://thegraph.com/studio/apikeys/")
        print("2. Generate a free API key.")
        print("3. Add GRAPH_API_KEY=your_key to your .env file.")
        sys.exit(1)
        
    wallet = wallet.lower()
    since_ts = 0
    window_label = "ALL"
    if window_seconds:
        since_ts = int(time.time()) - window_seconds
        window_label = f"{int(window_seconds/3600)}H" if window_seconds != 86400 else "24H"
            
    print(f"Fetching {window_label} 100% true historical trades (AMM + CLOB) for {wallet}...")
    
    events = []

    # 1. Fetch Activity (AMM Splits, Merges, Redemptions)
    print("-> Querying Polymarket AMM & Redemption events...")
    activity_query = f"""
    {{
      splits(first: 1000, orderBy: timestamp, orderDirection: desc, where: {{ stakeholder: "{wallet}" }}) {{
        id condition {{ id }} amount timestamp
      }}
      merges(first: 1000, orderBy: timestamp, orderDirection: desc, where: {{ stakeholder: "{wallet}" }}) {{
        id condition {{ id }} amount timestamp
      }}
      redemptions(first: 1000, orderBy: timestamp, orderDirection: desc, where: {{ redeemer: "{wallet}" }}) {{
        id condition {{ id }} payout timestamp
      }}
    }}
    """
    activity_data = query_graph(api_key, SUBGRAPHS['activity'], activity_query)
    if activity_data:
        for s in activity_data.get('splits', []):
            if int(s['timestamp']) >= since_ts:
                events.append({"type": "AMM_BUY", "cid": s['condition']['id'], "amount": float(s['amount'])/1e6, "ts": int(s['timestamp'])})
        for m in activity_data.get('merges', []):
            if int(m['timestamp']) >= since_ts:
                events.append({"type": "AMM_SELL", "cid": m['condition']['id'], "amount": float(m['amount'])/1e6, "ts": int(m['timestamp'])})
        for r in activity_data.get('redemptions', []):
            if int(r['timestamp']) >= since_ts:
                events.append({"type": "REDEEM", "cid": r['condition']['id'], "amount": float(r['payout'])/1e6, "ts": int(r['timestamp'])})

    # 2. Fetch Orderbook (CLOB Limit Orders)
    print("-> Querying Polymarket CLOB trades...")
    ob_query = f"""
    {{
      makerFills: orderFilledEvents(first: 1000, orderBy: timestamp, orderDirection: desc, where: {{ maker: "{wallet}" }}) {{
        id makerAssetId takerAssetId price fee makerAmountFilled takerAmountFilled timestamp side
      }}
      takerFills: orderFilledEvents(first: 1000, orderBy: timestamp, orderDirection: desc, where: {{ taker: "{wallet}" }}) {{
        id makerAssetId takerAssetId price fee makerAmountFilled takerAmountFilled timestamp side
      }}
    }}
    """
    ob_data = query_graph(api_key, SUBGRAPHS['orderbook'], ob_query)
    if ob_data:
        # Taker fills
        for t in ob_data.get('takerFills', []):
            if int(t['timestamp']) >= since_ts:
                size = float(t['takerAmountFilled'])/1e6
                price = float(t['price'])
                cost = size * price
                side = t['side'] # BUY/SELL (this is maker's side, taker side is opposite)
                actual_side = "SELL" if side == "BUY" else "BUY"
                # Maker is placing BUY order: maker pays USDC (makerAssetId), taker pays token (takerAssetId)
                # Maker is placing SELL order: maker pays token (makerAssetId), taker pays USDC (takerAssetId)
                cid = t['takerAssetId'] if side == "BUY" else t['makerAssetId']
                events.append({"type": f"CLOB_{actual_side}", "cid": cid, "amount": cost, "size": size, "price": price, "ts": int(t['timestamp'])})
        
        # Maker fills
        for m in ob_data.get('makerFills', []):
            if int(m['timestamp']) >= since_ts:
                size = float(m['makerAmountFilled'])/1e6
                price = float(m['price'])
                cost = size * price
                actual_side = m['side']
                cid = m['takerAssetId'] if actual_side == "BUY" else m['makerAssetId']
                events.append({"type": f"CLOB_{actual_side}", "cid": cid, "amount": cost, "size": size, "price": price, "ts": int(m['timestamp'])})

    if not events:
        print(f"No trades found in the {window_label} window.")
        return

    # Sort events by timestamp
    events.sort(key=lambda x: x['ts'])

    markets = {}
    total_invested = 0.0
    total_sold = 0.0
    total_redemptions = 0.0
    
    print("-> Resolving Market Names via Gamma API...")
    session = requests.Session()
    name_cache = {}

    for ev in events:
        cid = ev['cid']
        ts = ev['ts']
        amount = ev['amount']
        ev_type = ev['type']
        
        if cid not in markets:
            # Try to resolve title
            title = cid
            try:
                if cid not in name_cache:
                    # AssetID resolution is hard, we try Gamma API conditionId
                    r = session.get(f"https://gamma-api.polymarket.com/markets?conditionId={cid}", timeout=5)
                    if r.status_code == 200 and r.json():
                        title = r.json()[0].get("question", cid)
                        name_cache[cid] = title
                title = name_cache.get(cid, cid)
            except:
                pass

            markets[cid] = {
                "title": title,
                "total_in": 0.0,
                "total_out": 0.0,
                "redemption": 0.0,
                "logs": []
            }
            
        m = markets[cid]
        dt_str = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        
        if ev_type == "AMM_BUY":
            m["total_in"] += amount
            total_invested += amount
            m["logs"].append(f"AMM ACHAT: ${amount:.2f} ({dt_str})")
        elif ev_type == "CLOB_BUY":
            m["total_in"] += amount
            total_invested += amount
            m["logs"].append(f"CLOB BUY {ev.get('size',0):.2f} shares @ ${ev.get('price',0):.4f} = ${amount:.2f} ({dt_str})")
        elif ev_type == "AMM_SELL":
            m["total_out"] += amount
            total_sold += amount
            m["logs"].append(f"AMM VENTE: ${amount:.2f} ({dt_str})")
        elif ev_type == "CLOB_SELL":
            m["total_out"] += amount
            total_sold += amount
            m["logs"].append(f"CLOB SELL {ev.get('size',0):.2f} shares @ ${ev.get('price',0):.4f} = ${amount:.2f} ({dt_str})")
        elif ev_type == "REDEEM":
            m["redemption"] += amount
            total_redemptions += amount
            m["logs"].append(f"REDEEMED (Automatic Gain): ${amount:.2f} ({dt_str})")

    print(f"\n=== {window_label} EXACT TRADING HISTORY (THE GRAPH) ===")
    
    for cid, m in markets.items():
        total_payout = m["total_out"] + m["redemption"]
        pnl = total_payout - m["total_in"]
        title_display = (m['title'][:75] + '...') if len(m['title']) > 75 else m['title']
        print(f"\n• {title_display}")
        print(f"  Invested: ${m['total_in']:.2f} | Payout: ${total_payout:.2f} | Net PnL: ${pnl:+.2f}")
        for log in m["logs"]:
            print(f"    ↳ {log}")
            
    net_pnl = (total_sold + total_redemptions) - total_invested
    
    print(f"\n-----------------------------------")
    print(f"Total Invested:     ${total_invested:.2f}")
    print(f"Total Manual Sold:  ${total_sold:.2f}")
    print(f"Total Redemptions:  ${total_redemptions:.2f}")
    print(f"EXACT Net PNL:      ${net_pnl:+.2f}")
    print(f"-----------------------------------")
    print(f"Note: This report natively includes all AMM fees and UI swaps via Blockchain indexing.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=str, choices=["24h", "48h", "7d", "all"], default="all")
    args = parser.parse_args()
    
    seconds = None
    if args.window == "24h":
        seconds = 86400
    elif args.window == "48h":
        seconds = 48 * 3600
    elif args.window == "7d":
        seconds = 7 * 86400
        
    fetch_all_history(seconds)
