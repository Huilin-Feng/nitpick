USERS = []


def create_user(name, email, role):
    user = {"name": name, "email": email, "role": role}
    USERS.append(user)
    return user
