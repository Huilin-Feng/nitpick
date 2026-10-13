import re


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def post_url(title: str) -> str:
    return f"/posts/{_slugify(title)}"
