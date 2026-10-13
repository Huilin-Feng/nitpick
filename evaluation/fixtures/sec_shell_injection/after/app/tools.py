import subprocess


def ping(host):
    """Return True if the host answers one ping. `host` comes from the request."""
    result = subprocess.run(f"ping -c 1 {host}", shell=True, capture_output=True)
    return result.returncode == 0
