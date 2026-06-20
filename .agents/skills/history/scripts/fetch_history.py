#!/usr/bin/env python3
import sqlite3
import argparse
import os
import sys
from datetime import datetime

DB_PATH = '/home/adry7/Deribit_bot/live_trades.db'

def fetch_live_history(window_label):
    if not os.path.exists(DB_PATH):
        print("No live_trades.db found. The bot has not executed any live trades yet.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        trades = conn.execute('SELECT * FROM paper_trades ORDER BY id ASC').fetchall()

    if not trades:
        print(f"No trades found in live_trades.db")
        return

    markets = {}
    total_invested = 0.0
    total_realized_pnl = 0.0
    total_payout = 0.0
    
    for t in trades:
        title = f"{t['market_title']} ({t['direction']})"
        if title not in markets:
            markets[title] = {
                "invested": 0.0,
                "payout": 0.0,
                "pnl": 0.0,
                "logs": []
            }
            
        m = markets[title]
        size_usdc = t['size_usdc']
        entry_price = t['entry_polymarket_price']
        status = t['status']
        exit_price = t['exit_price']
        realized_pnl = t['realized_pnl'] or 0.0
        
        m['invested'] += size_usdc
        total_invested += size_usdc
        
        entry_shares = size_usdc / entry_price if entry_price else 0
        
        dt_str = t['created_at'] + " UTC"
        m['logs'].append(f"BUY {entry_shares:.2f} shares @ ${entry_price:.3f} = ${size_usdc:.2f} ({dt_str})")
        
        if status == 'CLOSED':
            payout = size_usdc + realized_pnl
            m['payout'] += payout
            m['pnl'] += realized_pnl
            total_realized_pnl += realized_pnl
            total_payout += payout
            
            exit_str = t['closed_at'] + " UTC"
            reason = t['exit_reason']
            m['logs'].append(f"SELL/REDEEM [{reason}]: ${payout:.2f} payout, PnL: ${realized_pnl:+.2f} ({exit_str})")
        else:
            m['logs'].append(f"OPEN POSITION: Currently active, peak price was ${t['peak_price']:.3f}")

    print(f"\n=== {window_label} EXACT TRADING HISTORY (LIVE DB) ===")
    
    for title, m in markets.items():
        print(f"\n• {title}")
        print(f"  Invested: ${m['invested']:.2f} | Payout: ${m['payout']:.2f} | Net PnL: ${m['pnl']:+.2f}")
        for log in m["logs"]:
            print(f"    ↳ {log}")
            
    print(f"\n-----------------------------------")
    print(f"Total Invested:     ${total_invested:.2f}")
    print(f"Total Payout:       ${total_payout:.2f}")
    print(f"EXACT Net PNL:      ${total_realized_pnl:+.2f}")
    print(f"-----------------------------------")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=str, choices=["24h", "48h", "7d", "all"], default="all")
    args = parser.parse_args()
    
    fetch_live_history(args.window.upper())
