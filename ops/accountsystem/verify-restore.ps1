param(
    [Parameter(Mandatory = $true)][string]$DumpPath,
    [Parameter(Mandatory = $true)][string]$TargetDatabase,
    [string]$HostName = "localhost",
    [int]$Port = 5432,
    [string]$AdminUser = "telefonagent",
    [string]$AdminPassword,
    [string]$DatabaseContainer
)

$ErrorActionPreference = "Stop"
if ($TargetDatabase -notmatch '^telefonagent_(restore|accountsystem)_[a-zA-Z0-9_]+$') {
    throw "Der Zielname muss mit telefonagent_restore_ oder telefonagent_accountsystem_ beginnen."
}
$resolvedDump = (Resolve-Path -LiteralPath $DumpPath).Path
if ($AdminUser -notmatch '^[a-zA-Z0-9_]+$') {
    throw "Der Admin-Benutzername enthält unzulässige Zeichen."
}

if ($DatabaseContainer) {
    if ($DatabaseContainer -notmatch '^[a-zA-Z0-9_.-]+$') {
        throw "Der Containername enthält unzulässige Zeichen."
    }
    $dumpDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedDump).Hash.ToLowerInvariant()
    $containerDump = "/tmp/telefonagent-restore-$dumpDigest.dump"
    try {
        & docker cp $resolvedDump "${DatabaseContainer}:$containerDump"
        if ($LASTEXITCODE -ne 0) { throw "Der Dump konnte nicht in den Datenbankcontainer kopiert werden." }
        & docker exec $DatabaseContainer dropdb --username=$AdminUser --if-exists --force $TargetDatabase
        if ($LASTEXITCODE -ne 0) { throw "Die isolierte Zieldatenbank konnte nicht entfernt werden." }
        & docker exec $DatabaseContainer createdb --username=$AdminUser --owner=$AdminUser $TargetDatabase
        if ($LASTEXITCODE -ne 0) { throw "Die isolierte Zieldatenbank konnte nicht erstellt werden." }
        & docker exec $DatabaseContainer pg_restore --username=$AdminUser --dbname=$TargetDatabase --no-owner --no-privileges --exit-on-error $containerDump
        if ($LASTEXITCODE -ne 0) { throw "Der Restore ist fehlgeschlagen." }
        & docker exec $DatabaseContainer psql --username=$AdminUser --dbname=$TargetDatabase --tuples-only --no-align --command="SELECT version_num FROM alembic_version"
        if ($LASTEXITCODE -ne 0) { throw "Der Alembic-Stand konnte nicht gelesen werden." }
    }
    finally {
        & docker exec $DatabaseContainer rm -f -- $containerDump | Out-Null
    }
}
else {
    if (-not $AdminPassword) { throw "AdminPassword muss im Host-Modus gesetzt sein." }
    $env:PGPASSWORD = $AdminPassword
    try {
        & dropdb --host=$HostName --port=$Port --username=$AdminUser --if-exists --force $TargetDatabase
        if ($LASTEXITCODE -ne 0) { throw "Die isolierte Zieldatenbank konnte nicht entfernt werden." }
        & createdb --host=$HostName --port=$Port --username=$AdminUser $TargetDatabase
        if ($LASTEXITCODE -ne 0) { throw "Die isolierte Zieldatenbank konnte nicht erstellt werden." }
        & pg_restore --host=$HostName --port=$Port --username=$AdminUser --dbname=$TargetDatabase --no-owner --no-privileges --exit-on-error $resolvedDump
        if ($LASTEXITCODE -ne 0) { throw "Der Restore ist fehlgeschlagen." }
        & psql --host=$HostName --port=$Port --username=$AdminUser --dbname=$TargetDatabase --tuples-only --no-align --command="SELECT version_num FROM alembic_version"
        if ($LASTEXITCODE -ne 0) { throw "Der Alembic-Stand konnte nicht gelesen werden." }
    }
    finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
}
