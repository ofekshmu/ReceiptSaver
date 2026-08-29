"""
outlook_auth.py
---------------
One-time interactive sign-in for the Microsoft 365 (Outlook) account(s).

Run this whenever the startup scan reports
"<account> needs re-authorization". It performs the MSAL device-code flow
(prints a URL + code to enter at https://microsoft.com/device) and writes the
refreshed token cache to token_<label>.json, after which the automated scan
uses it silently.

    python outlook_auth.py
"""

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")

import receipt_saver
import outlook_provider


def main():
    accounts = [a for a in receipt_saver.ACCOUNTS if a.get("provider") == "outlook"]
    if not accounts:
        print("No Outlook accounts configured in receipt_saver.ACCOUNTS.")
        return
    failed = False
    for account in accounts:
        if not account["creds_file"].exists():
            print(f"skip {account['label']}: {account['creds_file'].name} missing")
            continue
        print(f"\n=== {account['email']} ({account['label']}) ===")
        try:
            outlook_provider.get_service(account, interactive=True)
            print(f"OK  {account['label']} authorized -> {account['token_file'].name}")
        except Exception as e:
            print(f"FAIL  {account['label']}: {e}")
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
