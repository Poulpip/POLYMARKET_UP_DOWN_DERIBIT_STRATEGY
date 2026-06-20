#!/usr/bin/env python3
import requests
import os
import sys
import time
import datetime
import argparse
from config import Config, logger
from py_clob_client_v2.client import ClobClient

SUBGRAPHS = {
    "activity": "Qmf3qPUsfQ8et6E3QNBmuXXKqUJi91mo5zbsaTkQrSnMAP"
}

def query_graph(api_key, ipfs_hash, query):
    url = f"https://gateway.thegraph.com/api/{api_key}/deployments/id/{ipfs_hash}"
    r = requests.post(url, json={"query": query}, timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    if 'errors' in data:
        return None
    return data.get('data')

def fetch_api_history():
    wallet = Config.WALLET_ADDRESS
    if not wallet:
        print("Error: FUNDER_ADDRESS not found in .env")
        return
        
    api_key = os.environ.get('GRAPH_API_KEY')
    if not api_key:
        print("Error: GRAPH_API_KEY missing from .env")
        return
        
    print(f"Fetching EXACT Polymarket Trades via CLOB API & Subgraph for {wallet}...\n")
    events = []

    # 1. Fetch Redemptions (Payouts & Expiries)
    print("-> Querying On-chain Redemptions (The Graph)...")
    activity_query = f"""
    {{
      redemptions(first: 1000, orderBy: timestamp, orderDirection: desc, where: {{ redeemer: "{wallet.lower()}" }}) {{
        condition {{ id }} payout timestamp
      }}
    }}
    """
    activity_data = query_graph(api_key, SUBGRAPHS['activity'], activity_query)
    if activity_data:
        for r in activity_data.get('redemptions', []):
            cond = r.get('condition')
            cid = cond.get('id') if isinstance(cond, dict) else cond
            if cid: 
                events.append({"type": "REDEEM", "cid": cid, "amount": float(r['payout'])/1e6, "ts": int(r['timestamp'])})

    # 2. Fetch CLOB trades directly from Polymarket API
    print("-> Querying Off-chain CLOB Trades (Polymarket REST API)...")
    try:
        client = ClobClient(host=Config.CLOB_HOST, chain_id=137, key=Config.TRADING_PRIVATE_KEY, signature_type=2, funder=wallet, use_server_time=True)
        client.set_api_creds(client.derive_api_key())
        
        cursor = ''
        clob_count = 0
        for _ in range(10): # Fetch up to 1000 recent trades
            res = client.get_trades(only_first_page=True, next_cursor=cursor)
            if not isinstance(res, dict) or 'data' not in res:
                break
            for t in res['data']:
                cid = t['asset_id']
                price = float(t['price'])
                size = float(t['size'])
                side = t['side'] # BUY or SELL
                cost = price * size
                events.append({"type": side, "cid": cid, "amount": cost, "price": price, "size": size, "ts": int(t['timestamp'])})
                clob_count += 1
            
            cursor = res.get('next_cursor')
            if not cursor or cursor == 'LTE=':
                break
    except Exception as e:
        print(f"Failed to fetch CLOB API: {e}")

    if not events:
        print(f"No trades found.")
        return

    # Sort events by timestamp desc
    events.sort(key=lambda x: x['ts'], reverse=True)

    print("-> Resolving Market Names...")
    session = requests.Session()
    name_cache = {}
    
    markets = {}
    total_invested = 0.0
    total_payout = 0.0

    for ev in events:
        cid = ev['cid']
        
        if cid not in markets:
            title = cid
            try:
                if cid not in name_cache:
                    r = session.get(f"https://gamma-api.polymarket.com/markets?conditionId={cid}", timeout=2)
                    if r.status_code == 200 and r.json():
                        title = r.json()[0].get("question", cid)
                        name_cache[cid] = title
                    else:
                        r = session.get(f"https://gamma-api.polymarket.com/markets?clobTokenIds={cid}", timeout=2)
                        if r.status_code == 200 and r.json():
                            title = r.json()[0].get("question", cid)
                            name_cache[cid] = title
                title = name_cache.get(cid, cid)
            except:
                pass

            markets[cid] = {
                "title": title,
                "invested": 0.0,
                "payout": 0.0,
                "logs": []
            }
            
        m = markets[cid]
        ev_type = ev['type']
        amt = ev['amount']
        ts = ev['ts']
        
        now = int(time.time())
        diff_hours = (now - ts) / 3600
        if diff_hours < 24:
            time_ago = f"{int(diff_hours)}h il y a" if diff_hours >= 1 else "Moins d'une heure"
        else:
            time_ago = f"{int(diff_hours/24)}j il y a"
            
        if ev_type == "BUY":
            m["invested"] += amt
            total_invested += amt
            m["logs"].append(f"Achat | {ev['size']} positions @ {int(ev['price']*100)}¢ | -${amt:.2f} ({time_ago})")
        elif ev_type == "SELL":
            m["payout"] += amt
            total_payout += amt
            m["logs"].append(f"Vente | {ev['size']} positions @ {int(ev['price']*100)}¢ | +${amt:.2f} ({time_ago})")
        elif ev_type == "REDEEM":
            m["payout"] += amt
            total_payout += amt
            # If payout > 0 it's Échanger/Gain, if 0 it's Perte
            if amt > 0:
                m["logs"].append(f"Échanger | +${amt:.2f} ({time_ago})")
            else:
                m["logs"].append(f"Perte | - ({time_ago})")

    print("\n=== POLYMARKET EXACT API TRADING REPORT ===")
    
    for cid, m in markets.items():
        if m["invested"] > 0 or m["payout"] > 0:
            pnl = m["payout"] - m["invested"]
            print(f"\n• {m['title']}")
            print(f"  Invested: ${m['invested']:.2f} | Payout: ${m['payout']:.2f} | Net PnL: ${pnl:+.2f}")
            for log in m["logs"]:
                print(f"    ↳ {log}")

    net_pnl = total_payout - total_invested
    print("\n-----------------------------------")
    print(f"Total Invested: ${total_invested:.2f}")
    print(f"Total Payouts:  ${total_payout:.2f}")
    print(f"Exact Net PNL:  ${net_pnl:+.2f}")
    print("-----------------------------------")

if __name__ == "__main__":
    fetch_api_history()
