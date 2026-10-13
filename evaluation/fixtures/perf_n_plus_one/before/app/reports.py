def order_summary(db, orders):
    customer_ids = {o.customer_id for o in orders}
    customers = db.get_customers(customer_ids)
    return [(o.id, customers[o.customer_id].name, o.total) for o in orders]
