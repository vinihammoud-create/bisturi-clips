"""Revisão de 26/11/2026, lote D1: TwitchDownloaderCLI com versão e SHA-256 fixos (R-019)."""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from cs2clip import terceiros
from cs2clip.terceiros import TWITCHDL, ErroTerceiros

RAIZ = Path(__file__).resolve().parents[1]


def _zip_bytes(arquivos: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nome, dados in arquivos.items():
            z.writestr(nome, dados)
    return buf.getvalue()


class _Resposta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        return None


def _baixar_com(pacote: bytes):
    def baixar(t, destino):
        Path(destino).write_bytes(pacote)
        return Path(destino)
    return baixar


PACOTE = _zip_bytes({"COPYRIGHT.txt": b"aviso", "libHarfBuzzSharp.pdb": b"pdb", "TwitchDownloaderCLI.exe": b"MZ-novo"})


# ── R-019: versão e hash fixos, como o HLAE e o boiler ────────────────────────────────────────

def test_twitchdl_tem_url_exata_versao_hash_e_tamanho_fixos():
    assert TWITCHDL.versao == "1.56.5" and terceiros.TERCEIROS["twitchdl"] is TWITCHDL
    assert TWITCHDL.url == ("https://github.com/lay295/TwitchDownloader/releases/download/1.56.5/"
                            "TwitchDownloaderCLI-1.56.5-Windows-x64.zip")
    assert re.fullmatch(r"[0-9a-f]{64}", TWITCHDL.sha256) and TWITCHDL.tamanho == 52_226_305
    assert "latest" not in TWITCHDL.url


def test_instalar_twitchdl_poe_so_o_exe_em_bin_e_registra(tmp_path, monkeypatch):
    monkeypatch.setattr(terceiros, "REGISTRO", tmp_path / "TERCEIROS.json")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "TwitchDownloaderCLI.exe").write_bytes(b"MZ-antigo")                  # uma versão anterior é trocada
    exe = terceiros.instalar_twitchdl(bin_dir, baixar=_baixar_com(PACOTE))
    assert exe == bin_dir / "TwitchDownloaderCLI.exe" and exe.read_bytes() == b"MZ-novo"
    assert sorted(p.name for p in bin_dir.iterdir()) == ["TwitchDownloaderCLI.exe"]   # sem zip, sem pasta temporária, sem .pdb
    assert terceiros.instalado("twitchdl", bin_dir) and terceiros.caminho_twitchdl(bin_dir) == exe
    reg = json.loads((tmp_path / "TERCEIROS.json").read_text(encoding="utf-8"))
    assert reg["twitchdl"]["versao"] == "1.56.5" and reg["twitchdl"]["sha256"] == TWITCHDL.sha256


def test_hash_ou_tamanho_diferente_nao_instala_nada(tmp_path, monkeypatch):
    monkeypatch.setattr(terceiros, "REGISTRO", tmp_path / "TERCEIROS.json")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "TwitchDownloaderCLI.exe").write_bytes(b"MZ-antigo")
    adulterado = b"x" * TWITCHDL.tamanho                                            # mesmo tamanho do oficial, outro conteúdo: o hash recusa
    assert hashlib.sha256(adulterado).hexdigest() != TWITCHDL.sha256
    for resposta, msg in ((adulterado, "SHA-256"), (PACOTE, "tamanho")):
        with pytest.raises(ErroTerceiros, match=msg):
            terceiros.instalar_twitchdl(bin_dir, baixar=lambda t, d, r=resposta: terceiros.baixar_zip(t, d, abrir=lambda req, timeout=0: _Resposta(r)))
        assert sorted(p.name for p in bin_dir.iterdir()) == ["TwitchDownloaderCLI.exe"]
        assert (bin_dir / "TwitchDownloaderCLI.exe").read_bytes() == b"MZ-antigo"      # o que já estava fica intacto
    assert not (tmp_path / "TERCEIROS.json").exists()


def test_zip_sem_o_exe_ou_com_caminho_de_fuga_e_recusado_sem_sobrar_nada(tmp_path, monkeypatch):
    monkeypatch.setattr(terceiros, "REGISTRO", tmp_path / "TERCEIROS.json")
    bin_dir = tmp_path / "bin"
    with pytest.raises(ErroTerceiros, match="TwitchDownloaderCLI.exe"):
        terceiros.instalar_twitchdl(bin_dir, baixar=_baixar_com(_zip_bytes({"COPYRIGHT.txt": b"x"})))
    with pytest.raises(ErroTerceiros, match="fora da pasta"):
        terceiros.instalar_twitchdl(bin_dir, baixar=_baixar_com(_zip_bytes({"../fora.txt": b"mau", "TwitchDownloaderCLI.exe": b"MZ"})))
    assert list(bin_dir.iterdir()) == [] and not (tmp_path / "fora.txt").exists()


def test_cli_instalar_twitchdl_so_quando_pedido(monkeypatch, capsys):
    chamados = []
    monkeypatch.setattr(terceiros, "instalar_twitchdl", lambda *a, **k: chamados.append("twitchdl") or Path("bin/TwitchDownloaderCLI.exe"))
    monkeypatch.setattr(terceiros, "instalar_hlae", lambda *a, **k: chamados.append("hlae") or Path("bin/hlae"))
    monkeypatch.setattr(terceiros, "instalar_boiler", lambda *a, **k: chamados.append("boiler") or Path("bin/boiler"))
    assert terceiros.main(["instalar", "twitchdl"]) == 0 and chamados == ["twitchdl"]
    chamados.clear()
    assert terceiros.main(["instalar", "tudo"]) == 0 and chamados == ["hlae", "boiler"]      # "tudo" = gravar pela demo; o chat é opcional
    capsys.readouterr()
    assert terceiros.main(["estado"]) == 0 and "TwitchDownloaderCLI 1.56.5" in capsys.readouterr().out


def test_instalador_nao_baixa_mais_da_ultima_release():
    bat = (RAIZ / "instalar.bat").read_text(encoding="utf-8")
    assert "releases/latest" not in bat and "Invoke-RestMethod" not in bat and "Expand-Archive" not in bat and "Invoke-WebRequest" not in bat
    assert "-m cs2clip.terceiros instalar twitchdl" in bat
    assert 'if not exist "bin\\TwitchDownloaderCLI.exe"' in bat                           # quem já tem o chat não baixa de novo
