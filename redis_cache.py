"""
redis_cache.py — Thin Redis wrapper for caching Polymarket market data.

All cache keys are namespaced under 'polybot:' to avoid collisions.
Gracefully degrades to no-cache if Redis is unavailable (returns None on get).
"""

import json
import os
from typing import Optional

try:
    # pyrefly: ignore [missing-import]
    import redis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

_client: Optional[object] = None
_REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
_REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
_REDIS_DB   = int(os.environ.get("REDIS_DB", "0"))


def _get_client():
    """Lazy-init Redis client, return None if unavailable."""
    global _client
    if _client is not None:
        return _client
    if not _REDIS_AVAILABLE:
        return None
    try:
        r = redis.Redis(host=_REDIS_HOST, port=_REDIS_PORT, db=_REDIS_DB, socket_timeout=1)
        r.ping()
        _client = r
        return _client
    except Exception:
        return None


def cache_set(key: str, value: dict, ttl_seconds: int = 60) -> bool:
    """Store a dict in Redis with TTL. Returns True on success."""
    r = _get_client()
    if r is None:
        return False
    try:
        r.setex(f"polybot:{key}", ttl_seconds, json.dumps(value))
        return True
    except Exception:
        return False


def cache_get(key: str) -> Optional[dict]:
    """Retrieve a dict from Redis. Returns None on miss or error."""
    r = _get_client()
    if r is None:
        return None
    try:
        raw = r.get(f"polybot:{key}")
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def cache_delete(key: str) -> bool:
    """Delete a key from Redis."""
    r = _get_client()
    if r is None:
        return False
    try:
        r.delete(f"polybot:{key}")
        return True
    except Exception:
        return False


def is_available() -> bool:
    """Check if Redis connection is alive."""
    return _get_client() is not None
