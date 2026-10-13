def to_cents(amount):
    """Convert a dollar amount to an integer number of cents."""
    return round(amount * 100)


def format_cents(cents):
    """Format an integer number of cents as a dollar string, e.g. "$1.50"."""
    return f"${cents / 100:.2f}"
