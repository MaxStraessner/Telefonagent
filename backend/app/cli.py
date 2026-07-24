import argparse
import getpass
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.provisioning import (
    ProvisioningConflictError,
    ProvisioningNotFoundError,
    ProvisioningService,
)


def _password(environment_variable: str | None) -> str:
    if environment_variable:
        value = os.environ.get(environment_variable)
        if value is None:
            raise ValueError(
                f"Die Umgebungsvariable {environment_variable} ist nicht gesetzt."
            )
        return value
    first = getpass.getpass("Passwort: ")
    second = getpass.getpass("Passwort wiederholen: ")
    if first != second:
        raise ValueError("Die Passwörter stimmen nicht überein.")
    return first


def _password_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--password-env",
        help="Name einer Umgebungsvariable, die das Passwort enthält",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telefonagent-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)

    provision = subparsers.add_parser("provision-tenant")
    provision.add_argument("--slug", required=True)
    provision.add_argument("--name", required=True)
    provision.add_argument("--industry", required=True)
    provision.add_argument("--timezone", default="Europe/Berlin")
    provision.add_argument("--username", required=True)
    provision.add_argument("--display-name", required=True)
    provision.add_argument("--email")
    _password_option(provision)

    platform_admin = subparsers.add_parser("create-platform-admin")
    platform_admin.add_argument("--username", required=True)
    platform_admin.add_argument("--display-name", required=True)
    platform_admin.add_argument("--email")
    _password_option(platform_admin)

    set_password = subparsers.add_parser("set-password")
    set_password.add_argument("--username", required=True)
    _password_option(set_password)

    deactivate_user = subparsers.add_parser("deactivate-user")
    deactivate_user.add_argument("--username", required=True)

    deactivate_tenant = subparsers.add_parser("deactivate-tenant")
    deactivate_tenant.add_argument("--slug", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        settings = get_settings()
        engine = create_engine(
            settings.migration_database_url or settings.database_url,
            pool_pre_ping=True,
        )
        with Session(engine) as db:
            service = ProvisioningService(db)
            if args.command == "provision-tenant":
                tenant, user = service.provision_tenant(
                    slug=args.slug,
                    name=args.name,
                    industry=args.industry,
                    timezone_name=args.timezone,
                    username=args.username,
                    display_name=args.display_name,
                    email=args.email,
                    password=_password(args.password_env),
                )
                print(f"Tenant {tenant.slug} und Owner {user.username} sind bereit.")
            elif args.command == "create-platform-admin":
                user = service.create_platform_admin(
                    username=args.username,
                    display_name=args.display_name,
                    email=args.email,
                    password=_password(args.password_env),
                )
                print(f"Plattformadministrator {user.username} ist bereit.")
            elif args.command == "set-password":
                user = service.set_password(
                    args.username, _password(args.password_env)
                )
                print(f"Passwort für {user.username} wurde gesetzt.")
            elif args.command == "deactivate-user":
                user = service.deactivate_user(args.username)
                print(f"Benutzer {user.username} wurde deaktiviert.")
            else:
                tenant = service.deactivate_tenant(args.slug)
                print(f"Tenant {tenant.slug} wurde deaktiviert.")
    except (ValueError, ProvisioningConflictError, ProvisioningNotFoundError) as exc:
        print(f"Abbruch: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
