param(
    [string]$DatabaseUrl,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$DatabaseContainer,
    [string]$DatabaseName = "telefonagent",
    [string]$DatabaseUser = "telefonagent"
)

$ErrorActionPreference = "Stop"
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dumpPath = Join-Path $resolvedOutput "telefonagent-$timestamp.dump"
$hashPath = "$dumpPath.sha256"
$listPath = "$dumpPath.list.txt"

if ($DatabaseContainer) {
    if ($DatabaseContainer -notmatch '^[a-zA-Z0-9_.-]+$') {
        throw "Der Containername enthält unzulässige Zeichen."
    }
    if ($DatabaseName -notmatch '^[a-zA-Z0-9_]+$' -or $DatabaseUser -notmatch '^[a-zA-Z0-9_]+$') {
        throw "Datenbank- und Benutzername dürfen nur Buchstaben, Zahlen und Unterstriche enthalten."
    }
    $containerDump = "/tmp/telefonagent-backup-$timestamp.dump"
    try {
        & docker exec $DatabaseContainer pg_dump --username=$DatabaseUser --dbname=$DatabaseName --format=custom --no-owner --no-privileges --file=$containerDump
        if ($LASTEXITCODE -ne 0) { throw "pg_dump im Datenbankcontainer ist fehlgeschlagen." }
        & docker cp "${DatabaseContainer}:$containerDump" $dumpPath
        if ($LASTEXITCODE -ne 0) { throw "Der Dump konnte nicht aus dem Datenbankcontainer kopiert werden." }
        & docker exec $DatabaseContainer pg_restore --list $containerDump | Set-Content -Path $listPath -Encoding UTF8
        if ($LASTEXITCODE -ne 0) { throw "pg_restore --list im Datenbankcontainer ist fehlgeschlagen." }
    }
    finally {
        & docker exec $DatabaseContainer rm -f -- $containerDump | Out-Null
    }
}
else {
    if (-not $DatabaseUrl) { throw "DatabaseUrl oder DatabaseContainer muss gesetzt sein." }
    & pg_dump --format=custom --no-owner --no-privileges --file=$dumpPath $DatabaseUrl
    if ($LASTEXITCODE -ne 0) { throw "pg_dump ist fehlgeschlagen." }
    & pg_restore --list $dumpPath | Set-Content -Path $listPath -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "pg_restore --list ist fehlgeschlagen." }
}
$digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $dumpPath).Hash
Set-Content -Path $hashPath -Value "$digest  $([System.IO.Path]::GetFileName($dumpPath))" -Encoding ASCII

[pscustomobject]@{
    Dump = $dumpPath
    Sha256 = $digest
    Contents = $listPath
}
