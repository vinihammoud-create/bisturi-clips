"""Teste de fumaça do Bisturi Clips (P5, item 8): roda COM O PYTHON DO PROGRAMA e confere que o programa instalado de fato anda.

Uso:
  python instalador/smoke/smoke.py --raiz <pasta do programa> --dados <pasta de dados> [--python <exe>] [--sem-processar]

Passos (cada um imprime OK ou FALHA com o motivo; a saída é 0 só se nenhum falhar):
  1. `python -m cs2clip --versao` bate com `cs2clip.__version__`.
  2. `python -m cs2clip.instalacao checar` sai 0 (se o módulo não existe, usa `python -m cs2clip --checar`).
  3. sobe a interface sem janela, confere `/health`, lê o token do HTML de `/` (como o navegador) e confere `/api/versao`
     COM o token e o 403 SEM o token.
  4. gera um vídeo de 60 s com o ffmpeg, processa pela FILA e por uma RECEITA da interface e confere o que ficou no disco.
  5. encerra a interface pelo Encerrar e confere que o processo saiu e que não sobrou processo filho.

Segurança: tudo roda numa subpasta de teste (`smoke-<id>`) criada DENTRO de `--dados` e apagada no fim; o `config.toml`, as saídas e os
modelos do dono nunca são tocados (o programa de teste enxerga só a subpasta, por `BISTURI_DADOS`). Nada de rede (`HF_HUB_OFFLINE=1`),
nenhum comando de shell (sempre lista de argumentos) e nenhum texto de fora vira comando. Por padrão o vídeo não tem trilha de som
(`--sem-voz`): o programa pula a transcrição e o Whisper não carrega nem baixa nada; `--com-voz` põe um tom no vídeo.
Só biblioteca padrão.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PORTA_MIN, PORTA_MAX = 8765, 8775        # as portas que a interface aceita (`ui/__main__.py`)
PREFIXO_TESTE = "smoke-"
PRAZO_HTTP_S = 15.0
PRAZO_SUBIR_S = 40.0
PRAZO_SAIR_S = 60.0                      # o Encerrar espera os gerenciadores (até 20 s) e depois o uvicorn fechar
PRAZO_TRABALHO_S = 240.0
DURACAO_VIDEO_S = 60
VIDEO_DA_FILA = "fila ação.mp4"          # espaço e acento de propósito: o caminho tem de funcionar
VIDEO_DA_RECEITA = "receita d'água.mp4"  # apóstrofo também
FINAIS_DA_FILA = {"pronto", "com_problemas", "erro", "cancelado", "precisa_jogador"}
FINAIS_DA_RECEITA = {"pronta", "erro", "cancelada", "interrompida"}

# O programa de teste usa o que há de mais leve: sem placa (libx264), sem modelo grande, sem música, sem shorts e poucos processos.
CONFIG_DE_TESTE = """[deteccao]
processos = 2

[qualidade]
encoder = "libx264"
renders_paralelos = 1

[transcricao]
modelo = "small"

[montagem]
musica = ""

[shorts]
gerar = false

[fails]
gerar = false
"""


class ErroSmoke(Exception):
    """Um passo falhou; a mensagem já diz o motivo."""


@dataclass
class Resultado:
    ok: bool
    mensagem: str = ""
    pulado: bool = False


# ── funções puras (testadas sem subir nada) ──────────────────────────────────────────────────────────────────────────────────────
def extrair_token(html: str) -> str:
    """O token que a interface põe no HTML de `/`: `<meta name="ui-token" content="...">`. Vazio se não há (ou se ficou o texto-modelo)."""
    achado = re.search(r'<meta\s+name="ui-token"\s+content="([^"]*)"', html)
    token = achado.group(1) if achado else ""
    return "" if token == "__UI_TOKEN__" else token


def escolher_porta(ocupada: Callable[[int], bool] | None = None) -> int:
    """A primeira porta livre entre 8765 e 8775 (a interface não aceita outras)."""
    def tenta(porta: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", porta))
            except OSError:
                return True
        return False
    ocupada = ocupada or tenta
    for porta in range(PORTA_MIN, PORTA_MAX + 1):
        if not ocupada(porta):
            return porta
    raise ErroSmoke(f"Não há porta livre entre {PORTA_MIN} e {PORTA_MAX}. Feche o Bisturi Clips que está aberto e tente de novo.")


def comando_do_video(ffmpeg: str, destino: Path, com_voz: bool) -> list[str]:
    """O comando que gera o vídeo de teste (lista de argumentos, nunca texto de shell). Sem som por padrão: o Whisper nem entra."""
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={DURACAO_VIDEO_S}"]
    if com_voz:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={DURACAO_VIDEO_S}", "-c:a", "aac", "-b:a", "96k"]
    return cmd + ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-t", str(DURACAO_VIDEO_S), str(destino)]


def ambiente_de_teste(teste: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """O ambiente dos filhos: os dados são a pasta de TESTE, sem rede, sem placa, sem usar o cache de modelos do dono."""
    ambiente = dict(os.environ if base is None else base)
    ambiente["BISTURI_DADOS"] = str(teste)
    ambiente["HF_HOME"] = str(teste / "modelos")
    ambiente["HF_HUB_OFFLINE"] = "1"
    ambiente["CUDA_VISIBLE_DEVICES"] = "-1"
    ambiente["PYTHONIOENCODING"] = "utf-8"
    return ambiente


def criar_pasta_de_teste(dados: Path) -> Path:
    """`<dados>/smoke-<id>`: só dentro da pasta de dados informada, nome novo (nunca reaproveita pasta que já existe)."""
    dados = dados.resolve()
    pasta = dados / f"{PREFIXO_TESTE}{uuid.uuid4().hex[:8]}"
    pasta.mkdir()                                   # sem exist_ok: uma pasta que já existia não é nossa
    if pasta.resolve().parent != dados:
        pasta.rmdir()
        raise ErroSmoke("A pasta de teste não ficou dentro da pasta de dados.")
    (pasta / "config.toml").write_text(CONFIG_DE_TESTE, encoding="utf-8")
    return pasta


def apagar_pasta_de_teste(pasta: Path, dados: Path) -> bool:
    """Apaga SÓ a pasta que o próprio smoke criou (filha direta de `dados`, nome `smoke-<id>`, não é link). Devolve se apagou."""
    pasta, dados = Path(pasta), Path(dados).resolve()
    if not pasta.name.startswith(PREFIXO_TESTE) or pasta.is_symlink() or pasta.resolve().parent != dados or not pasta.is_dir():
        return False
    shutil.rmtree(pasta, ignore_errors=True)
    return not pasta.exists()


def descendentes(processos: dict[int, tuple[int, str]], raiz_pid: int) -> dict[int, str]:
    """{pid: nome} de todos os descendentes de `raiz_pid` num retrato `{pid: (pid do pai, nome)}`."""
    achados: dict[int, str] = {}
    pendentes = [raiz_pid]
    while pendentes:
        pai = pendentes.pop()
        for pid, (ppid, nome) in processos.items():
            if ppid == pai and pid not in achados and pid != raiz_pid:
                achados[pid] = nome
                pendentes.append(pid)
    return achados


def sobreviventes(vistos: dict[int, str], agora: dict[int, tuple[int, str]]) -> dict[int, str]:
    """Dos processos vistos, os que ainda estão vivos com o MESMO nome (o Windows reaproveita número de processo)."""
    return {pid: nome for pid, nome in vistos.items() if pid in agora and agora[pid][1].lower() == nome.lower()}


def retrato_de_processos() -> dict[int, tuple[int, str]] | None:
    """{pid: (pid do pai, nome do .exe)} dos processos vivos (Windows, pelo kernel32). Fora do Windows: None (não dá para conferir)."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class ENTRADA(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("modulo", wintypes.DWORD), ("threads", wintypes.DWORD), ("ppid", wintypes.DWORD), ("prioridade", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ENTRADA)]
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ENTRADA)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    foto = k32.CreateToolhelp32Snapshot(0x2, 0)               # TH32CS_SNAPPROCESS
    if foto in (None, ctypes.c_void_p(-1).value):
        return None
    achados: dict[int, tuple[int, str]] = {}
    try:
        e = ENTRADA()
        e.dwSize = ctypes.sizeof(ENTRADA)
        ok = k32.Process32FirstW(foto, ctypes.byref(e))
        while ok:
            achados[int(e.pid)] = (int(e.ppid), str(e.exe))
            ok = k32.Process32NextW(foto, ctypes.byref(e))
    finally:
        k32.CloseHandle(foto)
    return achados


def final_do_log(arquivo: Path, linhas: int = 12) -> str:
    try:
        texto = arquivo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(texto.strip().splitlines()[-linhas:])


# ── o programa e a interface ─────────────────────────────────────────────────────────────────────────────────────────────────────
class Cliente:
    """O que o navegador faz: pedidos HTTP à interface, com o token no cabeçalho `X-Token` (sem proxy do sistema)."""

    def __init__(self, base: str, token: str = "", prazo_s: float = PRAZO_HTTP_S):
        self.base, self.token, self.prazo_s = base.rstrip("/"), token, prazo_s
        self._abrir = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def pedir(self, metodo: str, caminho: str, corpo: Any = None, com_token: bool = True) -> tuple[int, Any]:
        dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
        req = urllib.request.Request(self.base + caminho, data=dados, method=metodo)
        if dados is not None:
            req.add_header("Content-Type", "application/json")
        if com_token and self.token:
            req.add_header("X-Token", self.token)
        try:
            with self._abrir(req, timeout=self.prazo_s) as resposta:
                status, bruto = resposta.status, resposta.read()
        except urllib.error.HTTPError as erro:
            status, bruto = erro.code, erro.read()
        except (urllib.error.URLError, OSError, TimeoutError) as erro:
            raise ErroSmoke(f"A interface não respondeu a {metodo} {caminho} ({type(erro).__name__}).") from erro
        try:
            return status, json.loads(bruto)
        except ValueError:
            return status, bruto.decode("utf-8", errors="replace")


@dataclass
class Contexto:
    raiz: Path                       # a pasta do programa (de onde `python -m cs2clip` e `python -m ui` rodam)
    dados: Path                      # a pasta de dados informada (só serve de lugar para a subpasta de teste)
    python: Path
    sem_processar: bool = False
    com_voz: bool = False
    guardar: bool = False
    prazo_s: float = PRAZO_TRABALHO_S
    prazo_sair_s: float = PRAZO_SAIR_S
    teste: Path | None = None
    ffmpeg: str | None = None
    versao: str = ""
    porta: int = 0
    token: str = ""
    cliente: Any = None
    processo: Any = None
    vistos: dict[int, str] = field(default_factory=dict)
    # dependências trocáveis nos testes
    rodar: Callable[..., Any] = subprocess.run
    subir: Callable[..., Any] | None = None
    novo_cliente: Callable[[str, str], Any] = Cliente
    dormir: Callable[[float], None] = time.sleep
    relogio: Callable[[], float] = time.monotonic
    processos: Callable[[], dict[int, tuple[int, str]] | None] = retrato_de_processos
    saida: Callable[[str], None] = print

    @property
    def ambiente(self) -> dict[str, str]:
        assert self.teste is not None
        return ambiente_de_teste(self.teste)

    def sem_janela(self) -> dict[str, Any]:
        return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

    def esperar(self, pronto: Callable[[], Any], prazo_s: float, intervalo_s: float = 1.0) -> Any:
        """Chama `pronto()` até devolver algo verdadeiro ou o prazo acabar (devolve o último valor)."""
        fim = self.relogio() + prazo_s
        valor = pronto()
        while not valor and self.relogio() < fim:
            self.dormir(intervalo_s)
            valor = pronto()
        return valor

    def pasta_log(self) -> Path:
        assert self.teste is not None
        return self.teste / "logs" / "ui.log"


def rodar_python(ctx: Contexto, argumentos: list[str], prazo_s: float = 120.0, dados: Path | None = None) -> Any:
    """Roda `python <argumentos>` na pasta do programa; `dados` troca a pasta de dados que o filho enxerga (padrão: a de teste)."""
    ambiente = ctx.ambiente
    if dados is not None:
        ambiente["BISTURI_DADOS"] = str(dados)
    try:
        return ctx.rodar([str(ctx.python), *argumentos], cwd=str(ctx.raiz), env=ambiente, shell=False, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=prazo_s, **ctx.sem_janela())
    except subprocess.TimeoutExpired as erro:
        raise ErroSmoke(f"Passou do prazo ({prazo_s:.0f} s) rodando {' '.join(argumentos[:3])}.") from erro
    except OSError as erro:
        raise ErroSmoke(f"Não consegui rodar o Python {ctx.python}: {erro}") from erro


# ── os passos ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def passo_versao(ctx: Contexto) -> Resultado:
    pedido = rodar_python(ctx, ["-m", "cs2clip", "--versao"], 60)
    esperado = rodar_python(ctx, ["-c", "import sys, cs2clip; sys.stdout.write(cs2clip.__version__)"], 60)
    if esperado.returncode != 0 or not str(esperado.stdout).strip():
        raise ErroSmoke(f"Não consegui ler cs2clip.__version__ (código {esperado.returncode}): {str(esperado.stderr).strip()[-300:]}")
    ctx.versao = str(esperado.stdout).strip()
    if pedido.returncode != 0:
        raise ErroSmoke(f"`--versao` saiu com código {pedido.returncode}: {str(pedido.stderr).strip()[-300:]}")
    if str(pedido.stdout).strip().split()[-1:] != [ctx.versao]:
        raise ErroSmoke(f"`--versao` disse {str(pedido.stdout).strip()!r}, mas cs2clip.__version__ é {ctx.versao!r}.")
    return Resultado(True, f"versão {ctx.versao}")


def passo_checar(ctx: Contexto) -> Resultado:
    tem_modulo = (ctx.raiz / "cs2clip" / "instalacao.py").is_file()
    argumentos = ["-m", "cs2clip.instalacao", "checar"] if tem_modulo else ["-m", "cs2clip", "--checar"]
    assert ctx.teste is not None
    if not tem_modulo:
        argumentos += ["--saida", str(ctx.teste / "saida")]       # a checagem antiga olha a pasta de saída: a de teste, nunca a do dono
    r = rodar_python(ctx, argumentos, 180, dados=ctx.dados if tem_modulo else None)
    nome = "cs2clip.instalacao checar" if tem_modulo else "cs2clip --checar (cs2clip.instalacao ainda não existe aqui)"
    if r.returncode != 0:
        ultimas = "\n".join(str(r.stdout or r.stderr).strip().splitlines()[-6:])
        raise ErroSmoke(f"{nome} saiu com código {r.returncode}:\n{ultimas}")
    return Resultado(True, f"{nome} saiu 0")


def _subir_interface(ctx: Contexto, porta: int) -> Any:
    """Abre `python -m ui --sem-navegador --port <porta>` como o atalho do programa: com o pythonw.exe ao lado e SEM nenhuma saída padrão
    (sys.stdout e sys.stderr ficam None; com um DEVNULL o Python criaria os dois e o cenário do atalho não seria testado)."""
    exe = ctx.python
    pythonw = exe.with_name("pythonw.exe")
    extras: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32" and pythonw.is_file():
        exe = pythonw
        extras = {"stdin": None, "stdout": None, "stderr": None, "startupinfo": subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESTDHANDLES)}
    return subprocess.Popen([str(exe), "-m", "ui", "--sem-navegador", "--port", str(porta)], cwd=str(ctx.raiz), env=ctx.ambiente, shell=False,
                            **extras, **ctx.sem_janela())


def passo_interface(ctx: Contexto) -> Resultado:
    ctx.porta = escolher_porta()
    ctx.processo = (ctx.subir or _subir_interface)(ctx, ctx.porta)
    base = f"http://127.0.0.1:{ctx.porta}"
    livre = ctx.novo_cliente(base, "")

    def respondeu() -> bool:
        if ctx.processo.poll() is not None:
            raise ErroSmoke(f"A interface fechou sozinha (código {ctx.processo.poll()}). Final do ui.log:\n{final_do_log(ctx.pasta_log())}")
        try:
            status, corpo = livre.pedir("GET", "/health", com_token=False)
        except ErroSmoke:
            return False
        return status == 200 and isinstance(corpo, dict) and corpo.get("ok") is True

    if not ctx.esperar(respondeu, PRAZO_SUBIR_S, 0.5):
        raise ErroSmoke(f"A interface não respondeu em /health em {PRAZO_SUBIR_S:.0f} s. Final do ui.log:\n{final_do_log(ctx.pasta_log())}")
    status, html = livre.pedir("GET", "/", com_token=False)
    ctx.token = extrair_token(html) if isinstance(html, str) else ""
    if status != 200 or not ctx.token:
        raise ErroSmoke(f"Não achei o token da interface no HTML de / (status {status}).")
    ctx.cliente = ctx.novo_cliente(base, ctx.token)
    status, corpo = ctx.cliente.pedir("GET", "/api/versao")
    if status != 200 or not isinstance(corpo, dict) or corpo.get("versao") != ctx.versao:
        raise ErroSmoke(f"/api/versao com o token deu {status} {corpo!r}; esperava a versão {ctx.versao!r}.")
    status, _ = livre.pedir("GET", "/api/versao", com_token=False)
    if status != 403:
        raise ErroSmoke(f"/api/versao SEM o token deu {status}; esperava 403.")
    status, _ = ctx.novo_cliente(base, "token-errado").pedir("GET", "/api/versao")
    if status != 403:
        raise ErroSmoke(f"/api/versao com um token errado deu {status}; esperava 403.")
    sem_console = "; sem console (ui.log criado)" if ctx.pasta_log().is_file() else ""
    return Resultado(True, f"porta {ctx.porta}: /health livre, /api/versao {ctx.versao} com o token, 403 sem token e com token errado{sem_console}")


def observar_filhos(ctx: Contexto) -> None:
    """Anota os processos descendentes da interface (o que ela abriu: núcleo, ffmpeg...), para conferir no fim que nenhum sobrou."""
    retrato = ctx.processos()
    if retrato is not None and ctx.processo is not None:
        ctx.vistos.update(descendentes(retrato, int(ctx.processo.pid)))


def _gerar_videos(ctx: Contexto) -> tuple[Path, Path]:
    assert ctx.teste is not None
    ffmpeg = ctx.ffmpeg
    if not ffmpeg:
        raise ErroSmoke("Não achei o ffmpeg (nem em <programa>\\bin nem no PATH).")
    entrada = ctx.teste / "entrada"
    entrada.mkdir(exist_ok=True)
    primeiro, segundo = entrada / VIDEO_DA_FILA, entrada / VIDEO_DA_RECEITA
    r = ctx.rodar(comando_do_video(ffmpeg, primeiro, ctx.com_voz), shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
                  timeout=180, **ctx.sem_janela())
    if r.returncode != 0 or not primeiro.is_file():
        raise ErroSmoke(f"O ffmpeg não gerou o vídeo de teste (código {r.returncode}): {str(r.stderr).strip()[-300:]}")
    shutil.copyfile(primeiro, segundo)
    return primeiro, segundo


def _conferir_kit(ctx: Contexto, nome_do_video: str) -> str:
    """momentos.json (e qualidade.json, se o programa o gerou) da pasta de saída do vídeo `nome_do_video`."""
    assert ctx.teste is not None and ctx.cliente is not None
    status, indice = ctx.cliente.pedir("GET", "/api/indice")
    itens = indice.get("itens", []) if status == 200 and isinstance(indice, dict) else []
    item = next((i for i in itens if i.get("arquivo") == nome_do_video), None)
    if item is None or not isinstance(item.get("id"), str):
        raise ErroSmoke(f"{nome_do_video} não apareceu no índice da Biblioteca depois de processar.")
    pasta = (ctx.teste / "saida" / item["id"]).resolve()
    if (ctx.teste / "saida").resolve() not in pasta.parents:
        raise ErroSmoke(f"A pasta de saída de {nome_do_video} ficou fora da pasta de teste.")
    try:
        momentos = json.loads((pasta / "momentos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as erro:
        raise ErroSmoke(f"{nome_do_video}: momentos.json não existe ou não é JSON ({type(erro).__name__}).") from erro
    if not isinstance(momentos, dict) or not isinstance(momentos.get("momentos"), list):
        raise ErroSmoke(f"{nome_do_video}: momentos.json sem a lista `momentos`.")
    qualidade = pasta / "qualidade.json"
    if not qualidade.is_file():
        # O vídeo de teste não tem nenhum abate: não há arquivo final para conferir e o programa não grava o qualidade.json.
        return f"{len(momentos['momentos'])} momento(s); qualidade.json não se aplica (sem arquivo final)"
    try:
        dados = json.loads(qualidade.read_text(encoding="utf-8"))
    except (OSError, ValueError) as erro:
        raise ErroSmoke(f"{nome_do_video}: qualidade.json não é JSON ({type(erro).__name__}).") from erro
    if not isinstance(dados, dict):
        raise ErroSmoke(f"{nome_do_video}: qualidade.json tem formato inesperado.")
    return f"{len(momentos['momentos'])} momento(s); qualidade.json ok"


def _item_da_fila(ctx: Contexto, nome: str) -> dict[str, Any] | None:
    status, fila = ctx.cliente.pedir("GET", "/api/fila")
    if status != 200 or not isinstance(fila, dict):
        raise ErroSmoke(f"/api/fila deu {status}.")
    return next((j for j in reversed(fila.get("itens", [])) if j.get("nome") == nome), None)


def _viva(ctx: Contexto) -> None:
    if ctx.processo is not None and ctx.processo.poll() is not None:
        raise ErroSmoke(f"A interface fechou no meio do trabalho (código {ctx.processo.poll()}). Final do ui.log:\n{final_do_log(ctx.pasta_log())}")


def _esperar_item_da_fila(ctx: Contexto, nome: str) -> dict[str, Any]:
    def terminou() -> dict[str, Any] | None:
        _viva(ctx)
        observar_filhos(ctx)
        item = _item_da_fila(ctx, nome)
        return item if item is not None and item.get("estado") in FINAIS_DA_FILA else None
    item = ctx.esperar(terminou, ctx.prazo_s)
    if not item:
        raise ErroSmoke(f"{nome}: a análise não terminou em {ctx.prazo_s:.0f} s.")
    if item.get("estado") != "pronto" or item.get("codigo_saida") != 0:
        raise ErroSmoke(f"{nome}: estado {item.get('estado')!r}, código de saída {item.get('codigo_saida')!r}. "
                        f"{item.get('mensagem', '')} Final da saída: {' | '.join(item.get('detalhes', [])[-5:])}")
    return item


def passo_fila(ctx: Contexto) -> Resultado:
    video_fila, _ = _gerar_videos(ctx)
    status, entrada = ctx.cliente.pedir("GET", "/api/entrada")
    nomes = {i.get("nome") for i in entrada.get("itens", [])} if status == 200 and isinstance(entrada, dict) else set()
    if video_fila.name not in nomes:
        raise ErroSmoke(f"A pasta entrada da interface não lista {video_fila.name}.")
    opcoes = {"gerar_cortes": True, "gerar_shorts": True, "gerar_montagem": True}
    status, corpo = ctx.cliente.pedir("POST", "/api/fila", {"arquivos": [f"entrada:{video_fila.name}"], "opcoes": opcoes})
    if status != 200:
        raise ErroSmoke(f"POST /api/fila deu {status}: {corpo!r}")
    _esperar_item_da_fila(ctx, video_fila.name)
    return Resultado(True, f"fila: {video_fila.name} saiu com código 0; {_conferir_kit(ctx, video_fila.name)}")


def passo_receita(ctx: Contexto) -> Resultado:
    assert ctx.teste is not None
    video_receita = ctx.teste / "entrada" / VIDEO_DA_RECEITA          # criado em `passo_fila`
    if not video_receita.is_file():
        raise ErroSmoke("O vídeo da receita não existe (o passo da fila não rodou?).")
    corpo_pedido = {"texto": str(video_receita), "sem_demo": True, "limpo": True}
    status, corpo = ctx.cliente.pedir("POST", "/api/trabalhos", corpo_pedido)
    if status != 200 or not isinstance(corpo, dict) or not corpo.get("id"):
        raise ErroSmoke(f"POST /api/trabalhos deu {status}: {corpo!r}")
    rid = str(corpo["id"])

    def terminou() -> dict[str, Any] | None:
        _viva(ctx)
        observar_filhos(ctx)
        st, r = ctx.cliente.pedir("GET", f"/api/trabalhos/{rid}")
        if st != 200 or not isinstance(r, dict):
            raise ErroSmoke(f"GET /api/trabalhos/{rid} deu {st}.")
        return r if r.get("estado") in FINAIS_DA_RECEITA else None

    receita = ctx.esperar(terminou, ctx.prazo_s)
    if not receita:
        raise ErroSmoke(f"A receita não terminou em {ctx.prazo_s:.0f} s.")
    if receita.get("estado") != "pronta":
        raise ErroSmoke(f"A receita terminou como {receita.get('estado')!r}: {receita.get('erro', '')} {' | '.join(receita.get('linhas', [])[-4:])}")
    item = _item_da_fila(ctx, video_receita.name)
    if item is None or item.get("estado") != "pronto" or item.get("codigo_saida") != 0:
        raise ErroSmoke(f"A análise da receita ({video_receita.name}) não saiu com código 0: {item!r}")
    return Resultado(True, f"receita: {video_receita.name} pronta, análise com código 0; {_conferir_kit(ctx, video_receita.name)}")


def passo_processar(ctx: Contexto) -> Resultado:
    if ctx.sem_processar:
        return Resultado(True, "pulado (--sem-processar)", pulado=True)
    if ctx.cliente is None:
        raise ErroSmoke("a interface não subiu (passo 3).")
    fila = passo_fila(ctx)
    receita = passo_receita(ctx)
    return Resultado(True, f"{fila.mensagem}; {receita.mensagem}")


def _derrubar(ctx: Contexto, pids: list[int]) -> None:
    """Último recurso (limpeza): derruba só os processos que ESTE smoke abriu, pelo número deles, em lista de argumentos."""
    for pid in pids:
        if sys.platform == "win32":
            ctx.rodar(["taskkill", "/PID", str(int(pid)), "/T", "/F"], shell=False, capture_output=True, timeout=20, **ctx.sem_janela())
        else:
            try:
                os.kill(int(pid), 9)
            except OSError:
                pass


def passo_encerrar(ctx: Contexto) -> Resultado:
    if ctx.processo is None:
        raise ErroSmoke("a interface não subiu (passo 3).")
    observar_filhos(ctx)
    pid = int(ctx.processo.pid)
    try:
        if ctx.cliente is None:
            raise ErroSmoke("sem cliente HTTP: não deu para pedir o Encerrar.")
        status, corpo = ctx.cliente.pedir("POST", "/api/encerrar", {})
        if status != 200 or not (isinstance(corpo, dict) and corpo.get("sucesso")):
            raise ErroSmoke(f"POST /api/encerrar deu {status}: {corpo!r}")
        if not ctx.esperar(lambda: ctx.processo.poll() is not None, ctx.prazo_sair_s, 0.5):
            raise ErroSmoke(f"A interface continuou rodando {ctx.prazo_sair_s:.0f} s depois do Encerrar (pedido aceito, processo vivo).")
        codigo = ctx.processo.poll()
        if codigo != 0:
            raise ErroSmoke(f"A interface saiu com código {codigo} (esperava 0).")
    except ErroSmoke:
        if ctx.processo.poll() is None:
            _derrubar(ctx, [pid])                      # limpeza: nunca deixa a interface de teste viva
        raise
    if ctx.processos() is None:
        return Resultado(True, "a interface saiu com código 0 (filhos: conferência só existe no Windows)")
    vivos = ctx.esperar(lambda: not sobreviventes(ctx.vistos, ctx.processos() or {}), 5.0, 0.25)
    sobrou = sobreviventes(ctx.vistos, ctx.processos() or {})
    if not vivos and sobrou:
        _derrubar(ctx, list(sobrou))
        raise ErroSmoke(f"Ficaram processos vivos depois do Encerrar: {sobrou}.")
    return Resultado(True, f"a interface saiu com código 0 e nenhum dos {len(ctx.vistos)} processo(s) filho(s) ficou vivo")


PASSOS: list[tuple[str, Callable[[Contexto], Resultado]]] = [
    ("versão", passo_versao),
    ("checagem", passo_checar),
    ("interface", passo_interface),
    ("processar (fila e receita)", passo_processar),
    ("encerrar", passo_encerrar),
]


def executar(ctx: Contexto, passos: list[tuple[str, Callable[[Contexto], Resultado]]] | None = None) -> int:
    """Roda os passos na ordem, imprime OK/FALHA de cada um e devolve 0 (tudo certo) ou 1. O Encerrar roda sempre, para a interface não ficar viva."""
    passos = PASSOS if passos is None else passos
    falhou = False
    for n, (nome, funcao) in enumerate(passos, 1):
        rotulo = f"[{n}/{len(passos)}] {nome}"
        if falhou and funcao is not passo_encerrar:
            ctx.saida(f"PULADO {rotulo}: um passo anterior falhou.")
            continue
        try:
            r = funcao(ctx)
            ctx.saida(f"{'PULADO' if r.pulado else 'OK'} {rotulo}: {r.mensagem}" if r.ok else f"FALHA {rotulo}: {r.mensagem}")
            falhou = falhou or not r.ok
        except ErroSmoke as erro:
            ctx.saida(f"FALHA {rotulo}: {erro}")
            falhou = True
        except Exception as erro:  # noqa: BLE001  (qualquer erro inesperado vira FALHA com o tipo dele, nunca um traceback solto)
            ctx.saida(f"FALHA {rotulo}: erro inesperado ({type(erro).__name__}): {erro}")
            falhou = True
    ctx.saida("SMOKE: " + ("FALHOU" if falhou else "tudo certo"))
    return 1 if falhou else 0


# ── linha de comando ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def achar_ffmpeg(raiz: Path) -> str | None:
    """O ffmpeg da pasta `bin` do programa, se existir; senão o do PATH."""
    do_programa = raiz / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    return str(do_programa) if do_programa.is_file() else shutil.which("ffmpeg")


def ler_argumentos(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="smoke", description="Teste de fumaça do Bisturi Clips (roda com o Python do programa).")
    ap.add_argument("--raiz", required=True, type=Path, help="a pasta do programa (a que tem as pastas cs2clip e ui)")
    ap.add_argument("--dados", required=True, type=Path, help="a pasta de dados; o smoke cria nela uma subpasta de teste e a apaga no fim")
    ap.add_argument("--python", type=Path, default=Path(sys.executable), help="o Python do programa (padrão: o que roda este script)")
    ap.add_argument("--sem-processar", action="store_true", help="pula o passo 4 (vídeo, fila e receita)")
    voz = ap.add_mutually_exclusive_group()
    voz.add_argument("--sem-voz", dest="com_voz", action="store_false", help="vídeo sem som: a transcrição é pulada (padrão)")
    voz.add_argument("--com-voz", dest="com_voz", action="store_true", help="vídeo com um tom de áudio (o Whisper pode rodar)")
    ap.add_argument("--prazo", type=float, default=PRAZO_TRABALHO_S, help="segundos que cada análise pode levar (padrão %(default)s)")
    ap.add_argument("--prazo-encerrar", type=float, default=PRAZO_SAIR_S, help="segundos que a interface pode levar para sair depois do Encerrar (padrão %(default)s)")
    ap.add_argument("--guardar", action="store_true", help="não apaga a pasta de teste no fim (para investigar uma falha)")
    ap.set_defaults(com_voz=False)
    return ap.parse_args(argv)


def principal(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")      # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    a = ler_argumentos(argv)
    raiz, dados, python = a.raiz.resolve(), a.dados.resolve(), a.python.resolve()
    erros = []
    if not (raiz / "cs2clip" / "__init__.py").is_file() or not (raiz / "ui" / "__main__.py").is_file():
        erros.append(f"--raiz {raiz} não é a pasta do programa (faltam cs2clip\\ e ui\\).")
    if not dados.is_dir():
        erros.append(f"--dados {dados} não é uma pasta que existe.")
    if not python.is_file():
        erros.append(f"--python {python} não existe.")
    if a.prazo <= 0 or a.prazo_encerrar <= 0:
        erros.append("--prazo e --prazo-encerrar precisam ser maiores que zero.")
    if erros:
        print("\n".join(erros), file=sys.stderr)
        return 2
    ctx = Contexto(raiz=raiz, dados=dados, python=python, sem_processar=a.sem_processar, com_voz=a.com_voz, guardar=a.guardar, prazo_s=a.prazo,
                   prazo_sair_s=a.prazo_encerrar, ffmpeg=achar_ffmpeg(raiz))
    try:
        ctx.teste = criar_pasta_de_teste(dados)
    except (OSError, ErroSmoke) as erro:
        print(f"Não consegui criar a pasta de teste em {dados}: {erro}", file=sys.stderr)
        return 2
    print(f"Pasta de teste: {ctx.teste}")
    try:
        return executar(ctx)
    finally:
        if ctx.processo is not None and ctx.processo.poll() is None:
            _derrubar(ctx, [int(ctx.processo.pid)])
        if not a.guardar:
            apagar_pasta_de_teste(ctx.teste, dados)


if __name__ == "__main__":
    raise SystemExit(principal())
