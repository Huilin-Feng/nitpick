from app.storage import load_items


def all_items(path):
    return load_items(path)


def cheapest_first(path):
    items = load_items(path)
    items.sort(key=lambda item: item["price"])
    return items
