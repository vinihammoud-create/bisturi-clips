"""P2.1 do plano de publicação (26/11/2026): onde moram o programa e os dados da pessoa (`cs2clip/caminhos.py`).

Dois blocos:
  1. Resolução da pasta de dados (`BISTURI_DADOS` → `pasta_dados.txt` na pasta do programa → a própria pasta do programa). Estes PASSAM desde a P2.1.
  2. "Nada é escrito na pasta do programa" e "os pontos de entrada carregam caminhos.py antes do huggingface_hub": PASSAM desde a P2.2/P2.3.

Os testes que dependem do ambiente real (o que acontece ao IMPORTAR o módulo) rodam num processo à parte, com o ambiente controlado: o processo
do pytest pode ter `BISTURI_DADOS` definida (a P2 vai pôr uma no conftest) e o módulo já importado.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cs2clip import caminhos
from cs2clip.caminhos import ErroPastaDados, resolver_pasta_dados, resolver_pasta_logs
from cs2clip.tools import ROOT, sem_janela

RAIZ = Path(__file__).resolve().parents[1]
NO_WINDOWS = sys.platform == "win32"


def _rodar(codigo: str, cwd: Path, extra: dict[str, str] | None = None, args: tuple[str, ...] = (), prazo_s: float = 180) -> dict:
    """Roda `codigo` num Python novo (o mesmo do pytest) em `cwd`, sem `BISTURI_DADOS` nem `HF_HOME` herdados (a não ser os de `extra`).
    O código imprime um JSON na última linha; devolve esse JSON."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in {"BISTURI_DADOS", "HF_HOME"}}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
    env.update(extra or {})
    r = subprocess.run([sys.executable, "-c", codigo, *args], cwd=str(cwd), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=prazo_s, **sem_janela())
    assert r.returncode == 0, f"o processo saiu com {r.returncode}:\n{r.stderr[-4000:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def _programa_pequeno(destino: Path) -> Path:
    """Uma "pasta do programa" de verdade, como o `app\\` do instalador: cópia do pacote `cs2clip` (sem `__pycache__`)."""
    shutil.copytree(RAIZ / "cs2clip", destino / "cs2clip", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return destino


def _unidade_que_nao_existe() -> str | None:
    for letra in "ZYXWVUTSRQPONMLKJIHGFED":
        if not Path(f"{letra}:\\").exists():
            return f"{letra}:"
    return None


# Lê o módulo de verdade (importado agora, com o ambiente deste processo) e o que ele deixou no ambiente.
CODIGO_INFO = r"""
import json, os, sys
antes = dict(os.environ)
from cs2clip import caminhos as cm
depois = dict(os.environ)
hf_importado_junto = "huggingface_hub" in sys.modules
biblioteca = None
try:
    import huggingface_hub.constants as k        # importado DEPOIS do caminhos.py: tem de enxergar o HF_HOME dele
    biblioteca = [str(k.HF_HOME), str(k.HF_HUB_CACHE)]
except ImportError:
    pass
import cs2clip
print(json.dumps({"pacote": cs2clip.__file__, "raiz": str(cm.RAIZ_PROGRAMA), "dados": str(cm.PASTA_DADOS), "logs": str(cm.PASTA_LOGS),
                  "instalado": cm.modo_instalado(), "hf_home": os.environ.get("HF_HOME"), "biblioteca": biblioteca,
                  "mudou_no_ambiente": sorted(k for k in set(antes) | set(depois) if antes.get(k) != depois.get(k)),
                  "hf_importado_junto": hf_importado_junto, "dados_existe": cm.PASTA_DADOS.exists(), "logs_existe": cm.PASTA_LOGS.exists()}))
"""


# ═══ Bloco 1: resolução (passa desde a P2.1) ═════════════════════════════════════════════════════════════════════════════════════

def test_sem_variavel_e_sem_arquivo_os_dados_ficam_na_pasta_do_programa(tmp_path):
    assert resolver_pasta_dados(tmp_path, {}) == tmp_path.resolve()


def test_a_variavel_vence_o_arquivo_e_o_arquivo_vence_a_pasta_do_programa(tmp_path):
    programa, pelo_arquivo, pela_variavel = tmp_path / "app", tmp_path / "dados_do_arquivo", tmp_path / "dados_da_variavel"
    programa.mkdir()
    (programa / "pasta_dados.txt").write_text(str(pelo_arquivo), encoding="utf-8-sig")
    assert resolver_pasta_dados(programa, {}) == pelo_arquivo.resolve()
    assert resolver_pasta_dados(programa, {"BISTURI_DADOS": str(pela_variavel)}) == pela_variavel.resolve()


def test_pasta_dados_txt_como_o_instalador_grava_bom_acento_espaco_e_quebra_de_linha(tmp_path):
    programa, dados = tmp_path / "app", tmp_path / "Vídeos" / "Bisturi Clips"
    programa.mkdir()
    (programa / "pasta_dados.txt").write_bytes(b"\xef\xbb\xbf" + f"  {dados}\r\n".encode("utf-8"))   # BOM do UTF-8, como o Inno grava
    resolvida = resolver_pasta_dados(programa, {})
    assert resolvida == dados.resolve() and "\ufeff" not in str(resolvida)


def test_sem_ambiente_explicito_usa_o_ambiente_do_processo(tmp_path, monkeypatch):
    monkeypatch.setenv("BISTURI_DADOS", str(tmp_path / "dados"))
    assert resolver_pasta_dados(tmp_path / "app") == (tmp_path / "dados").resolve()


def test_variavel_apontando_para_a_propria_pasta_do_programa_e_o_modo_portatil(tmp_path):
    assert resolver_pasta_dados(tmp_path, {"BISTURI_DADOS": str(tmp_path) + os.sep}) == tmp_path.resolve()
    assert resolver_pasta_logs(tmp_path, tmp_path) == tmp_path.resolve() / "saida"                    # portátil: o ui.log continua em saida\
    assert resolver_pasta_logs(tmp_path / "app", tmp_path / "dados") == (tmp_path / "dados").resolve() / "logs"   # instalado: <dados>\logs


def test_aspas_em_volta_do_caminho_sao_tiradas(tmp_path):
    """`set BISTURI_DADOS="C:\\..."` no cmd guarda as aspas junto; aspas nunca fazem parte de um nome de pasta no Windows."""
    assert resolver_pasta_dados(tmp_path, {"BISTURI_DADOS": f'"{tmp_path / "dados"}"'}) == (tmp_path / "dados").resolve()


@pytest.mark.parametrize("valor, trecho", [
    ("", "vazia"),
    ("   \t", "vazia"),
    ('""', "vazia"),
    ("dados", "caminho completo"),
    ("C:dados", "caminho completo"),                  # relativo à pasta atual do disco C:
    ("\\dados", "caminho completo"),                  # relativo ao disco atual
    ("\\\\servidor\\compartilhada\\dados", "rede"),   # UNC
    ("//servidor/compartilhada/dados", "rede"),
    ("\\/servidor/compartilhada", "rede"),
    ("\\\\?\\C:\\dados", "rede"),                     # caminho "longo" do Windows: começa com \\, tratado como especial
    ("\\\\.\\C:", "rede"),                            # dispositivo
    ("\\??\\C:\\dados", "rede"),                      # caminho do núcleo do Windows
    ("C:\\a\x00b", "inválido"),
    ("C:\\a\nC:\\b", "mais de uma linha"),
])
def test_valor_invalido_na_variavel_da_erro_claro_em_portugues(tmp_path, valor, trecho):
    with pytest.raises(ErroPastaDados) as erro:
        resolver_pasta_dados(tmp_path, {"BISTURI_DADOS": valor})
    texto = str(erro.value)
    assert isinstance(erro.value, ValueError)
    assert "pasta de dados" in texto and "BISTURI_DADOS" in texto and trecho in texto, texto


@pytest.mark.parametrize("conteudo, trecho", [("", "vazia"), ("\r\n", "vazia"), ("Bisturi Clips", "caminho completo"),
                                              ("\\\\servidor\\dados", "rede")])
def test_valor_invalido_no_pasta_dados_txt_da_erro_que_cita_o_arquivo(tmp_path, conteudo, trecho):
    (tmp_path / "pasta_dados.txt").write_text(conteudo, encoding="utf-8-sig")
    with pytest.raises(ErroPastaDados) as erro:
        resolver_pasta_dados(tmp_path, {})
    assert "pasta_dados.txt" in str(erro.value) and trecho in str(erro.value), str(erro.value)


def test_pasta_dados_txt_ilegivel_da_erro_claro(tmp_path):
    (tmp_path / "pasta_dados.txt").write_bytes(b"\xff\xfeC\x00:\x00")       # UTF-16 (não é o que o instalador grava)
    with pytest.raises(ErroPastaDados, match="pasta_dados.txt"):
        resolver_pasta_dados(tmp_path, {})


def test_caminho_que_e_um_arquivo_da_erro(tmp_path):
    arquivo = tmp_path / "isto_e_um_arquivo.txt"
    arquivo.write_text("x", encoding="utf-8")
    with pytest.raises(ErroPastaDados, match="arquivo, não uma pasta"):
        resolver_pasta_dados(tmp_path, {"BISTURI_DADOS": str(arquivo)})


@pytest.mark.skipif(not NO_WINDOWS, reason="letra de disco só existe no Windows")
def test_disco_que_nao_existe_da_erro_claro(tmp_path):
    unidade = _unidade_que_nao_existe()
    if unidade is None:
        pytest.skip("todas as letras de D: a Z: existem neste PC")
    with pytest.raises(ErroPastaDados, match="não existe neste PC"):
        resolver_pasta_dados(tmp_path, {"BISTURI_DADOS": f"{unidade}\\Bisturi Clips"})


def test_pasta_que_ainda_nao_existe_vale_e_a_resolucao_nao_cria_nada(tmp_path):
    """O instalador cria a pasta; se a pessoa a apagou, quem grava recria a subpasta de que precisa. Resolver nunca cria pasta."""
    dados = tmp_path / "ainda" / "nao" / "existe"
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "pasta_dados.txt").write_text(str(dados), encoding="utf-8-sig")
    antes = sorted(tmp_path.rglob("*"))
    assert resolver_pasta_dados(tmp_path / "app", {}) == dados.resolve()
    assert resolver_pasta_dados(tmp_path / "app", {"BISTURI_DADOS": str(dados)}) == dados.resolve()
    assert resolver_pasta_logs(tmp_path / "app", dados) == dados.resolve() / "logs"
    assert sorted(tmp_path.rglob("*")) == antes and not dados.exists()


def test_raiz_do_programa_e_a_mesma_do_tools_root():
    assert caminhos.RAIZ_PROGRAMA == ROOT == RAIZ


def test_modo_portatil_de_verdade_devolve_os_caminhos_de_hoje_e_nao_mexe_no_ambiente():
    """Rodando da pasta do projeto, sem variável e sem `pasta_dados.txt` (como o dono roda): os dados SÃO a raiz, o log continua em
    `saida\\ui.log`, o HF_HOME não é definido (o modelo continua em %USERPROFILE%\\.cache\\huggingface) e todas as constantes de hoje batem."""
    assert not (RAIZ / "pasta_dados.txt").exists(), "há um pasta_dados.txt na raiz do projeto: o modo portátil não é o de hoje"
    codigo = CODIGO_INFO.replace("print(json.dumps({", r"""
from cs2clip import baixar_demo, demos_cs2, editar_demo, gravador_demo, montagem, steam_api, tools
import ui.__main__ as um
d = cm.PASTA_DADOS
hoje = {"baixar_demo.PASTA_DEMOS": (baixar_demo.PASTA_DEMOS, d / "demos"), "baixar_demo.CONFIG": (baixar_demo.CONFIG, d / "config.toml"),
        "demos_cs2.PASTA_DEMOS_PROJETO": (demos_cs2.PASTA_DEMOS_PROJETO, d / "demos"),
        "demos_cs2.PASTA_CACHE_VOD": (demos_cs2.PASTA_CACHE_VOD, d / "dados" / "vod_info"),
        "steam_api.ARQUIVO": (steam_api.ARQUIVO, d / "dados" / "segredos_steam.json"),
        "editar_demo.PASTA_GRAVACAO": (editar_demo.PASTA_GRAVACAO, d / "gravacao_demo"), "editar_demo.PASTA_SAIDA": (editar_demo.PASTA_SAIDA, d / "saida"),
        "gravador_demo.PASTA_PADRAO": (gravador_demo.PASTA_PADRAO, d / "gravacao_demo"), "montagem.PASTA_MUSICAS": (montagem.PASTA_MUSICAS, d / "musicas"),
        "ui.__main__ log": (um.RAIZ / "saida", cm.PASTA_LOGS), "tools.ROOT": (tools.ROOT, cm.RAIZ_PROGRAMA)}
print(json.dumps({"hoje": {k: [str(a), str(b)] for k, (a, b) in hoje.items()},""", 1)
    info = _rodar(codigo, RAIZ)
    assert Path(info["pacote"]).resolve().is_relative_to(RAIZ / "cs2clip")
    assert Path(info["raiz"]) == RAIZ and Path(info["dados"]) == RAIZ and Path(info["logs"]) == RAIZ / "saida"
    assert info["instalado"] is False
    assert info["mudou_no_ambiente"] == [] and info["hf_home"] is None and info["hf_importado_junto"] is False
    diferentes = {k: v for k, v in info["hoje"].items() if Path(v[0]) != Path(v[1])}
    assert diferentes == {}, diferentes
    if info["biblioteca"] is not None:                 # o huggingface_hub segue no lugar de sempre (não é a pasta do projeto)
        assert not Path(info["biblioteca"][0]).resolve().is_relative_to(RAIZ)


def test_modo_instalado_pela_variavel_dados_em_outra_pasta_logs_e_modelos_dentro_dela(tmp_path):
    dados = tmp_path / "Vídeos" / "Bisturi Clips"
    info = _rodar(CODIGO_INFO, RAIZ, {"BISTURI_DADOS": str(dados)})
    assert Path(info["raiz"]) == RAIZ and Path(info["dados"]) == dados.resolve() and info["instalado"] is True
    assert Path(info["logs"]) == dados.resolve() / "logs"
    assert Path(info["hf_home"]) == dados.resolve() / "modelos" and info["mudou_no_ambiente"] == ["HF_HOME"]
    assert info["hf_importado_junto"] is False             # o caminhos.py não puxa o huggingface_hub (senão o HF_HOME chegaria tarde)
    if info["biblioteca"] is not None:
        assert Path(info["biblioteca"][0]) == dados.resolve() / "modelos" and Path(info["biblioteca"][1]) == dados.resolve() / "modelos" / "hub"
    assert info["dados_existe"] is False and info["logs_existe"] is False and not dados.exists()   # importar não cria pasta


def test_modo_instalado_nao_troca_o_hf_home_que_a_pessoa_ja_tem(tmp_path):
    proprio = tmp_path / "meus_modelos"
    info = _rodar(CODIGO_INFO, RAIZ, {"BISTURI_DADOS": str(tmp_path / "dados"), "HF_HOME": str(proprio)})
    assert info["instalado"] is True and Path(info["hf_home"]) == proprio and info["mudou_no_ambiente"] == []


def test_modo_instalado_pelo_pasta_dados_txt_ao_lado_do_programa(tmp_path):
    """Como o atalho do instalador abre: sem variável; o `app\\pasta_dados.txt` (UTF-8 com BOM) diz onde estão os dados."""
    programa, dados = _programa_pequeno(tmp_path / "Programs" / "Bisturi Clips" / "app"), tmp_path / "Vídeos" / "Bisturi Clips"
    (programa / "pasta_dados.txt").write_text(f"{dados}\r\n", encoding="utf-8-sig")
    info = _rodar(CODIGO_INFO, programa)
    assert Path(info["pacote"]).resolve().is_relative_to(programa.resolve())        # usou a cópia, não o projeto
    assert Path(info["raiz"]) == programa.resolve() and Path(info["dados"]) == dados.resolve() and info["instalado"] is True
    assert Path(info["logs"]) == dados.resolve() / "logs" and Path(info["hf_home"]) == dados.resolve() / "modelos"
    assert not dados.exists()


def test_valor_invalido_ao_importar_para_com_a_mensagem_e_nao_com_um_caminho_esquisito():
    env = {k: v for k, v in os.environ.items() if k.upper() not in {"BISTURI_DADOS", "HF_HOME"}}
    env.update({"BISTURI_DADOS": "Bisturi Clips", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
    r = subprocess.run([sys.executable, "-c", "import cs2clip.caminhos"], cwd=str(RAIZ), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120, **sem_janela())
    assert r.returncode != 0
    assert "ErroPastaDados" in r.stderr and "caminho completo" in r.stderr and "BISTURI_DADOS" in r.stderr, r.stderr[-2000:]


# ═══ Bloco 2: ainda não passa (P2.2/P2.3 trocam o código que grava na raiz) ═════════════════════════════════════════════════════

def _foto(raiz: Path) -> dict[str, tuple[int, int]]:
    """Nome, tamanho e data de tudo dentro de `raiz` (pastas também: a data da pasta muda quando algo é criado ou apagado nela,
    o que pega até arquivo temporário que nasceu e sumiu). `__pycache__` fica de fora: o instalador já entrega os .pyc."""
    foto: dict[str, tuple[int, int]] = {}
    for pasta, subpastas, arquivos in os.walk(raiz):
        subpastas[:] = [s for s in subpastas if s != "__pycache__"]
        base = Path(pasta)
        foto[str(base.relative_to(raiz)) + os.sep] = (0, base.stat().st_mtime_ns)
        for nome in arquivos:
            st = (base / nome).stat()
            foto[str((base / nome).relative_to(raiz))] = (st.st_size, st.st_mtime_ns)
    return foto


def _diferencas(antes: dict[str, tuple[int, int]], depois: dict[str, tuple[int, int]]) -> str:
    novos = sorted(set(depois) - set(antes))
    sumiram = sorted(set(antes) - set(depois))
    mudaram = sorted(k for k in set(antes) & set(depois) if antes[k] != depois[k])
    return f"novos={novos[:25]} sumiram={sumiram[:25]} mudaram={mudaram[:25]}"


# Roda no processo à parte, com a CÓPIA do programa como pasta atual. Junta os problemas em vez de parar no primeiro (quem tirar a
# marca xfail vê a lista inteira). Os processos filhos nunca são abertos: os "popen" falsos levantam.
CODIGO_USO_COMPLETO = r"""
import json, re, sys, traceback, types
from pathlib import Path

par = json.loads(sys.argv[1])
prog, dados, musica = Path(par["programa"]).resolve(), Path(par["dados"]).resolve(), Path(par["musica"])
problemas = []

def passo(nome, funcao):
    try:
        funcao()
    except Exception as e:
        problemas.append(f"{nome}: {type(e).__name__}: {e} | {traceback.format_exc(limit=3)[-600:]}")

def conferir(cond, texto):
    if not cond:
        problemas.append(texto)

import cs2clip
if not Path(cs2clip.__file__).resolve().is_relative_to(prog):
    raise SystemExit(f"importou o cs2clip errado: {cs2clip.__file__}")
import cs2clip.caminhos as cm
conferir(cm.RAIZ_PROGRAMA == prog and cm.PASTA_DADOS == dados and cm.modo_instalado(), f"resolução: {cm.RAIZ_PROGRAMA} {cm.PASTA_DADOS}")

def log():
    import ui.__main__ as um
    um._SEM_CONSOLE = True                      # como no atalho (pythonw): o log vai para arquivo
    arq = um.preparar_log()
    conferir(arq is not None and Path(arq).resolve().parent == cm.PASTA_LOGS, f"o ui.log foi para {arq}, não para {cm.PASTA_LOGS}")

def processar():
    # um "vídeo" na pasta entrada da pessoa, processado pelos caminhos PADRÃO do CLI (sem --saida/--config): o config nasce do exemplo
    (dados / "entrada").mkdir(parents=True, exist_ok=True)
    (dados / "entrada" / "partida.mp4").write_bytes(b"x")
    import cs2clip.pipeline as pl
    from cs2clip.__main__ import main
    vistos = {}
    def analise(p, cfg, o, pd, R, **extras):
        vistos.update(saida=Path(o).resolve(), jogador=Path(pd).resolve())
        pasta = Path(o) / p.stem
        pasta.mkdir(parents=True, exist_ok=True)
        return types.SimpleNamespace(duration=10.0), p.stem, pasta, [], [], None
    pl.analyze = analise
    pl.render_all = lambda *a, **k: ([], [], [])
    rc = main(["--so-analise", "--forcar-gpu"])
    conferir(rc == 0, f"processar devolveu {rc}")
    conferir(vistos.get("saida") == dados / "saida", f"saída do processamento: {vistos.get('saida')}")
    conferir(vistos.get("jogador") == dados / "dados" / "jogador", f"pasta do jogador: {vistos.get('jogador')}")
    conferir((dados / "saida" / "progresso.json").is_file(), "progresso.json não foi para <dados>\\saida")
    nascido = dados / "config.toml"
    conferir(nascido.is_file() and nascido.read_bytes() == (prog / "config.exemplo.toml").read_bytes(),
             "o config.toml não nasceu do exemplo do programa dentro da pasta de dados")

def interface():
    from fastapi.testclient import TestClient
    import cs2clip.musicas_catalogo as mc
    from ui.servidor import create_app
    mc.medir_faixa = lambda arq: {"duracao_s": 150.0, "bpm": 128.0, "batidas_detectadas": 300, "energia": 0.8, "drop_s": 40.0,
                                  "impacto_db": 9.0, "emocao": 0.7}
    def nao_abrir(*a, **k):
        raise AssertionError("este teste não abre processo")
    app = create_app(port=8765, popen_factory=nao_abrir, vod_popen_factory=nao_abrir, gravacao_popen_factory=nao_abrir,
                     receita_popen_factory=nao_abrir, gravacao_replays=lambda: None, receita_conta_logada=lambda: 0,
                     receita_downloads=musica.parent, steam_conta_aberta=lambda: 0, chooser=lambda: [],
                     music_chooser=lambda: [{"caminho": str(musica), "nome": musica.name, "tamanho": musica.stat().st_size}],
                     open_folder_fn=lambda p: None, checar_fn=lambda p: {})
    gets = sorted({r.path for r in app.routes if getattr(r, "path", "").startswith("/api/") and "GET" in (getattr(r, "methods", None) or ())})
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        achado = re.search(r'name="ui-token" content="([^"]+)"', c.get("/").text)   # o token vem do index.html, como a página faz
        if not achado:
            raise AssertionError("o index.html não trouxe o token")
        h = {"X-Token": achado.group(1)}
        for rota in gets:
            url = re.sub(r"\{[^}]+\}", "nao-existe", rota)
            r = c.get(url, headers=h)
            conferir(r.status_code != 403 and r.status_code < 500, f"GET {url} = {r.status_code} {r.text[:200]}")
        r = c.post("/api/config", json={"alteracoes": {"shorts.marca": "@teste"}}, headers=h)
        conferir(r.status_code == 200, f"salvar config = {r.status_code} {r.text[:200]}")
        conferir("@teste" in (dados / "config.toml").read_text(encoding="utf-8") if (dados / "config.toml").is_file() else False,
                 "a configuração salva não está em <dados>\\config.toml")
        r = c.post("/api/boas-vindas", json={}, headers=h)
        conferir(r.status_code == 200 and (dados / "dados" / "boas_vindas.json").is_file(), f"boas-vindas = {r.status_code} {r.text[:200]}")
        r = c.post("/api/musicas/escolher", headers=h)
        conferir(r.status_code == 200, f"escolher música = {r.status_code} {r.text[:200]}")
        r = c.post("/api/musicas/adicionar", json={"caminho": str(musica), "licenca": "cc0", "autor": "Autor Teste", "titulo": "Faixa Teste"},
                   headers=h)
        conferir(r.status_code == 200, f"adicionar música = {r.status_code} {r.text[:200]}")
        conferir((dados / "musicas" / musica.name).is_file(), "a música adicionada não está em <dados>\\musicas")

passo("log da interface", log)
passo("processar um vídeo falso", processar)
passo("abrir a interface, salvar configuração e adicionar música", interface)
print(json.dumps({"problemas": problemas}))
"""


# Passa desde que o núcleo (P2.2), a interface (P2.3) e as músicas (item 6) usam a pasta de dados: a marca xfail saiu em 26/11/2026.
def test_nada_e_escrito_na_pasta_do_programa(tmp_path):
    """Modo instalado simulado: uma cópia do programa (`app\\` com `pasta_dados.txt`) e os dados em outra pasta. Abrir a interface (token em
    todo GET de /api/), salvar a configuração, adicionar música, processar um vídeo falso e nascer o config do exemplo não podem criar,
    mudar nem apagar nada na pasta do programa."""
    programa = tmp_path / "Programs" / "Bisturi Clips" / "app"
    dados = tmp_path / "Vídeos" / "Bisturi Clips"
    musica = tmp_path / "Downloads" / "Faixa Teste.mp3"
    ignorar = shutil.ignore_patterns("__pycache__", "*.pyc")
    for pasta in ("cs2clip", "ui"):
        shutil.copytree(RAIZ / pasta, programa / pasta, ignore=ignorar)
    shutil.copy2(RAIZ / "config.exemplo.toml", programa / "config.exemplo.toml")
    (programa / "musicas").mkdir()
    shutil.copy2(RAIZ / "musicas" / "catalogo.json", programa / "musicas" / "catalogo.json")   # catálogo de fábrica (os mp3 não vão no teste)
    (programa / "pasta_dados.txt").write_text(f"{dados}\r\n", encoding="utf-8-sig")
    musica.parent.mkdir(parents=True)
    musica.write_bytes(b"x" * 90_000)

    antes = _foto(programa)
    par = json.dumps({"programa": str(programa), "dados": str(dados), "musica": str(musica)})
    resultado = _rodar(CODIGO_USO_COMPLETO, programa, args=(par,), prazo_s=300)
    depois = _foto(programa)

    assert depois == antes, "a pasta do programa mudou: " + _diferencas(antes, depois)
    assert resultado["problemas"] == [], "\n".join(resultado["problemas"])


CODIGO_PONTO_DE_ENTRADA = r"""
import importlib, json, sys
importlib.import_module(sys.argv[1])
biblioteca = None
try:
    import huggingface_hub.constants as k
    biblioteca = str(k.HF_HOME)
except ImportError:
    pass
print(json.dumps({"caminhos": "cs2clip.caminhos" in sys.modules, "biblioteca": biblioteca}))
"""


# Os quatro pontos de entrada importam o caminhos.py como PRIMEIRO import do pacote (núcleo na P2.2, interface na P2.3).
@pytest.mark.parametrize("modulo", ["cs2clip.__main__", "cs2clip.gravador_demo", "cs2clip.editar_demo", "ui.__main__"])
def test_pontos_de_entrada_carregam_caminhos_antes_do_huggingface(tmp_path, modulo):
    """O HF_HOME do modo instalado só vale se o caminhos.py for importado ANTES do huggingface_hub: cada ponto de entrada tem de carregá-lo."""
    dados = tmp_path / "dados"
    info = _rodar(CODIGO_PONTO_DE_ENTRADA, RAIZ, {"BISTURI_DADOS": str(dados)}, args=(modulo,))
    assert info["caminhos"] is True
    if info["biblioteca"] is not None:
        assert Path(info["biblioteca"]) == dados.resolve() / "modelos"
