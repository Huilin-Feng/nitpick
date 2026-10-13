def add_item(item, cart=[]):
    """Add an item to the cart. Creates a new cart if none is given."""
    cart.append(item)
    return cart
