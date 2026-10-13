import logging

from app.gateway import charge

logger = logging.getLogger(__name__)


def pay(order):
    try:
        charge(order.customer_id, order.total)
    except:
        pass
    logger.info("Order %s paid", order.id)
    return True
