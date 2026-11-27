"""P2.3 do plano de publicação (itens 4 e 5 da P2, 26/11/2026): a interface com DUAS bases.

- a do PROGRAMA (`root_dir`): só os arquivos da interface (`static`) e a pasta de trabalho dos processos filhos (`python -m cs2clip` roda de lá);
- a dos DADOS da pessoa (`dados_dir`): `saida`, `entrada`, `dados`, `config.toml`, `trabalhos`, `demos`, `gravacao_demo` e o que a interface aceita
  como "arquivo do projeto".
No modo portátil (e nos ~35 arquivos de teste que passam só `root_dir=tmp`) as duas são a mesma pasta, como sempre foi.
Errar a base é erro de segurança: servir arquivo do programa, ou recusar o da pessoa. Os filhos recebem `BISTURI_DADOS` (provado com processo de verdade).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cs2clip import caminhos
from cs2clip import entrada as ent
from cs2clip.config import Config
from cs2clip.tools import sem_janela
from test_pasta_dados import _diferencas, _foto
from test_ui_dados_seguranca import PORT, criar_projeto
from ui import servidor
from ui.fila import GerenciadorFila
from ui.gravacao import GerenciadorGravacao
from ui.receitas import GerenciadorReceitas
from ui.servidor import create_app

RAIZ = Path(__file__).resolve().parents[1]
DEMO = "match730_003849321203751714819_0599033075_201.dem"
DEMO_DO_PROGRAMA = "match730_003849321203751714819_0599033075_202.dem"
BASE_STEAM = 76561197960265728
GETS_DA_API = ["/api/indice", "/api/entrada", "/api/fila", "/api/config", "/api/gravacao", "/api/progresso", "/api/log", "/api/videos/v1",
               "/api/musicas", "/api/trabalhos", "/api/boas-vindas", "/api/baixar-vod", "/api/partidas-novas"]

# O "programa" que o filho roda nos testes de processo de verdade: diz o que recebeu (a variável e a pasta de trabalho) e sai.
IMPRIME_DADOS = (
    "import os, sys\n"
    "sys.stdout.reconfigure(encoding='utf-8')\n"
    "print('BISTURI_DADOS=' + os.environ.get('BISTURI_DADOS', '<nenhuma>'))\n"
    "print('PASTA_ATUAL=' + os.getcwd())\n"
)


class ProcessoParado:
    """Imita o Popen de um filho que já terminou bem (pid None: o encerramento nunca manda taskkill para um PID que pode ser de outro programa)."""
    pid = None

    def __init__(self, *_a, **_k):
        self.stdout = iter(())

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


def _isca_do_programa(programa: Path) -> None:
    """A pasta do programa com "dados" de mentira dentro: nada disto pode ser lido, servido, aceito ou mudado pela interface no modo instalado."""
    for kit in ("v1", "p1"):
        (programa / "saida" / kit / "cortes").mkdir(parents=True)
        (programa / "saida" / kit / "cortes" / "corte.mp4").write_bytes(b"DO-PROGRAMA")
        (programa / "saida" / kit / "momentos.json").write_text('{"formato": 1, "momentos": []}', encoding="utf-8")
    (programa / "saida" / "indice.json").write_text(json.dumps([{"id": "p1", "arquivo": "isca.mp4"}]), encoding="utf-8")
    (programa / "entrada").mkdir()
    (programa / "entrada" / "do_programa.mp4").write_bytes(b"x")
    (programa / "solto.mp4").write_bytes(b"x")
    (programa / "config.toml").write_text('[shorts]\nmarca = "@do_programa"\n', encoding="utf-8")
    (programa / "dados").mkdir()
    (programa / "dados" / "boas_vindas.json").write_text('{"visto_em": "isca"}', encoding="utf-8")
    (programa / "demos").mkdir()
    (programa / "demos" / DEMO_DO_PROGRAMA).write_bytes(ent.CABECALHO_CS2 + b"isca")
    (programa / "trabalhos" / "isca").mkdir(parents=True)
    (programa / "trabalhos" / "isca" / "receita.json").write_text(json.dumps(
        {"id": "isca", "criada_em": 1.0, "titulo": "isca", "entrada": {"tipo": "video", "valor": "x", "detalhes": {}}, "opcoes": {},
         "estado": "pronta", "etapas": [], "dados": {}, "resultado": "", "erro": ""}), encoding="utf-8")
    (programa / "gravacao_demo" / Path(DEMO).stem).mkdir(parents=True)
    (programa / "gravacao_demo" / Path(DEMO).stem / "registro.json").write_text('{"resultados": [{"round": 99}]}', encoding="utf-8")
    (programa / "imprime_dados.py").write_text(IMPRIME_DADOS, encoding="utf-8")


@pytest.fixture
def bases(tmp_path):
    """Modo instalado simulado: o programa em `Programs\\Bisturi Clips\\app` (com iscas) e os dados da pessoa em outra pasta."""
    programa = tmp_path / "Programs" / "Bisturi Clips" / "app"
    dados = tmp_path / "Vídeos" / "Bisturi Clips"
    _isca_do_programa(programa)
    criar_projeto(dados)
    (dados / "demos").mkdir()
    (dados / "demos" / DEMO).write_bytes(ent.CABECALHO_CS2 + b"corpo")
    (dados / "gravacao_demo" / Path(DEMO).stem).mkdir(parents=True)
    (dados / "gravacao_demo" / Path(DEMO).stem / "registro.json").write_text('{"resultados": [{"round": 3}]}', encoding="utf-8")
    return programa, dados


class Cliente(TestClient):
    estado: Any          # o `app.state` do app de teste (gerenciadores, token, as duas bases)
    rotas: list[Any]


def _cliente(programa: Path, dados: Path | None, **extra: Any) -> Cliente:
    kw: dict[str, Any] = dict(port=PORT, popen_factory=ProcessoParado, vod_popen_factory=ProcessoParado, gravacao_popen_factory=ProcessoParado,
                              receita_popen_factory=ProcessoParado, gravacao_replays=lambda: None, receita_conta_logada=lambda: 0,
                              steam_conta_aberta=lambda: 0, chooser=lambda: [], music_chooser=lambda: [], open_folder_fn=lambda p: None,
                              checar_fn=lambda p: {"codigo": 0, "texto": "ok"})
    kw.update(extra)
    app = create_app(root_dir=programa, dados_dir=dados, **kw) if dados is not None else create_app(root_dir=programa, **kw)
    c = Cliente(app, base_url=f"http://127.0.0.1:{PORT}")
    c.estado, c.rotas = app.state, list(app.routes)
    return c


def _h(c: Cliente) -> dict[str, str]:
    return {"X-Token": c.estado.token}


def _esperar(condicao, prazo_s: float = 60.0) -> bool:
    fim = time.monotonic() + prazo_s
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.02)
    return condicao()


# ═══ (a) modo portátil / só `root_dir`: tudo como hoje ═══════════════════════════════════════════════════════════════════════════

def test_a_so_root_dir_programa_e_dados_sao_a_mesma_pasta_como_hoje(tmp_path):
    criar_projeto(tmp_path)
    with _cliente(tmp_path, None) as c:
        st = c.estado
        assert st.raiz == st.raiz_dados == tmp_path.resolve()
        assert st.dados.saida == tmp_path.resolve() / "saida" and st.dados.entrada == tmp_path.resolve() / "entrada"
        assert st.fila.raiz == st.fila.programa == tmp_path.resolve()
        assert Path(st.receitas.raiz).resolve() == Path(st.receitas.programa).resolve() == tmp_path.resolve()
        assert st.receitas.pasta == tmp_path / "trabalhos" and (tmp_path / "trabalhos").is_dir()
        assert Path(st.gravacao.raiz).resolve() == Path(st.gravacao.programa).resolve() == tmp_path.resolve()
        assert st.configuracao.caminho == tmp_path.resolve() / "config.toml"
        assert st.vods.entrada == tmp_path.resolve() / "entrada"
        assert c.get("/media/v1/cortes/corte.mp4").content == b"0123456789ABCDEFGHIJ"
        solto = tmp_path / "solto.mp4"                                     # qualquer arquivo dentro da raiz continua aceito sem o seletor
        solto.write_bytes(b"x")
        assert c.post("/api/fila", json={"arquivos": [str(solto)]}, headers=_h(c)).status_code == 200


def test_a_sem_root_dir_o_programa_e_a_pasta_do_codigo_e_os_dados_vem_de_pasta_dados(tmp_path, monkeypatch):
    """Como o `ui/__main__` abre (sem `root_dir`): o programa é a pasta do código e os dados, a `PASTA_DADOS` (lida na hora: no portátil, a mesma pasta)."""
    dados = tmp_path / "dados_da_pessoa"
    monkeypatch.setattr(caminhos, "PASTA_DADOS", dados.resolve())
    app = create_app(port=PORT, popen_factory=ProcessoParado, gravacao_replays=lambda: None)
    try:
        assert app.state.raiz == caminhos.RAIZ_PROGRAMA == RAIZ
        assert app.state.raiz_dados == dados.resolve()
        assert (dados / "saida").is_dir() and (dados / "entrada").is_dir() and (dados / "trabalhos").is_dir()
        assert app.state.fila.raiz == dados.resolve() and app.state.fila.programa == RAIZ
        assert app.state.configuracao.caminho == dados.resolve() / "config.toml"
    finally:
        app.state.fila.encerrar()


# ═══ (b) modo instalado: tudo vai para a pasta de dados e nada é criado na pasta do programa ═══════════════════════════════════

def test_b_instalado_tudo_da_pessoa_vai_para_a_pasta_de_dados_e_o_programa_nao_muda(bases):
    programa, dados = bases
    antes = _foto(programa)
    with _cliente(programa, dados) as c:
        st = c.estado
        assert st.raiz == programa.resolve() and st.raiz_dados == dados.resolve()
        assert st.fila.programa == programa.resolve() and Path(st.receitas.programa).resolve() == programa.resolve()
        assert Path(st.gravacao.programa).resolve() == programa.resolve()
        assert (dados / "trabalhos").is_dir()
        h = _h(c)
        # saída e entrada: as da pessoa (o índice e o arquivo "do programa" não aparecem)
        assert [i["id"] for i in c.get("/api/indice", headers=h).json()["itens"]] == ["v1"]
        assert c.get("/api/entrada", headers=h).json()["itens"] == []
        envio = c.put("/api/upload/arquivo", params={"nome": "enviado.mp4"}, content=b"abc", headers={**h, "Content-Length": "3"})
        assert envio.status_code == 200 and (dados / "entrada" / "enviado.mp4").read_bytes() == b"abc"
        assert [i["nome"] for i in c.get("/api/entrada", headers=h).json()["itens"]] == ["enviado.mp4"]
        # config.toml: o da pessoa (o "do programa" nunca é lido nem mudado)
        marca = next(x for x in c.get("/api/config", headers=h).json()["campos"] if x["secao"] == "shorts" and x["chave"] == "marca")
        assert marca["valor"] != "@do_programa"
        assert c.post("/api/config", json={"alteracoes": {"shorts.marca": "@da_pessoa"}}, headers=h).status_code == 200
        assert "@da_pessoa" in (dados / "config.toml").read_text(encoding="utf-8")
        # dados/: a marca de boas-vindas da pessoa (a do programa não conta)
        assert c.get("/api/boas-vindas", headers=h).json()["mostrar"] is True
        assert c.post("/api/boas-vindas", json={}, headers=h).status_code == 200 and (dados / "dados" / "boas_vindas.json").is_file()
        # demos e gravações: as da pessoa
        assert [d["nome"] for d in c.get("/api/gravacao", headers=h).json()["demos"]] == [DEMO]
        assert c.get("/api/gravacao/resultados", params={"demo": DEMO}, headers=h).json()["resultados"] == [{"round": 3}]
        # trabalhos: os da pessoa (o "isca" do programa não aparece); um trabalho novo é salvo em <dados>\trabalhos
        assert c.get("/api/trabalhos", headers=h).json()["itens"] == []
        r = c.post("/api/trabalhos", json={"texto": str(dados / "demos" / DEMO)}, headers=h)
        assert r.status_code == 200, r.text
        assert (dados / "trabalhos" / r.json()["id"] / "receita.json").is_file()
        st.receitas.esperar(r.json()["id"], tempo=30)
    depois = _foto(programa)
    assert depois == antes, "a pasta do programa mudou: " + _diferencas(antes, depois)


# ═══ (c) /media e "dentro do projeto": só a pasta de dados ════════════════════════════════════════════════════════════════════

def test_c_media_serve_so_da_pasta_de_dados(bases):
    programa, dados = bases
    with _cliente(programa, dados) as c:
        assert c.get("/media/v1/cortes/corte.mp4").content == b"0123456789ABCDEFGHIJ"       # o kit da pessoa, não o homônimo do programa
        assert c.get("/media/p1/cortes/corte.mp4").status_code == 404                       # o kit que só existe no programa
        assert c.get("/media-geral/indice.json").json()[0]["id"] == "v1"
        assert c.get("/media/v1/..%2F..%2Fconfig.toml").status_code in (403, 404)


def test_c_arquivo_do_programa_nao_e_dentro_do_projeto_e_o_da_pessoa_e(bases):
    programa, dados = bases
    da_pessoa = dados / "solto.mp4"
    da_pessoa.write_bytes(b"x")
    with _cliente(programa, dados) as c:
        h = _h(c)
        recusado = c.post("/api/fila", json={"arquivos": [str(programa / "solto.mp4")]}, headers=h)
        assert recusado.status_code == 403 and recusado.json()["motivo"] == "precisa_seletor"
        assert c.post("/api/fila", json={"arquivos": ["entrada:do_programa.mp4"]}, headers=h).status_code == 404
        aceito = c.post("/api/fila", json={"arquivos": [str(da_pessoa)]}, headers=h)
        assert aceito.status_code == 200 and aceito.json()["itens"][0]["arquivo"] == str(da_pessoa.resolve())


def test_c_programa_dentro_da_pasta_de_dados_continua_fora_do_projeto(tmp_path):
    """A pessoa escolheu para os dados a pasta ACIMA do `app\\`: o que está dentro do programa continua não sendo "da pessoa"."""
    dados = tmp_path / "Bisturi Clips"
    programa = dados / "app"
    _isca_do_programa(programa)
    criar_projeto(dados)
    da_pessoa = dados / "solto.mp4"
    da_pessoa.write_bytes(b"x")
    with _cliente(programa, dados) as c:
        h = _h(c)
        assert c.post("/api/fila", json={"arquivos": [str(programa / "solto.mp4")]}, headers=h).status_code == 403
        assert c.post("/api/fila", json={"arquivos": [str(da_pessoa)]}, headers=h).status_code == 200
        assert c.estado.receitas.plano(str(programa / "solto.mp4"))["entrada"]["aceita"] is False
        assert c.estado.receitas.plano(str(da_pessoa))["entrada"]["aceita"] is True


# ═══ (d) a fila e as receitas recusam o que está fora dos dados, como recusavam fora da raiz ═════════════════════════════════

def test_d_receitas_aceitam_so_arquivo_da_pasta_de_dados(bases, tmp_path):
    programa, dados = bases
    fora = tmp_path / "fora" / "partida.mp4"
    fora.parent.mkdir()
    fora.write_bytes(b"x")
    da_pessoa = dados / "entrada" / "partida.mp4"
    da_pessoa.write_bytes(b"x")
    with _cliente(programa, dados) as c:
        h = _h(c)
        for recusado in (programa / "solto.mp4", programa / "demos" / DEMO_DO_PROGRAMA, fora):
            plano = c.post("/api/entrada/reconhecer", json={"texto": str(recusado)}, headers=h).json()
            assert plano["entrada"]["aceita"] is False and "Escolher arquivo" in plano["entrada"]["mensagem"], recusado
            assert c.post("/api/trabalhos", json={"texto": str(recusado)}, headers=h).status_code == 403, recusado
        assert c.post("/api/entrada/reconhecer", json={"texto": str(da_pessoa)}, headers=h).json()["entrada"]["aceita"] is True
        assert c.post("/api/entrada/reconhecer", json={"texto": str(dados / "demos" / DEMO)}, headers=h).json()["entrada"]["aceita"] is True


def test_d_fila_recusa_arquivo_de_fora_como_antes(bases, tmp_path):
    programa, dados = bases
    fora = tmp_path / "fora" / "partida.mp4"
    fora.parent.mkdir()
    fora.write_bytes(b"x")
    with _cliente(programa, dados) as c:
        r = c.post("/api/fila", json={"arquivos": [str(fora)]}, headers=_h(c))
        assert r.status_code == 403 and r.json()["motivo"] == "precisa_seletor"


# ═══ (e) o token continua valendo em todo /api/ (revisão de 26/11) ════════════════════════════════════════════════════════════

def test_e_token_em_todo_api_no_modo_instalado(bases):
    programa, dados = bases
    with _cliente(programa, dados) as c:
        rotas = [(r.path, m) for r in c.rotas if getattr(r, "path", "").startswith("/api/")
                 for m in sorted(getattr(r, "methods", None) or ())]
        assert len(rotas) > 40
        for caminho, metodo in rotas:
            url = caminho.replace("{arquivo}", "x.mp3")
            for nome in ("video_id", "trabalho_id", "job_id"):
                url = url.replace("{" + nome + "}", "v1")
            r = c.request(metodo, url, json={} if metodo in ("POST", "PUT", "PATCH", "DELETE") else None)
            if metodo in ("GET", "HEAD") and caminho == "/api/musicas/audio/{arquivo}":
                assert r.status_code != 403, (metodo, url)       # a única exceção de sempre (a tag <audio> não manda cabeçalho)
            else:
                assert r.status_code == 403, (metodo, url, r.status_code)
        for rota in GETS_DA_API:
            assert c.get(rota, headers={"X-Token": "errado"}).status_code == 403, rota
            assert c.get(rota, headers=_h(c)).status_code != 403, rota


# ═══ (f) os filhos recebem BISTURI_DADOS (processo de verdade) ═══════════════════════════════════════════════════════════════

def _o_que_o_filho_disse(linhas: list[str]) -> dict[str, str]:
    return dict(x.split("=", 1) for x in linhas if x.startswith(("BISTURI_DADOS=", "PASTA_ATUAL=")))


def _rodar_o_imprime(cmd, **kw):
    """O Popen DE VERDADE, com os argumentos que o gerenciador montou (pasta, ambiente, sem janela), só que rodando o `imprime_dados` do programa de teste."""
    return subprocess.Popen([sys.executable, "-m", "imprime_dados"], **kw)


@pytest.fixture
def ambiente_de_fora(monkeypatch, tmp_path):
    """O processo da interface tem OUTRO `BISTURI_DADOS` no ambiente: o filho tem de receber o da interface, não o herdado."""
    monkeypatch.setenv("BISTURI_DADOS", str(tmp_path / "outra_pasta"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")


def _conferir_filho(disse: dict[str, str], programa: Path, dados: Path) -> None:
    assert Path(disse["BISTURI_DADOS"]) == dados.resolve(), disse
    assert Path(disse["PASTA_ATUAL"]).resolve() == programa.resolve(), disse


def test_f_filho_da_fila_recebe_bisturi_dados(bases, ambiente_de_fora):
    programa, dados = bases
    video = dados / "entrada" / "partida.mp4"
    video.write_bytes(b"x")
    fila = GerenciadorFila(dados, command_builder=lambda raiz, arquivos, opcoes: [sys.executable, "-m", "imprime_dados"], programa=programa)
    try:
        job = fila.adicionar(video)
        assert _esperar(lambda: next(j for j in fila.listar()["itens"] if j["id"] == job["id"])["estado"] not in ("na_fila", "rodando"))
        fim = next(j for j in fila.listar()["itens"] if j["id"] == job["id"])
        assert fim["estado"] == "pronto", fim
        _conferir_filho(_o_que_o_filho_disse(fim["detalhes"]), programa, dados)
    finally:
        fila.encerrar()


def test_f_filho_da_receita_recebe_bisturi_dados(bases, ambiente_de_fora):
    programa, dados = bases
    cfg = Config()
    cfg.gravacao_demo.conta_steamid, cfg.gravacao_demo.conta_principal_id = str(BASE_STEAM + 200), str(BASE_STEAM + 100)
    g = GerenciadorReceitas(dados, popen_factory=_rodar_o_imprime, python=sys.executable, conta_logada=lambda: 200, config_fn=lambda: cfg,
                            poll_s=0.01, procurar_em=lambda: [dados / "demos"], programa=programa)
    r = g.iniciar(str(dados / "demos" / DEMO))
    fim = g.esperar(r["id"], tempo=60)
    assert fim["estado"] == "pronta", fim
    _conferir_filho(_o_que_o_filho_disse(fim["linhas"]), programa, dados)


def test_f_filho_da_gravacao_recebe_bisturi_dados(bases, ambiente_de_fora):
    programa, dados = bases
    g = GerenciadorGravacao(dados, popen_factory=_rodar_o_imprime, python=sys.executable, pasta_replays=lambda: None, programa=programa)
    g.iniciar_download(so_listar=True)
    assert _esperar(lambda: g.estado()["job"]["estado"] != "rodando")
    assert g.estado()["job"]["estado"] == "pronto", g.estado()
    _conferir_filho(_o_que_o_filho_disse(g.estado()["linhas"]), programa, dados)


def test_f_filho_da_checagem_recebe_bisturi_dados(bases, ambiente_de_fora, monkeypatch):
    programa, dados = bases
    original = subprocess.run

    def rodar(cmd, **kw):
        assert cmd[1:] == ["-m", "cs2clip", "--checar"]
        return original([sys.executable, "-m", "imprime_dados"], **kw)
    monkeypatch.setattr(servidor.subprocess, "run", rodar)
    with _cliente(programa, dados, checar_fn=None) as c:
        r = c.post("/api/checar", headers=_h(c))
    assert r.status_code == 200 and r.json()["codigo"] == 0, r.text
    _conferir_filho(_o_que_o_filho_disse(r.json()["texto"].splitlines()), programa, dados)


# ═══ log e erro de importação de `ui/__main__` (processo à parte: o módulo lê a pasta de dados ao ser importado) ══════════════

def _python(codigo: str, extra: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k.upper() not in {"BISTURI_DADOS", "HF_HOME", "BISTURI_AVISO_JANELA"}}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", **extra})
    return subprocess.run([sys.executable, *(("-c", codigo) if codigo else ()), *args], cwd=str(RAIZ), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180, **sem_janela())


def test_log_do_modo_instalado_vai_para_dados_logs(tmp_path):
    dados = tmp_path / "Vídeos" / "Bisturi Clips"
    codigo = ("import json, sys\n"
              "import ui.__main__ as um\n"
              "um._SEM_CONSOLE = True\n"
              "arq = um.preparar_log()\n"
              "print(json.dumps({'arq': str(arq), 'pasta': str(um.PASTA_LOG)}))\n")
    r = _python(codigo, {"BISTURI_DADOS": str(dados)})
    assert r.returncode == 0, r.stderr[-3000:]
    info = json.loads(r.stdout.strip().splitlines()[-1])
    assert Path(info["arq"]) == dados.resolve() / "logs" / "ui.log" and Path(info["pasta"]) == dados.resolve() / "logs"
    assert (dados / "logs" / "ui.log").is_file()


def test_log_do_modo_portatil_continua_em_saida(tmp_path):
    r = _python("import json, ui.__main__ as um; print(json.dumps(str(um.PASTA_LOG)))", {})
    assert r.returncode == 0, r.stderr[-3000:]
    assert Path(json.loads(r.stdout.strip().splitlines()[-1])) == RAIZ / "saida"


def test_pasta_de_dados_invalida_com_console_da_a_mensagem_e_nao_um_rastro(tmp_path):
    # `--port 1`: se a importação NÃO parasse (o erro de hoje), o main() sai na conferência da porta, antes de criar o app ou abrir servidor
    r = _python("", {"BISTURI_DADOS": "Bisturi Clips"}, "-m", "ui", "--sem-navegador", "--port", "1")
    assert r.returncode == 1
    assert "Não consegui abrir o Bisturi Clips" in r.stderr and "caminho completo" in r.stderr and "BISTURI_DADOS" in r.stderr, r.stderr[-3000:]
    assert "Traceback" not in r.stderr, r.stderr[-3000:]


def test_pasta_de_dados_invalida_sem_console_vira_caixa_de_aviso(tmp_path):
    """Pelo atalho (pythonw) não há onde imprimir: sem a caixa do Windows, o programa fechava sem dizer nada."""
    codigo = ("import json, sys, types\n"
              "caixas = []\n"
              "sys.modules['ctypes'] = types.SimpleNamespace(windll=types.SimpleNamespace(user32=types.SimpleNamespace(\n"
              "    MessageBoxW=lambda *a: caixas.append(a[1]))))\n"
              "erro, sys.stderr = sys.stderr, None\n"
              "saiu = None\n"
              "try:\n"
              "    import ui.__main__\n"
              "except SystemExit as e:\n"
              "    saiu = e.code\n"
              "sys.stderr = erro\n"
              "print(json.dumps({'saiu': saiu, 'caixas': caixas}))\n")
    r = _python(codigo, {"BISTURI_DADOS": "Bisturi Clips"})
    assert r.returncode == 0, r.stderr[-3000:]
    info = json.loads(r.stdout.strip().splitlines()[-1])
    assert info["saiu"] == 1 and len(info["caixas"]) == 1
    assert "Não consegui abrir o Bisturi Clips" in info["caixas"][0] and "caminho completo" in info["caixas"][0]
