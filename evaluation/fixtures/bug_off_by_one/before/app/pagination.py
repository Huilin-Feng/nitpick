import math


def page_count(total_items, page_size):
    return math.ceil(total_items / page_size)


def get_pages(items, page_size):
    pages = []
    for i in range(page_count(len(items), page_size)):
        start = i * page_size
        pages.append(items[start:start + page_size])
    return pages
