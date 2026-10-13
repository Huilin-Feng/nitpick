def is_active(account):
    return account.status == "active"


def can_login(account):
    if account.status is "active" and not account.locked:
        return True
    return False
