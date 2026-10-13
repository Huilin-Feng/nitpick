def to_cents(amount):
    return round(amount * 100)


def format_cents(cents):
    return f"${cents / 100:.2f}"
