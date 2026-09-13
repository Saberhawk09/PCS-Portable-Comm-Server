"""Read-only client count shared by LCD and public web status."""
import subprocess

def parse_ap_client_count(output: str) -> int:
    """Count active PCS client neighbors while excluding fixed infrastructure."""
    clients: set[str] = set()
    for line in output.splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith("10.42.0."):
            continue
        if any(state in parts for state in ("FAILED", "INCOMPLETE")):
            continue
        try:
            host = int(parts[0].rsplit(".", 1)[1])
        except (ValueError, IndexError):
            continue
        if host not in {1, 2, 3}:
            clients.add(parts[0])
    return len(clients)


def read_ap_client_count() -> int | None:
    try:
        result = subprocess.run(
            ["ip", "neigh", "show", "dev", "eth0"],
            text=True,
            capture_output=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_ap_client_count(result.stdout) if result.returncode == 0 else None

