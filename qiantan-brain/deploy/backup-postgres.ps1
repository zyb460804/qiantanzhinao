param(
  [string]$ComposeFile = "docker-compose.yml",
  [string]$ProdComposeFile = "docker-compose.prod.yml",
  [int]$RetentionDays = 14
)

$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$fileName = "qiantan-$stamp.dump"
$containerFile = "/backups/$fileName"

docker compose -f $ComposeFile -f $ProdComposeFile exec -T `
  -e BACKUP_FILE=$containerFile db sh -c `
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl > "$BACKUP_FILE"'
if ($LASTEXITCODE -ne 0) { throw "PostgreSQL backup failed" }

docker compose -f $ComposeFile -f $ProdComposeFile exec -T `
  -e RETENTION_DAYS=$RetentionDays db sh -c `
  'find /backups -maxdepth 1 -type f -name "qiantan-*.dump" -mtime +"$RETENTION_DAYS" -delete'
if ($LASTEXITCODE -ne 0) { throw "Backup retention cleanup failed" }
Write-Host "Backup created: deploy/backups/$fileName"
