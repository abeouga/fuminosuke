"""Host-only admission/revocation, using an existing Cognito sub; never sends invitations."""
from __future__ import annotations

import argparse

from app.cloud.config import CloudSettings
from app.cloud.registry import Registry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["admit", "disable", "enable"])
    parser.add_argument("subject")
    args = parser.parse_args()
    config = CloudSettings()
    registry = Registry(config.root, config.max_users)
    if args.action == "admit":
        registry.admit(args.subject)
    else:
        registry.set_active(args.subject, args.action == "enable")
    print("Account admission state updated.")


if __name__ == "__main__":
    main()
