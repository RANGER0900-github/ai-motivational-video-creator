import os
from instagrapi import Client

USERNAME = os.getenv("IG_USERNAME")
PASSWORD = os.getenv("IG_PASSWORD")
SESSION_FILE = os.getenv("IG_SESSION_FILE", "state/ig_session.json")


def main() -> None:
    if not USERNAME or not PASSWORD:
        raise SystemExit("Set IG_USERNAME and IG_PASSWORD in the environment.")

    cl = Client()
    if os.path.exists(SESSION_FILE):
        cl.load_settings(SESSION_FILE)
    cl.login(USERNAME, PASSWORD)
    cl.dump_settings(SESSION_FILE)
    print(f"Saved session to {SESSION_FILE}")


if __name__ == "__main__":
    main()
