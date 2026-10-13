from datetime import date


def format_day(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def format_money(cents: int) -> str:
    return f"${cents / 100:.2f}"
