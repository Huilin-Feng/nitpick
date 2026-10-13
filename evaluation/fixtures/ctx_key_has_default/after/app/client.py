import httpx

from app.settings import get_config


def fetch(url):
    return httpx.get(url, timeout=get_config()["timeout"])
