$ErrorActionPreference = 'Stop'
# so ASCII neste arquivo: o PowerShell 5.1 le .ps1 sem BOM como ANSI e estragaria os acentos
$uiDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $uiDir
# pythonw.exe = o Python sem janela preta (so a janela do app abre). Sem ele, cai no iniciar.bat (com console).
$pythonw = Join-Path $rootDir '.venv\Scripts\pythonw.exe'
if (Test-Path $pythonw) { $target = $pythonw; $argumentos = '-m ui' } else { $target = Join-Path $uiDir 'iniciar.bat'; $argumentos = '' }
$desktop = [Environment]::GetFolderPath('Desktop')
# Bisturi Clips.lnk e o nome do atalho do programa INSTALADO (o do publico): o da oficina tem nome proprio
$linkPath = Join-Path $desktop 'Bisturi Clips (oficina).lnk'
$antigo = Join-Path $desktop 'CS2 Highlights.lnk'   # nome antigo do programa
if (Test-Path $antigo) { Remove-Item $antigo }
# o atalho antigo da oficina tinha o nome do atalho do programa instalado: so sai se apontar para ESTA pasta
# (o do programa instalado aponta para a pasta dele e fica); se nao der para ler o atalho, ele fica
function Remover-AtalhoAntigoDaOficina([string]$caminho, [string]$raiz) {
  if (-not (Test-Path -LiteralPath $caminho)) { return }
  try { $alvo = (New-Object -ComObject WScript.Shell).CreateShortcut($caminho).TargetPath } catch { return }
  $prefixo = $raiz.TrimEnd('\') + '\'
  if ($alvo -and $alvo.StartsWith($prefixo, [StringComparison]::OrdinalIgnoreCase)) { Remove-Item -LiteralPath $caminho }
}
Remover-AtalhoAntigoDaOficina (Join-Path $desktop 'Bisturi Clips.lnk') $rootDir
$wsh = New-Object -ComObject WScript.Shell
$link = $wsh.CreateShortcut($linkPath)
$link.TargetPath = $target
$link.Arguments = $argumentos
$link.WorkingDirectory = $rootDir
$link.Description = 'Abrir o Bisturi Clips'
$link.WindowStyle = 7
$icone = Join-Path $uiDir 'static\icone.ico'
if (Test-Path $icone) { $link.IconLocation = "$icone,0" }
$link.Save()
Write-Host "Atalho criado na Area de Trabalho: Bisturi Clips (oficina)"
