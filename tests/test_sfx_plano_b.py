"""Plano B dos efeitos sonoros (P2, item 8): pasta do programa sem escrita -> `<dados>/cache/sfx`.

Nada aqui mexe em permissão real de disco: a "pasta só de leitura" é um dublê de `Path.mkdir` e de `wave.open`."""
from __future__ import annotations

import hashlib
import wave
from pathlib import Path

import pytest

from cs2clip import sfx

CHAVES = {"whoosh", "impacto", "riser", "bass_drop"}


def _hashes(arqs: dict[str, Path]) -> dict[str, str]:
    return {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in arqs.items()}


def _dentro(p: Path, pasta: Path) -> bool:
    p = Path(p).resolve()
    return p == pasta.resolve() or pasta.resolve() in p.parents


@pytest.fixture
def programa(tmp_path, monkeypatch) -> Path:
    """Aponta a pasta de sfx do programa para um lugar temporário e limpa BISTURI_DADOS."""
    pasta = tmp_path / "programa" / "assets" / "sfx"
    monkeypatch.setattr(sfx, "PASTA_SFX", pasta)
    monkeypatch.delenv("BISTURI_DADOS", raising=False)
    return pasta


@pytest.fixture
def bloquear(monkeypatch):
    """Devolve uma função que torna uma pasta não gravável (criar pasta e abrir .wav para escrever falham)."""
    def _bloquear(pasta: Path) -> None:
        mkdir_real = Path.mkdir
        wave_open_real = wave.open

        def mkdir_falso(self, *a, **kw):
            if _dentro(self, pasta) and not (kw.get("exist_ok") and self.is_dir()):   # como o disco: pasta que existe passa
                raise PermissionError(13, "Acesso negado (dublê)", str(self))
            return mkdir_real(self, *a, **kw)

        def wave_open_falso(arq, *a, **kw):
            if _dentro(Path(str(arq)), pasta):
                raise PermissionError(13, "Acesso negado (dublê)", str(arq))
            return wave_open_real(arq, *a, **kw)

        monkeypatch.setattr(Path, "mkdir", mkdir_falso)
        monkeypatch.setattr(wave, "open", wave_open_falso)
    return _bloquear


def test_a_pasta_do_programa_gravavel_usa_assets_sfx(programa, tmp_path, monkeypatch):
    monkeypatch.setenv("BISTURI_DADOS", str(tmp_path / "dados"))
    arqs = sfx.garantir_sfx()
    assert set(arqs) == CHAVES
    for p in arqs.values():
        assert p.parent == programa and p.is_file()
    assert not (tmp_path / "dados").exists()          # o plano B nem foi tocado


def test_a_sem_dados_e_gravavel_continua_igual_a_hoje(programa):
    arqs = sfx.garantir_sfx()
    assert all(p.parent == programa and p.is_file() for p in arqs.values())


def test_b_nao_gravavel_com_dados_gera_em_cache_sfx(programa, tmp_path, monkeypatch, bloquear):
    dados = tmp_path / "dados"
    monkeypatch.setenv("BISTURI_DADOS", str(dados))
    bloquear(programa.parent.parent)                  # bloqueia a pasta do programa inteira
    arqs = sfx.garantir_sfx()
    assert set(arqs) == CHAVES
    for p in arqs.values():
        assert p.parent == dados / "cache" / "sfx" and p.stat().st_size > 1000
    assert not programa.exists()


def test_b_pasta_existe_mas_arquivo_nao_pode_ser_escrito(programa, tmp_path, monkeypatch, bloquear):
    programa.mkdir(parents=True)                      # a pasta existe (mkdir passa), a escrita é que falha
    dados = tmp_path / "dados"
    monkeypatch.setenv("BISTURI_DADOS", str(dados))
    bloquear(programa)
    arqs = sfx.garantir_sfx()
    assert all(p.parent == dados / "cache" / "sfx" for p in arqs.values())


def test_b_arquivos_ja_entregues_no_programa_sao_usados_sem_escrever(programa, tmp_path, monkeypatch, bloquear):
    sfx.garantir_sfx()                                # o "instalador" entregou os 4 wav
    monkeypatch.setenv("BISTURI_DADOS", str(tmp_path / "dados"))
    bloquear(programa.parent.parent)
    arqs = sfx.garantir_sfx()                         # nada a gerar: usa o que veio pronto, sem desviar
    assert set(arqs) == CHAVES and all(p.parent == programa and p.is_file() for p in arqs.values())
    assert not (tmp_path / "dados").exists()


def test_c_nao_gravavel_e_sem_dados_da_erro_claro_em_portugues(programa, monkeypatch, bloquear):
    monkeypatch.delenv("BISTURI_DADOS", raising=False)
    bloquear(programa.parent.parent)
    with pytest.raises(sfx.SfxIndisponivelError) as exc:
        sfx.garantir_sfx()
    msg = str(exc.value)
    assert "BISTURI_DADOS" in msg and "efeitos sonoros" in msg.lower()
    assert not isinstance(exc.value, PermissionError)


def test_c_dados_vazio_conta_como_ausente(programa, monkeypatch, bloquear):
    monkeypatch.setenv("BISTURI_DADOS", "   ")
    bloquear(programa.parent.parent)
    with pytest.raises(sfx.SfxIndisponivelError):
        sfx.garantir_sfx()


def test_c_dados_tambem_nao_gravavel_da_erro_claro(programa, tmp_path, monkeypatch, bloquear):
    monkeypatch.setenv("BISTURI_DADOS", str(programa.parent.parent / "dados"))   # dentro do bloqueio
    bloquear(programa.parent.parent)
    with pytest.raises(sfx.SfxIndisponivelError):
        sfx.garantir_sfx()


def test_d_conteudo_identico_nos_dois_lugares(programa, tmp_path, monkeypatch, bloquear):
    no_programa = _hashes(sfx.garantir_sfx())
    # recomeça com o programa bloqueado e os dados em outro lugar
    monkeypatch.setattr(sfx, "PASTA_SFX", tmp_path / "outro" / "assets" / "sfx")
    monkeypatch.setenv("BISTURI_DADOS", str(tmp_path / "dados"))
    bloquear(tmp_path / "outro")
    nos_dados = _hashes(sfx.garantir_sfx())
    assert nos_dados == no_programa and len(set(no_programa.values())) == 4


def test_d_hash_igual_a_linha_de_base_de_antes_da_mudanca():
    """Hashes dos 4 .wav gerados pelo código ANTERIOR (sha256): a mudança não pode alterar o conteúdo."""
    esperado = {
        "whoosh": "d41f1745d0d8ae3a5c1644c4d9236e988623dcf36e7939aef2fc16acdfb356dc",
        "impacto": "57e4b7b661ec630d1b5d9d8f8f6904a3a02d821f4d4cfab4d4f3330c3ec0a73e",
        "riser": "fce582df2df4937161f96ca10c00f70b42a256274fdbb5ded6667afd937e0fb3",
        "bass_drop": "7a140bc382c07bce58398c8fa02f9034d255b6fc3847b4cee3dbb9f7092da622",
    }
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert _hashes(sfx.garantir_sfx(Path(d) / "sfx")) == esperado


def test_pasta_explicita_nao_usa_plano_b(tmp_path, monkeypatch, bloquear):
    """Quem passa a pasta (testes, ferramentas) decide: se ela não é gravável, o erro é do chamador, sem desvio."""
    monkeypatch.setenv("BISTURI_DADOS", str(tmp_path / "dados"))
    alvo = tmp_path / "fechada" / "sfx"
    bloquear(tmp_path / "fechada")
    with pytest.raises(PermissionError):
        sfx.garantir_sfx(alvo)
    assert not (tmp_path / "dados").exists()


def test_pasta_de_dados_le_a_variavel(monkeypatch, tmp_path):
    monkeypatch.delenv("BISTURI_DADOS", raising=False)
    assert sfx._pasta_dados() is None
    monkeypatch.setenv("BISTURI_DADOS", str(tmp_path))
    assert sfx._pasta_dados() == tmp_path.resolve()
