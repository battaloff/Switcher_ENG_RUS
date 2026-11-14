# The Notepad end-to-end check on CI, with a time limit on each part.
#
# A part that hangs used to take the whole job with it (45 minutes), and a job that times out keeps
# no annotations at all - nothing to read.  Here each part writes to a file and is stopped after its
# limit; the output is printed afterwards, and where it stopped becomes an error annotation.

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUNBUFFERED = "1"   # a stopped process must not take unwritten output with it
$logs = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }

function Invoke-Limited([string]$Title, [string]$File, [string[]]$Arguments, [int]$Seconds) {
    $log = Join-Path $logs ($Title -replace '\W', '_')
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -PassThru -NoNewWindow `
        -RedirectStandardOutput "$log.out" -RedirectStandardError "$log.err"
    $null = $process.Handle   # without this the exit code can't be read later
    $finished = $process.WaitForExit($Seconds * 1000)
    if (-not $finished) { taskkill /T /F /PID $process.Id | Out-Null }
    foreach ($path in "$log.out", "$log.err") {
        if (Test-Path $path) { Get-Content $path -Encoding utf8 | ForEach-Object { Write-Host $_ } }
    }
    if (-not $finished) {
        $tail = @(Get-Content "$log.out" -Encoding utf8 -Tail 30 | Where-Object { $_ -notmatch '^::' })
        $text = ($tail | ForEach-Object { $_ -replace '%', '%25' -replace "`r", '' }) -join '%0A'
        Write-Host "::error title=$Title hung::no end after $Seconds s; the output stopped at:%0A$text"
        return $null
    }
    return $process.ExitCode
}

$languages = Get-WinUserLanguageList
$languages.Add("ru-RU")
Set-WinUserLanguageList $languages -Force

# AutoHotkey, for the scripts manager and for keys typed by a script
$code = Invoke-Limited "AutoHotkey install" "choco" @("install", "autohotkey", "--no-progress", "-y") 300
if ($code -in 0, 3010) { $env:SWITCHER_EXPECT_AHK = "1" }
else { Write-Host "::warning::AutoHotkey could not be installed (exit '$code'): its checks are skipped" }

$code = Invoke-Limited "Notepad E2E" "python" @("tools/windows_smoke.py") 480
Get-Process notepad, AutoHotkey* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
if ($code -ne 0) { exit 1 }
exit 0
