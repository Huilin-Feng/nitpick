import re


def _to_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def post_url(title: str) -> str:
    return f"/posts/{_to_slug(title)}"
