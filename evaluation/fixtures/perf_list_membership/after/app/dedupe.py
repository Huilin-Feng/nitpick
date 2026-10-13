def normalize(email):
    return email.strip().lower()


def unique_emails(emails):
    """Remove duplicates while keeping order. Called with ~1M emails nightly."""
    seen = []
    for e in emails:
        e = normalize(e)
        if e not in seen:
            seen.append(e)
    return seen
