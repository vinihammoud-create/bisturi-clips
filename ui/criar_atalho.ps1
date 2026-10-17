$ErrorActionPreference = 'Stop'
# so ASCII neste arquivo: o PowerShell 5.1 le .ps1 sem BOM como ANSI e estragaria os acentos
$uiDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $uiDir
$target = Join-Path $uiDir 'iniciar.bat'
$desktop = [Environment]::GetFolderPath('Desktop')
$linkPath = Join-Path $desktop 'Bisturi Clips.lnk'
$antigo = Join-Path $desktop 'CS2 Highlights.lnk'   # nome antigo do programa
if (Test-Path $antigo) { Remove-Item $antigo }
$wsh = New-Object -ComObject WScript.Shell
$link = $wsh.CreateShortcut($linkPath)
$link.TargetPath = $target
$link.WorkingDirectory = $rootDir
$link.Description = 'Abrir o Bisturi Clips'
$link.WindowStyle = 1
$icone = Join-Path $uiDir 'static\icone.ico'
if (Test-Path $icone) { $link.IconLocation = "$icone,0" }
$link.Save()
Write-Host "Atalho criado na Area de Trabalho: Bisturi Clips"
