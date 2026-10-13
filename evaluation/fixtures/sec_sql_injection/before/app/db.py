import sqlite3


def connect(path="app.db"):
    return sqlite3.connect(path)
