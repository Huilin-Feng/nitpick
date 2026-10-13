from app.services import create_user


def signup(request):
    return create_user(request["name"], request["email"])
