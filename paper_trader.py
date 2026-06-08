import time
import logging
import sys
import threading

# EARLY LOGGER INITIALIZATION (per user rules)
from config import Config
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("paper_trader")

# Local imports
from strategy_runner import evaluate_market_edge
from db_manager import init_db, record_paper_trade, get_open_trades, close_paper_trade
from live_client import LiveTrader
from market_ws import MarketWebsocket

TRADE_SIZE_USDC = getattr(Config, 'MAX_USDC_PER_TRADE', 100.0)
TAKE_PROFIT_MULTIPLIER = 1.20  # +20% gain target

live_trader = LiveTrader()
market_ws = MarketWebsocket()

# Thread-safe flag to avoid duplicate sells
sell_lock = threading.Lock()
sold_trades = set()

def on_ws_price_update(token_id, bid, ask):
    """Callback fired by WebSocket on every orderbook update for open positions."""
    open_trades = get_open_trades()
    for trade in open_trades:
        if trade['token_id'] == token_id:
            entry_price = trade['entry_polymarket_price']
            take_profit_target = entry_price * TAKE_PROFIT_MULTIPLIER
            
            # The bid is what we can sell at
            if bid >= take_profit_target:
                with sell_lock:
                    if trade['id'] in sold_trades:
                        continue
                    sold_trades.add(trade['id'])
                    
                logger.info(f"⚡ INSTANT TAKE PROFIT via WebSocket for {trade['direction']}!")
                logger.info(f"Entry: ${entry_price:.3f} | Best Bid: ${bid:.3f}")
                
                # Execute Sell
                size_shares = trade['size_usdc'] / entry_price
                result = live_trader.execute_market_trade(token_id, "SELL", size_shares, bid)
                
                if result['status'] in ('filled', 'paper'):
                    exec_price = result.get('exec_price', bid)
                    realized_pnl = (size_shares * exec_price) - trade['size_usdc']
                    close_paper_trade(trade['id'], exec_price, realized_pnl, result.get('tx_hash'))
                    logger.info(f"✅ Position Closed. Realized PnL: ${realized_pnl:.2f}")

def check_take_profit_polling(poly_data, current_market_title):
    """Fallback take profit check via REST polling during main cycle."""
    open_trades = get_open_trades()
    for trade in open_trades:
        if trade['market_title'] == current_market_title:
            direction = trade['direction']
            entry_price = trade['entry_polymarket_price']
            
            current_price = poly_data.get('prob_up') if direction == 'UP' else poly_data.get('prob_down')
            
            if current_price:
                take_profit_target = entry_price * TAKE_PROFIT_MULTIPLIER
                if current_price >= take_profit_target:
                    with sell_lock:
                        if trade['id'] in sold_trades:
                            continue
                        sold_trades.add(trade['id'])

                    size_shares = trade['size_usdc'] / entry_price
                    logger.info(f"TAKE PROFIT (REST Polling) for {direction} on {current_market_title}!")
                    
                    result = live_trader.execute_market_trade(trade['token_id'], "SELL", size_shares, current_price)
                    
                    if result['status'] in ('filled', 'paper'):
                        exec_price = result.get('exec_price', current_price)
                        realized_pnl = (size_shares * exec_price) - trade['size_usdc']
                        close_paper_trade(trade['id'], exec_price, realized_pnl, result.get('tx_hash'))

def update_ws_subscriptions():
    """Ensure websocket is listening to all current open position tokens."""
    open_trades = get_open_trades()
    tokens = [t['token_id'] for t in open_trades if t.get('token_id')]
    market_ws.subscribe_to_tokens(tokens)

def run_loop():
    logger.info(f"Starting Trading Daemon (LIVE_MODE={Config.LIVE_MODE})...")
    init_db()
    
    # Start WebSocket Monitor
    market_ws.register_callback(on_ws_price_update)
    market_ws.start()
    
    # Subscribe to existing positions on startup
    update_ws_subscriptions()
    
    while True:
        try:
            logger.info("--- Waking up to evaluate market edge ---")
            result = evaluate_market_edge(alpha_up=1.5, alpha_down=1.5, floor_up=0.35, floor_down=0.35)
            
            if result:
                market_title = result['market_title']
                poly_data = result['poly_data']
                opportunities = result['valid_opportunities']
                
                # 1. Fallback REST Take Profit check
                check_take_profit_polling(poly_data, market_title)
                
                # 2. Check for New Opportunities
                open_trades = get_open_trades()
                
                for opp in opportunities:
                    direction = opp['direction']
                    market_entry = opp['market_entry']
                    model_prob = opp['model_prob']
                    token_id = opp.get('token_id')
                    
                    already_open = any(
                        t['market_title'] == market_title and t['direction'] == direction 
                        for t in open_trades
                    )
                    
                    if not already_open and token_id:
                        logger.info(f"Found valid edge! Buying {direction} for ${market_entry:.3f}")
                        
                        trade_res = live_trader.execute_market_trade(token_id, "BUY", TRADE_SIZE_USDC, market_entry)
                        
                        if trade_res['status'] in ('filled', 'paper'):
                            record_paper_trade(
                                market_title=market_title,
                                direction=direction,
                                entry_price=trade_res.get('exec_price', market_entry),
                                model_prob=model_prob,
                                size_usdc=TRADE_SIZE_USDC,
                                token_id=token_id,
                                tx_hash=trade_res.get('tx_hash')
                            )
                            # Instantly add to WS subscription to track price
                            update_ws_subscriptions()
                    elif not token_id:
                        logger.warning(f"Edge exists but no token_id found for {direction}.")
                    else:
                        logger.info(f"Edge exists for {direction}, but we already have an open position.")
            else:
                logger.info("No active market data retrieved. Waiting for next cycle.")
                
        except Exception as e:
            logger.error(f"Error in trading loop: {e}", exc_info=True)
            
        logger.info("Sleeping for 5 minutes...")
        time.sleep(300)

if __name__ == "__main__":
    run_loop()
