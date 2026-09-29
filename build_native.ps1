param(
    [ValidateSet('windows', 'android', 'all')]
    [string]$Target = 'all'
)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed ($LASTEXITCODE)" }
}

Get-Command flutter -ErrorAction Stop | Out-Null
Push-Location (Join-Path $Root 'native_app')
try {
    Invoke-Checked 'flutter' @('pub', 'get')
    Invoke-Checked 'flutter' @('analyze')
    Invoke-Checked 'flutter' @('test')

    if ($Target -eq 'windows' -or $Target -eq 'all') {
        $BuildDir = Join-Path $Root '.native-build'
        New-Item -ItemType Directory -Force $BuildDir | Out-Null
        $Python = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
        Invoke-Checked $Python @('-m', 'venv', (Join-Path $BuildDir 'venv'))
        $VenvPython = Join-Path $BuildDir 'venv/Scripts/python.exe'
        Invoke-Checked $VenvPython @('-m', 'pip', 'install', '-r', (Join-Path $Root 'native_service/requirements.txt'), 'pyinstaller>=6.15')
        Invoke-Checked $VenvPython @('-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--console',
            '--name', 'erm_hub', '--distpath', (Join-Path $BuildDir 'dist'),
            '--workpath', (Join-Path $BuildDir 'work'), '--specpath', $BuildDir,
            (Join-Path $Root 'native_service/server.py'))
        Invoke-Checked 'flutter' @('build', 'windows', '--release')
        $Release = Join-Path $Root 'native_app/build/windows/x64/runner/Release'
        Copy-Item (Join-Path $BuildDir 'dist/erm_hub.exe') $Release -Force
        $Dist = Join-Path $Root 'native_app/dist'
        New-Item -ItemType Directory -Force $Dist | Out-Null
        Compress-Archive -Path "$Release/*" -DestinationPath (Join-Path $Dist 'ERM-Windows.zip') -Force
        Write-Host "Windows portable app: $Dist/ERM-Windows.zip"
        $Iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($Iscc) {
            Invoke-Checked $Iscc.Source @((Join-Path $Root 'native_app/installer.iss'))
        } else {
            Write-Host 'Optional: install Inno Setup 6 and run ISCC.exe native_app/installer.iss for a Setup.exe.'
        }
    }

    if ($Target -eq 'android' -or $Target -eq 'all') {
        Invoke-Checked 'flutter' @('build', 'apk', '--release')
        Write-Host 'Android APK: native_app/build/app/outputs/flutter-apk/app-release.apk'
    }
} finally {
    Pop-Location
}
