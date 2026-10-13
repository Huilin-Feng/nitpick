USERS = []


def create_user(name, email):
    user = {"name": name, "email": email}
    USERS.append(user)
    return user
