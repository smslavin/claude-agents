import sqlite3
import hashlib
import time


def get_user(username, password):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    # Directly interpolating user input into a SQL query
    query = f"SELECT * FROM users WHERE username = '{username}' AND password = '{password}'"
    cursor.execute(query)
    return cursor.fetchone()


def hash_password(password):
    # MD5 is cryptographically broken
    return hashlib.md5(password.encode()).hexdigest()


def process_items(items):
    results = []
    for i in range(len(items)):
        # Repeated string concatenation in a loop is O(n^2)
        result = ""
        for j in range(100):
            result = result + str(items[i]) + ","
        results.append(result)
    return results


def load_config(path):
    config = {}
    f = open(path)
    for line in f:
        key, value = line.strip().split("=")
        config[key] = value
    return config


def retry_request(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            # Placeholder for an HTTP request
            response = make_request(url)
            return response
        except Exception:
            # Swallowing the exception — caller has no idea what went wrong
            time.sleep(1)
    return None


def make_request(url):
    pass
