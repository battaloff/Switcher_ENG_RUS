# The Notepad end-to-end check on CI, with a time limit on each part.
#
# A part that hangs used to take the whole job with it (45 minutes), and a job that times out keeps
# no annotations at all - nothing to read.  So here:
#  - each part runs in a window of its own (no handles inherited), with its output in a file: a
#    process it leaves behind cannot hold this step's output open, which keeps a step from ending;
#  - each part is stopped after its limit, and its output is printed afterwards;
#  - what happened is also sent to GitHub as a check run ("Notepad check: ..."), readable through
#    the API even if the job never ends:
#      gh api repos/OWNER/REPO/commits/SHA/check-runs --jq '.check_runs[] | .name, .output.text'

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUNBUFFERED = "1"   # a stopped process must not take unwritten output with it
$logs = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }

function Send-Report([string]$Title, [string]$Text) {
    if (-not $env:GH_TOKEN -or -not $env:GITHUB_REPOSITORY) { return }
    if ($Text.Length -gt 60000) { $Text = $Text.Substring($Text.Length - 60000) }
    $body = @{
        name = "Notepad check: $Title"; head_sha = $env:GITHUB_SHA; status = "completed"; conclusion = "neutral"
        output = @{ title = $Title; summary = "Output of this part of the check (its end)."; text = "``````text`n$Text`n``````" }
    } | ConvertTo-Json -Depth 5
    try {
        $null = Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$env:GITHUB_REPOSITORY/check-runs" `
            -Headers @{ Authorization = "Bearer $env:GH_TOKEN"; Accept = "application/vnd.github+json" } `
            -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType "application/json; charset=utf-8" -TimeoutSec 30
    } catch { Write-Host "::warning::could not send the report '$Title': $_" }
}

function Invoke-Limited([string]$Title, [string]$Command, [int]$Seconds) {
    $log = Join-Path $logs (($Title -replace '\W', '_') + ".log")
    $process = Start-Process -FilePath "cmd.exe" -ArgumentList "/d /c `"$Command > `"$log`" 2>&1`"" `
        -PassThru -WindowStyle Hidden -WorkingDirectory (Get-Location).Path
    $null = $process.Handle   # without this the exit code can't be read later
    $finished = $process.WaitForExit($Seconds * 1000)
    if (-not $finished) { taskkill /T /F /PID $process.Id | Out-Null }
    $lines = @(if (Test-Path $log) { Get-Content $log -Encoding utf8 })
    $lines | ForEach-Object { Write-Host $_ }
    $code = if ($finished) { $process.ExitCode } else { $null }
    $state = if ($finished) { "exit code $code" } else { "STOPPED: no end after $Seconds s" }
    Send-Report "$Title - $state" ($lines -join "`n")
    if (-not $finished) {
        $tail = @($lines | Select-Object -Last 30 | Where-Object { $_ -notmatch '^::' })
        $text = ($tail | ForEach-Object { $_ -replace '%', '%25' -replace "`r", '' }) -join '%0A'
        Write-Host "::error title=$Title hung::no end after $Seconds s; the output stopped at:%0A$text"
    }
    return $code
}

$languages = Get-WinUserLanguageList
$languages.Add("ru-RU")
Set-WinUserLanguageList $languages -Force
Send-Report "started" "The Russian layout is added; installing AutoHotkey next."

# AutoHotkey, for the scripts manager and for keys typed by a script
$code = Invoke-Limited "AutoHotkey install" "choco install autohotkey --no-progress -y" 300
if ($code -in 0, 3010) { $env:SWITCHER_EXPECT_AHK = "1" }
else {
    Write-Host "::warning::AutoHotkey could not be installed (exit '$code'): its checks are skipped"
    Get-Process -Name "AutoHotkey*", "choco*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

$code = Invoke-Limited "Notepad E2E" "python tools/windows_smoke.py" 480
Get-Process -Name "notepad", "AutoHotkey*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
if ($code -ne 0) { exit 1 }
exit 0
