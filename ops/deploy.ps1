[CmdletBinding()]
param(
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$expectedOrigin = "https://github.com/MaxStraessner/Telefonagent.git"
$productionBranch = "main"
$sshHost = "telefonagent-prod"
$appRoot = "/docker/telefonagent"
$composeProject = "telefonagent"
$backendHealth = "http://127.0.0.1:18001/api/v1/health"
$frontendHealth = "http://127.0.0.1:18081/"

function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & $FilePath @ArgumentList 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "$FilePath $($ArgumentList -join ' ') ist fehlgeschlagen: $($output -join [Environment]::NewLine)"
    }
    return ($output -join [Environment]::NewLine).Trim()
}

$repositoryRoot = Invoke-NativeCapture -FilePath "git" -ArgumentList @("rev-parse", "--show-toplevel")
Set-Location -LiteralPath $repositoryRoot

$origin = Invoke-NativeCapture -FilePath "git" -ArgumentList @("remote", "get-url", "origin")
if ($origin -ne $expectedOrigin) {
    throw "Unerwartetes origin '$origin'. Erwartet wird '$expectedOrigin'."
}

if ($ValidateOnly) {
    [pscustomobject]@{
        Valid = $true
        Repository = $repositoryRoot
        Origin = $origin
        ProductionBranch = $productionBranch
        SshHost = $sshHost
        ApplicationRoot = $appRoot
        ComposeProject = $composeProject
        BackendHealth = $backendHealth
        FrontendHealth = $frontendHealth
        StandardCommand = ".\ops\deploy.ps1"
    }
    return
}

$currentBranch = Invoke-NativeCapture -FilePath "git" -ArgumentList @("branch", "--show-current")
if ($currentBranch -ne $productionBranch) {
    throw "Deployment ist nur vom Branch '$productionBranch' erlaubt; aktuell ist '$currentBranch' aktiv."
}

$worktree = Invoke-NativeCapture -FilePath "git" -ArgumentList @("status", "--porcelain=v1", "--untracked-files=normal")
if ($worktree) {
    throw "Der Arbeitsbaum ist nicht sauber. Committe oder klaere die angezeigten Aenderungen vor dem Deployment."
}

Invoke-NativeCapture -FilePath "git" -ArgumentList @("fetch", "--prune", "origin", $productionBranch) | Out-Null
$localCommit = Invoke-NativeCapture -FilePath "git" -ArgumentList @("rev-parse", "HEAD")
$remoteCommit = Invoke-NativeCapture -FilePath "git" -ArgumentList @("rev-parse", "origin/$productionBranch")
if ($localCommit -ne $remoteCommit) {
    throw "Lokales HEAD und origin/$productionBranch stimmen nicht ueberein. Aktualisiere main per Fast-Forward."
}
if ($localCommit -notmatch '^[0-9a-f]{40}$') {
    throw "Ungueltige Commit-ID '$localCommit'."
}

$remoteScript = @'
set -Eeuo pipefail
umask 077

revision="$1"
repo_url="https://github.com/MaxStraessner/Telefonagent.git"
app_root="/docker/telefonagent"
releases_dir="$app_root/releases"
repository_dir="$app_root/repository.git"
backups_dir="$app_root/backups"
project="telefonagent"
backend_container="telefonagent-backend-1"
frontend_container="telefonagent-frontend-1"
database_container="telefonagent-database-1"
backend_health="http://127.0.0.1:18001/api/v1/health"
frontend_health="http://127.0.0.1:18081/"

fail() {
  printf 'deployment_error=%s\n' "$1" >&2
  exit 1
}

case "$revision" in
  *[!0-9a-f]*|'') fail "invalid_revision" ;;
esac
[ "${#revision}" -eq 40 ] || fail "invalid_revision_length"

command -v git >/dev/null || fail "git_missing"
command -v docker >/dev/null || fail "docker_missing"
command -v curl >/dev/null || fail "curl_missing"
[ "$(id -un)" = "telefonagent-deploy" ] || fail "unexpected_ssh_user"
docker compose version >/dev/null 2>&1 || fail "docker_compose_plugin_missing"
docker info >/dev/null 2>&1 || fail "docker_access_missing"
docker inspect "$backend_container" >/dev/null 2>&1 || fail "active_backend_not_found"
docker inspect "$frontend_container" >/dev/null 2>&1 || fail "active_frontend_not_found"
docker inspect "$database_container" >/dev/null 2>&1 || fail "active_database_not_found"

current_config="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$backend_container")"
current_config="${current_config%%,*}"
[ -n "$current_config" ] || fail "active_compose_path_missing"
current_dir="$(dirname "$current_config")"
case "$current_dir" in
  "$releases_dir"/*|"$app_root/app") ;;
  *) fail "active_compose_path_outside_telefonagent" ;;
esac
[ -f "$current_dir/.env" ] || fail "active_environment_file_missing"

require_env_value() {
  key="$1"
  grep -Eq "^${key}=.+$" "$current_dir/.env" || fail "required_environment_key_missing_${key}"
}

require_env_value POSTGRES_PASSWORD
require_env_value AUTH_HMAC_SECRET
require_env_value MIGRATION_DATABASE_URL
require_env_value RUNTIME_DATABASE_USER
require_env_value RUNTIME_DATABASE_PASSWORD
require_env_value FRONTEND_URL
require_env_value APP_BASE_URL
require_env_value PUBLIC_HOST
require_env_value TRAEFIK_NETWORK
require_env_value BACKEND_BIND_ADDRESS
require_env_value FRONTEND_BIND_ADDRESS
require_env_value MAIL_ENABLED
grep -Eq '^APP_ENV=production$' "$current_dir/.env" || fail "app_env_not_production"
grep -Eq '^DEV_BOOTSTRAP_ENABLED=false$' "$current_dir/.env" || fail "development_bootstrap_not_disabled"
grep -Eq '^ALLOW_DEVELOPMENT_TENANT_FALLBACK=false$' "$current_dir/.env" || fail "development_tenant_fallback_not_disabled"
grep -Eq '^BACKEND_BIND_ADDRESS=127\.0\.0\.1$' "$current_dir/.env" || fail "backend_not_bound_to_loopback"
grep -Eq '^FRONTEND_BIND_ADDRESS=127\.0\.0\.1$' "$current_dir/.env" || fail "frontend_not_bound_to_loopback"

env_value() {
  key="$1"
  sed -n "s/^${key}=//p" "$current_dir/.env" | tail -n 1
}

runtime_user="$(env_value RUNTIME_DATABASE_USER)"
public_host="$(env_value PUBLIC_HOST)"
traefik_network="$(env_value TRAEFIK_NETWORK)"
case "$runtime_user" in *[!a-zA-Z0-9_]*|'') fail "invalid_runtime_database_user" ;; esac
case "$public_host" in *[!a-zA-Z0-9.-]*|'') fail "invalid_public_host" ;; esac
case "$traefik_network" in *[!a-zA-Z0-9_.-]*|'') fail "invalid_traefik_network" ;; esac
[ "$(env_value APP_BASE_URL)" = "https://$public_host" ] || fail "app_base_url_public_host_mismatch"
[ "$(env_value FRONTEND_URL)" = "https://$public_host" ] || fail "frontend_url_public_host_mismatch"
docker network inspect "$traefik_network" >/dev/null 2>&1 || fail "traefik_network_missing"

role_flags="$(docker exec "$database_container" sh -c 'exec psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --tuples-only --no-align --command="$1"' sh "SELECT rolsuper::text || '|' || rolbypassrls::text FROM pg_roles WHERE rolname = '$runtime_user'")"
[ "$role_flags" = "false|false" ] || fail "runtime_database_role_missing_or_privileged"

mkdir -p "$releases_dir" "$backups_dir"

if [ ! -d "$repository_dir" ]; then
  git clone --mirror "$repo_url" "$repository_dir"
else
  actual_origin="$(git --git-dir="$repository_dir" remote get-url origin)"
  [ "$actual_origin" = "$repo_url" ] || fail "unexpected_remote_repository"
  git --git-dir="$repository_dir" remote update --prune
fi

github_main="$(git --git-dir="$repository_dir" rev-parse refs/heads/main)"
[ "$github_main" = "$revision" ] || fail "revision_is_not_github_main"
git --git-dir="$repository_dir" cat-file -e "$revision^{commit}" || fail "revision_not_found"

release_dir="$releases_dir/$revision"
if [ ! -d "$release_dir" ]; then
  mkdir "$release_dir"
  git --git-dir="$repository_dir" archive "$revision" | tar -x -C "$release_dir"
fi
[ -f "$release_dir/docker-compose.yml" ] || fail "release_compose_missing"
[ -f "$release_dir/docker-compose.prod.yml" ] || fail "release_production_compose_missing"
if [ ! -f "$release_dir/.env" ]; then
  install -m 600 "$current_dir/.env" "$release_dir/.env"
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="$backups_dir/predeploy-$timestamp-$revision.dump"
container_dump="/tmp/predeploy-$timestamp-$revision.dump"
cleanup_dump() {
  docker exec "$database_container" rm -f -- "$container_dump" >/dev/null 2>&1 || true
}
trap cleanup_dump EXIT
docker exec "$database_container" sh -c 'exec pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --no-owner --no-privileges --file="$1"' sh "$container_dump"
docker exec "$database_container" pg_restore --list "$container_dump" >/dev/null
docker cp "$database_container:$container_dump" "$backup_path"
sha256sum "$backup_path" > "$backup_path.sha256"
cleanup_dump
trap - EXIT

compose() {
  docker compose \
    --project-name "$project" \
    --env-file "$release_dir/.env" \
    -f "$release_dir/docker-compose.yml" \
    -f "$release_dir/docker-compose.prod.yml" \
    "$@"
}

compose config --quiet
compose build
docker stop "$backend_container" "$frontend_container" >/dev/null
compose run --rm migrate

database_name="$(docker exec "$database_container" printenv POSTGRES_DB)"
case "$database_name" in *[!a-zA-Z0-9_]*|'') fail "invalid_database_name" ;; esac
docker exec -i "$database_container" sh -c 'exec psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --set=ON_ERROR_STOP=1' <<SQL
GRANT CONNECT ON DATABASE "$database_name" TO "$runtime_user";
GRANT USAGE ON SCHEMA public TO "$runtime_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "$runtime_user";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO "$runtime_user";
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO "$runtime_user";
SQL

owned_tables="$(docker exec "$database_container" sh -c 'exec psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --tuples-only --no-align --command="$1"' sh "SELECT count(*) FROM pg_class table_info JOIN pg_roles owner_role ON owner_role.oid = table_info.relowner WHERE owner_role.rolname = '$runtime_user' AND table_info.relkind IN ('r', 'p')")"
[ "$owned_tables" = "0" ] || fail "runtime_database_role_owns_tables"

compose up -d database backend frontend

healthy=false
for _ in $(seq 1 45); do
  if curl --fail --silent --show-error "$backend_health" >/dev/null 2>&1 && curl --fail --silent --show-error "$frontend_health" >/dev/null 2>&1; then
    healthy=true
    break
  fi
  sleep 2
done
[ "$healthy" = true ] || fail "health_check_failed"

compose exec -T backend alembic current
compose ps
ln -sfn "$release_dir" "$app_root/current"

printf 'deployed_commit=%s\n' "$revision"
printf 'previous_release=%s\n' "$current_dir"
printf 'database_backup=%s\n' "$backup_path"
printf 'backend_health=ok\n'
printf 'frontend_health=ok\n'
'@

$normalizedRemoteScript = $remoteScript -replace "`r`n", "`n"
$normalizedRemoteScript | & ssh -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes $sshHost "bash -s -- $localCommit"
if ($LASTEXITCODE -ne 0) {
    throw "Das Standarddeployment ist fehlgeschlagen. Diagnose den bestehenden Weg; verwende keinen alternativen Deploymentmechanismus."
}
