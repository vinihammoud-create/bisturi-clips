"""Configuração comum dos testes: nenhum teste pode depender de o CS2 ou o OBS estarem abertos neste PC."""
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

# ── P2, item 10 (26/11/2026): a suíte inteira roda com a pasta de dados numa pasta temporária, nunca na do dono ─────────────────────
# A `PASTA_DADOS` é fixada quando o `cs2clip.caminhos` é importado; por isso a variável é definida AQUI, no topo do conftest, antes de
# qualquer teste importar o pacote (uma fixture com `monkeypatch.setenv` chegaria tarde). Os processos do xdist herdam a pasta do
# principal (`BISTURI_DADOS_DA_SUITE`), e os processos filhos que os testes abrem herdam a variável. Teste que prova o modo portátil
# roda num Python à parte, sem `BISTURI_DADOS` (`tests/test_pasta_dados.py`).
RAIZ_REAL = Path(__file__).resolve().parents[1]
if "cs2clip.caminhos" in sys.modules:
    raise RuntimeError("o cs2clip foi importado antes do tests/conftest.py: a pasta de dados da suíte chegaria tarde")
PASTA_DADOS_DA_SUITE = Path(os.environ.get("BISTURI_DADOS_DA_SUITE") or RAIZ_REAL / ".pytest_tmp" / f"dados-{os.getpid()}")
os.environ["BISTURI_DADOS_DA_SUITE"] = str(PASTA_DADOS_DA_SUITE)
os.environ["BISTURI_DADOS"] = str(PASTA_DADOS_DA_SUITE)
# Com a variável, o `caminhos.py` poria o HF_HOME na pasta temporária (modo instalado) e um teste que carregasse o modelo de voz o
# baixaria de novo. Fixo o lugar de sempre da biblioteca (o mesmo cálculo do `huggingface_hub.constants`), só se a pessoa não tem um.
os.environ.setdefault("HF_HOME", os.path.join(os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache")), "huggingface"))


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Cada execução usa a SUA pasta temporária (`.pytest_tmp/exec-<pid>`): duas rodadas ao mesmo tempo não se apagam (QB3, 13/11).

    O `pytest.ini` aponta `--basetemp=.pytest_tmp`; aqui só acrescento a subpasta da execução. Com `-n` (pytest-xdist), os processos
    filhos herdam a pasta do principal, então não mexo neles. De passagem, apago as pastas de execuções antigas (mais de 2 h)."""
    if hasattr(config, "workerinput"):          # processo filho do xdist: a pasta já veio do principal
        return
    base = getattr(config.option, "basetemp", None)
    if not base or Path(base).name != ".pytest_tmp":
        return                                  # alguém passou outra pasta de propósito: respeito
    raiz = Path(base) if Path(base).is_absolute() else Path(config.rootpath) / base   # sempre dentro do projeto, mesmo rodando de outra pasta
    agora = time.time()
    raiz.mkdir(parents=True, exist_ok=True)     # clone novo (CI, cópia limpa): a pasta .pytest_tmp ainda não existe e o pytest não a cria sozinho
    if raiz.is_dir():
        for antiga in raiz.iterdir():
            try:
                if antiga.is_dir() and agora - antiga.stat().st_mtime > 2 * 3600:
                    shutil.rmtree(antiga, ignore_errors=True)
            except OSError:
                pass                            # pasta em uso por outra rodada: fica
    config.option.basetemp = str(raiz / f"exec-{os.getpid()}")


@pytest.fixture(autouse=True)
def _historico_de_musica_isolado(monkeypatch, tmp_path_factory):
    """O rodízio de músicas grava `historico.json`; teste nenhum pode sujar (nem depender de) o histórico real da pasta musicas/."""
    monkeypatch.setenv("BISTURI_HISTORICO_MUSICA", str(tmp_path_factory.mktemp("hist") / "historico.json"))


@pytest.fixture(autouse=True)
def _sem_jogo_aberto(monkeypatch):
    import cs2clip.checagem as checagem
    monkeypatch.setattr(checagem, "processos_ativos", lambda: set())


# ── F2 (14/11/2026): nenhum teste lê, cria ou altera o config.toml REAL da raiz (o do dono) ───────────────────────────────────
CONFIG_REAL = RAIZ_REAL / "config.toml"


def _marca_do_config_real() -> tuple[int, int] | None:
    """Tamanho e data do config.toml da raiz (None se não existe). Só `stat`: o conteúdo nunca é aberto."""
    try:
        st = CONFIG_REAL.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


@pytest.fixture(scope="session", autouse=True)
def _config_real_nao_muda_durante_a_suite():
    """Se um teste CRIAR (cópia limpa: nasceria do exemplo), APAGAR ou REESCREVER o config.toml da raiz, a suíte acusa no fim."""
    antes = _marca_do_config_real()
    yield
    depois = _marca_do_config_real()
    assert antes == depois, (f"o config.toml da raiz mudou durante a suíte (antes={antes}, depois={depois}): "
                             "algum teste chamou o programa sem --config ou escreveu na pasta do projeto")


# ── P2, item 10: as pastas de dados REAIS da raiz não ganham nem perdem nada durante a suíte ──────────────────────────────────────
# Só nomes (nada é aberto nem lido): a lista de cada pasta de dados e se os arquivos de música da pessoa existem. Pega o teste que grava
# por um caminho montado na mão (`ROOT / "saida"`) em vez da pasta de dados; numa cópia limpa (CI, worktree) as pastas nem existem.
PASTAS_DE_DADOS_REAIS = ("dados", "saida", "entrada", "demos", "gravacao_demo", "trabalhos", "logs", "modelos")
ARQUIVOS_DE_DADOS_REAIS = ("musicas/catalogo_usuario.json", "musicas/historico.json")


def _foto_dos_dados_reais() -> dict[str, object]:
    foto: dict[str, object] = {}
    for nome in PASTAS_DE_DADOS_REAIS:
        try:
            foto[nome] = sorted(os.listdir(RAIZ_REAL / nome))
        except FileNotFoundError:
            foto[nome] = None
        except OSError as e:
            foto[nome] = f"ilegível ({type(e).__name__})"
    for nome in ARQUIVOS_DE_DADOS_REAIS:
        foto[nome] = (RAIZ_REAL / nome).exists()
    return foto


@pytest.fixture(scope="session", autouse=True)
def _dados_reais_nao_mudam_durante_a_suite():
    antes = _foto_dos_dados_reais()
    yield
    depois = _foto_dos_dados_reais()
    mudou = {k: (antes[k], depois[k]) for k in antes if antes[k] != depois[k]}
    assert not mudou, (f"a suíte mexeu nas pastas de dados reais da raiz (antes, depois): {mudou}. Algum teste gravou por um caminho fixo "
                       f"da raiz em vez da pasta de dados ({PASTA_DADOS_DA_SUITE}) ou de uma pasta temporária")


@pytest.fixture(autouse=True)
def _nenhum_teste_le_o_config_real(monkeypatch, request):
    """Ler o config real faz o teste depender do PC do dono. Teste que precisa de um config usa `--config <tmp>`; marque `@pytest.mark.config_real` só com motivo."""
    if request.node.get_closest_marker("config_real"):
        return
    import cs2clip.__main__ as principal
    import cs2clip.config as cfgmod
    original = cfgmod.load_config

    def guardado(path=None, *args, **kwargs):
        if path is not None and Path(path).resolve() == CONFIG_REAL.resolve():
            raise AssertionError("este teste leu o config.toml REAL da raiz; passe --config <arquivo temporário> (tests/conftest.py)")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(cfgmod, "load_config", guardado)
    monkeypatch.setattr(principal, "load_config", guardado)


@pytest.fixture(autouse=True)
def _modelo_de_voz_fora_do_cache_real(monkeypatch, tmp_path_factory):
    """P4.2: o `modelo_voz` procura o modelo no cache do Hugging Face. Na suíte, o cache padrão é uma pasta temporária vazia: nenhum teste
    depende (nem muda) do modelo que o dono tem no PC, e o `transcribe.get_model` dos testes recebe o NOME do modelo, como sempre."""
    import cs2clip.modelo_voz as mv
    monkeypatch.setattr(mv, "_cache_padrao", lambda: tmp_path_factory.getbasetemp() / "cache_hf_vazio")


@pytest.fixture(autouse=True)
def _suite_nao_fala_com_a_twitch(monkeypatch, tmp_path_factory):
    """M3: nenhum teste consulta a Twitch (`TwitchDownloaderCLI info`) por engano: o baixador padrão aponta para um arquivo que não existe. Teste que precisa dele
    passa `cli=` explícito (um arquivo falso) ou troca `twitch_vod.info_do_vod`."""
    import cs2clip.twitch_vod as tv
    monkeypatch.setattr(tv, "CLI", tmp_path_factory.getbasetemp() / "sem_baixador_da_twitch.exe")
