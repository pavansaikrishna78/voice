# Generate voice files from a Windows PC, with no prompts.
#
#   powershell -ExecutionPolicy Bypass -File voice.ps1 -Script "C:\scripts\Part2.md"
#
# Uploads the script, renders it on the Ubuntu GPU server, then downloads the files.
# The job keeps running on the server if this PC disconnects. To collect it later:
#   powershell -ExecutionPolicy Bypass -File voice.ps1 -JobId 20261107-101500-1234
#
# Exit code: 0 = files downloaded, 1 = failed. Safe to use from Task Scheduler.

param(
    [string]$Script,
    [string]$JobId,
    [string]$OutDir = "$HOME\Downloads\voice",
    [string]$Server = "user@YOUR_SERVER_IP",
    [string]$Key = "$HOME\.ssh\voice_server",
    [int]$PollSeconds = 30,
    [int]$TimeoutHours = 24
)

$ErrorActionPreference = "Stop"
$runner = "/home/pavan/voice/voice_job.sh"
$sshOpts = @("-i", $Key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20")

function Fail($message) {
    Write-Host "FAILED: $message" -ForegroundColor Red
    exit 1
}

function Remote($command) {
    $result = & ssh @sshOpts $Server $command
    if ($LASTEXITCODE -ne 0) { throw "ssh command failed: $command" }
    return $result
}

if (-not $Script -and -not $JobId) { Fail "Pass -Script <file.md> or -JobId <id>." }
if (-not (Test-Path $Key)) { Fail "SSH key not found: $Key" }

try {
    if ($Script) {
        if (-not (Test-Path $Script -PathType Leaf)) { Fail "Script not found: $Script" }
        $JobId = (Remote "$runner new" | Select-Object -First 1).Trim()
        & scp @sshOpts "$Script" "${Server}:/home/pavan/voice/jobs/$JobId/input.md"
        if ($LASTEXITCODE -ne 0) { Fail "Could not upload $Script" }
        Remote "$runner start $JobId" | Out-Null
        Write-Host "Started job $JobId"
    }
} catch {
    Fail $_.Exception.Message
}

# Wait for the job. Short network drops are tolerated, as the job runs on the server.
$deadline = (Get-Date).AddHours($TimeoutHours)
$networkErrors = 0
$state = ""
while ($true) {
    try {
        $state = (Remote "$runner status $JobId" | Select-Object -First 1).Trim()
        $networkErrors = 0
    } catch {
        $networkErrors++
        Write-Host "Connection problem ($networkErrors of 20), retrying..."
        if ($networkErrors -ge 20) { Fail "Lost the server. To collect it later, use -JobId $JobId" }
    }
    if ($state -eq "done") { break }
    if ($state -eq "failed") {
        Write-Host (Remote "$runner log $JobId" | Out-String)
        Fail "Job $JobId failed on the server (log above)."
    }
    if ((Get-Date) -gt $deadline) { Fail "Timed out. To collect it later, use -JobId $JobId" }
    Write-Host "$(Get-Date -Format HH:mm:ss) $state"
    Start-Sleep -Seconds $PollSeconds
}

$dest = Join-Path $OutDir $JobId
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$remoteOut = "/home/pavan/voice/jobs/$JobId/output"
& scp @sshOpts "${Server}:$remoteOut/Voice_*" "${Server}:$remoteOut/report.csv" "$dest\"
if ($LASTEXITCODE -ne 0) { Fail "Download failed. Retry with -JobId $JobId" }

Write-Host "DONE: $dest" -ForegroundColor Green
exit 0
