# Optional: start Rubato (Docker Compose) when you log on to Windows.
# Registers a scheduled task for the current user. Run it yourself if you want it:
#   powershell -ExecutionPolicy Bypass -File scripts/ops/arranque_windows.ps1            # register
#   powershell -ExecutionPolicy Bypass -File scripts/ops/arranque_windows.ps1 -Remove    # undo
# Docker Desktop must also start at logon (Docker Desktop > Settings > General).
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$name = 'Rubato (docker compose up)'
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; "removed '$name'"; return }
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$cmd = "Set-Location -LiteralPath '$repo'; for (`$i=0; `$i -lt 60; `$i++) { docker info *> `$null; if (`$LASTEXITCODE -eq 0) { break }; Start-Sleep 5 }; docker compose up -d"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Description 'Starts the Rubato service (normal mode) after Docker Desktop is up.' | Out-Null
"registered '$name' for $env:USERNAME (repository $repo)"
