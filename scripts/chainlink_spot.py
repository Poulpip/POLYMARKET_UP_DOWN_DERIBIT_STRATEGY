import requests
import logging
import time
import sys
import os

logger = logging.getLogger(__name__)

# Chainlink BTC/USD Aggregator on Polygon
CHAINLINK_BTC_USD_POLYGON = '0xc907E116054Ad103354f2D350FD2514433D57F6f'
# Keccak256("latestRoundData()")[:4]
METHOD_SIG = '0xfeaf968c'

RPC_NODES = [
    'https://polygon.publicnode.com',
    'https://polygon-public.nodies.app',
    'https://1rpc.io/matic',
    'https://polygon.api.onfinality.io/public',
    'https://polygon.drpc.org'
]

# Redis cache — gracefully degrades to no-op if Redis is unavailable
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from redis_cache import cache_get, cache_set
except ImportError:
    def cache_get(k): return None
    def cache_set(k, v, ttl=10): return False

# Chainlink BTC/USD feed on Polygon updates every ~27 seconds.
# 10s TTL keeps us fresh without re-hitting the RPC on every 60s bot cycle.
_CHAINLINK_CACHE_TTL = 10
_CHAINLINK_CACHE_KEY = "chainlink_btc_usd"


def get_chainlink_btc_price() -> float:
    """
    Fetches the latest BTC/USD price from the Chainlink oracle on Polygon.
    This is the exact oracle Polymarket uses to resolve 15-minute BTC markets.

    - Redis-cached for 10s to avoid repeated RPC calls on fast bot cycles.
    - Hard 8s global timeout via ThreadPoolExecutor so dead RPC nodes never hang the bot.
    """
    cached = cache_get(_CHAINLINK_CACHE_KEY)
    if cached is not None:
        return cached["price"]

    data = {
        'jsonrpc': '2.0',
        'method': 'eth_call',
        'params': [{'to': CHAINLINK_BTC_USD_POLYGON, 'data': METHOD_SIG}, 'latest'],
        'id': 1
    }

    def _try_fetch():
        for rpc_url in RPC_NODES:
            try:
                r = requests.post(rpc_url, json=data, timeout=(2, 4))
                r.raise_for_status()
                res = r.json()
                if 'result' in res and res['result'] and res['result'] != '0x':
                    hex_ans = res['result'][2+64:2+128]
                    return int(hex_ans, 16) / 10**8
            except Exception as e:
                logger.debug(f"Chainlink RPC {rpc_url} failed: {e}")
                continue
        return None

    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_try_fetch)
        try:
            price = future.result(timeout=8)
        except (FutureTimeout, Exception) as e:
            logger.debug(f"Chainlink fetch timed out or failed: {e}")
            price = None

    if price is not None:
        cache_set(_CHAINLINK_CACHE_KEY, {"price": price}, _CHAINLINK_CACHE_TTL)
        return price

    raise RuntimeError("Failed to fetch Chainlink BTC/USD price from all Polygon RPC nodes")



if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    print(f"Chainlink BTC/USD Price: ${get_chainlink_btc_price():.2f}")

