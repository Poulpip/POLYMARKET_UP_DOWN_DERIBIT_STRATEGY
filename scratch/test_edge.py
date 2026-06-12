from strategy_runner import evaluate_market_edge
from config import logger
import json

if __name__ == "__main__":
    result = evaluate_market_edge()
    if result:
        print("Opportunities:")
        print(json.dumps(result['valid_opportunities'], indent=2))
        
        print("\nAll parsed data:")
        for opp in result['model_data'].get('opportunities', []):
            print(f"Side: {opp['direction']}, Entry: {opp['market_entry']}, Model Prob: {opp['model_prob']}, Has Edge: {opp['has_edge']}")
    else:
        print("Result was None.")
