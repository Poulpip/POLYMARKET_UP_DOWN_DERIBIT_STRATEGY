#!/usr/bin/env python3
"""Fetch closest Bitcoin Up/Down daily market from Polymarket."""

import argparse
import json
import re
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import sys
import os

# Redis cache — gracefully degrades to no-op if Redis is unavailable
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from redis_cache import cache_get, cache_set, is_available as redis_available
except ImportError:
    def cache_get(k): return None
    def cache_set(k, v, ttl=60): return False
    def redis_available(): return False

# Add parent directory to path to import other scripts if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from scripts.chainlink_spot import get_chainlink_btc_price
except ImportError:
    def get_chainlink_btc_price(): return None

GAMMA_URL = "https://gamma-api.polymarket.com"
BINANCE_URL = "https://api.binance.com/api/v3"


def _parse_all_reference_times(description):
    """Extract all ET date/time references from the market description.

    Matches both formats:
      - "Feb 5 '26 12:00 in the ET timezone" (barrier/reference time)
      - "Feb 6 '26 12:00 ET" (resolution time)

    Returns list of UTC datetimes in order of appearance.
    """
    pattern = r"(\w{3}) (\d{1,2}) '(\d{2}) (\d{1,2}):(\d{2}) (?:in the )?ET"
    matches = re.findall(pattern, description)

    months = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
    }

    results = []
    et_tz = ZoneInfo("America/New_York")
    for month_str, day, year, hour, minute in matches:
        month = months.get(month_str)
        if not month:
            continue
        year_full = 2000 + int(year)
        et_dt = datetime(year_full, month, int(day), int(hour), int(minute), tzinfo=et_tz)
        results.append(et_dt.astimezone(timezone.utc))

    return results


def parse_reference_time(description):
    """Extract the barrier/reference candle time (first date) from the description."""
    times = _parse_all_reference_times(description)
    return times[0] if times else None


def parse_title_reference_time(title):
    """Extract the barrier/reference time from titles like 'Bitcoin Up or Down - June 19, 11:00AM-11:05AM ET'."""
    # Match: "June 19, 11:00AM" or "Jun 19, 11:00AM"
    pattern = r"([a-zA-Z]+)\s+(\d{1,2}),\s+(\d{1,2}):(\d{2})(AM|PM)"
    match = re.search(pattern, title)
    if not match:
        return None
    
    month_str, day, hour, minute, ampm = match.groups()
    months = {
        "January": 1, "Jan": 1, "February": 2, "Feb": 2, "March": 3, "Mar": 3,
        "April": 4, "Apr": 4, "May": 5, "June": 6, "Jun": 6,
        "July": 7, "Jul": 7, "August": 8, "Aug": 8, "September": 9, "Sep": 9,
        "October": 10, "Oct": 10, "November": 11, "Nov": 11, "December": 12, "Dec": 12
    }
    month = months.get(month_str)
    if not month:
        return None
    
    h = int(hour)
    if ampm == "PM" and h != 12:
        h += 12
    elif ampm == "AM" and h == 12:
        h = 0
        
    now = datetime.now(timezone.utc)
    et_tz = ZoneInfo("America/New_York")
    et_dt = datetime(now.year, month, int(day), h, int(minute), tzinfo=et_tz)
    
    return et_dt.astimezone(timezone.utc)


def parse_resolution_time(description):
    """Extract the resolution candle time (second date) from the description."""
    times = _parse_all_reference_times(description)
    return times[1] if len(times) >= 2 else times[0] if times else None


def get_binance_price(timestamp_utc):
    """Fetch BTC/USDT close price for a specific 1-minute candle from Binance.
    
    Result is cached in Redis for 30 seconds — the barrier candle price does not
    change once the candle is closed, so this is always safe to cache.
    """
    cache_key = f"binance_kline_{int(timestamp_utc.timestamp())}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached.get("price")

    url = f"{BINANCE_URL}/klines"
    start_time_ms = int(timestamp_utc.timestamp() * 1000)

    params = {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "startTime": start_time_ms,
        "limit": 1
    }

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    if data:
        # Kline format: [open_time, open, high, low, close, volume, ...]
        price = float(data[0][4])
        cache_set(cache_key, {"price": price}, 30)
        return price
    return None


def get_current_btc_price():
    """Fetch current BTC/USDT price from Binance.
    
    Cached in Redis for 10 seconds to avoid hammering Binance on rapid cycles.
    """
    cached = cache_get("binance_spot")
    if cached is not None:
        return cached.get("price")

    url = f"{BINANCE_URL}/ticker/price"
    params = {"symbol": "BTCUSDT"}

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    price = float(data.get("price", 0))
    cache_set("binance_spot", {"price": price}, 10)
    return price


def search_btc_daily_markets(timeframe="daily"):
    """Search for Bitcoin Up or Down markets (today or upcoming).
    
    For 15min timeframe: uses end_date_min/max window (the ONLY reliable way
    to discover these short-lived markets via the Gamma API).
    For daily timeframe: wide 48-hour window covering today and tomorrow.
    Results are cached in Redis to avoid hammering the API every 60s.
    """
    now = datetime.now(timezone.utc)
    
    if timeframe == "15min":
        # 15-min markets expire within the next 15-30 minutes.
        # Search window: last 2 minutes to next 30 minutes to catch the current one.
        end_date_min = (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_date_max = (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Cache key is per-minute so expired markets aren't served from cache
        cache_key = f"btc_15min_{now.strftime('%Y%m%d_%H%M')}"
        cache_ttl = 20  # 20-second TTL — short so we always get fresh data
    else:
        # Daily: cover today + tomorrow
        end_date_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_date_max = (now + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache_key = f"btc_daily_{now.strftime('%Y%m%d_%H')}"
        cache_ttl = 300  # 5-minute cache for daily markets

    # Try Redis cache first
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    # Fetch from Gamma API using date-range filter with pagination
    url = f"{GAMMA_URL}/events"
    params = {
        "active": "true",
        "closed": "false",
        "limit": 100,
        "offset": 0,
        "end_date_min": end_date_min,
        "end_date_max": end_date_max,
    }
    
    all_data = []
    while True:
        try:
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list) or not data:
                break
            
            all_data.extend(data)
            
            # If we received fewer items than the limit, we've reached the end
            if len(data) < params["limit"]:
                break
                
            params["offset"] += params["limit"]
        except Exception as e:
            # Polymarket API returns 422 when offset is too large (e.g. >2000)
            break

    # Filter for BTC events only
    btc_items = [
        e for e in all_data
        if 'bitcoin' in str(e.get('title', '')).lower()
        or 'btc' in str(e.get('title', '')).lower()
    ]

    # Store in Redis cache
    cache_set(cache_key, btc_items, cache_ttl)
    return btc_items


def find_closest_active_market(data, timeframe="daily"):
    """Filter for closest non-expired market matching the timeframe pattern."""
    now = datetime.now(timezone.utc)

    if timeframe == "15min":
        # 15min markets: title format "Bitcoin Up or Down - June 19, 7:50AM-7:55AM ET"
        # Multi-hour formats like "4:00AM-8:00AM" must be excluded.
        # We match anything with HH:MM time-range and then validate the span is ≤20 min.
        patterns = [
            re.compile(r"Bitcoin Up or Down - \w+ \d+, \d{1,2}:\d{2}(?:AM|PM)", re.IGNORECASE),
            re.compile(r"Bitcoin 15.?Min\w* Up or Down", re.IGNORECASE),
            re.compile(r"BTC Up or Down 15m", re.IGNORECASE),
        ]
        # Exclude: pure hourly format like "7AM ET" (no minutes) 
        hourly_exclude = re.compile(
            r"Bitcoin Up or Down - \w+ \d+, \d{1,2}(?:AM|PM) ET$", re.IGNORECASE
        )
        # Regex to extract start and end times from range titles
        time_range_re = re.compile(
            r"(\d{1,2}):(\d{2})(AM|PM)-(\d{1,2}):(\d{2})(AM|PM)", re.IGNORECASE
        )
    else:
        # Daily: matches "Bitcoin Up or Down on January 28?" or "Bitcoin Up or Down - Jan 28, 8AM ET"
        # Excludes 15min time-range titles
        patterns = [
            re.compile(r"Bitcoin Up or Down on \w+ \d+\??", re.IGNORECASE),
            re.compile(r"BTC Up or Down on \w+ \d+\??", re.IGNORECASE),
        ]
        hourly_exclude = None
        time_range_re = None

    # The API returns events in a dict with 'events' key, or a list directly
    events = data if isinstance(data, list) else data.get("events", [])

    candidates = []
    for event in events:
        title = event.get("title", "")

        # Check if title matches any of the patterns
        if not any(p.search(title) for p in patterns):
            continue

        # For 15min mode: exclude pure hourly markets (no minutes in time)
        if timeframe == "15min" and hourly_exclude and hourly_exclude.search(title):
            continue

        # For 15min mode: verify the time span is ≤ 20 minutes (reject 4h bracket markets)
        if timeframe == "15min" and time_range_re:
            m = time_range_re.search(title)
            if m:
                h1, m1, ap1, h2, m2, ap2 = m.groups()
                def to_min(h, mn, ap):
                    h = int(h) % 12
                    if ap.upper() == 'PM': h += 12
                    return h * 60 + int(mn)
                start_min = to_min(h1, m1, ap1)
                end_min = to_min(h2, m2, ap2)
                span = (end_min - start_min) % (24 * 60)
                if not (10 <= span <= 20):  # reject 5-min markets and multi-hour brackets
                    continue

        # Skip closed markets
        if event.get("closed", False):
            continue


        # Parse end date
        end_date_str = event.get("endDate")
        if not end_date_str:
            continue

        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        # Skip expired markets or markets too close to expiry (e.g. less than 5 minutes for 15min mode)
        min_remaining_seconds = 300 if timeframe == "15min" else 0
        if (end_date - now).total_seconds() <= min_remaining_seconds:
            continue

        candidates.append((end_date, event))

    if not candidates:
        return None

    # Sort by end date ascending to get the closest one
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def market_to_dict(event):
    """Extract market data into a dictionary for JSON output.

    Args:
        event: Polymarket event data

    Returns:
        Dictionary with structured market data, or None if parsing fails
    """
    title = event.get("title", "Unknown Market")
    description = event.get("description", "")
    end_date_str = event.get("endDate")

    result = {
        "market_title": title,
        "market_id": None,
        "barrier": None,
        "current_price": None,
        "hours_remaining": None,
        "hours": None,
        "minutes": None,
        "prob_up": None,
        "prob_down": None,
        "token_up": None,
        "token_down": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "expiry_timestamp": end_date_str,
    }

    # Calculate time remaining
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            remaining = end_date - now
            total_seconds = int(remaining.total_seconds())

            if total_seconds > 0:
                hours, remainder = divmod(total_seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                result["hours"] = hours
                result["minutes"] = minutes
                result["hours_remaining"] = hours + minutes / 60
        except ValueError:
            pass

    # Get reference price (barrier) from Binance
    ref_time = parse_reference_time(description)
    if not ref_time:
        ref_time = parse_title_reference_time(title)

    if ref_time:
        try:
            ref_price = get_binance_price(ref_time)
            
            # Use Chainlink for spot with Binance fallback
            current_price = None
            try:
                current_price = get_chainlink_btc_price()
                if current_price is None:
                    raise ValueError("Chainlink returned None")
            except Exception:
                current_price = get_current_btc_price()
                
            if ref_price:
                result["barrier"] = ref_price
            if current_price:
                result["current_price"] = current_price
        except Exception:
            pass
    else:
        # 15min market: no explicit reference time in description.
        # Barrier = current BTC spot price (market resolves on price change from open).
        try:
            current_price = get_chainlink_btc_price()
            if current_price is None:
                raise ValueError("Chainlink returned None")
        except Exception:
            try:
                current_price = get_current_btc_price()
            except Exception:
                current_price = None
        if current_price:
            result["barrier"] = current_price
            result["current_price"] = current_price

    # Extract market data from the markets array
    markets = event.get("markets", [])
    if not markets:
        return result

    market = markets[0]
    result["market_id"] = market.get("id")
    outcomes = market.get("outcomes", [])
    outcome_prices = market.get("outcomePrices", [])

    # API sometimes returns JSON strings instead of lists
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except json.JSONDecodeError:
            outcomes = ["Up", "Down"]

    if isinstance(outcome_prices, str):
        try:
            outcome_prices = json.loads(outcome_prices)
        except json.JSONDecodeError:
            outcome_prices = []

    clob_tokens = market.get("clobTokenIds", [])
    if isinstance(clob_tokens, str):
        try:
            clob_tokens = json.loads(clob_tokens)
        except json.JSONDecodeError:
            clob_tokens = []

    # Extract probabilities and token IDs
    for i, outcome in enumerate(outcomes):
        if i < len(outcome_prices):
            try:
                price = float(outcome_prices[i])
                outcome_lower = outcome.lower()
                token_id = clob_tokens[i] if i < len(clob_tokens) else None
                if outcome_lower == "up":
                    result["prob_up"] = price
                    result["token_up"] = token_id
                elif outcome_lower == "down":
                    result["prob_down"] = price
                    result["token_down"] = token_id
            except (ValueError, TypeError):
                pass

    return result


def display_market_info(event):
    """Display probabilities and prices.

    Returns:
        Dictionary with market data for JSON output
    """
    title = event.get("title", "Unknown Market")
    description = event.get("description", "")
    end_date_str = event.get("endDate")

    print(title)

    # Calculate time remaining and show expiry times
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            remaining = end_date - now

            # Display expiry time in ET, UTC, and Paris
            end_utc_str = end_date.strftime("%H:%M UTC")
            # ET is UTC-5 (EST) or UTC-4 (EDT) - using EST for winter
            end_et = end_date.replace(tzinfo=None) - timedelta(hours=5)
            end_et_str = end_et.strftime("%H:%M ET")
            # Paris is UTC+1 (CET) in winter
            end_paris = end_date.replace(tzinfo=None) + timedelta(hours=1)
            end_paris_str = end_paris.strftime("%H:%M Paris")
            print(f"Expires: {end_et_str} / {end_utc_str} / {end_paris_str}")

            total_seconds = int(remaining.total_seconds())
            if total_seconds > 0:
                hours, remainder = divmod(total_seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                print(f"Time remaining: {hours}h {minutes}m")
            else:
                print("Market has expired")
        except ValueError:
            pass

    print("-" * 40)

    # Get reference price from Chainlink (with Binance fallback)
    ref_time = parse_reference_time(description)
    if ref_time:
        try:
            ref_price = get_binance_price(ref_time)
            
            # Fetch Chainlink price for current spot
            current_price = None
            try:
                current_price = get_chainlink_btc_price()
                if current_price is None:
                    raise ValueError("Chainlink returned None")
                print("Price source: Chainlink Oracle")
            except Exception as e:
                # Fallback to Binance
                current_price = get_current_btc_price()
                print("Price source: Binance (Chainlink failed)")
                
            if ref_price:
                print(f"Price to beat: ${ref_price:,.2f}")
            if current_price:
                print(f"Current price: ${current_price:,.2f}")
                if ref_price:
                    diff = current_price - ref_price
                    pct = (diff / ref_price) * 100
                    direction = "above" if diff >= 0 else "below"
                    print(f"Difference:    {direction} by ${abs(diff):,.2f} ({pct:+.2f}%)")
            print("-" * 40)
        except Exception:
            pass

    # Extract market data from the markets array
    markets = event.get("markets", [])

    if not markets:
        print("No market data available")
        return market_to_dict(event)

    # Usually there's one market per event for these binary markets
    market = markets[0]
    outcomes = market.get("outcomes", [])
    outcome_prices = market.get("outcomePrices", [])

    # API sometimes returns JSON strings instead of lists
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except json.JSONDecodeError:
            outcomes = ["Up", "Down"]

    if isinstance(outcome_prices, str):
        try:
            outcome_prices = json.loads(outcome_prices)
        except json.JSONDecodeError:
            outcome_prices = []

    # Display each outcome
    for i, outcome in enumerate(outcomes):
        if i < len(outcome_prices):
            try:
                price = float(outcome_prices[i])
                probability = price * 100
                outcome_upper = outcome.upper()
                print(f"{outcome_upper:5} {probability:5.1f}% (${price:.3f})")
            except (ValueError, TypeError):
                print(f"{outcome}: Price unavailable")
        else:
            print(f"{outcome}: Price unavailable")

    return market_to_dict(event)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fetch closest Bitcoin Up/Down market from Polymarket"
    )
    parser.add_argument(
        "--json",
        type=Path,
        metavar="PATH",
        help="Output JSON file path for structured data"
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default="daily",
        choices=["daily", "15min"],
        help="Target timeframe: daily or 15min"
    )
    args = parser.parse_args()

    try:
        print(f"Fetching Bitcoin Up/Down {args.timeframe} markets from Polymarket...\n")
        data = search_btc_daily_markets(timeframe=args.timeframe)
        closest = find_closest_active_market(data, timeframe=args.timeframe)

        if closest:
            market_data = display_market_info(closest)

            # Write JSON output if requested
            if args.json and market_data:
                args.json.parent.mkdir(parents=True, exist_ok=True)
                with open(args.json, "w") as f:
                    json.dump(market_data, f, indent=2)
                print(f"\nJSON data written to {args.json}")
        else:
            print(f"No active Bitcoin Up/Down {args.timeframe} market found")

    except requests.RequestException as e:
        print(f"Error fetching data: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
