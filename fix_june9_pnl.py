import sqlite3

def fix_pnl(db_name):
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Get all June 9 trades
    c.execute("SELECT * FROM paper_trades WHERE market_title LIKE '%June 9%'")
    trades = c.fetchall()
    
    updated_count = 0
    for trade in trades:
        trade_id = trade['id']
        direction = trade['direction']
        size_usdc = trade['size_usdc']
        entry_price = trade['entry_polymarket_price']
        
        if direction == 'DOWN':
            # DOWN won
            exit_price = 1.00
            payout = size_usdc / entry_price
            realized_pnl = payout - size_usdc
        else:
            # UP lost
            exit_price = 0.00
            realized_pnl = -size_usdc
            
        c.execute('''
            UPDATE paper_trades 
            SET exit_price = ?, realized_pnl = ?, exit_reason = 'RESOLVED_WIN' 
            WHERE id = ?
        ''', (exit_price, realized_pnl, trade_id))
        
        # If it lost, update reason
        if direction == 'UP':
            c.execute("UPDATE paper_trades SET exit_reason = 'RESOLVED_LOSS' WHERE id = ?", (trade_id,))
            
        updated_count += 1
        
    conn.commit()
    conn.close()
    print(f"Updated {updated_count} trades in {db_name}")

if __name__ == '__main__':
    fix_pnl('paper_trades.db')
    fix_pnl('live_trades.db')
