# Telefonagent repository instructions

These instructions apply to the complete repository.

## Required workflow

1. Inspect `git status --short --branch`, the current branch, the intended diff, and `git remote -v` before changing Git state.
2. Preserve unrelated and untracked user files. Never reset, stash, overwrite, or stage them implicitly.
3. Run the relevant checks before committing. For a full feature or release use:
   - Backend: `backend\.venv\Scripts\python.exe -m ruff check backend\app backend\tests`
   - Backend: `backend\.venv\Scripts\python.exe -m pytest -q backend\tests`
   - Frontend: from `frontend`, `npm.cmd run lint`, `npm.cmd test -- --run`, and `npm.cmd run build`
   - Compose: `docker-compose config --quiet`
   - Git hygiene: `git diff --check`
4. Stage only reviewed files and use a concise conventional commit message.
5. Before any GitHub publication, run `gh auth status`. Push the current feature branch to the existing `origin` repository and open a draft PR against `main` unless the user explicitly requests another reviewed target.
6. Production is deployed only from a clean local `main` that exactly matches `origin/main`. Read [docs/deployment.md](docs/deployment.md), then use the one standard command: `.\ops\deploy.ps1`.

## Deployment invariants

- The only production path is local repository -> GitHub `main` -> SSH alias `telefonagent-prod` -> `.\ops\deploy.ps1` -> health checks.
- Do not invent or substitute SCP uploads, Hostinger Docker-project updates, ad-hoc archive uploads, direct root-password sessions, alternate Compose projects, or alternate directories.
- If deployment fails, diagnose the existing script, SSH alias, active release, Compose project, migration service, and health checks. Do not replace the process with another deployment mechanism.
- Keep the production Compose project name `telefonagent`, root `/docker/telefonagent`, backend port `18001`, frontend port `18081`, and the existing named database volume.
- Never print, copy into logs, or commit `.env` values, credentials, OAuth tokens, encryption keys, API keys, database passwords, or private SSH keys.
- Never remove or recreate production containers, networks, database volumes, releases, backups, or product data unless the user explicitly names and authorizes that destructive operation.
- Never run `docker compose down -v`, `docker volume rm`, database drop/restore, Alembic downgrade, or release cleanup as part of a normal deployment.
- A production deployment, push, PR merge, rollback, or VPS configuration change always requires explicit user authorization for that action.

## Account-system operations

The account-system migration, backup, restore rehearsal, activation, and rollback rules remain specialized procedures in [docs/accountsystem-operations.md](docs/accountsystem-operations.md). The general deployment script's pre-deploy dump does not replace those procedures. For an account-schema activation or rollback, follow that document in addition to the standard deployment path.
