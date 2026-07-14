param(
  [Parameter(Mandatory = $true)][string]$BackupFile,
  [string]$ComposeFile = "docker-compose.yml",
  [string]$ProdComposeFile = "docker-compose.prod.yml",
  [switch]$ConfirmRestore
)

$ErrorActionPreference = "Stop"
if (-not $ConfirmRestore) {
  throw "Restore is destructive. Re-run with -ConfirmRestore after verifying the backup and maintenance window."
}
$backupRoot = (Resolve-Path -LiteralPath "deploy/backups").Path
$resolved = (Resolve-Path -LiteralPath $BackupFile).Path
if ([IO.Path]::GetDirectoryName($resolved) -ne $backupRoot) {
  throw "BackupFile must be directly inside deploy/backups"
}
$fileName = [IO.Path]::GetFileName($resolved)
if ($fileName -notmatch '^qiantan-[0-9]{8}-[0-9]{6}\.dump$') {
  throw "Unexpected backup filename"
}
$containerFile = "/backups/$fileName"

docker compose -f $ComposeFile -f $ProdComposeFile exec -T `
  -e BACKUP_FILE=$containerFile db sh -c `
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-acl "$BACKUP_FILE"'
if ($LASTEXITCODE -ne 0) { throw "PostgreSQL restore failed" }
Write-Host "Restore completed from: $resolved"
