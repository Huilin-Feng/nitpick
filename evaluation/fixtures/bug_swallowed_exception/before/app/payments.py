from app.gateway import charge


def pay(order):
    charge(order.customer_id, order.total)
    return True
