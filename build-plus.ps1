param(
    [string]$OutputDirectory = 'dist',
    [string]$BuildDirectory = 'build'
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$aliasPath = Join-Path $env:LOCALAPPDATA 'SeraphinePlusBuild'
if (Test-Path -LiteralPath $aliasPath) {
    $aliasItem = Get-Item -LiteralPath $aliasPath
    if ($aliasItem.LinkType -ne 'Junction' -or $aliasItem.Target -ne $PSScriptRoot) {
        throw 'Existing build alias has a different target'
    }
} else {
    New-Item -ItemType Junction -Path $aliasPath -Target $PSScriptRoot | Out-Null
}
$python = Join-Path $aliasPath '.venv\Scripts\python.exe'
$taskOutput = [IO.Path]::GetFullPath((Join-Path $aliasPath $OutputDirectory))
$taskWork = [IO.Path]::GetFullPath((Join-Path $aliasPath $BuildDirectory))
$voiceModel = Join-Path $PSScriptRoot 'models\sensevoice'
if (-not (Test-Path -LiteralPath (Join-Path $voiceModel 'model.int8.onnx'))) {
    throw 'Run .venv\Scripts\python.exe scripts\download_voice_model.py first'
}
& $python -m PyInstaller --noconfirm --windowed --name Seraphine --icon (Join-Path $aliasPath 'app/resource/images/logo.ico') --contents-directory . --distpath $taskOutput --workpath $taskWork --specpath $taskWork --add-data ((Join-Path $aliasPath 'app/resource') + ';app/resource') --add-data ((Join-Path $aliasPath 'LICENSE') + ';.') --collect-all sherpa_onnx --copy-metadata sherpa-onnx --copy-metadata sherpa-onnx-core --copy-metadata numpy (Join-Path $aliasPath 'main.py')
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
$taskInputOutput = Join-Path $taskWork 'input-dist'
$taskInputWork = Join-Path $taskWork 'input-build'
& $python -m PyInstaller --noconfirm --windowed --name SeraphineInput --contents-directory . --distpath $taskInputOutput --workpath $taskInputWork --specpath $taskWork (Join-Path $aliasPath 'keyboard_worker.py')
if ($LASTEXITCODE -ne 0) { throw 'Keyboard service build failed' }
$taskService = Join-Path $taskInputOutput 'SeraphineInput'
$taskBundle = Join-Path $taskOutput 'Seraphine'
Get-ChildItem -LiteralPath $taskService -Recurse -File | ForEach-Object {
    $taskRelative = $_.FullName.Substring($taskService.Length + 1)
    if ($taskRelative -eq 'base_library.zip') { return }
    $taskDestination = Join-Path $taskBundle $taskRelative
    if (-not (Test-Path -LiteralPath $taskDestination)) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $taskDestination) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $taskDestination
    } elseif ((Get-FileHash -LiteralPath $_.FullName).Hash -ne (Get-FileHash -LiteralPath $taskDestination).Hash) {
        if ($taskRelative -ne 'SeraphineInput.exe') { throw "Dependency mismatch: $taskRelative" }
        Copy-Item -LiteralPath $_.FullName -Destination $taskDestination -Force
    }
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '使用说明-增强版.md') -Destination (Join-Path $taskBundle '使用说明.md')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'MODIFICATIONS.md') -Destination $taskBundle
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'readme.md') -Destination $taskBundle
$taskDocs = Join-Path $taskBundle 'document'
New-Item -ItemType Directory -Path $taskDocs -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'document\UPSTREAM_README.md') -Destination $taskDocs
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'document\便携版使用说明.md') -Destination $taskDocs
$voiceDestination = Join-Path $taskBundle 'models'
New-Item -ItemType Directory -Path $voiceDestination -Force | Out-Null
Copy-Item -LiteralPath $voiceModel -Destination $voiceDestination -Recurse -Force
Write-Output "Free Windows bundle ready: $taskBundle"
