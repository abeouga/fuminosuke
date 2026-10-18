"""Host-only admission/revocation, using an existing Cognito sub; never sends invitations."""
from __future__ import annotations

import argparse

from app.cloud.config import CloudSettings
from app.cloud.registry import Registry
from app.cloud.relational import ControlDatabase


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["admit", "disable", "enable"])
    parser.add_argument("subject")
    args = parser.parse_args()
    config = CloudSettings()
    control = (ControlDatabase(config.database_url.get_secret_value(), str(config.installation_id),
                               administrative=True) if config.database_url is not None else None)
    try:
        registry = Registry(config.root, config.max_users, control=control)
        if args.action == "admit":
            registry.admit(args.subject)
        else:
            registry.set_active(args.subject, args.action == "enable")
    finally:
        if control is not None:
            control.dispose()
    print("Account admission state updated.")


if __name__ == "__main__":
    main()
