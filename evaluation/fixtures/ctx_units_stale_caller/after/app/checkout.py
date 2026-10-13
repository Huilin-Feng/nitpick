from app.pricing import apply_discount

MEMBER_DISCOUNT = 10  # percent


def member_total(cart_total):
    return apply_discount(cart_total, MEMBER_DISCOUNT)
