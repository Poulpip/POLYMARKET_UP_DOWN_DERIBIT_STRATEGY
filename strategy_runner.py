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

logger = logging.getLogger(__name__)

def evaluate_market_edge(alpha_up=1.5, alpha_down=1.5, floor_up=0.35, floor_down=0.35):
    """
    Evaluates the active daily BTC market against Deribit options to find edges.
    Returns:
        dict: Containing 'poly_data', 'model_data', and a list of 'opportunities'.
    """
    logger.info("Fetching active Polymarket Daily BTC data...")
    poly_data = run_polymarket_script(verbose=False)
    
    if poly_data.get("barrier") is None:
        logger.warning("Could not parse Polymarket data or no active market found.")
        return None
        
    if poly_data.get("hours_remaining", 0) <= 0:
        logger.info("Active market has expired or no time remaining.")
        return None

    barrier = poly_data['barrier']
    hours_rem = poly_data['hours_remaining']
    
    logger.info(f"Running model calibration for ${barrier:,.0f} with {hours_rem:.2f}h remaining...")
    model_data = run_terminal_script(barrier, hours_rem, verbose=False)
    
    if model_data.get("prob_above") is None or model_data.get("prob_below") is None:
        logger.error("Could not parse model probabilities.")
        return None

    logger.info("Finding opportunities based on configured edge curve...")
    opportunities = find_opportunities(
        poly_data, model_data,
        alpha_up=alpha_up, alpha_down=alpha_down,
        floor_up=floor_up, floor_down=floor_down
    )
    
    # Filter for only opportunities that have an edge
    valid_opportunities = [opp for opp in opportunities if opp.get("has_edge")]
    
    return {
        "market_title": poly_data.get("market_title", f"Bitcoin Up/Down on {barrier}"), # We'll need the title
        "poly_data": poly_data,
        "model_data": model_data,
        "valid_opportunities": valid_opportunities
    }
