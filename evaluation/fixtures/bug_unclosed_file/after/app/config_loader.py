import json

DEFAULTS = {"debug": False, "port": 8000}


def load_config(path):
    f = open(path)
    data = json.load(f)
    return {**DEFAULTS, **data}
