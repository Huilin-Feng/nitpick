def apply_discount(price, discount):
    """Return the price after a discount given in percent (e.g. 10 for 10%)."""
    return round(price * (1 - discount / 100), 2)
