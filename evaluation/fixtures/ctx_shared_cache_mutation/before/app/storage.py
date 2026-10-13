import json

_CACHE: dict[str, list] = {}


def load_items(path: str) -> list:
    """Load items from a JSON file. Returns the cached list itself, not a copy."""
    if path not in _CACHE:
        with open(path, encoding="utf-8") as f:
            _CACHE[path] = json.load(f)
    return _CACHE[path]
