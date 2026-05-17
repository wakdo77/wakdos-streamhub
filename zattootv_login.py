import sys
import json
import getpass
import argparse
from pathlib import Path
from lib.utils.helpers import load_pickle, save_pickle
from lib.streamers.factory import get_streamer

ZATTOOTV_COOKIE_FILE = "cache/zattootv/login.pkl"
ZATTOOTV_RAW_COOKIE_FILE = "cache/zattootv/login.cookies.json"

def ensure_parent_dir(file_path: str) -> Path:
    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def save_cookies_json(file_path: str, cookiejar) -> None:
    p = ensure_parent_dir(file_path)
    data = [_cookie_to_chrome_json(c) for c in cookiejar]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description="ZattooTV login helper")
    parser.add_argument("--cookie-file", default=ZATTOOTV_COOKIE_FILE, help="Path to pickle cookie file")
    parser.add_argument("--saveraw", action="store_true", help="Also save cookies as JSON (Chrome/DevTools style)")
    parser.add_argument("--raw-file", default=ZATTOOTV_RAW_COOKIE_FILE, help="Path to raw JSON cookie file")
    args = parser.parse_args()

    print("ZattooTV Login Helper")

    cookies = load_pickle(args.cookie_file)
    if cookies:
        print("Existing cookies found:")
        print(cookies.items())
        print("beaker.session.id " + cookies.get("beaker.session.id", "N/A")[:40] + "...")
        use_existing = input("Use existing cookies? (y/n): ").strip().lower()
        if use_existing == "y":
            if args.saveraw:
                save_cookies_json(args.raw_file, cookies)
                print(f"Raw JSON cookies written to: {args.raw_file}")
            print("Using existing cookies. Exiting.")
            return
        print("Discarding existing cookies and proceeding with login.")

    username = input("Login / E-Mail: ").strip()
    password = getpass.getpass("Password: ")

    if not username or not password:
        print("Username and password are required.")
        sys.exit(1)

    zattoo = get_streamer("zattootv")
    zattoo.debug = False
    zattoo.boot(login_flow=True)

    ok = zattoo._login(username, password)
    if not ok:
        print("Login failed.")
        sys.exit(1)

    print("client_app_token:", zattoo.client_app_token)
    print("beaker.session.id:", zattoo.http.cookies.get("beaker.session.id", "N/A")[:40] + "...")
    print("Storing the cookies for use in the main app...")
    save_pickle(args.cookie_file, zattoo.http.cookies)

    if args.saveraw:
        save_cookies_json(args.raw_file, zattoo.http.cookies)
        print(f"Raw JSON cookies written to: {args.raw_file}")

if __name__ == "__main__":
    main()