#!/usr/bin/env python3
import sys
import re
from collections import defaultdict

def parse_ui_history(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
    except FileNotFoundError:
        print(f"Error: Could not find {file_path}")
        return

    # Regex patterns
    money_pattern = re.compile(r'[-+]?\$\d+\.\d{2}')
    position_pattern = re.compile(r'(\d+(\.\d+)?) positions')

    total_invested = 0.0
    total_payout = 0.0
    
    actions = ["Achat", "Vente", "Perte", "Gain", "Échanger", "Remboursement"]
    markets = defaultdict(lambda: {"invested": 0.0, "payout": 0.0})
    
    for i, line in enumerate(lines):
        if line in actions:
            market_name = "Unknown Market"
            for j in range(i+1, min(i+5, len(lines))):
                if lines[j].startswith("icon for "):
                    market_name = lines[j].replace("icon for ", "").strip()
                    break
                    
            amt = 0.0
            for j in range(i+1, min(i+8, len(lines))):
                # If we hit another action, stop searching for amount
                if lines[j] in actions:
                    break
                if money_pattern.search(lines[j]):
                    amt_str = money_pattern.search(lines[j]).group(0).replace('$', '')
                    amt = float(amt_str)
                    break
                    
            if amt != 0.0:
                if amt < 0:
                    total_invested += abs(amt)
                    markets[market_name]["invested"] += abs(amt)
                else:
                    total_payout += amt
                    markets[market_name]["payout"] += amt

    print("\n=== UI PARSED TRADING REPORT ===")
    for market, data in markets.items():
        if data["invested"] > 0 or data["payout"] > 0:
            pnl = data["payout"] - data["invested"]
            print(f"• {market}")
            print(f"  Invested: ${data['invested']:.2f} | Payout: ${data['payout']:.2f} | Net PnL: ${pnl:+.2f}")

    net_pnl = total_payout - total_invested
    print("\n-----------------------------------")
    print(f"Total Invested: ${total_invested:.2f}")
    print(f"Total Payouts:  ${total_payout:.2f}")
    print(f"Exact Net PNL:  ${net_pnl:+.2f}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_ui_history.py <path_to_pasted_text_file.txt>")
        sys.exit(1)
    parse_ui_history(sys.argv[1])
