import time
import logging
import sys
import threading
from datetime import datetime, timezone

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
from db_manager import init_db, record_paper_trade, get_open_trades, close_paper_trade, update_peak_price
from live_client import LiveTrader
from market_ws import MarketWebsocket

# Trade parameter constants (robust backtest configuration)
TAKE_PROFIT_PCT = 0.30
TRAIL_ACTIVATION_PCT = 0.20
TRAIL_DISTANCE_PCT = 0.15
ALLOW_CONCURRENT = True

ALPHA_UP = 2.0
ALPHA_DOWN = 1.0
FLOOR_UP = 0.65
FLOOR_DOWN = 0.55

TRADE_SIZE_USDC = getattr(Config, 'MAX_USDC_PER_TRADE', 100.0)

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
            
            current_peak = trade.get('peak_price')
            if current_peak is None:
                current_peak = entry_price
            
            peak_price = max(current_peak, bid)
            if peak_price > (trade.get('peak_price') or 0.0):
                update_peak_price(trade['id'], peak_price)
                trade['peak_price'] = peak_price
                
            take_profit_target = entry_price * (1 + TAKE_PROFIT_PCT)
            activation_target = entry_price * (1 + TRAIL_ACTIVATION_PCT)
            trail_level = peak_price - entry_price * TRAIL_DISTANCE_PCT
            
            exit_triggered = False
            exit_reason = None
            exit_price = bid
            
            if bid >= take_profit_target:
                exit_triggered = True
                exit_reason = "TP"
                exit_price = take_profit_target
            elif peak_price >= activation_target and bid <= trail_level:
                exit_triggered = True
                exit_reason = "TRAIL"
                exit_price = trail_level
                
            if exit_triggered:
                with sell_lock:
                    if trade['id'] in sold_trades:
                        continue
                    sold_trades.add(trade['id'])
                    
                logger.info(f"⚡ INSTANT EXIT via WebSocket ({exit_reason}) for {trade['direction']}!")
                logger.info(f"Entry: ${entry_price:.3f} | Exit: ${exit_price:.3f} | Bid: ${bid:.3f}")
                
                # Execute Sell Live
                size_shares = trade['size_usdc'] / entry_price
                result = live_trader.execute_market_trade(token_id, "SELL", size_shares, exit_price)
                
                # Close Paper Trade perfectly
                realized_pnl = (size_shares * exit_price) - trade['size_usdc']
                close_paper_trade(trade['id'], exit_price, realized_pnl, result.get('tx_hash'), exit_reason)
                logger.info(f"✅ Paper Position Closed. Realized PnL: ${realized_pnl:.2f}")

def check_open_trades_exits_polling(poly_data, current_market_title):
    """Fallback exits check via REST polling during main cycle."""
    open_trades = get_open_trades()
    for trade in open_trades:
        if trade['market_title'] == current_market_title:
            direction = trade['direction']
            entry_price = trade['entry_polymarket_price']
            
            current_price = poly_data.get('prob_up') if direction == 'UP' else poly_data.get('prob_down')
            
            if current_price:
                current_peak = trade.get('peak_price')
                if current_peak is None:
                    current_peak = entry_price
                    
                peak_price = max(current_peak, current_price)
                if peak_price > (trade.get('peak_price') or 0.0):
                    update_peak_price(trade['id'], peak_price)
                    trade['peak_price'] = peak_price
                    
                take_profit_target = entry_price * (1 + TAKE_PROFIT_PCT)
                activation_target = entry_price * (1 + TRAIL_ACTIVATION_PCT)
                trail_level = peak_price - entry_price * TRAIL_DISTANCE_PCT
                
                exit_triggered = False
                exit_reason = None
                exit_price = current_price
                
                if current_price >= take_profit_target:
                    exit_triggered = True
                    exit_reason = "TP"
                    exit_price = take_profit_target
                elif peak_price >= activation_target and current_price <= trail_level:
                    exit_triggered = True
                    exit_reason = "TRAIL"
                    exit_price = trail_level
                    
                if exit_triggered:
                    with sell_lock:
                        if trade['id'] in sold_trades:
                            continue
                        sold_trades.add(trade['id'])
                        
                    logger.info(f"EXIT (REST Polling) ({exit_reason}) for {direction} on {current_market_title}!")
                    
                    size_shares = trade['size_usdc'] / entry_price
                    result = live_trader.execute_market_trade(trade['token_id'], "SELL", size_shares, exit_price)
                    
                    realized_pnl = (size_shares * exit_price) - trade['size_usdc']
                    close_paper_trade(trade['id'], exit_price, realized_pnl, result.get('tx_hash'), exit_reason)
                    logger.info(f"✅ Paper Position Closed. Realized PnL: ${realized_pnl:.2f}")

def resolve_expired_trades():
    """Query Binance for BTC price at expiry of any open trades that have expired and resolve them."""
    from scripts.polymarket_btc_daily import get_binance_price
    
    open_trades = get_open_trades()
    now = datetime.now(timezone.utc)
    
    for trade in open_trades:
        expiry_str = trade.get('expiry_timestamp')
        if not expiry_str:
            continue
            
        try:
            expiry_dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        except Exception:
            continue
            
        if now >= expiry_dt:
            logger.info(f"⏳ Trade {trade['id']} ({trade['market_title']}) has expired. Resolving...")
            
            # Fetch BTC price at expiry
            spot_price = None
            try:
                spot_price = get_binance_price(expiry_dt)
            except Exception as e:
                logger.error(f"Error fetching Binance price at expiry: {e}")
                
            if spot_price is None:
                # Fallback to current BTC price if specific candle is not available yet
                try:
                    from scripts.polymarket_btc_daily import get_current_btc_price
                    spot_price = get_current_btc_price()
                except Exception:
                    pass
                    
            if spot_price is not None:
                barrier = trade.get('barrier')
                direction = trade['direction']
                
                # Determine win/loss
                if direction == 'UP':
                    won = spot_price >= barrier
                else:
                    won = spot_price < barrier
                    
                exit_price = 1.0 if won else 0.0
                realized_pnl = (trade['size_usdc'] / trade['entry_polymarket_price']) * exit_price - trade['size_usdc']
                exit_reason = "WIN_EXPIRY" if won else "LOSS_EXPIRY"
                
                close_paper_trade(trade['id'], exit_price, realized_pnl, exit_reason=exit_reason)
                logger.info(f"Resolved expired trade {trade['id']}: Direction={direction}, Spot={spot_price:.2f}, Barrier={barrier:.2f}, Result={exit_reason}, PnL=${realized_pnl:.2f}")

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
            
            # Resolve any expired open trades first
            resolve_expired_trades()
            
            result = evaluate_market_edge(
                alpha_up=ALPHA_UP, alpha_down=ALPHA_DOWN,
                floor_up=FLOOR_UP, floor_down=FLOOR_DOWN
            )
            
            if result:
                market_title = result['market_title']
                poly_data = result['poly_data']
                opportunities = result['valid_opportunities']
                
                # 1. Check REST polling exits
                check_open_trades_exits_polling(poly_data, market_title)
                
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
                    
                    if (ALLOW_CONCURRENT or not already_open) and token_id:
                        logger.info(f"Found valid edge! Buying {direction} for ${market_entry:.3f}")
                        
                        trade_res = live_trader.execute_market_trade(token_id, "BUY", TRADE_SIZE_USDC, market_entry)
                        
                        # Always record paper trade to track theoretical edge
                        record_paper_trade(
                            market_title=market_title,
                            direction=direction,
                            entry_price=market_entry,
                            model_prob=model_prob,
                            size_usdc=TRADE_SIZE_USDC,
                            token_id=token_id,
                            tx_hash=trade_res.get('tx_hash'),
                            peak_price=market_entry,
                            barrier=poly_data.get('barrier'),
                            expiry_timestamp=poly_data.get('expiry_timestamp')
                        )
                        # Instantly add to WS subscription to track price
                        update_ws_subscriptions()
                    elif not token_id:
                        logger.warning(f"Edge exists but no token_id found for {direction}.")
                    else:
                        logger.info(f"Edge exists for {direction}, but we already have an open position (concurrent={ALLOW_CONCURRENT}).")
            else:
                logger.info("No active market data retrieved. Waiting for next cycle.")
                
        except Exception as e:
            logger.error(f"Error in trading loop: {e}", exc_info=True)
            
        logger.info("Sleeping for 5 minutes...")
        time.sleep(300)

if __name__ == "__main__":
    run_loop()
  
