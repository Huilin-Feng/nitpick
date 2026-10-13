def order_summary(db, orders):
    rows = []
    for o in orders:
        customer = db.get_customer(o.customer_id)
        rows.append((o.id, customer.name, o.total))
    return rows
