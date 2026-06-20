import sys
import logging
from pathlib import Path

# Add the cloned strategy to Python path
STRATEGY_DIR = Path(__file__).parent
sys.path.insert(0, str(STRATEGY_DIR))

# Import logic from the cloned repo
try:
    from scripts.polymarket_edge import (
        run_polymarket_script, 
        run_terminal_script, 
        find_opportunities
    )
except ImportError as e:
    logging.error(f"Failed to import strategy logic: {e}")
    raise

from config import logger

# Redis cache — gracefully degrades to no-op if Redis is unavailable
try:
    from redis_cache import cache_get, cache_set, is_available as redis_available
except ImportError:
    def cache_get(k): return None
    def cache_set(k, v, ttl=60): return False
    def redis_available(): return False

# TTL for model calibration cache (seconds).
# 3 minutes is safe for 15min markets; daily markets barely move.
_MODEL_CACHE_TTL = 180


def _model_cache_key(barrier: float, hours_remaining: float) -> str:
    """Build a cache key from barrier (rounded to $50) and TTM (rounded to 2min)."""
    barrier_bucket = round(barrier / 50) * 50
    # Round hours_remaining to nearest 2 minutes (2/60 hours)
    ttm_bucket = round(hours_remaining * 30) / 30  # 1/30 h = 2 min
    return f"model_calib_{barrier_bucket}_{ttm_bucket:.4f}"


def evaluate_market_edge(alpha_up=1.5, alpha_down=1.5, floor_up=0.35, floor_down=0.35, timeframe="daily"):
    """
    Evaluates the active daily BTC market against Deribit options to find edges.
    Model calibration results are cached in Redis for 3 minutes to reduce latency
    from ~15s to ~5s on cache hits.
    Returns:
        EdgeResult with market details and decision.
    """
    logger.info(f"Fetching active Polymarket BTC data ({timeframe})...")
    poly_data = run_polymarket_script(verbose=False, timeframe=timeframe)
    
    if poly_data.get("barrier") is None:
        logger.warning("Could not parse Polymarket data or no active market found.")
        return None
        
    if poly_data.get("hours_remaining", 0) <= 0:
        logger.info("Active market has expired or no time remaining.")
        return None

    barrier = poly_data['barrier']
    hours_rem = poly_data['hours_remaining']

    # --- Model calibration with Redis cache ---
    cache_key = _model_cache_key(barrier, hours_rem)
    model_data = cache_get(cache_key)

    if model_data is not None:
        logger.info(f"[CACHE HIT] Model calibration loaded from Redis (barrier=${barrier:,.0f}, ttm={hours_rem:.2f}h). Skipping SSVI/MC run.")
    else:
        logger.info(f"Running model calibration for ${barrier:,.0f} with {hours_rem:.2f}h remaining...")
        model_data = run_terminal_script(barrier, hours_rem, verbose=False)

        if model_data.get("prob_above") is None or model_data.get("prob_below") is None:
            logger.error("Could not parse model probabilities.")
            return None

        # Store in Redis for next cycles
        if cache_set(cache_key, model_data, _MODEL_CACHE_TTL):
            logger.info(f"[CACHE SET] Model calibration cached for {_MODEL_CACHE_TTL}s (key={cache_key})")
        else:
            logger.debug("Redis unavailable — model calibration not cached.")

    logger.info("Finding opportunities based on configured edge curve...")
    opportunities = find_opportunities(
        poly_data, model_data,
        alpha_up=alpha_up, alpha_down=alpha_down,
        floor_up=floor_up, floor_down=floor_down
    )
    
    # Log the comparison so the user can monitor what's happening
    if poly_data.get("prob_up") is not None and model_data.get("prob_above") is not None:
        logger.info(f"[UP] Model: {model_data['prob_above']*100:.1f}% vs Poly: {poly_data['prob_up']*100:.1f}%")
    if poly_data.get("prob_down") is not None and model_data.get("prob_below") is not None:
        logger.info(f"[DOWN] Model: {model_data['prob_below']*100:.1f}% vs Poly: {poly_data['prob_down']*100:.1f}%")
    
    # Filter for only opportunities that have an edge
    valid_opportunities = [opp for opp in opportunities if opp.get("has_edge")]
    if not valid_opportunities:
        logger.info("No valid edges found on this cycle.")
    
    return {
        "market_title": poly_data.get("market_title", f"Bitcoin Up/Down on {barrier}"),
        "poly_data": poly_data,
        "model_data": model_data,
        "valid_opportunities": valid_opportunities
    }

