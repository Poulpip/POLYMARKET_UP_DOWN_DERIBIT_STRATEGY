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
    
    # Simple state machine to parse the block
    markets = defaultdict(lambda: {"invested": 0.0, "payout": 0.0})
    
    current_market = "Unknown Market"
    
    for i, line in enumerate(lines):
        if line.startswith("icon for "):
            current_market = line.replace("icon for ", "").strip()
            
        elif line == "Achat" or line == "Vente":
            # The UI lists the purchase or sale amount shortly after
            for j in range(i+1, min(i+8, len(lines))):
                if money_pattern.search(lines[j]):
                    amt_str = money_pattern.search(lines[j]).group(0).replace('$', '')
                    amt = float(amt_str)
                    if amt < 0:
                        total_invested += abs(amt)
                        markets[current_market]["invested"] += abs(amt)
                    else:
                        total_payout += amt
                        markets[current_market]["payout"] += amt
                    break
                    
        elif line in ["Gain", "Perte", "Remboursement"]:
            # For Gains or Refunds, the amount is usually displayed right below or near
            for j in range(i+1, min(i+4, len(lines))):
                if money_pattern.search(lines[j]):
                    amt_str = money_pattern.search(lines[j]).group(0).replace('$', '')
                    amt = float(amt_str)
                    if line == "Gain" or line == "Remboursement":
                        if amt > 0:
                            total_payout += amt
                            markets[current_market]["payout"] += amt
                    break

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
