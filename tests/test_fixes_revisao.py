"""Correções da revisão final da 1.1.0 (`docs/revisoes/REVISAO_FINAL_1_1_0.md`, itens 1, 4, 5 e parte do 11).

Item 1: o `release.yml` roda `montar.py --validar` e `--pacote` numa máquina do GitHub SEM FFmpeg no PATH. O `montar.py` lê o `terceiros.py`,
que importava o FFMPEG do `tools.py` no topo (SystemExit "ffmpeg não encontrado"). Aqui os dois comandos rodam num processo à parte, com o
PATH só com o System32 e o `_find` do `tools` falhando como numa máquina sem FFmpeg (assim o teste vale mesmo num PC que tem o FFmpeg em `bin`).
Nada usa a rede: o pacote é montado numa raiz falsa com um zip falso do uv.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from cs2clip import terceiros, tools
from cs2clip.terceiros import HLAE, ErroTerceiros
from cs2clip.tools import sem_janela

RAIZ = Path(__file__).resolve().parents[1]
MONTAR = RAIZ / "instalador" / "montar.py"

# roda no processo à parte: o FFmpeg "some" (o `_find` falha como sem ele) e depois o código pedido
_SEM_FFMPEG = (
    "import sys\n"
    "import cs2clip.tools as t\n"
    "def _sem(nome):\n"
    "    raise SystemExit(nome + ' não encontrado (teste: máquina sem FFmpeg)')\n"
    "t._find = _sem\n"
)


def _ambiente_sem_ffmpeg(tmp_path: Path) -> dict[str, str]:
    """O PATH do runner sem FFmpeg (só o System32) e uma pasta de usuário vazia (sem os atalhos do winget)."""
    vazio = tmp_path / "localappdata_vazio"
    vazio.mkdir(exist_ok=True)
    sistema = os.environ.get("SystemRoot", r"C:\Windows")
    return {**os.environ, "PATH": str(Path(sistema) / "system32"), "LOCALAPPDATA": str(vazio), "PYTHONIOENCODING": "utf-8"}


def _rodar_sem_ffmpeg(tmp_path: Path, codigo: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", _SEM_FFMPEG + codigo], cwd=RAIZ, env=_ambiente_sem_ffmpeg(tmp_path), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300, check=False, **sem_janela())


def test_controle_a_simulacao_tira_mesmo_o_ffmpeg(tmp_path):
    """CONTROLE: no processo à parte, quem lê o FFMPEG recebe o SystemExit (a simulação vale)."""
    r = _rodar_sem_ffmpeg(tmp_path, "try:\n    t.FFMPEG\nexcept SystemExit as e:\n    print('SAIU', e)\n")
    assert "SAIU ffmpeg não encontrado" in r.stdout, r.stdout + r.stderr


def test_montar_validar_roda_sem_ffmpeg(tmp_path):
    """Item 1 (bloqueio): `python instalador/montar.py --validar` como script, sem FFmpeg (o passo "Conferir os dados" do release.yml)."""
    r = _rodar_sem_ffmpeg(tmp_path, f"import runpy\nsys.argv = ['montar.py', '--validar']\nrunpy.run_path({str(MONTAR)!r}, run_name='__main__')\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok: uv" in r.stdout and "não encontrado" not in r.stdout + r.stderr


def _escrever(raiz: Path, rel: str, conteudo: bytes | str = "x") -> None:
    p = raiz / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(conteudo if isinstance(conteudo, bytes) else conteudo.encode("utf-8"))


def _raiz_falsa_com_uv_no_cache(tmp_path: Path) -> Path:
    """Um projeto mínimo que o `--pacote` aceita, com o zip (falso) do uv já no cache e o hash dele no componentes.json."""
    raiz = tmp_path / "proj"
    zip_uv = raiz / ".tmp_agentes" / "cache_instalador" / "uv-x86_64-pc-windows-msvc.zip"
    zip_uv.parent.mkdir(parents=True)
    with zipfile.ZipFile(zip_uv, "w") as z:
        z.writestr("uv.exe", b"MZ-uv-falso")
    h = "a" * 64
    dados = {"esquema": 1,
             "uv": {"versao": "0.13.10", "url": "https://github.com/astral-sh/uv/releases/download/0.13.10/uv-x86_64-pc-windows-msvc.zip",
                    "sha256": hashlib.sha256(zip_uv.read_bytes()).hexdigest(), "tamanho": zip_uv.stat().st_size},
             "ffmpeg": {"versao": "9.1", "url": "https://github.com/GyanD/codexffmpeg/releases/download/9.1/ffmpeg-9.1-essentials_build.7z",
                        "sha256": h, "tamanho": 10, "tamanho_extraido": 100},
             "musicas": {"faixas": [{"arquivo": "a.mp3", "titulo": "A", "sha256": h, "tamanho": 10,
                                     "url": "https://raw.githubusercontent.com/SoundSafari/CC0-1.0-Music/abc/freepd.com/a.mp3"}],
                         "fora": []}}
    for rel, c in {"cs2clip/__init__.py": '__version__ = "9.9.9"\n', "ui/static/index.html": "<html>", "config.exemplo.toml": "[a]\n",
                   "componentes.json": "{}", "overrides.txt": "x==1\n", "LICENSE": "MIT", "CREDITOS.md": "c", "README.md": "r",
                   "requirements-runtime.lock": "a==1", "requirements-gpu.lock": "b==1", "musicas/catalogo.json": '{"faixas": [{"arquivo": "a.mp3"}]}',
                   "musicas/licencas.txt": "l", "musicas/COMO_ADICIONAR_MUSICAS.md": "m", "instalador/componentes.json": json.dumps(dados)}.items():
        _escrever(raiz, rel, c)
    return raiz


def test_montar_pacote_roda_sem_ffmpeg_e_gera_os_efeitos_sonoros(tmp_path):
    """Item 1: o `--pacote` (o passo de montagem do release.yml, sem o --baixar e o --compilar) também roda sem FFmpeg, com o gerador
    de efeitos sonoros de verdade (`cs2clip.sfx`)."""
    raiz = _raiz_falsa_com_uv_no_cache(tmp_path)
    codigo = ("import importlib.util\n"
              f"spec = importlib.util.spec_from_file_location('montar_sem_ffmpeg', {str(MONTAR)!r})\n"
              "m = importlib.util.module_from_spec(spec)\n"
              "sys.modules[spec.name] = m\n"
              "spec.loader.exec_module(m)\n"
              f"sys.exit(m.main(['--pacote', '.tmp_agentes/pacote'], raiz=m.Path({str(raiz)!r})))\n")
    r = _rodar_sem_ffmpeg(tmp_path, codigo)
    assert r.returncode == 0, r.stdout + r.stderr
    pacote = raiz / ".tmp_agentes" / "pacote"
    assert (pacote / "uv.exe").read_bytes() == b"MZ-uv-falso"
    assert len(list((pacote / "app" / "cs2clip" / "assets" / "sfx").glob("*.wav"))) == 4


def test_terceiros_importa_sem_ffmpeg_e_hlae_sem_ffmpeg_recusa_antes_de_baixar(tmp_path):
    """Sem FFmpeg o `import cs2clip.terceiros` funciona (o montar só lê as versões e os hashes) e o HLAE, que precisa do FFmpeg, recusa
    com mensagem ANTES de baixar qualquer coisa."""
    codigo = ("import cs2clip.terceiros as tc\n"
              "print('IMPORTOU', tc.HLAE.versao)\n"
              "baixados = []\n"
              "try:\n"
              f"    tc.instalar_hlae(tc.Path({str(tmp_path / 'bin')!r}), baixar=lambda t, d: baixados.append(t))\n"
              "except tc.ErroTerceiros as e:\n"
              "    print('RECUSOU', e)\n"
              "print('BAIXADOS', len(baixados))\n")
    r = _rodar_sem_ffmpeg(tmp_path, codigo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"IMPORTOU {HLAE.versao}" in r.stdout
    assert "RECUSOU não achei o FFmpeg do programa" in r.stdout and "BAIXADOS 0" in r.stdout


def _zip_hlae() -> bytes:
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("HLAE.exe", b"MZ")
    return buf.getvalue()


def test_hlae_sem_ffmpeg_explicito_usa_o_ffmpeg_do_programa(tmp_path, monkeypatch):
    """O comportamento no programa continua o mesmo: sem `ffmpeg=` o HLAE recebe o FFMPEG que o `tools` acha (lido na hora de instalar)."""
    monkeypatch.setattr(terceiros, "REGISTRO", tmp_path / "TERCEIROS.json")
    ff = tmp_path / "ffmpeg_do_programa.exe"
    ff.write_bytes(b"FFMPEG-DO-PROGRAMA")
    monkeypatch.setattr(tools, "FFMPEG", str(ff), raising=False)
    pasta = terceiros.instalar_hlae(tmp_path / "bin", baixar=lambda t, d: (Path(d).write_bytes(_zip_hlae()), Path(d))[1])
    assert (pasta / "ffmpeg" / "bin" / "ffmpeg.exe").read_bytes() == b"FFMPEG-DO-PROGRAMA"


def test_hlae_sem_ffmpeg_no_processo_atual_vira_erro_de_terceiros(tmp_path, monkeypatch):
    """O SystemExit do `tools` (sem FFmpeg) vira ErroTerceiros: o `instalacao.pos` o pega e a mensagem chega ao instalador."""
    monkeypatch.delattr(tools, "FFMPEG", raising=False)
    monkeypatch.setattr(tools, "_find", lambda nome: (_ for _ in ()).throw(SystemExit(f"{nome} não encontrado")))
    chamadas: list = []
    with pytest.raises(ErroTerceiros, match="FFmpeg"):
        terceiros.instalar_hlae(tmp_path / "bin", baixar=lambda t, d: chamadas.append(t))
    assert chamadas == []


# ── item 5: o atalho da oficina não tem mais o nome do atalho do programa instalado ──────────────────────────────────────────────
ATALHO_PS1 = RAIZ / "ui" / "criar_atalho.ps1"
FUNCAO_PS1 = "Remover-AtalhoAntigoDaOficina"


def _ps1() -> str:
    bruto = ATALHO_PS1.read_bytes()
    assert b"\r\n" in bruto and b"\n" not in bruto.replace(b"\r\n", b""), "CRLF, como os outros scripts do Windows"
    return bruto.decode("ascii")                       # só ASCII: o PowerShell 5.1 lê .ps1 sem BOM como ANSI


def _funcao_ps1(texto: str) -> str:
    import re
    m = re.search(rf"^function {FUNCAO_PS1}\b.*?^\}}\r?$", texto, re.S | re.M)
    assert m, f"não achei a função {FUNCAO_PS1} no criar_atalho.ps1"
    return m.group(0)


def test_atalho_da_oficina_tem_nome_proprio_e_o_antigo_so_sai_pela_funcao():
    """O instalador cria `Bisturi Clips.lnk` (o do público); o script da oficina cria `Bisturi Clips (oficina).lnk`. O `Bisturi Clips.lnk`
    só é apagado pela função que confere que ele aponta para esta pasta (pode ser o do programa instalado)."""
    s = _ps1()
    assert "$linkPath = Join-Path $desktop 'Bisturi Clips (oficina).lnk'" in s
    assert f"{FUNCAO_PS1} (Join-Path $desktop 'Bisturi Clips.lnk') $rootDir" in s
    funcao = _funcao_ps1(s)
    fora = s.replace(funcao, "")
    assert "'Bisturi Clips.lnk'" not in fora.replace(f"{FUNCAO_PS1} (Join-Path $desktop 'Bisturi Clips.lnk') $rootDir", "")
    assert fora.count("Remove-Item") == 1 and "CS2 Highlights.lnk" in fora      # o outro Remove-Item é o do nome antigo, já existente
    assert s.index(funcao) < s.index(f"{FUNCAO_PS1} (Join-Path")                  # definida antes do uso


def _powershell() -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


@pytest.mark.skipif(sys.platform != "win32", reason="atalho .lnk e WScript.Shell são do Windows")
def test_funcao_so_apaga_o_atalho_antigo_que_aponta_para_a_pasta_do_projeto(tmp_path):
    """Roda SÓ a função (tirada do script) em pasta temporária: nada na Área de Trabalho. Atalho para dentro da pasta do projeto sai; para
    outra pasta (o programa instalado, ou uma pasta com o mesmo começo de nome) fica; um `.lnk` que não é atalho fica."""
    raiz = tmp_path / "proj"
    for p in (raiz / ".venv" / "Scripts" / "pythonw.exe", tmp_path / "proj2" / "pythonw.exe", tmp_path / "Programa" / "venv" / "pythonw.exe"):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MZ")
    roteiro = tmp_path / "roteiro.ps1"
    roteiro.write_text("$ErrorActionPreference = 'Stop'\r\n" + _funcao_ps1(_ps1()) + "\r\n" + r"""
$t = $env:BISTURI_TESTE
$wsh = New-Object -ComObject WScript.Shell
foreach ($par in @(@('dentro.lnk', 'proj\.venv\Scripts\pythonw.exe'), @('vizinho.lnk', 'proj2\pythonw.exe'), @('instalado.lnk', 'Programa\venv\pythonw.exe'))) {
  $l = $wsh.CreateShortcut((Join-Path $t $par[0])); $l.TargetPath = (Join-Path $t $par[1]); $l.Save()
}
[IO.File]::WriteAllText((Join-Path $t 'quebrado.lnk'), 'nao sou um atalho')
foreach ($n in @('dentro.lnk', 'vizinho.lnk', 'instalado.lnk', 'quebrado.lnk', 'nao_existe.lnk')) { Remover-AtalhoAntigoDaOficina (Join-Path $t $n) (Join-Path $t 'proj') }
foreach ($n in @('dentro.lnk', 'vizinho.lnk', 'instalado.lnk', 'quebrado.lnk')) { Write-Output ($n + '=' + (Test-Path -LiteralPath (Join-Path $t $n))) }
""", encoding="utf-8-sig")
    r = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(roteiro)],
                       env={**os.environ, "BISTURI_TESTE": str(tmp_path)}, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120, check=False, **sem_janela())
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.split() == ["dentro.lnk=False", "vizinho.lnk=True", "instalado.lnk=True", "quebrado.lnk=True"], r.stdout + r.stderr
