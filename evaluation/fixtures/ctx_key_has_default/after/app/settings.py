import os

DEFAULTS = {"timeout": 30, "retries": 3}


def get_config() -> dict:
    """Return settings; every key in DEFAULTS is always present."""
    config = dict(DEFAULTS)
    if os.getenv("APP_TIMEOUT"):
        config["timeout"] = int(os.environ["APP_TIMEOUT"])
    return config
