# Installs the freshly built SwitcherSetup-*.exe silently, runs the installed
# app (self-test, then a real start), and uninstalls it again.
$ErrorActionPreference = "Stop"

$setup = Get-ChildItem dist\SwitcherSetup-*.exe | Select-Object -First 1
Write-Host ("Installing {0} ({1:N1} MB)" -f $setup.Name, ($setup.Length / 1MB))
$p = Start-Process $setup.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=autostart" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "setup exit code $($p.ExitCode)" }

$app = Join-Path $env:LOCALAPPDATA "Programs\Switcher"
$exe = Join-Path $app "Switcher.exe"
if (-not (Test-Path $exe)) { throw "Switcher.exe was not installed" }
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$autostart = (Get-ItemProperty $runKey).Switcher
Write-Host "Autostart entry: $autostart"
if ($autostart -notlike "*Switcher.exe*") { throw "autostart entry missing" }

$report = Join-Path $env:RUNNER_TEMP "installed-selftest.log"
$p = Start-Process $exe -ArgumentList "--selftest", "`"$report`"" -Wait -PassThru
Get-Content $report
if ($p.ExitCode -ne 0) { throw "installed self-test failed" }

Write-Host "Starting the installed app for real"
$log = Join-Path $env:APPDATA "Switcher\switcher.log"
$proc = Start-Process $exe -PassThru
Start-Sleep -Seconds 15
Get-Content $log -ErrorAction SilentlyContinue
if ($proc.HasExited) { throw "Switcher exited on its own (code $($proc.ExitCode))" }
if (-not (Select-String -Path $log -Pattern "Switcher started" -Quiet)) { throw "no start message in the log" }
Stop-Process -Id $proc.Id -Force
Start-Sleep -Seconds 2

Write-Host "Uninstalling"
Start-Process (Join-Path $app "unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
for ($i = 0; $i -lt 30 -and (Test-Path $exe); $i++) { Start-Sleep -Seconds 1 }
if (Test-Path $exe) { throw "uninstall left Switcher.exe behind" }
if ((Get-ItemProperty $runKey).PSObject.Properties.Name -contains "Switcher") { throw "autostart entry left behind" }
Write-Host "Install / start / uninstall: OK"
