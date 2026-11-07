# Telugu voice-over client. Windows PowerShell 5.1 compatible. This file is ASCII on purpose:
# PowerShell 5.1 misreads UTF-8 scripts without a BOM.
#
#   tts.ps1 voice | make | status | full | watch | download | live [text-file]
#
# Every server call goes through bin/rpc.sh with base64 arguments, so spaces and Telugu in
# names can never be damaged on the way. Server replies are ASCII JSON.

param(
    [Parameter(Position = 0)][string]$Command = 'help',
    [Parameter(Position = 1)][string]$Arg1 = ''
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Utf8 = New-Object System.Text.UTF8Encoding($false)
try { [Console]::OutputEncoding = $Utf8; $OutputEncoding = $Utf8 } catch { }

$Dirs = @{
    Clips  = [IO.Path]::Combine($Root, '01_voice_clips')
    Inbox  = [IO.Path]::Combine($Root, '02_scripts_inbox')
    Output = [IO.Path]::Combine($Root, '03_output')
    Done   = [IO.Path]::Combine($Root, '04_done')
    Logs   = [IO.Path]::Combine($Root, 'logs')
}
$StateDir = [IO.Path]::Combine($Dirs.Logs, 'state')
$ConfigPath = [IO.Path]::Combine($Root, 'config.ini')
$RemoteBin = '/home/pavan/voice/bin'
$RemoteIncoming = '/home/pavan/voice/voices/_incoming'
$ServerUser = 'pavan'
$ClipTypes = @('.mp3', '.m4a', '.wav', '.ogg', '.opus')
$ScriptTypes = @('.md', '.txt')
$MaxNetworkErrors = 30
$DebounceSeconds = 10
$TunnelPort = 8000

# ---------------------------------------------------------------- basics

function Write-Log([string]$Text) {
    try {
        $file = [IO.Path]::Combine($Dirs.Logs, ((Get-Date -Format 'yyyy-MM-dd') + '.log'))
        $line = (Get-Date -Format 'HH:mm:ss') + ' ' + $Text + [Environment]::NewLine
        [IO.File]::AppendAllText($file, $line, $Utf8)
    } catch { }
}

function Say([string]$Text, [string]$Color = 'Gray') {
    Write-Host $Text -ForegroundColor $Color
    Write-Log $Text
}

function Ensure-Dir([string]$Path) {
    [void][IO.Directory]::CreateDirectory($Path)
}

function Initialize-Client {
    foreach ($dir in $Dirs.Values) { Ensure-Dir $dir }
    Ensure-Dir $StateDir
    if (-not [IO.File]::Exists($ConfigPath)) {
        $default = "ServerAlias=tts-server`r`nVoice=pavan`r`nTestOnly=true`r`nPollSeconds=20`r`n"
        [IO.File]::WriteAllText($ConfigPath, $default, $Utf8)
    }
}

function Read-Config {
    $config = @{ ServerAlias = 'tts-server'; Voice = 'pavan'; TestOnly = 'true'; PollSeconds = '20' }
    foreach ($line in [IO.File]::ReadAllLines($ConfigPath)) {
        $line = $line.Trim([char[]]@(0xFEFF, 32, 9))
        if ($line -eq '' -or $line.StartsWith('#') -or $line.StartsWith(';')) { continue }
        $pair = $line.Split('=', 2)
        if ($pair.Count -eq 2) { $config[$pair[0].Trim()] = $pair[1].Trim() }
    }
    $seconds = 20
    if (-not [int]::TryParse($config.PollSeconds, [ref]$seconds) -or $seconds -lt 2) { $seconds = 20 }
    $config.PollSeconds = $seconds
    $config.TestOnly = ($config.TestOnly -match '^(true|yes|1)$')
    return $config
}

function Quote-Arg([string]$Value) {
    # Windows argument rules: double any backslashes before a quote, and escape the quote.
    if ($Value -ne '' -and $Value -notmatch '[\s"]') { return $Value }
    $escaped = [regex]::Replace($Value, '(\\*)"', { param($m) $m.Groups[1].Value + $m.Groups[1].Value + '\"' })
    $escaped = [regex]::Replace($escaped, '(\\+)$', { param($m) $m.Groups[1].Value + $m.Groups[1].Value })
    return '"' + $escaped + '"'
}

function New-ProcessInfo([string]$Exe, [string[]]$ArgList) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Exe
    $info.Arguments = (($ArgList | ForEach-Object { Quote-Arg $_ }) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = $Utf8
    $info.StandardErrorEncoding = $Utf8
    return $info
}

function Invoke-Program([string]$Exe, [string[]]$ArgList) {
    # A Process, not the & operator: PS 5.1 mangles native stderr, quotes and encodings.
    $process = [System.Diagnostics.Process]::Start((New-ProcessInfo $Exe $ArgList))
    $process.StandardInput.Close()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    return @{ Code = $process.ExitCode; Out = $stdoutTask.Result; Err = $stderrTask.Result.Trim() }
}

function Get-SshOptions {
    return @('-o', 'BatchMode=yes', '-o', 'ServerAliveInterval=30', '-o', 'ConnectTimeout=20')
}

function Connection-Help([string]$Detail) {
    return "Cannot connect to the server '$($Config.ServerAlias)'. Check your internet and that the server is powered on, then try again. Test it with: ssh $($Config.ServerAlias) whoami. Details: $Detail"
}

function Invoke-Rpc([string]$Name, [string[]]$ArgList = @()) {
    # Throws 'NETWORK: ...' for connection trouble, and the server's own sentence otherwise.
    $encoded = @()
    foreach ($value in $ArgList) { $encoded += [Convert]::ToBase64String($Utf8.GetBytes($value)) }
    $remote = "$RemoteBin/rpc.sh $Name $($encoded -join ' ')".Trim()
    $result = Invoke-Program 'ssh' ((Get-SshOptions) + @($Config.ServerAlias, $remote))
    if ($result.Code -eq 255) { throw "NETWORK: $($result.Err)" }
    if ($result.Code -ne 0) {
        $message = $result.Err
        if ($message -eq '') { $message = "The server command '$Name' failed (exit code $($result.Code))." }
        throw $message
    }
    $lines = @($result.Out -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
    if ($lines.Count -eq 0) { return $null }
    return ConvertFrom-Json -InputObject $lines[$lines.Count - 1]
}

function Invoke-Scp([string[]]$ArgList, [string]$What) {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $result = Invoke-Program 'scp' ((Get-SshOptions) + @('-q') + $ArgList)
        if ($result.Code -eq 0) { return }
        Say "  Transfer of $What failed (attempt $attempt of 3): $($result.Err)" 'Yellow'
        if ($attempt -lt 3) { Start-Sleep -Seconds 5 }
    }
    throw "Could not transfer $What after 3 attempts. Check your connection and try again."
}

function Test-Server {
    # Files made by any other account would break the jobs, so this is checked first.
    $reply = $null
    try { $reply = Invoke-Rpc 'ping' }
    catch {
        $text = $_.Exception.Message
        if ($text.StartsWith('NETWORK:')) { throw (Connection-Help $text.Substring(8).Trim()) }
        throw "The server is reachable, but the voice tools did not answer. Details: $text"
    }
    if ($reply.user -ne $ServerUser) {
        throw "The SSH alias '$($Config.ServerAlias)' logs in as '$($reply.user)', but it must log in as '$ServerUser'. See 'First-time setup' in README_WINDOWS.txt."
    }
}

function Get-Slug([string]$Text) {
    $slug = ([regex]::Replace($Text.ToLowerInvariant(), '[^a-z0-9]+', '-')).Trim('-')
    if ($slug.Length -gt 30) { $slug = $slug.Substring(0, 30).Trim('-') }
    if ($slug -eq '') { $slug = 'item' }
    return $slug
}

function Get-UniquePath([string]$Folder, [string]$Name) {
    $target = [IO.Path]::Combine($Folder, $Name)
    if (-not (Test-Path -LiteralPath $target)) { return $target }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $base = [IO.Path]::GetFileNameWithoutExtension($Name)
    $extension = [IO.Path]::GetExtension($Name)
    return [IO.Path]::Combine($Folder, "${base}_$stamp$extension")
}

function Move-Path([string]$From, [string]$To) {
    # .NET moves treat names literally, so [brackets] in a file name are safe.
    if ([IO.Directory]::Exists($From)) { [IO.Directory]::Move($From, $To) }
    else { [IO.File]::Move($From, $To) }
}

function Move-ToDone([string]$Path, [string]$SubFolder = '') {
    $folder = $Dirs.Done
    if ($SubFolder -ne '') { $folder = [IO.Path]::Combine($folder, $SubFolder); Ensure-Dir $folder }
    $target = Get-UniquePath $folder ([IO.Path]::GetFileName($Path))
    Move-Path $Path $target
    return $target
}

function Play-Notification {
    try {
        [System.Media.SystemSounds]::Asterisk.Play()
        Start-Sleep -Milliseconds 800
        [System.Media.SystemSounds]::Asterisk.Play()
    } catch { }
}

function Show-JobFailure([string]$JobDir, [string]$Message) {
    Say '' 'Red'
    Say 'THE JOB FAILED' 'Red'
    Say "Reason: $Message" 'Red'
    try {
        $log = Invoke-Rpc 'job_log' @($JobDir, '20')
        if ($log -and $log.lines) {
            Say '--- last lines of the server log ---' 'Yellow'
            foreach ($line in $log.lines) { Say $line 'Gray' }
            Say '------------------------------------' 'Yellow'
        }
    } catch { Say "(Could not read the server log: $($_.Exception.Message))" 'Yellow' }
    Say 'Your input was not moved. Fix the problem and run this again; a failed job continues where it stopped.' 'Yellow'
}

# ---------------------------------------------------------------- voices

function Show-AutoTranscriptWarning([string]$VoiceName, [string]$Transcript) {
    Write-Host ''
    Say '**************************************************************' 'Red'
    Say "  CHECK THE TRANSCRIPT OF VOICE '$VoiceName'" 'Red'
    Say '  It was not checked by a person. Telugu speech recognition' 'Red'
    Say '  makes mistakes, and a wrong transcript is the most common' 'Red'
    Say '  cause of bad voice output.' 'Red'
    Say '**************************************************************' 'Red'
    if ($Transcript) { Say "Current transcript: $Transcript" 'Yellow' }
    Say "To fix it: put the clip and a '$VoiceName.txt' with the exact words (saved as UTF-8)" 'Yellow'
    Say 'in 01_voice_clips, then run 1_Add_My_Voice again.' 'Yellow'
}

function Add-Voices {
    Test-Server
    $clips = @(Get-ChildItem -LiteralPath $Dirs.Clips -File | Where-Object { $ClipTypes -contains $_.Extension.ToLowerInvariant() })
    if ($clips.Count -eq 0) {
        Say "No recordings found. Put one in: $($Dirs.Clips)" 'Yellow'
        Say 'The file name becomes the voice name, for example pavan.m4a. Types: mp3, m4a, wav, ogg, opus.' 'Yellow'
        return $true
    }
    $allGood = $true
    foreach ($clip in $clips) {
        $name = $clip.BaseName
        Say "Adding voice '$name' from $($clip.Name)..." 'Cyan'
        try {
            $id = (Get-Date -Format 'yyyyMMdd-HHmmss') + '_' + (Get-Slug $name) + '_' + (Get-Random -Maximum 99999)
            $remoteClip = "$RemoteIncoming/$id$($clip.Extension.ToLowerInvariant())"
            Invoke-Scp @($clip.FullName, "$($Config.ServerAlias):$remoteClip") $clip.Name
            $rpcArgs = @($name, $remoteClip)
            $transcriptPath = [IO.Path]::Combine($Dirs.Clips, "$name.txt")
            $hasTranscript = [IO.File]::Exists($transcriptPath)
            if ($hasTranscript) {
                $remoteText = "$RemoteIncoming/$id.txt"
                Invoke-Scp @($transcriptPath, "$($Config.ServerAlias):$remoteText") "$name.txt"
                $rpcArgs += $remoteText
            } else {
                Say '  No transcript file, so the server will write one automatically.' 'Yellow'
            }
            $reply = Invoke-Rpc 'voice_add' $rpcArgs
            if ($reply.unchanged) { Say "  Already in the library, nothing changed." 'Green' }
            else { Say "  Saved: $($reply.duration_seconds) s, $($reply.lufs) LUFS." 'Green' }
            foreach ($warning in $reply.warnings) {
                if ($warning -notmatch '^CHECK THE TRANSCRIPT') { Say "  WARNING: $warning" 'Yellow' }
            }
            if ($reply.transcript_auto) { Show-AutoTranscriptWarning $name $reply.transcript }
            else { Say "  Transcript: $($reply.transcript)" 'Gray' }
            [void](Move-ToDone $clip.FullName 'voices')
            if ($hasTranscript) { [void](Move-ToDone $transcriptPath 'voices') }
        } catch {
            $allGood = $false
            $text = $_.Exception.Message
            if ($text.StartsWith('NETWORK:')) { $text = Connection-Help $text.Substring(8).Trim() }
            Say "Could not add '$name': $text" 'Red'
            Say 'Your recording was not moved.' 'Yellow'
        }
    }
    return $allGood
}

function Show-VoiceReminder {
    try {
        foreach ($voice in (Invoke-Rpc 'voice_list')) {
            if ($voice.name -eq $Config.Voice -and $voice.transcript_auto) {
                Say "NOTE: the transcript of voice '$($voice.name)' has not been checked, which can lower the quality. Run 1_Add_My_Voice with a corrected '$($voice.name).txt' when you can." 'Yellow'
            }
        }
    } catch { }
}

# ---------------------------------------------------------------- jobs

function Wait-ForJob([string]$JobDir) {
    # Network drops are fine: the job keeps running on the server. Returns the final status.
    $errors = 0
    $lastLine = ''
    while ($true) {
        $status = $null
        try {
            $status = Invoke-Rpc 'job_status' @($JobDir)
            $errors = 0
        } catch {
            if (-not $_.Exception.Message.StartsWith('NETWORK:')) { throw }
            $errors++
            Say "Connection problem ($errors of $MaxNetworkErrors). The job keeps running on the server. Retrying..." 'Yellow'
            if ($errors -ge $MaxNetworkErrors) {
                throw 'The connection stayed down for too long. Run this again later to collect the finished job.'
            }
        }
        if ($status) {
            if ($status.state -eq 'done' -or $status.state -eq 'failed') { return $status }
            $line = "Slide $($status.progress) - $($status.phase)"
            if ($line -ne $lastLine) {
                Say ((Get-Date -Format 'HH:mm:ss') + "  $line") 'Cyan'
                $lastLine = $line
            }
        }
        Start-Sleep -Seconds $Config.PollSeconds
    }
}

function Save-JobOutput([string]$JobDir, [string]$JobName, [string]$Name) {
    # Downloads to a temporary folder and checks every size before touching earlier results.
    $safeName = [regex]::Replace($Name, '[<>:"/\\|?*]', '_')
    $dest = [IO.Path]::Combine($Dirs.Output, $safeName)
    Ensure-Dir $dest
    $partial = [IO.Path]::Combine($dest, ".partial_$JobName")
    if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Recurse -Force }
    $expected = Invoke-Rpc 'job_files' @($JobDir)
    Say 'Downloading the audio...' 'Cyan'
    Invoke-Scp @('-r', "$($Config.ServerAlias):$JobDir/out", $partial) 'the finished audio'
    foreach ($file in $expected) {
        $local = [IO.Path]::Combine($partial, $file.name)
        if (-not [IO.File]::Exists($local) -or (New-Object IO.FileInfo($local)).Length -ne $file.size) {
            throw "The download of $($file.name) is incomplete. Run this again to retry."
        }
    }
    # Earlier files (for example the test run) are kept, not overwritten.
    $earlier = @(Get-ChildItem -LiteralPath $dest | Where-Object { -not $_.Name.StartsWith('.partial_') -and -not $_.Name.StartsWith('_previous_') })
    if ($earlier.Count -gt 0) {
        $keep = [IO.Path]::Combine($dest, '_previous_' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        Ensure-Dir $keep
        foreach ($item in $earlier) {
            Move-Path $item.FullName ([IO.Path]::Combine($keep, $item.Name))
        }
    }
    foreach ($item in @(Get-ChildItem -LiteralPath $partial -File)) {
        Move-Path $item.FullName ([IO.Path]::Combine($dest, $item.Name))
    }
    Remove-Item -LiteralPath $partial -Recurse -Force
    [void](Invoke-Rpc 'job_downloaded' @($JobDir))
    return $dest
}

function Get-InboxItems {
    $items = @()
    foreach ($item in @(Get-ChildItem -LiteralPath $Dirs.Inbox)) {
        if ($item.Name.StartsWith('.') -or $item.Name.StartsWith('~')) { continue }
        if ($item.PSIsContainer) { $items += $item; continue }
        if ($ScriptTypes -contains $item.Extension.ToLowerInvariant()) { $items += $item }
    }
    return $items
}

function Get-SlideFiles($Folder) {
    return @(Get-ChildItem -LiteralPath $Folder.FullName -File |
        Where-Object { $_.Extension -ieq '.txt' -and -not $_.Name.StartsWith('.') } |
        Sort-Object { [regex]::Replace($_.Name.ToLowerInvariant(), '\d+', { param($m) $m.Value.PadLeft(20, '0') }) })
}

function Test-ItemReady($Item) {
    # False while Windows is still copying a file into the inbox.
    $files = @($Item)
    if ($Item.PSIsContainer) { $files = @(Get-ChildItem -LiteralPath $Item.FullName -File) }
    foreach ($file in $files) {
        try {
            $stream = [IO.File]::Open($file.FullName, 'Open', 'Read', 'None')
            $stream.Close()
        } catch { return $false }
    }
    return $true
}

function Get-ItemHash($Item) {
    if (-not $Item.PSIsContainer) { return (Get-FileHash -LiteralPath $Item.FullName -Algorithm SHA256).Hash }
    $parts = @()
    foreach ($file in (Get-SlideFiles $Item)) {
        $parts += $file.Name + ':' + (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $bytes = $sha.ComputeHash($Utf8.GetBytes(($parts -join '|')))
    return ([BitConverter]::ToString($bytes) -replace '-', '')
}

function Send-ItemToJob($Item, [string]$JobDir) {
    if ($Item.PSIsContainer) {
        $slides = Get-SlideFiles $Item
        $number = 0
        foreach ($slide in $slides) {
            # Plain numbered names on the server keep this PC's order and avoid name problems.
            $number++
            $remote = '{0}/in/slide_{1:D4}.txt' -f $JobDir, $number
            Invoke-Scp @($slide.FullName, "$($Config.ServerAlias):$remote") $slide.Name
        }
    } else {
        $remoteName = 'script.md'
        if ($Item.Extension -ieq '.txt') { $remoteName = 'slide_0001.txt' }
        Invoke-Scp @($Item.FullName, "$($Config.ServerAlias):$JobDir/in/$remoteName") $Item.Name
    }
}

function Complete-Job([string]$JobDir, [string]$Name) {
    # Waits, then downloads. Returns the output folder, or $null if the job failed.
    $status = Wait-ForJob $JobDir
    if ($status.state -eq 'failed') {
        Show-JobFailure $JobDir $status.error
        return $null
    }
    $dest = Save-JobOutput $JobDir $status.job $Name
    if ($status.metrics) {
        Say "Made $($status.metrics.files) file(s), $($status.metrics.audio_minutes) minutes of audio." 'Green'
    }
    return $dest
}

function Invoke-Item-Job($Item) {
    # Returns $true on success. Safe to repeat: state remembers a job already on the server.
    $name = $Item.BaseName
    if ($Item.PSIsContainer) { $name = $Item.Name }
    Say ''
    Say "=== $($Item.Name) ===" 'Cyan'
    $mode = 'script'
    if ($Item.PSIsContainer -or $Item.Extension -ieq '.txt') { $mode = 'slides' }
    if ($Item.PSIsContainer -and (Get-SlideFiles $Item).Count -eq 0) {
        Say "The folder '$($Item.Name)' has no .txt files, so it was skipped." 'Yellow'
        return $false
    }
    $testText = 'false'
    if ($Config.TestOnly) { $testText = 'true' }
    $stateFile = [IO.Path]::Combine($StateDir, ((Get-ItemHash $Item) + "_$testText.json"))

    try {
        $jobDir = $null
        $needsUpload = $true
        if ([IO.File]::Exists($stateFile)) {
            $saved = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($stateFile, $Utf8))
            try {
                $current = Invoke-Rpc 'job_status' @($saved.job_dir)
                $jobDir = $saved.job_dir
                $needsUpload = -not $current.launched
                Say "Continuing the earlier job $($current.job) ($($current.state))." 'Cyan'
            } catch {
                if ($_.Exception.Message.StartsWith('NETWORK:')) { throw }
                [IO.File]::Delete($stateFile)   # the server no longer has it, so start again
            }
        }
        if (-not $jobDir) {
            $created = Invoke-Rpc 'job_create' @($name, $Config.Voice, $mode, $testText)
            $jobDir = $created.job_dir
            [IO.File]::WriteAllText($stateFile, (@{ job_dir = $jobDir; name = $name } | ConvertTo-Json -Compress), $Utf8)
            Say "Created job $($created.job)." 'Gray'
        }
        if ($needsUpload) {
            Say 'Uploading...' 'Cyan'
            Send-ItemToJob $Item $jobDir
        }
        $started = Invoke-Rpc 'job_start' @($jobDir)
        if ($started.started) { Say 'Started on the server. It keeps running even if this PC disconnects.' 'Gray' }

        $dest = Complete-Job $jobDir $name
        if (-not $dest) { return $false }

        [void](Move-ToDone $Item.FullName)
        [IO.File]::Delete($stateFile)
        Say "DONE. Your audio is in: $dest" 'Green'
        if ($Config.TestOnly) {
            Say 'This was a TEST of the first 3 sections. Listen to it. If it sounds right, run 4_Full_Batch_Approved.' 'Yellow'
        }
        Play-Notification
        return $true
    } catch {
        $text = $_.Exception.Message
        if ($text.StartsWith('NETWORK:')) { $text = Connection-Help $text.Substring(8).Trim() }
        Say "Could not finish '$($Item.Name)': $text" 'Red'
        Say 'Your input was not moved. Run this again to retry.' 'Yellow'
        return $false
    }
}

function Start-Voiceovers {
    Test-Server
    $items = @(Get-InboxItems)
    if ($items.Count -eq 0) {
        Say "Nothing to do. Put a .md script or a folder of slide_01.txt files in: $($Dirs.Inbox)" 'Yellow'
        return $true
    }
    Show-VoiceReminder
    $allGood = $true
    foreach ($item in $items) {
        if (-not (Test-ItemReady $item)) {
            Say "'$($item.Name)' is still being copied, so it was skipped for now." 'Yellow'
            $allGood = $false
            continue
        }
        if (-not (Invoke-Item-Job $item)) { $allGood = $false }
    }
    return $allGood
}

function Show-Status {
    Test-Server
    $jobs = Invoke-Rpc 'job_list' @('15')
    $any = $false
    Write-Host ''
    foreach ($job in $jobs) {
        $any = $true
        $color = 'Gray'
        if ($job.state -eq 'running') { $color = 'Cyan' }
        elseif ($job.state -eq 'done') { $color = 'Green' }
        elseif ($job.state -eq 'failed') { $color = 'Red' }
        $kind = 'full'
        if ($job.test_only) { $kind = 'test' }
        $note = ''
        if ($job.downloaded) { $note = ' (downloaded)' }
        Write-Host ("{0,-8} {1,-7} {2,-5} {3}  {4}{5}" -f $job.state, $job.progress, $kind, $job.job, $job.name, $note) -ForegroundColor $color
        if ($job.state -eq 'running' -or $job.state -eq 'queued') { Write-Host "         $($job.phase)" -ForegroundColor Gray }
        if ($job.state -eq 'failed' -and $job.error) { Write-Host "         $($job.error)" -ForegroundColor Red }
    }
    if (-not $any) { Say 'There are no jobs yet.' }
    return $true
}

function Start-FullBatch {
    Test-Server
    $test = Invoke-Rpc 'job_last_test'
    if (-not $test) {
        Say 'No finished test job found. Run 2_Make_Voiceover first, with TestOnly=true in config.ini.' 'Yellow'
        return $false
    }
    Say "Approving the test job: $($test.job) ($($test.name))" 'Cyan'
    try {
        $full = Invoke-Rpc 'job_clone_full' @($test.job_dir)
        $status = Invoke-Rpc 'job_status' @($full.job_dir)
        if ($status.state -eq 'done' -and $status.downloaded) {
            Say "This full batch is already finished and downloaded: $($Dirs.Output)\$($test.name)" 'Green'
            return $true
        }
        $started = Invoke-Rpc 'job_start' @($full.job_dir)
        if ($started.started) { Say 'Full batch started on the server. It keeps running even if this PC disconnects.' 'Gray' }
        else { Say "Continuing the full batch $($full.job)." 'Gray' }
        $dest = Complete-Job $full.job_dir $test.name
        if (-not $dest) { return $false }
        Say "DONE. Your full audio is in: $dest" 'Green'
        Play-Notification
        return $true
    } catch {
        $text = $_.Exception.Message
        if ($text.StartsWith('NETWORK:')) { $text = Connection-Help $text.Substring(8).Trim() }
        Say "Could not finish the full batch: $text" 'Red'
        return $false
    }
}

function Start-WatchMode {
    Test-Server
    Say "Watching $($Dirs.Inbox). New files start automatically. Close this window to stop." 'Cyan'
    $watcher = New-Object IO.FileSystemWatcher($Dirs.Inbox)
    $watcher.IncludeSubdirectories = $true
    $failed = @{}          # hash of an item that failed: it waits until the file changes
    $pending = $true       # also picks up files already there
    $lastChange = (Get-Date).AddSeconds(-$DebounceSeconds)
    $lastScan = Get-Date
    while ($true) {
        $event2 = $watcher.WaitForChanged([IO.WatcherChangeTypes]::All, 2000)
        if (-not $event2.TimedOut) {
            $pending = $true
            $lastChange = Get-Date
            continue
        }
        if (((Get-Date) - $lastScan).TotalSeconds -ge 60) { $pending = $true; $lastScan = Get-Date }
        if (-not $pending -or ((Get-Date) - $lastChange).TotalSeconds -lt $DebounceSeconds) { continue }
        $pending = $false
        foreach ($item in @(Get-InboxItems)) {
            try {
                if (-not (Test-ItemReady $item)) { $pending = $true; continue }
                $hash = Get-ItemHash $item
                if ($failed.ContainsKey($hash)) { continue }
                if (-not (Invoke-Item-Job $item)) {
                    $failed[$hash] = $true
                    Say "'$($item.Name)' will not be retried until you change the file or restart Watch Mode." 'Yellow'
                }
            } catch {
                Say "Problem with '$($item.Name)': $($_.Exception.Message)" 'Red'
            }
        }
    }
}

# ---------------------------------------------------------------- download everything

function Test-JobFolderComplete([string]$Folder, $Expected) {
    foreach ($file in $Expected) {
        $local = [IO.Path]::Combine($Folder, $file.name)
        if (-not [IO.File]::Exists($local) -or (New-Object IO.FileInfo($local)).Length -ne $file.size) { return $false }
    }
    return $true
}

function Get-AllOutputs {
    # Copies only. Nothing on the server is changed or deleted, and earlier downloads stay.
    Test-Server
    $root = [IO.Path]::Combine($Dirs.Output, 'all_jobs')
    Ensure-Dir $root
    $copied = 0; $skipped = 0; $failed = 0; $notReady = 0
    foreach ($job in (Invoke-Rpc 'job_list' @('100000'))) {
        if ($job.state -ne 'done') { $notReady++; continue }
        $dest = [IO.Path]::Combine($root, $job.job)
        try {
            $expected = @(Invoke-Rpc 'job_files' @($job.job_dir))
            if (Test-JobFolderComplete $dest $expected) { $skipped++; continue }
            Say "Downloading $($job.job) ($($expected.Count) files)..." 'Cyan'
            $partial = [IO.Path]::Combine($root, ".partial_$($job.job)_$PID")
            if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Recurse -Force }
            Invoke-Scp @('-r', "$($Config.ServerAlias):$($job.job_dir)/out", $partial) $job.job
            if (-not (Test-JobFolderComplete $partial $expected)) { throw 'A file arrived with the wrong size.' }
            Ensure-Dir $dest
            foreach ($item in @(Get-ChildItem -LiteralPath $partial -File)) {
                [IO.File]::Copy($item.FullName, [IO.Path]::Combine($dest, $item.Name), $true)
            }
            Remove-Item -LiteralPath $partial -Recurse -Force
            $copied++
        } catch {
            $failed++
            $text = $_.Exception.Message
            if ($text.StartsWith('NETWORK:')) { $text = Connection-Help $text.Substring(8).Trim() }
            Say "Could not download $($job.job): $text" 'Red'
        }
    }
    Say ''
    Say "Downloaded $copied job(s). Already complete: $skipped. Failed: $failed. Not finished or failed on the server: $notReady." 'Green'
    Say "Your files are in: $root" 'Green'
    if ($failed -gt 0) { Say 'Run this again to retry. Finished downloads will be skipped.' 'Yellow' }
    return ($failed -eq 0)
}

# ---------------------------------------------------------------- live mode

function Start-Live([string]$TextFile) {
    Test-Server
    if ($TextFile -eq '') { $TextFile = [IO.Path]::Combine($Root, 'live_text.txt') }
    if (-not [IO.File]::Exists($TextFile)) {
        Say "Put the Telugu text in this file (saved as UTF-8), then run this again: $TextFile" 'Yellow'
        return $false
    }
    $text = [IO.File]::ReadAllText($TextFile).Trim()   # detects UTF-8 and UTF-16 byte order marks
    if ($text -eq '') { Say "The file is empty: $TextFile" 'Yellow'; return $false }

    $forward = "${TunnelPort}:127.0.0.1:$TunnelPort"
    $tunnelArgs = @('-N', '-L', $forward, '-o', 'ExitOnForwardFailure=yes') + (Get-SshOptions) + @($Config.ServerAlias)
    $tunnel = [System.Diagnostics.Process]::Start((New-ProcessInfo 'ssh' $tunnelArgs))
    try {
        $base = "http://127.0.0.1:$TunnelPort"
        $healthy = $null
        for ($i = 0; $i -lt 30 -and -not $healthy; $i++) {
            Start-Sleep -Seconds 1
            try { $healthy = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 5 -UseBasicParsing } catch { }
        }
        if (-not $healthy) {
            Say 'The live service did not answer. On the server, run: systemctl --user status tts-api' 'Red'
            return $false
        }
        Say "Connected. Generating about $($text.Length) characters..." 'Cyan'
        $body = $Utf8.GetBytes((@{ text = $text; voice = $Config.Voice } | ConvertTo-Json -Compress))
        $folder = [IO.Path]::Combine($Dirs.Output, 'live')
        Ensure-Dir $folder
        $target = [IO.Path]::Combine($folder, ('live_' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.mp3'))
        try {
            Invoke-WebRequest -Uri "$base/speak" -Method Post -Body $body -ContentType 'application/json; charset=utf-8' -OutFile $target -TimeoutSec 900 -UseBasicParsing
        } catch {
            $detail = $_.Exception.Message
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $detail = $_.ErrorDetails.Message }
            Say "The live service could not make the audio: $detail" 'Red'
            return $false
        }
        Say "DONE. Saved: $target" 'Green'
        Play-Notification
        return $true
    } finally {
        if (-not $tunnel.HasExited) { $tunnel.Kill() }
    }
}

# ---------------------------------------------------------------- start

function Enter-SingleCopy {
    # Two copies working on one job fight over its download folder. Windows frees this
    # automatically when a window closes, even after a crash.
    $script:ClientMutex = New-Object System.Threading.Mutex($false, 'Local\TeluguVoiceClient')
    try { $script:HasClientMutex = $script:ClientMutex.WaitOne(0) }
    catch {
        if ($_.Exception.GetBaseException() -isnot [System.Threading.AbandonedMutexException]) { throw }
        $script:HasClientMutex = $true
    }
    return $script:HasClientMutex
}

function Show-Help {
    Write-Host 'Double-click the numbered .bat files, or run: tts.ps1 voice | make | status | full | watch | download | live [file]'
}

$exitCode = 0
try {
    Initialize-Client
    $Config = Read-Config
    Write-Log "--- command: $Command"
    $ok = $true
    if (@('voice', 'make', 'full', 'watch') -contains $Command.ToLowerInvariant()) {
        if (-not (Enter-SingleCopy)) {
            Say 'Another window of this tool is already running, so this one did nothing.' 'Yellow'
            Say 'Your job is safe. Use the other window, or close it (including Watch_Mode) and run this again.' 'Yellow'
            exit 1
        }
    }
    switch ($Command.ToLowerInvariant()) {
        'voice'  { $ok = Add-Voices }
        'make'   { $ok = Start-Voiceovers }
        'status' { $ok = Show-Status }
        'full'   { $ok = Start-FullBatch }
        'watch'  { Start-WatchMode }
        'download' { $ok = Get-AllOutputs }
        'live'   { $ok = Start-Live $Arg1 }
        'init'   { Say "Folders are ready in: $Root" 'Green' }
        default  { Show-Help }
    }
    if ($ok -ne $true) { $exitCode = 1 }
} catch {
    Say $_.Exception.Message 'Red'
    Write-Log $_.ScriptStackTrace
    $exitCode = 1
}
exit $exitCode
