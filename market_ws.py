import json
import time
import threading
import websocket
from config import Config, logger

class MarketWebsocket:
    def __init__(self):
        self.ws_url = Config.WS_MARKET_URL
        self.running = False
        self.ws = None
        
        self.subscribed_tokens = set()
        self.live_prices = {} # token_id -> {'bid': float, 'ask': float, 'ts': float}
        self.callbacks = []

    def start(self):
        if self.running:
            return
        logger.info(f"Starting Market WebSocket Monitor: {self.ws_url}")
        self.running = True
        threading.Thread(target=self._run_loop, daemon=True).start()

    def stop(self):
        self.running = False
        if self.ws:
            self.ws.close()

    def subscribe_to_tokens(self, token_ids: list):
        """Update subscriptions. token_ids should be the currently open positions."""
        if not self.ws or not self.ws.sock or not self.ws.sock.connected:
            return
            
        new_tokens = set(token_ids) - self.subscribed_tokens
        removed_tokens = self.subscribed_tokens - set(token_ids)
        
        if new_tokens:
            msg = {
                "assets_ids": list(new_tokens),
                "operation": "subscribe",
                "custom_feature_enabled": True
            }
            self.ws.send(json.dumps(msg))
            self.subscribed_tokens.update(new_tokens)
            logger.info(f"WS Subscribed to new tokens: {new_tokens}")
            
        if removed_tokens:
            msg = {
                "assets_ids": list(removed_tokens),
                "operation": "unsubscribe"
            }
            self.ws.send(json.dumps(msg))
            self.subscribed_tokens.difference_update(removed_tokens)
            logger.info(f"WS Unsubscribed from tokens: {removed_tokens}")
            # cleanup price cache
            for t in removed_tokens:
                self.live_prices.pop(t, None)

    def register_callback(self, callback_fn):
        """Register a callback fn(token_id, bid, ask) triggered on price change."""
        self.callbacks.append(callback_fn)

    def _run_loop(self):
        reconnect_attempts = 0
        proxy_kwargs = {}
        
        if Config.SOCKS_PROXY:
            from urllib.parse import urlparse
            p = urlparse(Config.SOCKS_PROXY)
            proxy_kwargs['proxy_type'] = 'socks5'
            proxy_kwargs['http_proxy_host'] = p.hostname
            proxy_kwargs['http_proxy_port'] = p.port or 1080
            if p.username and p.password:
                proxy_kwargs['http_proxy_auth'] = (p.username, p.password)
                
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
                self.ws.run_forever(ping_interval=10, ping_timeout=5, **proxy_kwargs)
            except Exception as e:
                logger.warning(f"Market WS thread exception: {e}")
                
            if self.running:
                wait_time = min(30, 2 ** reconnect_attempts)
                logger.info(f"Market WS reconnecting in {wait_time}s...")
                time.sleep(wait_time)
                reconnect_attempts += 1

    def _on_open(self, ws):
        logger.info("Market WebSocket connected.")
        # Subscribing to an empty array to establish the custom connection schema
        msg = {
            "assets_ids": list(self.subscribed_tokens),
            "type": "market",
            "custom_feature_enabled": True
        }
        ws.send(json.dumps(msg))
        
    def _on_message(self, ws, message):
        if message.strip().upper() == 'PONG':
            return
            
        try:
            data = json.loads(message)
            event_type = data.get('event_type')
            
            if event_type == 'best_bid_ask':
                self._handle_price(data.get('asset_id'), data.get('best_bid'), data.get('best_ask'))
            elif event_type == 'price_change':
                for pc in data.get('price_changes', []):
                    self._handle_price(pc.get('asset_id'), pc.get('best_bid'), pc.get('best_ask'))
        except Exception:
            pass
            
    def _handle_price(self, asset_id, bid, ask):
        if not asset_id or bid is None:
            return
            
        try:
            bid_val = float(bid)
            ask_val = float(ask) if ask is not None else 1.0
            
            self.live_prices[asset_id] = {
                'bid': bid_val,
                'ask': ask_val,
                'ts': time.time()
            }
            
            # Fire callbacks
            for cb in self.callbacks:
                try:
                    cb(asset_id, bid_val, ask_val)
                except Exception as e:
                    logger.debug(f"WS Callback error: {e}")
                    
        except ValueError:
            pass

    def _on_error(self, ws, error):
        logger.debug(f"Market WS Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logger.info("Market WS Closed.")
        self.subscribed_tokens.clear()
