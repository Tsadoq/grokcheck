_cache: dict[str, int] = {}


def lookup(key: str) -> int:
    if key not in _cache:
        _cache[key] = len(_cache)
    return _cache[key]


lookup("a")
lookup("a")
