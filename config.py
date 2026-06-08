import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

class Config:
    # Feature Flags
    LIVE_MODE = os.environ.get('LIVE_MODE', 'False').lower() in ['true', '1', 't', 'y', 'yes']

    # Trading & Execution Keys
    TRADING_PRIVATE_KEY = os.environ.get('MY_WALLET_PRIVATE_KEY') or os.environ.get('POLY_PRIVATE_KEY')
    WALLET_ADDRESS = os.environ.get('MY_WALLET')
    FUNDER_ADDRESS = os.environ.get('FUNDER_ADDRESS')

    # Endpoints
    CLOB_HOST = os.environ.get('CLOB_HOST', 'https://clob.polymarket.com')
    WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    # Execution Settings
    MAX_USDC_PER_TRADE = float(os.environ.get('MAX_USDC_PER_TRADE', '5.0'))
    
    # Proxies (if required)
    SOCKS_PROXY = os.environ.get('SOCKS_PROXY')
    HTTPS_PROXY = os.environ.get('HTTPS_PROXY')
    HTTP_PROXY = os.environ.get('HTTP_PROXY')

def setup_logger(name='DeribitBot'):
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # Console Handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(formatter)
        logger.addHandler(ch)
        
        # File Handler
        try:
            os.makedirs('logs', exist_ok=True)
            fh = logging.FileHandler('logs/bot.log')
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except Exception:
            pass
            
    return logger

# Initialize global logger
logger = setup_logger()
