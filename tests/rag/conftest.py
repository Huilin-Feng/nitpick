import textwrap
from pathlib import Path

import pytest

ORDERS = textwrap.dedent('''\
    """Order helpers."""
    TAX_RATE = 0.08


    def calculate_total(items, discount):
        subtotal = sum(i.price for i in items)
        return subtotal * (1 - discount) * (1 + TAX_RATE)
    ''')

CHECKOUT = textwrap.dedent('''\
    from orders import calculate_total


    def checkout(cart):
        total = calculate_total(cart.items, cart.discount)
        return {"total": total}
    ''')

UNRELATED = textwrap.dedent('''\
    def render_banner(title):
        return f"<h1>{title}</h1>"
    ''')

# PR: calculate_total gains a required `currency` argument; checkout.py is NOT updated.
PR_DIFF = textwrap.dedent('''\
    diff --git a/orders.py b/orders.py
    index 1111111..2222222 100644
    --- a/orders.py
    +++ b/orders.py
    @@ -3,5 +3,6 @@ TAX_RATE = 0.08
     
     
    -def calculate_total(items, discount):
    +def calculate_total(items, discount, currency):
         subtotal = sum(i.price for i in items)
    -    return subtotal * (1 - discount) * (1 + TAX_RATE)
    +    rate = convert_rate(currency)
    +    return subtotal * rate * (1 - discount) * (1 + TAX_RATE)
    ''')


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    (tmp_path / "orders.py").write_text(ORDERS)
    (tmp_path / "checkout.py").write_text(CHECKOUT)
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "banner.py").write_text(UNRELATED)
    (tmp_path / "README.md").write_text("not code")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("function x() {}")
    return tmp_path


@pytest.fixture
def pr_diff() -> str:
    return PR_DIFF
