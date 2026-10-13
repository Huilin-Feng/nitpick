from app.formatting import format_date, format_money


def report_line(order):
    return f"{format_date(order.created)}  {format_money(order.total_cents)}"
