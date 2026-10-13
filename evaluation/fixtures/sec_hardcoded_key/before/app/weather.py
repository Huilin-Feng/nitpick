import httpx

BASE_URL = "https://api.example-weather.com/v1"


def current(city):
    resp = httpx.get(f"{BASE_URL}/current", params={"q": city})
    resp.raise_for_status()
    return resp.json()
