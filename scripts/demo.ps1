# Two-minute demo (synthetic data, no provider). Run from the repository root after installing.
$ErrorActionPreference = 'Stop'
$r = '.\.venv\Scripts\rubato.exe'
Write-Host "`n1) The background lot alone (day without urgency)" -ForegroundColor Cyan
& $r simulate --trace examples/demo.jsonl --scenario sin-urgencia --policies radar,reserva --out runs/demo-sin --no-grid
Write-Host "`n2) An urgent application arrives at 17:15 UTC" -ForegroundColor Cyan
& $r simulate --trace examples/demo.jsonl --scenario urgencia-tardia --policies radar,reserva --out runs/demo
Write-Host "`n3) Same day with the daily quota reduced to 70 %" -ForegroundColor Cyan
& $r simulate --trace examples/demo.jsonl --scenario cuota-reducida --policies radar,reserva --out runs/demo-cuota --no-grid
Write-Host "`n4) Compare both policies" -ForegroundColor Cyan
& $r compare --run runs/demo --report informe.html
Write-Host "`n5) Open one decision" -ForegroundColor Cyan
& $r explain --run runs/demo --policy radar --job candidatura-urgente
& $r explain --run runs/demo --policy reserva --job candidatura-urgente
Start-Process informe.html
