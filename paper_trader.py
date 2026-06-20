import time
import logging
import sys
import threading
from datetime import datetime, timezone

# EARLY LOGGER INITIALIZATION (per user rules)
from config import Config, logger

# Local imports
from strategy_runner import evaluate_market_edge
from db_manager import init_db, record_paper_trade, get_open_trades, get_closed_trades, close_paper_trade, update_peak_price, update_tp_order_id
from live_client import LiveTrader
from market_ws import MarketWebsocket
from scripts.polymarket_edge import _has_edge

# Trade parameter constants — aligned with video Instance 1271 configuration
TAKE_PROFIT_PCT = 0.30       # +30% TP (video Instance 1271)
STOP_LOSS_PCT = getattr(Config, 'STOP_LOSS_PCT', 0.40)  # -40% SL (video Instance 1271)
TRAIL_ACTIVATION_PCT = 0.20
TRAIL_DISTANCE_PCT = 0.15
ALLOW_CONCURRENT = False

REENTRY_COOLDOWN_MINUTES = 60
REENTRY_EDGE_MULTIPLIER = 1.5

# Video Instance 1271: alpha=2.5, floor=0.45 (symmetric UP/DOWN)
ALPHA_UP = 2.5
ALPHA_DOWN = 2.5
FLOOR_UP = 0.45
FLOOR_DOWN = 0.45

TRADE_SIZE_USDC = getattr(Config, 'MAX_USDC_PER_TRADE', 100.0)
MAX_ENTRY_PRICE = 0.99  # Never buy above 99¢ — no meaningful upside after fees

# Timing constants per timeframe
TIMEFRAME = getattr(Config, 'TIMEFRAME', 'daily')
SLEEP_SECONDS = 20 if TIMEFRAME == '15min' else 300  # 20s poll for 15min, 5min for daily

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
            stop_loss_target = entry_price * (1 - STOP_LOSS_PCT)
            
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
            elif bid <= stop_loss_target:
                exit_triggered = True
                exit_reason = "SL"
                exit_price = bid
                
            if exit_triggered:
                with sell_lock:
                    if trade['id'] in sold_trades:
                        continue
                    sold_trades.add(trade['id'])
                    
                logger.info(f"⚡ INSTANT EXIT via WebSocket ({exit_reason}) for {trade['direction']}!")
                logger.info(f"Entry: ${entry_price:.3f} | Exit: ${exit_price:.3f} | Bid: ${bid:.3f}")
                
                # Cancel open TP limit order if exists
                tp_order_id = trade.get('tp_order_id')
                if tp_order_id:
                    live_trader.cancel_order(tp_order_id)
                    update_tp_order_id(trade['id'], None)

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
                stop_loss_target = entry_price * (1 - STOP_LOSS_PCT)
                
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
                elif current_price <= stop_loss_target:
                    exit_triggered = True
                    exit_reason = "SL"
                    exit_price = current_price
                    
                if exit_triggered:
                    with sell_lock:
                        if trade['id'] in sold_trades:
                            continue
                        sold_trades.add(trade['id'])
                        
                    logger.info(f"EXIT (REST Polling) ({exit_reason}) for {direction} on {current_market_title}!")
                    
                    # Cancel open TP limit order if exists
                    tp_order_id = trade.get('tp_order_id')
                    if tp_order_id:
                        live_trader.cancel_order(tp_order_id)
                        update_tp_order_id(trade['id'], None)

                    size_shares = trade['size_usdc'] / entry_price
                    result = live_trader.execute_market_trade(trade['token_id'], "SELL", size_shares, exit_price)
                    
                    realized_pnl = (size_shares * exit_price) - trade['size_usdc']
                    close_paper_trade(trade['id'], exit_price, realized_pnl, result.get('tx_hash'), exit_reason)
                    logger.info(f"✅ Paper Position Closed. Realized PnL: ${realized_pnl:.2f}")

def resolve_expired_trades():
    """Query Binance for BTC price at expiry of any open trades that have expired and resolve them."""
    import re
    from scripts.polymarket_btc_markets import get_binance_price
    
    open_trades = get_open_trades()
    now = datetime.now(timezone.utc)
    
    for trade in open_trades:
        expiry_str = trade.get('expiry_timestamp')
        expired = False
        expiry_dt = None
        
        if expiry_str:
            try:
                expiry_dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                expired = now >= expiry_dt
            except Exception:
                pass
        else:
            # Fallback: parse date from market title (e.g. "Bitcoin Up or Down on June 11?")
            match = re.search(r'June (\d+)', trade.get('market_title', ''))
            if match:
                day = int(match.group(1))
                # Build an approximate expiry: 16:00 UTC on that day
                try:
                    expiry_dt = datetime(now.year, now.month, day, 16, 0, 0, tzinfo=timezone.utc)
                    expired = now >= expiry_dt
                except ValueError:
                    pass
        
        if not expired:
            continue
            
        logger.info(f"⏳ Trade {trade['id']} ({trade['market_title']}) has expired. Resolving...")
        
        # Fetch BTC price at expiry
        spot_price = None
        try:
            if expiry_dt:
                spot_price = get_binance_price(expiry_dt)
        except Exception as e:
            logger.error(f"Error fetching Binance price at expiry: {e}")
            
        if spot_price is None:
            # Fallback to current BTC price if specific candle is not available yet
            try:
                from scripts.polymarket_btc_markets import get_current_btc_price
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
            
            # Cancel open TP limit order if exists
            tp_order_id = trade.get('tp_order_id')
            if tp_order_id:
                live_trader.cancel_order(tp_order_id)
                update_tp_order_id(trade['id'], None)
            
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
            
            loop_start = time.time()
            result = evaluate_market_edge(
                alpha_up=ALPHA_UP, 
                alpha_down=ALPHA_DOWN, 
                floor_up=FLOOR_UP, 
                floor_down=FLOOR_DOWN,
                timeframe=TIMEFRAME
            )
            eval_latency = time.time() - loop_start
            
            if result:
                market_title = result['market_title']
                poly_data = result['poly_data']
                opportunities = result['valid_opportunities']
                
                # 1. Check REST polling exits
                check_open_trades_exits_polling(poly_data, market_title)
                
                # 2. Check for New Opportunities
                open_trades = get_open_trades()
                closed_trades = get_closed_trades()
                
                for opp in opportunities:
                    direction = opp['direction']
                    market_entry = opp['market_entry']
                    model_prob = opp['model_prob']
                    token_id = opp.get('token_id')
                    
                    same_direction_open = any(
                        t['market_title'] == market_title and t['direction'] == direction 
                        for t in open_trades
                    )
                    
                    if same_direction_open:
                        logger.info(f"Already hold an OPEN {direction} position for {market_title}. Skipping duplicate entry.")
                        continue
                        
                    last_closed = next((t for t in sorted(closed_trades, key=lambda x: x.get('closed_at') or '', reverse=True) 
                                        if t['market_title'] == market_title and t['direction'] == direction), None)
                    if last_closed:
                        closed_at_str = last_closed.get('closed_at')
                        if closed_at_str:
                            try:
                                closed_at_dt = datetime.strptime(closed_at_str, '%Y-%m-%d %H:%M:%S.%f')
                            except ValueError:
                                closed_at_dt = datetime.strptime(closed_at_str, '%Y-%m-%d %H:%M:%S')
                            
                            mins_since_closed = (datetime.utcnow() - closed_at_dt).total_seconds() / 60.0
                            prev_edge = last_closed['entry_model_prob'] - last_closed['entry_polymarket_price']
                            curr_edge = model_prob - market_entry
                            
                            if curr_edge >= prev_edge * REENTRY_EDGE_MULTIPLIER:
                                logger.info(f"🔥 OVERRIDE COOLDOWN: New edge ({curr_edge:.4f}) is >= {REENTRY_EDGE_MULTIPLIER}x stronger than previous edge ({prev_edge:.4f}). Re-entering!")
                            elif mins_since_closed < REENTRY_COOLDOWN_MINUTES:
                                logger.info(f"Cooldown active for {market_title} {direction}. Closed {mins_since_closed:.1f}m ago (requires {REENTRY_COOLDOWN_MINUTES}m). Skipping.")
                                continue
                            else:
                                logger.info(f"Cooldown expired for {market_title} {direction} ({mins_since_closed:.1f}m > {REENTRY_COOLDOWN_MINUTES}m). Allowing re-entry.")
                                
                    opposite_open = any(
                        t['market_title'] == market_title and t['direction'] != direction 
                        for t in open_trades
                    )
                        
                    if (ALLOW_CONCURRENT or not opposite_open) and token_id:
                        logger.info(f"[PREDICTION] Market: {market_title} | Side: {direction} | Polymarket Price: ${market_entry:.4f} | Model Prob: {model_prob:.4f} | Eval Latency: {eval_latency:.2f}s")
                        if market_entry > MAX_ENTRY_PRICE:
                            logger.info(f"Edge exists for {direction} but entry price ${market_entry:.4f} > cap ${MAX_ENTRY_PRICE}. Skipping.")
                            continue
                        
                        # NEW: Fetch live CLOB price to prevent buying on stale edge
                        if Config.LIVE_MODE:
                            live_ask = live_trader.get_live_price(token_id, "BUY")
                            if live_ask:
                                # Re-verify edge with live price
                                is_edge = False
                                if direction == 'UP':
                                    is_edge = _has_edge(model_prob, live_ask, ALPHA_UP, FLOOR_UP)
                                else:
                                    is_edge = _has_edge(model_prob, live_ask, ALPHA_DOWN, FLOOR_DOWN)
                                
                                if not is_edge:
                                    logger.info(f"Live orderbook price ${live_ask:.4f} eliminated edge (Model: {model_prob:.4f}). Skipping.")
                                    continue
                                
                                logger.info(f"Edge verified at live price! Gamma: ${market_entry:.4f} -> Live: ${live_ask:.4f}")
                                market_entry = live_ask
                                
                                if market_entry > MAX_ENTRY_PRICE:
                                    logger.info(f"Live entry price ${market_entry:.4f} > cap ${MAX_ENTRY_PRICE}. Skipping.")
                                    continue
                            else:
                                logger.warning("Could not fetch live price from orderbook. Aborting trade to avoid stale FAK.")
                                continue

                        logger.info(f"Found valid edge! Buying {direction} for ${market_entry:.3f}")
                        
                        trade_res = live_trader.execute_market_trade(token_id, "BUY", TRADE_SIZE_USDC, market_entry)
                        
                        if Config.LIVE_MODE and trade_res.get('status') == 'failed':
                            logger.error(f"Live trade failed! Not recording to database. Error: {trade_res.get('error')}")
                        else:
                            # Record trade to database
                            trade_id = record_paper_trade(
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
                            
                            # Execute Limit Sell Order for TP
                            trade_shares = TRADE_SIZE_USDC / market_entry
                            tp_price = min(0.99, market_entry + (market_entry * TAKE_PROFIT_PCT))
                            
                            # Only place a limit sell if the target price is strictly above our entry price
                            if tp_price > market_entry:
                                # Wait 3 seconds for Polymarket subgraph/indexer to update our token balance
                                if Config.LIVE_MODE:
                                    logger.info("Waiting 3 seconds for token balance indexer to update...")
                                    time.sleep(3.0)
                                    
                                tp_res = live_trader.execute_limit_order(token_id, "SELL", trade_shares, tp_price)
                                if tp_res.get('status') in ('placed', 'paper'):
                                    tp_order_id = tp_res.get('order_id')
                                    update_tp_order_id(trade_id, tp_order_id)
                            else:
                                logger.info(f"TP price ${tp_price:.4f} <= entry price ${market_entry:.4f}. Skipping TP limit order, will wait for resolution.")
                    elif not token_id:
                        logger.warning(f"Edge exists but no token_id found for {direction}.")
                    else:
                        logger.info(f"Edge exists for {direction}, but we already have an OPPOSITE open position (concurrent={ALLOW_CONCURRENT}).")
            else:
                logger.info("No active market data retrieved. Waiting for next cycle.")
                
        except Exception as e:
            logger.error(f"Error in trading loop: {e}", exc_info=True)
            
        logger.info(f"Sleeping for {SLEEP_SECONDS}s ({TIMEFRAME} mode)...")
        time.sleep(SLEEP_SECONDS)

if __name__ == "__main__":
    run_loop()
  
