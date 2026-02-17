"""
launch.py

CURE — Codebase Update & Refactor Engine
Launches the Streamlit dashboard. Run from the project root:

    python ui/launch.py

The default port is 8502; override via STREAMLIT_PORT env var.
"""

import os
import sys
import subprocess
import socket


def get_local_ip() -> str:
    """Returns best-effort local IP for 'how to access' instructions."""
    ip = "localhost"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        pass
    return ip


def print_access_info(port: str) -> None:
    """Prints dashboard access URLs."""
    net_ip = get_local_ip()
    print()
    print("=" * 56)
    print("  CURE — Codebase Update & Refactor Engine")
    print("  Streamlit Dashboard")
    print("=" * 56)
    print()
    print(f"  Local:   http://localhost:{port}")
    print(f"  Network: http://{net_ip}:{port}")
    print()
    print("  Note: '0.0.0.0' is a server listening address,")
    print("  not a real URL. Use the addresses above.")
    print("=" * 56)
    print()


def main():
    if sys.version_info < (3, 9):
        print("ERROR: Python 3.9 or higher is required.", file=sys.stderr)
        sys.exit(1)

    # Locate the app file (support running from project root or ui/)
    app_path = "ui/app.py"
    if not os.path.isfile(app_path):
        alt = os.path.join(os.path.dirname(__file__), "app.py")
        if os.path.isfile(alt):
            app_path = alt
        else:
            print(
                "ERROR: Cannot find app.py. "
                "Run from the project root directory.",
                file=sys.stderr,
            )
            sys.exit(1)

    try:
        import streamlit  # noqa: F401
    except ImportError:
        print(
            "ERROR: Streamlit is not installed. "
            "Run: pip install streamlit",
            file=sys.stderr,
        )
        sys.exit(1)

    port = os.environ.get("STREAMLIT_PORT", "8502")
    print_access_info(port)

    try:
        subprocess.run(
            [
                sys.executable, "-m", "streamlit", "run",
                app_path,
                "--server.port", str(port),
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(
            f"ERROR: Streamlit exited with code {e.returncode}.",
            file=sys.stderr,
        )
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
