import math


def page_count(total_items, page_size):
    return math.ceil(total_items / page_size)


def get_pages(items, page_size):
    """Split items into pages, numbering pages from 1."""
    pages = []
    total = page_count(len(items), page_size)
    for page_number in range(1, total):
        start = (page_number - 1) * page_size
        pages.append(items[start:start + page_size])
    return pages
