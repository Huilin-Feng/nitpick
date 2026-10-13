from app.text import post_url


def link(post):
    return f'<a href="{post_url(post.title)}">{post.title}</a>'
