def apply_discount(price, discount):
    """Return the price after a discount given as a fraction (e.g. 0.1 for 10%)."""
    if not 0 <= discount <= 1:
        raise ValueError("discount must be between 0 and 1")
    return round(price * (1 - discount), 2)
