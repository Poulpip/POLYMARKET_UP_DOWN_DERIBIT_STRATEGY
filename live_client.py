import os
import time
from config import Config, logger

try:
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import MarketOrderArgsV2, PartialCreateOrderOptions, OrderArgs
    from py_clob_client_v2.exceptions import PolyApiException
    from py_clob_client_v2.order_builder.constants import BUY, SELL
except ImportError as e:
    logger.error(f"CLOB SDK import failed: {e}")
    ClobClient = None

class LiveTrader:
    def __init__(self):
        self.client = None
        self._init_clob_client()
        
    def _init_clob_client(self):
        if not Config.LIVE_MODE:
            logger.info("LIVE_MODE is False. Skipping live ClobClient initialization.")
            return
            
        try:
            private_key = Config.TRADING_PRIVATE_KEY
            if not private_key:
                raise ValueError("TRADING_PRIVATE_KEY not set in .env")

            signature_type = 2 # Gnosis safe signature
            funder = Config.FUNDER_ADDRESS
            
            logger.info("Initializing Live ClobClient...")
            self.client = ClobClient(
                host=Config.CLOB_HOST,
                chain_id=137,
                key=private_key,
                signature_type=signature_type,
                funder=funder,
                use_server_time=True
            )
            
            try:
                creds = self.client.derive_api_key()
                self.client.set_api_creds(creds)
                logger.info(f"✅ Live ClobClient initialized successfully")
            except Exception as e:
                logger.warning(f"Could not derive API key: {e}. Trying to create...")
                creds = self.client.create_or_derive_api_key()
                self.client.set_api_creds(creds)
                logger.info(f"✅ Live ClobClient created new API key.")
                
        except Exception as e:
            logger.error(f"❌ Failed to initialize ClobClient: {e}")
            self.client = None

    def execute_market_trade(self, token_id: str, side: str, size: float, price: float, min_order_size: float = 0.1):
        if not Config.LIVE_MODE or self.client is None:
            logger.info(f"[PAPER_TRADE] -> execute_market_trade: {side} {size} units of {token_id} at {price}")
            return {'status': 'paper', 'tx_hash': '', 'exec_price': price, 'exec_size': size}

        start = time.time()
        try:
            order_side = BUY if side.upper() == 'BUY' else SELL
            
            is_limit_order = False
            safe_price = round(min(0.99, max(0.01, float(price))), 4)
            
            if side.upper() == 'BUY':
                # The old bot treated size as USDC amount for BUY orders
                usdc_amount = float(size)
                if usdc_amount > Config.MAX_USDC_PER_TRADE:
                    usdc_amount = Config.MAX_USDC_PER_TRADE
                    
                if usdc_amount < 1.0:
                    is_limit_order = True
                    target_shares = usdc_amount / safe_price
                    raw_size = round(target_shares, 4)
                    safe_size = max(raw_size, float(min_order_size)) if min_order_size else raw_size
                    api_amount = safe_size
                else:
                    api_amount = round(usdc_amount, 4)
                    safe_size = round(api_amount / safe_price, 4)
            else:
                # SELL -> amount is in shares
                api_amount = round(float(size), 4)
                safe_size = api_amount

            order_options = PartialCreateOrderOptions(tick_size='0.01', neg_risk=False)
            
            logger.info(f"Placing live order: token={token_id[:16]} side={side} price={safe_price} "
                        f"amount={api_amount} (limit={is_limit_order})")

            if is_limit_order:
                result = self.client.create_and_post_order(
                    OrderArgs(token_id=token_id, price=safe_price, size=safe_size, side=order_side),
                    options=order_options
                )
            else:
                result = self.client.create_and_post_market_order(
                    MarketOrderArgsV2(token_id=token_id, amount=api_amount, side=order_side, price=safe_price),
                    options=order_options,
                    order_type='FAK'
                )

            latency = (time.time() - start) * 1000
            
            result_status = result.get('status', '')
            if result.get('success') or result_status in ('filled', 'matched'):
                status = 'filled'
                tx_hash = result.get('txHash') or result.get('transactionHash', '')
                if not tx_hash and isinstance(result.get('transactionsHashes'), list):
                    tx_hash = result['transactionsHashes'][0] if result['transactionsHashes'] else ''
                logger.info(f"✅ Order FILLED! TX: {tx_hash} ({latency:.1f}ms)")
            else:
                status = 'failed'
                tx_hash = ''
                logger.error(f"❌ Order failed: {result.get('error', result)}")

            return {
                'status': status,
                'tx_hash': tx_hash,
                'latency_ms': latency,
                'exec_price': result.get('price', safe_price),
                'exec_size': result.get('size', safe_size)
            }
        except PolyApiException as e:
            logger.error(f"Poly API error: {e}")
            return {'status': 'failed', 'error': str(e), 'latency_ms': (time.time()-start)*1000}
        except Exception as e:
            logger.error(f"Order failed: {e}")
            return {'status': 'failed', 'error': str(e), 'latency_ms': (time.time()-start)*1000}
