"""Operator-only offline entrypoint. Never loads or migrates the local development DB."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.cloud.backup import S3Backups, backup, restore
from app.cloud.config import CloudSettings
from app.cloud.journal import Journal, S3Receipts, s3_client
from app.cloud.lease import runtime_lease
from app.cloud.operations import Operations
from app.cloud.registry import Registry
from app.core.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init-journal", "backup", "restore", "delete-user"])
    parser.add_argument("--tenant")
    parser.add_argument("--backup-id")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path("/backup-work"))
    args = parser.parse_args()
    settings, config = Settings.load(None), CloudSettings()
    if settings.app.environment != "cloud" or not config.journal_bucket:
        raise ValueError("Maintenance requires explicit cloud configuration and a remote journal")
    client = s3_client()
    receipts = S3Receipts(client, config.journal_bucket, str(config.installation_id))
    if args.action == "restore":
        if not config.backup_bucket or not args.backup_id or not args.target:
            parser.error("restore requires the backup bucket, --backup-id and --target")
        result = restore(settings, config, S3Backups(client, config.backup_bucket,
                         str(config.installation_id)), args.backup_id, args.target, receipts)
    elif args.action == "backup":
        if not config.backup_bucket:
            parser.error("backup requires BETA_BACKUP_BUCKET")
        destination = S3Backups(client, config.backup_bucket, str(config.installation_id))
        result = {"backupId": backup(settings, config, receipts, destination, args.workspace)}
    else:
        with runtime_lease(config.root):
            registry = Registry(config.root, config.max_users)
            journal = Journal(config.root, str(config.installation_id), receipts)
            if args.action == "init-journal":
                journal.initialize()
            else:
                if not args.tenant:
                    parser.error("delete-user requires --tenant")
                journal.reconcile()
                Operations(settings, config, registry, journal).delete_tenant(args.tenant)
            result = {"action": args.action, "completed": True}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
