from app.money import to_cents


def test_to_cents_whole_dollars():
    assert to_cents(3) == 300


def test_to_cents_handles_fractions():
    assert to_cents(2.5) == 250
