"""Optional shared online presence when REDIS_URL is set (e.g. client + separate signaling service)."""
import os

_ONLINE_SET = "nexus:online_users"
_redis = None


def _redis_client():
    global _redis
    if _redis is not None:
        return _redis
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        _redis = False
        return None
    import redis

    _redis = redis.from_url(url, decode_responses=True)
    return _redis


def mark_online(user_id: int) -> None:
    r = _redis_client()
    if r:
        r.sadd(_ONLINE_SET, str(int(user_id)))


def mark_offline(user_id: int) -> None:
    r = _redis_client()
    if r:
        r.srem(_ONLINE_SET, str(int(user_id)))


def list_online_ids(local_ids):
    """Return Redis member set if configured, else the in-process fallback list."""
    r = _redis_client()
    if r:
        members = r.smembers(_ONLINE_SET)
        out = []
        for x in members:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        return out
    return list(local_ids)
