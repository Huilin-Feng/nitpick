USERS = {
    1: {"name": "Ada", "email": "ada@example.com"},
    2: {"name": "Linus", "email": "linus@example.com"},
}


def get_email(user_id):
    user = USERS.get(user_id)
    return user["email"].lower()
