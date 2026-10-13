import httpx

BASE_URL = "https://api.example-weather.com/v1"
API_KEY = "wx_live_9f8a7b6c5d4e3f2a1b0c"


def current(city):
    resp = httpx.get(f"{BASE_URL}/current", params={"q": city, "key": API_KEY})
    resp.raise_for_status()
    return resp.json()
