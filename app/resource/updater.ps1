$ErrorActionPreference = 'Stop'
$work = [IO.Path]::GetFullPath($PSScriptRoot)
$lock = $null
$movedOld = $false
$installedNew = $false
$movedPrevious = $false
$committed = $false

function Assert-OrdinaryDirectory([string]$path) {
    $item = Get-Item -LiteralPath $path -Force
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Update paths must be ordinary directories'
    }
}

function Move-Directory([string]$source, [string]$destination) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try {
            [IO.Directory]::Move($source, $destination)
            return
        } catch {
            if ($attempt -eq 19) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
}

function Start-Seraphine([string]$directory) {
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = Join-Path $directory 'Seraphine.exe'
    $start.WorkingDirectory = $directory
    $start.UseShellExecute = $false
    return [Diagnostics.Process]::Start($start)
}

try {
    $plan = Get-Content -LiteralPath (Join-Path $work 'plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $target = [IO.Path]::GetFullPath($plan.target)
    $parent = [IO.Path]::GetDirectoryName($target)
    $name = [IO.Path]::GetFileName($target)
    $stage = Join-Path $work 'Seraphine'
    $backup = Join-Path $parent ('.' + $name + '-previous')
    $older = Join-Path $work 'older-backup'
    if ([IO.Path]::GetDirectoryName($work) -ne $parent -or
        -not [IO.Path]::GetFileName($work).StartsWith('.' + $name + '-update-')) {
        throw 'Update workspace is outside the installation parent'
    }
    Assert-OrdinaryDirectory $work
    Assert-OrdinaryDirectory $target
    Assert-OrdinaryDirectory $stage
    $lockPath = Join-Path $parent ('.' + $name + '-update.lock')
    $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
    if (Test-Path -LiteralPath $backup) {
        Assert-OrdinaryDirectory $backup
        $marker = Get-Content -LiteralPath (Join-Path $backup '.seraphine-backup.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($marker.target -ne $target) { throw 'Existing backup belongs to another installation' }
    }
    $owner = Get-Process -Id $plan.parent_pid -ErrorAction SilentlyContinue
    Set-Content -LiteralPath (Join-Path $work 'ready') -Value 'ready' -Encoding UTF8
    if ($owner -and -not $owner.WaitForExit(60000)) { throw 'Seraphine did not exit in time' }
    if (Test-Path -LiteralPath $backup) {
        Move-Directory $backup $older
        $movedPrevious = $true
    }
    Move-Directory $target $backup
    $movedOld = $true
    @{target=$target} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backup '.seraphine-backup.json') -Encoding UTF8
    Move-Directory $stage $target
    $installedNew = $true
    $started = Start-Seraphine $target
    if ($started.WaitForExit(3000) -and $started.ExitCode -ne 0) { throw 'New version failed to start' }
    $committed = $true
    Set-Content -LiteralPath (Join-Path $work 'result.txt') -Value 'success' -Encoding UTF8
    if ($movedPrevious) {
        Assert-OrdinaryDirectory $older
        if ((Resolve-Path -LiteralPath $older).Path -ne (Join-Path $work 'older-backup')) {
            throw 'Backup cleanup path changed'
        }
        $links = Get-ChildItem -LiteralPath $older -Recurse -Force |
            Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }
        if ($links) { throw 'Previous backup contains linked files; retained for manual cleanup' }
        Remove-Item -LiteralPath $older -Recurse -Force
    }
} catch {
    $failure = $_.Exception.Message
    if (-not $committed) {
        try {
            if ($installedNew) { Move-Directory $target $stage }
            if ($movedOld) {
                Move-Directory $backup $target
                $restoredMarker = Join-Path $target '.seraphine-backup.json'
                if (Test-Path -LiteralPath $restoredMarker) { Remove-Item -LiteralPath $restoredMarker -Force }
            }
            if ($movedPrevious) { Move-Directory $older $backup }
            if ($movedOld) {
                $restored = Start-Seraphine $target
            }
        } catch {
            $failure += '; restore: ' + $_.Exception.Message
        }
    }
    Set-Content -LiteralPath (Join-Path $work 'error.txt') -Value $failure -Encoding UTF8
    exit 1
} finally {
    if ($lock) { $lock.Dispose() }
}
