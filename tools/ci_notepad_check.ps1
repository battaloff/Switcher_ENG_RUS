# The Notepad end-to-end check on CI, with a time limit on each part.
#
# A part that hangs used to take the whole job with it (45 minutes), and a job that times out keeps
# no annotations at all - nothing to read.  So here:
#  - each part runs in a window of its own (no handles inherited), with its output in a file: a
#    process it leaves behind cannot hold this step's output open, which keeps a step from ending;
#  - each part is stopped after its limit, and its output is printed afterwards;
#  - what a part has written is sent to GitHub as a check run ("Notepad check: ...") while it runs
#    and when it ends, readable through the API even if the job never ends:
#      gh api repos/OWNER/REPO/commits/SHA/check-runs --jq '.check_runs[] | .name, .output.text'

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUNBUFFERED = "1"   # a stopped process must not take unwritten output with it
$logs = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }

function Send-Report([string]$Title, [string]$Text, [string]$Id = "", [bool]$Running = $false) {
    # A check run named after this part: created, or updated when $Id is given.  Returns its id.
    if (-not $env:GH_TOKEN -or -not $env:GITHUB_REPOSITORY) { return "" }
    if ($Text.Length -gt 60000) { $Text = $Text.Substring($Text.Length - 60000) }
    $body = @{ output = @{ title = $Title; summary = "Output of this part of the check (its end)."
                           text = "``````text`n$Text`n``````" } }
    if ($Running) { $body.status = "in_progress" } else { $body.status = "completed"; $body.conclusion = "neutral" }
    $uri = "https://api.github.com/repos/$env:GITHUB_REPOSITORY/check-runs"
    $method = "Post"
    if ($Id) { $uri = "$uri/$Id"; $method = "Patch" }
    else { $body.name = "Notepad check: " + ($Title -replace ' - .*$', ''); $body.head_sha = $env:GITHUB_SHA }
    try {
        $answer = Invoke-RestMethod -Method $method -Uri $uri -TimeoutSec 20 `
            -Headers @{ Authorization = "Bearer $env:GH_TOKEN"; Accept = "application/vnd.github+json" } `
            -Body ([Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 5))) `
            -ContentType "application/json; charset=utf-8"
        return [string]$answer.id
    } catch {
        Write-Host "::warning::could not send the report '$Title': $_"
        return $Id
    }
}

function Read-Log([string]$Path, [int]$Last = 0) {
    if (-not (Test-Path $Path)) { return @() }
    if ($Last) { return @(Get-Content $Path -Encoding utf8 -Tail $Last) }
    return @(Get-Content $Path -Encoding utf8)
}

function Invoke-Limited([string]$Title, [string]$Command, [int]$Seconds) {
    $log = Join-Path $logs (($Title -replace '\W', '_') + ".log")
    $process = Start-Process -FilePath "cmd.exe" -ArgumentList "/d /c `"$Command > `"$log`" 2>&1`"" `
        -PassThru -WindowStyle Hidden -WorkingDirectory (Get-Location).Path
    $null = $process.Handle   # without this the exit code can't be read later
    $report = Send-Report "$Title - running" "started" "" $true
    $deadline = (Get-Date).AddSeconds($Seconds)
    $finished = $true
    $sent = -1
    while (-not $process.WaitForExit(5000)) {
        if ((Get-Date) -gt $deadline) { $finished = $false; break }
        $size = if (Test-Path $log) { (Get-Item $log).Length } else { 0 }
        if ($size -ne $sent) {   # what it has written so far: there to read if everything stops here
            $sent = $size
            $report = Send-Report "$Title - running" ((Read-Log $log 300) -join "`n") $report $true
        }
    }
    if (-not $finished) { taskkill /T /F /PID $process.Id | Out-Null }
    $lines = Read-Log $log
    $lines | ForEach-Object { Write-Host $_ }
    $code = if ($finished) { $process.ExitCode } else { $null }
    $state = if ($finished) { "exit code $code" } else { "STOPPED: no end after $Seconds s" }
    $null = Send-Report "$Title - $state" ($lines -join "`n") $report $false
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
$null = Send-Report "started" "The Russian layout is added; installing AutoHotkey next."

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
