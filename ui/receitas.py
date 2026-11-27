"""E2 (Plano E): a RECEITA — do que o dono colou até o vídeo pronto, em etapas, sem cliques no meio.

Cada receita é uma lista de etapas (baixar a demo, esperar a troca de conta, gravar, montar…) com o estado salvo em `trabalhos/<id>/receita.json`
(se a tela fechar ou a luz cair, dá para retomar). As etapas pesadas rodam como PROCESSO À PARTE (os mesmos `python -m cs2clip.…` de sempre);
o cancelar manda CTRL+BREAK (o gravador fecha o jogo e confere os arquivos antes de sair). Só UMA gravação por vez (o CS2 é um só).
O Bisturi nunca troca de conta: a etapa `esperar_conta` só olha o Steam e espera o dono trocar.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from collections.abc import Callable

from cs2clip import entrada as ent
from cs2clip import steam_local

from .seguranca import ambiente_dos_filhos, python_de_console, validar_caminho_sem_unc
from cs2clip.tools import cancelar_processo, grupo_novo, sem_janela, substituir_arquivo

VIVOS = {"na_fila", "rodando", "esperando_voce"}
FINAIS = {"pronta", "erro", "cancelada", "interrompida"}
# N3 (15/11/2026): a edição reescreve o kit no lugar; parada no meio, ele mistura arquivos novos e antigos. A tela mostra este aviso no cartão do trabalho.
AVISO_KIT_PELA_METADE = ("Atenção: a edição parou no meio e o kit pode ter ficado pela metade (parte dos arquivos da edição nova, parte da antiga). Não use os vídeos "
                         "dele ainda: clique em Retomar para terminar a edição.")


def _editando(r: dict[str, Any]) -> bool:
    """A etapa de edição estava mexendo no kit quando o trabalho parou?"""
    return any(et.get("nome") == "editar" and et.get("estado") == "rodando" for et in r.get("etapas", []))


def _marcar_interrompida(r: dict[str, Any]) -> None:
    """O programa fechou com o trabalho vivo (ou a luz caiu): ele fica `interrompida` e o Retomar continua da etapa que parou."""
    r["estado"] = "interrompida"
    if _editando(r):
        r["erro"] = AVISO_KIT_PELA_METADE
    for et in r.get("etapas", []):
        if et.get("estado") in ("rodando", "esperando_voce"):
            et["estado"] = "pendente"
            et["detalhe"] = "interrompida (o programa foi fechado); clique em Retomar"

LINHAS_MAX = 80
PADRAO_ROUNDS = re.compile(r"^\d{1,3}(,\d{1,3}){0,40}$")
MAX_TEXTO = 4096          # caracteres do que o dono cola (link, código ou caminho): o maior caminho do Windows de uso normal tem 260; um link do YouTube, ~100
LIMITE_VIVAS = 20         # trabalhos ao mesmo tempo (na fila, rodando ou esperando você); o uso real é 1 a 3

TITULOS = {
    "obter_demo": "Baixar a demo da Valve",
    "obter_video": "Baixar o VOD da Twitch",
    "aguardar_download": "Baixar o vídeo no YouTube Studio (você faz este passo)",
    "esperar_conta": "Trocar o Steam para a conta do Bisturi (você faz este passo)",
    "gravar": "Gravar os rounds pelo CS2",
    "editar": "Montar cortes, shorts, montagem e capa",
    "analisar": "Analisar o vídeo e montar",
    "parear": "Achar a demo desta partida e, se achar, regravar em imagem limpa",
}
LIMITE_PARES = 3          # uma live longa pode ter várias partidas; regrava no máximo estas
# Tempo médio medido em 08/11/2026 (1,7x o tempo real para gravar; edição ~2 min); só para o "≈" da tela
ESTIMATIVA_FIXA_S = {"obter_demo": 90, "obter_video": 600, "esperar_conta": 30, "editar": 150, "analisar": 600, "aguardar_download": 120}


class ErroEtapa(Exception):
    """A etapa parou; a mensagem já diz ao dono o que fazer."""


class Cancelada(Exception):
    pass


def _etapa_nova(nome: str, par: int | None = None, total: int = 1) -> dict[str, Any]:
    titulo = TITULOS[nome] + (f" (partida {par + 1} de {total})" if par is not None and total > 1 else "")
    et = {"nome": nome, "titulo": titulo, "estado": "pendente", "detalhe": "", "inicio": None, "fim": None, "estimativa_s": ESTIMATIVA_FIXA_S.get(nome)}
    if par is not None:
        et["par"] = par
    return et


def montar_etapas(e: ent.Entrada, opcoes: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """As etapas da receita para esse tipo de entrada. Vídeo da live (arquivo, Twitch, YouTube) ganha o passo `parear` no fim: se a análise
    confirmar uma demo da partida, ele acrescenta as etapas de regravar em imagem limpa e montar com a voz da live."""
    opcoes = opcoes or {}
    nomes: list[str]
    if e.tipo == "demo":
        nomes = ["esperar_conta", "gravar", "editar"]
    elif e.tipo == "codigo_partilha":
        nomes = ["esperar_conta", "obter_demo", "gravar", "editar"]     # A1: o boiler só roda na conta do Bisturi, então a troca vem ANTES de baixar
    elif e.tipo == "twitch_vod":
        nomes = ["obter_video", "analisar"]
    elif e.tipo == "youtube":
        nomes = ["aguardar_download", "analisar"]
    elif e.tipo in ("video", "pasta"):
        nomes = ["analisar"]
    else:
        raise ValueError(e.mensagem or "Não sei o que fazer com isso.")
    if e.tipo in ("video", "twitch_vod", "youtube") and not opcoes.get("sem_demo") and opcoes.get("limpo", True):
        nomes.append("parear")
    return [_etapa_nova(n) for n in nomes]


from cs2clip.mapas import NOMES_DE_MAPAS, nome_do_mapa  # noqa: E402,F401  (movidos para cs2clip/mapas.py; continuam aqui por compatibilidade)


def _mapa_da_demo(caminho: Path) -> str:
    """O mapa lido só do CABEÇALHO da demo (milissegundos, não lê a partida). Qualquer falha do leitor (inclusive o pânico do Rust) = vazio."""
    try:
        from cs2clip.demo import _abrir
        return str(_abrir(caminho).parse_header().get("map_name", "") or "")
    except BaseException as e:  # noqa: BLE001
        from cs2clip.demo import e_falha_do_parser
        if not e_falha_do_parser(e):
            raise
        return ""


def _data_da_demo(caminho: Path):
    """A data da partida (do `.dem.info` do lado) ou, sem ele, a data do arquivo. None se nem isso der."""
    from cs2clip import mapas
    return mapas.data_da_demo(caminho)


def titulo_da_demo(caminho: str | Path, placar: tuple[int, int] | None = None) -> str:
    """"Mirage 11x13 · 08/11" (mapa, placar do dono, dia). Sem o placar: "Mirage · 08/11". Nunca devolve vazio: no pior caso é o nome do arquivo."""
    from cs2clip import mapas
    caminho = Path(caminho)
    return mapas.titulo_da_partida(_mapa_da_demo(caminho), placar, _data_da_demo(caminho), sem_nada=caminho.name)


def placar_da_demo(caminho: str | Path, cfg: Any) -> tuple[int, int] | None:
    """O placar do dono (rounds dele x rounds do outro time). Lê a demo inteira (1–6 s): só chamar fora do clique do dono. Falha = None."""
    try:
        from cs2clip.demo import ler_demo
        partida = ler_demo(Path(caminho), cfg.gravacao_demo.conta_principal_id or cfg.demo.jogador or None)
        return (partida.placar[0], partida.placar[1]) if partida.jogador is not None else None
    except BaseException as e:  # noqa: BLE001
        from cs2clip.demo import e_falha_do_parser
        if not e_falha_do_parser(e):
            raise
        return None


def estimar_demo(caminho: str | Path, cfg: Any) -> dict[str, Any]:
    """Lê a demo (1–6 s) e diz quais rounds seriam gravados e quanto tempo leva. Falha de leitura = {}: a estimativa é opcional."""
    try:
        from cs2clip.demo import ler_demo, rounds_para_gravar
        g = cfg.gravacao_demo
        partida = ler_demo(Path(caminho), g.conta_principal_id or cfg.demo.jogador or None)
        if partida.jogador is None:
            return {}
        bons = rounds_para_gravar(partida, True, cfg.cortes.min_abates_round, g.margem_antes_s, g.margem_depois_s)
        segundos = sum(t.duracao_s() for t in bons)
        return {"partida": partida.titulo, "jogador": partida.nome, "rounds": [t.round for t in bons], "minutos_de_jogo": round(segundos / 60, 1),
                "gravar_s": int(segundos / 1.7 + 60 * (1 if bons else 0))}
    except BaseException as e:  # noqa: BLE001  # a estimativa é opcional: qualquer falha do parser (inclusive o pânico do Rust) = sem estimativa
        from cs2clip.demo import e_falha_do_parser
        if not e_falha_do_parser(e):
            raise
        return {}


class GerenciadorReceitas:
    def __init__(self, raiz: Path, popen_factory: Callable[..., Any] | None = None, python: str | None = None,
                 conta_logada: Callable[[], int] | None = None, config_fn: Callable[[], Any] | None = None,
                 fila: Any = None, vods: Any = None, pasta_downloads: Path | None = None, poll_s: float = 2.0,
                 espera_conta_max_s: float = 900.0, procurar_em: Callable[[], list[Path]] | None = None,
                 autorizado: Callable[[str], bool] | None = None, trava_cs2: Any = None, programa: Path | None = None):
        # P2 (duas bases): `raiz` = a pasta de DADOS (trabalhos/, config.toml, entrada/, saida/, demos/, gravacao_demo/); `programa` = de onde os
        # `python -m cs2clip.…` rodam. Sem `programa`, as duas são a mesma pasta (modo portátil e os testes).
        self.raiz = Path(raiz)
        self.programa = Path(programa) if programa is not None else self.raiz
        self.pasta = self.raiz / "trabalhos"
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._popen = popen_factory or subprocess.Popen
        self._python = python or str(python_de_console())     # NUNCA o pythonw.exe do app: o filho sem console abre uma janela preta a cada ffmpeg (N3)
        self._conta_logada = conta_logada or steam_local.conta_logada
        self._config_fn = config_fn or self._config_padrao
        self.fila, self.vods = fila, vods
        self._downloads = Path(pasta_downloads) if pasta_downloads else Path.home() / "Downloads"
        self.poll_s = poll_s
        self.espera_conta_max_s = espera_conta_max_s
        self._procurar_em = procurar_em or (lambda: [self.raiz / "demos"])
        self._autorizado = autorizado
        self._lock = threading.RLock()
        self._receitas: dict[str, dict[str, Any]] = {}
        self._linhas: dict[str, list[str]] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._procs: dict[str, Any] = {}
        self._trava_cs2 = trava_cs2 if trava_cs2 is not None else threading.Lock()   # G-003: o servidor entrega a MESMA trava da tela "Gravar pela demo"
        self._dono_cs2: str | None = None
        self._esperando_trava: set[str] = set()          # receitas paradas em `_pegar_cs2`, esperando outra soltar o CS2
        self._threads: dict[str, threading.Thread] = {}                  # a execução mais recente de cada trabalho (ver `retomar`)
        self._interrompidas: set[str] = set()                            # paradas pelo fim do programa (viram `interrompida`, não `cancelada`)
        self.espera_fim_s = 5.0                                          # quanto o Retomar espera a execução anterior terminar de vez
        self._carregar_do_disco()

    # ── disco ────────────────────────────────────────────────────────────────────────────────────
    def _config_padrao(self):
        from cs2clip.config import load_config
        return load_config(self.raiz / "config.toml")

    def _arquivo(self, rid: str) -> Path:
        return self.pasta / rid / "receita.json"

    def _salvar(self, r: dict[str, Any]) -> None:
        """Grava o estado em disco. No Windows a troca do arquivo pode ser negada por um instante (antivírus, indexador): `substituir_arquivo` tenta de novo
        algumas vezes e, se não der, o trabalho CONTINUA (o estado fica certo na memória e é regravado no próximo passo) em vez de virar erro por causa de um arquivo de estado.
        G-004 (revisão de 26/11): o retrato é tirado e gravado SOB a trava (nenhuma thread muda a receita no meio do `json.dumps`, e um retrato velho
        nunca passa por cima de um novo); o temporário tem nome único e some se a troca falhar."""
        arq = self._arquivo(r["id"])
        with self._lock:
            tmp = arq.with_name(f"{arq.name}.{uuid.uuid4().hex[:8]}.tmp")
            try:
                arq.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
                substituir_arquivo(tmp, arq)
                r.pop("disco_falhou", None)
            except OSError:
                r["disco_falhou"] = True
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    def _carregar_do_disco(self) -> None:
        """Receitas de antes: as que estavam vivas viram `interrompida` (o programa fechou no meio) e podem ser retomadas."""
        def _quando(a: Path) -> float:
            try:
                return a.stat().st_mtime
            except OSError:
                return 0.0
        for arq in sorted(self.pasta.glob("*/receita.json"), key=_quando)[-60:]:
            try:
                r = json.loads(arq.read_text(encoding="utf-8"))
                rid = str(r["id"])
            except (OSError, ValueError, KeyError):
                continue
            if r.get("estado") in VIVOS:
                _marcar_interrompida(r)
                self._receitas[rid] = r
                self._salvar(r)
            else:
                self._receitas[rid] = r

    # ── API ──────────────────────────────────────────────────────────────────────────────────────
    def reconhecer(self, texto: str) -> ent.Entrada:
        if len(texto or "") > MAX_TEXTO:
            return ent.Entrada("recusado", "", f"O texto é grande demais (o limite é {MAX_TEXTO} caracteres). Cole só o link, o código ou o caminho do arquivo.")
        return ent.reconhecer(texto, self._procurar_em())

    def ultima_demo(self) -> dict[str, Any] | None:
        """A demo mais nova das pastas conhecidas (replays do CS2 e `demos/`), para o botão "Última partida do CS2"."""
        from cs2clip import demos_cs2
        melhores = []
        for pasta in self._procurar_em():
            try:
                melhores += [d for d in demos_cs2.listar_demos(Path(pasta)) if ent.NOME_DEMO.match(d.caminho.name)]
            except OSError:
                continue
        if not melhores:
            return None
        d = max(melhores, key=lambda x: x.quando)
        return {"caminho": str(d.caminho), "nome": d.caminho.name, "data": d.quando.strftime("%d/%m/%Y %H:%M"), "aproximada": d.aproximada}

    # ── E2.4: partida nova na pasta do CS2 (só avisa; o dono decide) ──────────────────────────────
    def _arquivo_visto(self) -> Path:
        return self.pasta / "visto.json"

    def _dispensadas(self) -> set[str]:
        try:
            return {str(x) for x in json.loads(self._arquivo_visto().read_text(encoding="utf-8")).get("dispensadas", [])}
        except (OSError, ValueError, AttributeError):
            return set()

    def dispensar(self, nome: str) -> None:
        if not ent.NOME_DEMO.match(str(nome)):
            raise ValueError("Nome de demo inválido.")
        with self._lock:
            ja = sorted(self._dispensadas() | {str(nome)})[-200:]
            tmp = self._arquivo_visto().with_name("visto.json.tmp")
            tmp.write_text(json.dumps({"dispensadas": ja}, ensure_ascii=False), encoding="utf-8")
            substituir_arquivo(tmp, self._arquivo_visto())

    def partidas_novas(self, dias: int = 7, limite: int = 3, agora: float | None = None) -> list[dict[str, Any]]:
        """Demos dos últimos `dias` dias que ainda não viraram trabalho e que o dono não dispensou, da mais nova para a mais velha."""
        import datetime
        from cs2clip import demos_cs2
        corte = datetime.datetime.fromtimestamp((agora if agora is not None else time.time()) - dias * 86400)
        usadas = set()
        with self._lock:
            for r in self._receitas.values():
                if r["estado"] != "erro" or any(e["estado"] == "ok" for e in r["etapas"]):
                    for v in (r["entrada"].get("valor", ""), r["dados"].get("demo", "")):
                        if v:
                            usadas.add(Path(v).name)
        fora = self._dispensadas() | usadas
        achadas = []
        for pasta in self._procurar_em():
            try:
                achadas += [d for d in demos_cs2.listar_demos(Path(pasta)) if ent.NOME_DEMO.match(d.caminho.name)]
            except OSError:
                continue
        vistos, saida = set(), []
        for d in sorted(achadas, key=lambda x: x.quando, reverse=True):
            if d.caminho.name in vistos or d.caminho.name in fora or d.quando < corte:
                continue
            vistos.add(d.caminho.name)
            saida.append({"caminho": str(d.caminho), "nome": d.caminho.name, "data": d.quando.strftime("%d/%m/%Y %H:%M"),
                          "mb": round(d.caminho.stat().st_size / 1e6, 1)})
            if len(saida) >= limite:
                break
        return saida

    MENSAGEM_SELETOR = ("Para usar um arquivo do seu computador, escolha pelo botão 'Escolher arquivo' (ou use 'Última partida do CS2'). "
                        "Por segurança, a interface não abre qualquer caminho digitado.")

    def _local_nao_autorizado(self, e: ent.Entrada) -> bool:
        return bool(self._autorizado) and e.tipo in ("demo", "video", "pasta") and not self._autorizado(e.valor)

    def plano(self, texto: str, opcoes: dict[str, Any] | None = None) -> dict[str, Any]:
        e = self.reconhecer(texto)
        saida: dict[str, Any] = {"entrada": {"tipo": e.tipo, "valor": e.valor, "mensagem": e.mensagem, "detalhes": e.detalhes, "aceita": e.aceita}}
        if self._local_nao_autorizado(e):
            saida["entrada"].update(aceita=False, mensagem=self.MENSAGEM_SELETOR)
            return saida
        if e.aceita:
            etapas = montar_etapas(e, opcoes)
            if e.tipo == "demo":
                est = estimar_demo(e.valor, self._config_fn())
                saida["demo"] = est
                for et in etapas:
                    if et["nome"] == "gravar" and est:
                        et["estimativa_s"] = est["gravar_s"]
            saida["etapas"] = etapas
            saida["estimativa_s"] = sum(int(et["estimativa_s"] or 0) for et in etapas)
        return saida

    def iniciar(self, texto: str, opcoes: dict[str, Any] | None = None) -> dict[str, Any]:
        e = self.reconhecer(texto)
        if not e.aceita:
            raise ValueError(e.mensagem)
        if self._local_nao_autorizado(e):
            raise PermissionError(self.MENSAGEM_SELETOR)
        opcoes = dict(opcoes or {})
        rounds = str(opcoes.get("rounds") or "").strip()
        if rounds and not PADRAO_ROUNDS.match(rounds):
            raise ValueError("Os rounds precisam ser números separados por vírgula (ex.: 3,8,9).")
        if e.tipo in ("demo", "video"):
            validar_caminho_sem_unc(e.valor)
        r = {"id": uuid.uuid4().hex[:10], "criada_em": time.time(), "titulo": self._titulo(e),
             "entrada": {"tipo": e.tipo, "valor": e.valor, "detalhes": e.detalhes},
             "opcoes": {"rounds": rounds, "todos": bool(opcoes.get("todos")), "sem_demo": bool(opcoes.get("sem_demo")),
                        "limpo": bool(opcoes.get("limpo", True))},
             "estado": "na_fila", "etapas": montar_etapas(e, {"sem_demo": bool(opcoes.get("sem_demo")), "limpo": bool(opcoes.get("limpo", True))}),
             "dados": {}, "resultado": "", "erro": ""}
        with self._lock:
            if sum(1 for x in self._receitas.values() if x.get("estado") in VIVOS) >= LIMITE_VIVAS:
                raise ValueError(f"Já há {LIMITE_VIVAS} trabalhos em andamento. Espere alguns terminarem ou cancele os que não precisa.")
            self._receitas[r["id"]] = r
            self._cancel[r["id"]] = threading.Event()
            self._salvar(r)
            resposta = self._publica(r)      # o retrato do clique, tirado ANTES de a execução começar
        self._disparar(r["id"])
        return resposta

    def _conferir_retomavel(self, rid: str) -> dict[str, Any]:
        r = self._receitas.get(rid)
        if r is None:
            raise FileNotFoundError("Não achei esse trabalho.")
        if r["estado"] in VIVOS:
            raise RuntimeError("Esse trabalho já está rodando.")
        if r["estado"] == "pronta":
            raise RuntimeError("Esse trabalho já terminou.")
        return r

    def retomar(self, rid: str) -> dict[str, Any]:
        with self._lock:
            self._conferir_retomavel(rid)
            anterior = self._threads.get(rid)
        # B6 (revisão de 11/11): o estado vira `erro`/`cancelada` ANTES de a execução antiga soltar a trava do CS2 (no `finally`). Se o dono clicasse Retomar nesse
        # instante, a execução nova achava que já era a dona da trava e a antiga a soltava logo depois: duas gravações poderiam abrir o CS2. Por isso só se retoma
        # depois que a execução anterior terminou de vez. A espera é FORA da trava `_lock` (a execução antiga ainda usa essa trava para terminar).
        if anterior is not None and anterior.is_alive():
            anterior.join(self.espera_fim_s)
        with self._lock:
            r = self._conferir_retomavel(rid)
            anterior = self._threads.get(rid)
            if anterior is not None and anterior.is_alive():
                raise RuntimeError("Esse trabalho ainda está terminando o passo anterior. Espere alguns segundos e clique em Retomar de novo.")
            r["estado"], r["erro"] = "na_fila", ""
            self._interrompidas.discard(rid)
            for et in r["etapas"]:
                if et["estado"] in ("erro", "cancelada", "rodando", "esperando_voce"):
                    et["estado"], et["detalhe"] = "pendente", ""
            self._cancel[rid] = threading.Event()
            self._salvar(r)
            resposta = self._publica(r)      # idem: o retrato do clique
        self._disparar(rid)
        return resposta

    # ── R1: refazer só a edição de um kit de imagem limpa ─────────────────────────────────────────
    def _origem_do_kit(self, nome: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """A receita mais recente que fez o kit da demo `nome` (para manter a live, a voz e a webcam), e o par live+demo dela se for o caso."""
        for r in sorted(self._receitas.values(), key=lambda x: -x["criada_em"]):
            for p in r["dados"].get("pares") or []:
                if Path(str(p.get("demo", ""))).stem == nome:
                    return r, p
            for v in (r["entrada"].get("valor", "") if r["entrada"].get("tipo") == "demo" else "", r["dados"].get("demo", "")):
                if v and Path(str(v)).stem == nome:
                    return r, None
        return None, None

    def refazer_edicao(self, video_id: str) -> dict[str, Any]:
        """Refaz cortes, shorts, montagem e capa de um kit `…_limpo` a partir das gravações que já existem: um trabalho com SÓ a etapa de editar (não abre o CS2, não
        pede troca de conta). Mantém a live (voz e webcam) quando o kit nasceu de uma live + demo. É como a música nova de "Trocar a música" entra no kit de imagem limpa."""
        video_id = str(video_id or "")
        if not video_id.endswith("_limpo") or len(video_id) <= len("_limpo"):
            raise ValueError("Só o kit de imagem limpa (o nome termina em _limpo) pode ter a edição refeita assim; para os outros use 'Gerar de novo'.")
        nome = video_id[: -len("_limpo")]
        if not (self.raiz / "gravacao_demo" / nome / "registro.json").is_file():
            raise RuntimeError("Não achei as gravações dessa partida (pasta gravacao_demo). Grave de novo pela tela Início.")
        with self._lock:
            for x in self._receitas.values():
                if x["estado"] in VIVOS and x["opcoes"].get("so_editar") == nome:
                    raise RuntimeError("Já estou refazendo a edição dessa partida. Acompanhe em Trabalhos.")
            origem, par = self._origem_do_kit(nome)
        # câmera em outro jogador (A2, 17/11): a pasta é `<demo>__<steamid>`; a demo e o jogador vêm do registro.json da gravação
        jogador, demo_do_registro = "", ""
        if "__" in nome:
            try:
                reg = json.loads((self.raiz / "gravacao_demo" / nome / "registro.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                reg = {}
            jogador = str(reg.get("jogador") or "")
            demo_do_registro = str(reg.get("demo") or "")
            if not jogador.isdigit() or nome.rsplit("__", 1)[1] != jogador:
                raise RuntimeError("O registro dessa gravação não diz qual jogador a câmera seguiu. Grave de novo pela tela Gravar pela demo.")
        demo = Path(str((par or {}).get("demo") or (origem or {}).get("dados", {}).get("demo") or (origem or {}).get("entrada", {}).get("valor")
                        or demo_do_registro or ""))
        if not demo.is_file():
            demo = self._achar_demo(nome.split("__")[0] + ".dem") or demo
        if not demo.is_file():
            raise FileNotFoundError("A demo dessa partida não está mais no computador: não dá para refazer a edição.")
        validar_caminho_sem_unc(demo)
        if par is not None:
            etapa = _etapa_nova("editar", 0, 1)
            dados: dict[str, Any] = {"pares": [{**par, "demo": str(demo)}], "titulo_com_placar": True}
        else:
            etapa = _etapa_nova("editar")
            dados = {"demo": str(demo), "titulo_com_placar": True}
        if jogador:
            dados["jogador"] = jogador
        titulo_base = (origem or {}).get("titulo") or titulo_da_demo(demo)
        r = {"id": uuid.uuid4().hex[:10], "criada_em": time.time(), "titulo": f"Refazer a edição · {titulo_base}",
             "entrada": {"tipo": "demo", "valor": str(demo), "detalhes": {"nome": demo.name}},
             "opcoes": {"rounds": "", "todos": False, "sem_demo": True, "limpo": True, "so_editar": nome},
             "estado": "na_fila", "etapas": [etapa], "dados": dados, "resultado": "", "erro": ""}
        with self._lock:
            self._receitas[r["id"]] = r
            self._cancel[r["id"]] = threading.Event()
            self._salvar(r)
            resposta = self._publica(r)      # o retrato do clique, tirado ANTES de a execução começar
        self._disparar(r["id"])
        return resposta

    def cancelar(self, rid: str) -> dict[str, Any]:
        with self._lock:
            r = self._receitas.get(rid)
            if r is None:
                raise FileNotFoundError("Não achei esse trabalho.")
            if r["estado"] not in VIVOS:
                raise RuntimeError("Esse trabalho não está rodando.")
            self._cancel.setdefault(rid, threading.Event()).set()
            proc = self._procs.get(rid)
            aux = self._auxiliares(r)
        self._parar_processo(proc)
        self._parar_auxiliares(aux)
        with self._lock:
            return self._publica(r)

    def listar(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._publica(r) for r in sorted(self._receitas.values(), key=lambda x: -x["criada_em"])[:30]]

    # ── M-008: o que o servidor pergunta e como ele desliga (ui/coordenador.py) ───────────────────────
    def tem_estado(self, estados: set[str]) -> bool:
        """Alguma receita (TODAS, não só as 30 da tela) está num destes estados?"""
        with self._lock:
            return any(r.get("estado") in estados for r in self._receitas.values())

    def tem_viva(self) -> bool:
        return self.tem_estado(VIVOS)

    def rodando_de_fato(self) -> bool:
        """Alguma receita está trabalhando (ou na fila para trabalhar)? Não conta a que só espera a trava do CS2 enquanto quem tem a trava
        está esperando o dono (trocar a conta do Steam): nada vai andar sem ele, então ela também é "esperando você" (decisão do dono
        no R-001: isso não segura o programa aberto)."""
        with self._lock:
            dono = self._receitas.get(self._dono_cs2 or "")
            dono_espera_voce = dono is not None and dono.get("estado") == "esperando_voce"
            return any(r.get("estado") in ("na_fila", "rodando") and not (dono_espera_voce and rid in self._esperando_trava)
                       for rid, r in self._receitas.items())

    def editando(self) -> bool:
        """Alguma receita (TODAS, não só as 30 da tela) está na etapa que reescreve um kit em `saida/`?"""
        with self._lock:
            return any(r.get("estado") == "rodando" and _editando(r) for r in self._receitas.values())

    def interromper_todas(self) -> None:
        """Fim do programa (botão Encerrar ou servidor saindo): cada receita viva para e fica `interrompida`, como quando a luz cai
        (o Retomar continua da etapa que parou). A que só esperava você também: ela já estava salva."""
        alvos = []
        with self._lock:
            for rid, r in self._receitas.items():
                if r.get("estado") in VIVOS and rid not in self._interrompidas:
                    self._interrompidas.add(rid)
                    self._cancel.setdefault(rid, threading.Event()).set()
                    alvos.append((self._procs.get(rid), self._auxiliares(r)))
        for proc, aux in alvos:
            self._parar_processo(proc)
            self._parar_auxiliares(aux)

    def esperar_fim(self, prazo_s: float) -> None:
        """Espera as execuções terminarem; a que passou do prazo tem a árvore do processo derrubada."""
        from .coordenador import derrubar_arvore
        fim = time.monotonic() + max(0.0, prazo_s)
        with self._lock:
            fios = list(self._threads.items())
        for rid, fio in fios:
            fio.join(max(0.0, fim - time.monotonic()))
        for rid, fio in fios:
            if fio.is_alive():
                with self._lock:
                    proc = self._procs.get(rid)
                derrubar_arvore(proc)
                fio.join(2.0)

    def obter(self, rid: str) -> dict[str, Any]:
        with self._lock:
            r = self._receitas.get(rid)
            if r is None:
                raise FileNotFoundError("Não achei esse trabalho.")
            return self._publica(r)

    def esperar(self, rid: str, estados: set[str] | None = None, tempo: float = 30.0) -> dict[str, Any]:
        """Para os testes e scripts: espera a receita chegar num estado final (ou em um dos `estados`).
        No estado final, espera também a execução terminar de vez: o estado aparece antes de o `finally` soltar a trava do CS2 e o processo (B6)."""
        fim = time.monotonic() + tempo
        alvo = set(estados) if estados else FINAIS
        while time.monotonic() < fim:
            r = self.obter(rid)
            if r["estado"] in alvo:
                if not estados:
                    with self._lock:
                        fio = self._threads.get(rid)
                    if fio is not None and fio is not threading.current_thread():
                        fio.join(max(0.0, fim - time.monotonic()))
                    return self.obter(rid)
                return r
            time.sleep(0.01)
        return self.obter(rid)

    # ── internos ─────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _titulo(e: ent.Entrada) -> str:
        if e.tipo == "demo":                                       # "Mirage · 08/11" já no clique (só o cabeçalho da demo); o placar entra depois, em segundo plano
            return titulo_da_demo(e.valor)
        if e.tipo == "video":
            return e.detalhes.get("nome", e.valor)
        if e.tipo == "pasta":
            return f"{e.detalhes.get('videos', 0)} vídeo(s) da pasta {Path(e.valor).name}"
        if e.tipo == "codigo_partilha":
            return "Partida do código " + e.valor[:10] + "…"
        if e.tipo == "twitch_vod":
            return "VOD da Twitch " + e.valor
        return "Vídeo do YouTube " + e.detalhes.get("id", "")

    def _publica(self, r: dict[str, Any]) -> dict[str, Any]:
        copia = json.loads(json.dumps(r))
        copia["linhas"] = list(self._linhas.get(r["id"], []))[-30:]
        return copia

    def _disparar(self, rid: str) -> None:
        t = threading.Thread(target=self._executar, args=(rid,), daemon=True, name=f"receita-{rid}")
        with self._lock:
            self._threads[rid] = t
        t.start()

    def _marcar(self, r: dict[str, Any], **campos) -> None:
        with self._lock:
            r.update(campos)
            self._salvar(r)

    def _dados(self, r: dict[str, Any], **campos) -> None:
        """G-004: o que a etapa descobriu (demo baixada, vídeo, ids na fila e no baixador) entra em `dados` sob a trava."""
        with self._lock:
            r["dados"].update(campos)

    def _tirar_dados(self, r: dict[str, Any], *chaves: str) -> None:
        with self._lock:
            for chave in chaves:
                r["dados"].pop(chave, None)

    def _etapa(self, r: dict[str, Any], et: dict[str, Any], **campos) -> None:
        with self._lock:
            et.update(campos)
            self._salvar(r)

    def _checar_cancelada(self, rid: str) -> None:
        if self._cancel.get(rid) is not None and self._cancel[rid].is_set():
            raise Cancelada()

    def _titulo_com_placar(self, r: dict[str, Any]) -> None:
        """Depois de ter a demo (colada, ou baixada pelo código), acrescenta o placar ao título: "Mirage 11x13 · 08/11". Roda na execução do trabalho (a leitura
        inteira da demo leva alguns segundos e não pode travar o clique). Qualquer falha deixa o título como está."""
        if r["dados"].get("titulo_com_placar"):
            return
        caminho = r["entrada"]["valor"] if r["entrada"]["tipo"] == "demo" else r["dados"].get("demo")
        if not caminho or not Path(caminho).is_file():
            return
        try:
            titulo = titulo_da_demo(caminho, placar_da_demo(caminho, self._config_fn()))
        except Exception:  # noqa: BLE001  # o título é enfeite: nunca derruba o trabalho
            return
        with self._lock:
            r["titulo"] = titulo
            r["dados"]["titulo_com_placar"] = True
            self._salvar(r)

    def _executar(self, rid: str) -> None:
        r = self._receitas[rid]
        try:
            self._marcar(r, estado="rodando")            # dentro do `try`: um erro aqui vira "erro" na receita, nunca um "rodando" eterno
            self._titulo_com_placar(r)
            for et in r["etapas"]:
                if et["estado"] == "ok":
                    continue
                self._checar_cancelada(rid)
                self._etapa(r, et, estado="rodando", inicio=time.time(), detalhe="")
                detalhe = getattr(self, "_etapa_" + et["nome"])(r, et) or ""
                self._etapa(r, et, estado="ok", fim=time.time(), detalhe=str(detalhe)[:300])
                if et["nome"] == "obter_demo":
                    self._titulo_com_placar(r)
                if et["nome"] == "gravar":
                    self._soltar_cs2(rid)                                       # o CS2 já fechou: a próxima receita pode gravar
            self._marcar(r, estado="pronta")
        except Cancelada:
            if rid in self._interrompidas:                                     # o programa está fechando: fica `interrompida`, retomável
                with self._lock:
                    _marcar_interrompida(r)
                    self._salvar(r)
                return
            aviso = AVISO_KIT_PELA_METADE if _editando(r) else ""
            for et in r["etapas"]:
                if et["estado"] in ("rodando", "esperando_voce"):
                    self._etapa(r, et, estado="cancelada", fim=time.time())
            self._marcar(r, estado="cancelada", erro=aviso)
        except ErroEtapa as e:
            aviso = " " + AVISO_KIT_PELA_METADE if _editando(r) else ""
            for et in r["etapas"]:
                if et["estado"] in ("rodando", "esperando_voce"):
                    self._etapa(r, et, estado="erro", fim=time.time(), detalhe=str(e)[:300])
            self._marcar(r, estado="erro", erro=str(e)[:300] + aviso)
        except Exception as e:  # noqa: BLE001
            aviso = " " + AVISO_KIT_PELA_METADE if _editando(r) else ""
            for et in r["etapas"]:
                if et["estado"] in ("rodando", "esperando_voce"):
                    self._etapa(r, et, estado="erro", fim=time.time(), detalhe=f"erro inesperado ({type(e).__name__})")
            self._marcar(r, estado="erro", erro=f"Erro inesperado ({type(e).__name__}). Veja o registro da etapa e tente retomar." + aviso)
        finally:
            with self._lock:
                self._procs.pop(rid, None)
            self._soltar_cs2(rid)                                                # erro, cancelamento ou fim: nunca fica com a trava

    # a trava do CS2: pega no começo de `esperar_conta` e solta no fim de `gravar` (ou em qualquer erro/cancelamento)
    def _pegar_cs2(self, r: dict[str, Any], et: dict[str, Any]) -> None:
        rid = r["id"]
        if self._dono_cs2 == rid:
            return
        avisou = False
        with self._lock:
            self._esperando_trava.add(rid)
        try:
            while not self._trava_cs2.acquire(timeout=0.05):
                self._checar_cancelada(rid)
                if not avisou:
                    self._etapa(r, et, detalhe="Esperando outra gravação terminar (o CS2 só abre uma vez por vez).")
                    avisou = True
                self._cancel[rid].wait(min(self.poll_s, 0.5))
            self._dono_cs2 = rid
        finally:
            with self._lock:
                self._esperando_trava.discard(rid)

    def _soltar_cs2(self, rid: str) -> None:
        if self._dono_cs2 == rid:
            self._dono_cs2 = None
            try:
                self._trava_cs2.release()
            except RuntimeError:
                pass

    # ── processos à parte ────────────────────────────────────────────────────────────────────────
    def _rodar(self, r: dict[str, Any], et: dict[str, Any], argumentos: list[str]) -> tuple[int, str]:
        """Roda `python -m …` e mostra a última linha na etapa. Devolve (código, última linha que começa com '[parei]' ou vazia)."""
        rid = r["id"]
        cmd = [self._python, "-m", *argumentos]
        try:
            proc = self._popen(cmd, cwd=str(self.programa), env=ambiente_dos_filhos(self.raiz), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", **sem_janela(grupo_novo()))   # P2: o filho enxerga a mesma pasta de dados
        except OSError as e:
            raise ErroEtapa(f"Não consegui iniciar o programa dessa etapa: {e}") from e
        with self._lock:                                                 # G-004: o cancelar lê `_procs` sob a mesma trava (nada escapa entre os dois)
            self._procs[rid] = proc
            ja_cancelada = self._cancel[rid].is_set()
            linhas = self._linhas.setdefault(rid, [])
        if ja_cancelada:
            self._parar_processo(proc)
        parei = ""
        ultimo_disco = 0.0
        for linha in proc.stdout or []:
            texto = linha.rstrip("\r\n")
            if not texto.strip():
                continue
            if texto.strip().startswith("[parei]"):
                parei = texto.strip()
            with self._lock:
                linhas.append(texto)
                del linhas[:-LINHAS_MAX]
                et["detalhe"] = texto.strip()[:200]                      # a tela lê da memória; o disco só a cada 1 s (e nas mudanças de etapa)
            if time.monotonic() - ultimo_disco >= 1.0:
                self._salvar(r)
                ultimo_disco = time.monotonic()
        codigo = proc.wait()
        with self._lock:
            self._procs.pop(rid, None)
        self._checar_cancelada(rid)
        return codigo, parei

    @staticmethod
    def _parar_processo(proc: Any) -> None:
        cancelar_processo(proc)       # pedido educado (o gravador fecha o jogo) e, se não der, a ÁRVORE do trabalho cai (o ffmpeg neto não sobra; N3b)

    @staticmethod
    def _auxiliares(r: dict[str, Any]) -> dict[str, Any]:
        """O que a receita pediu à fila de análise e ao baixador da Twitch (tirado sob a trava, junto com o pedido de cancelar)."""
        return {"fila_id": r["dados"].get("fila_id"), "vod_id": r["dados"].get("vod_id")}

    def _parar_auxiliares(self, aux: dict[str, Any]) -> None:
        """Cancela o que a receita pediu à fila de análise ou ao baixador da Twitch."""
        try:
            if aux.get("fila_id") and self.fila is not None:
                self.fila.cancelar(aux["fila_id"])
            if aux.get("vod_id") and self.vods is not None:
                self.vods.cancelar(aux["vod_id"])          # G-004: só o download DESTA receita (nunca o de outro pedido)
        except Exception:  # noqa: BLE001
            pass

    def _falha(self, codigo: int, parei: str, o_que: str) -> ErroEtapa:
        return ErroEtapa(parei.removeprefix("[parei]").strip() if parei else f"{o_que} terminou com erro (código {codigo}). Veja as linhas do trabalho.")

    # ── as etapas ────────────────────────────────────────────────────────────────────────────────
    def _etapa_obter_demo(self, r, et):
        codigo_partilha = r["entrada"]["valor"]
        # G-003 (revisão final): o baixador exige o CS2 FECHADO e o abre por segundos. No caminho normal a trava já veio da etapa de conta (que roda
        # antes); numa RETOMADA em que a etapa de conta já estava ok, este download rodava sem a trava. Aqui ela é garantida (se já é desta
        # receita, não faz nada) e continua presa até o fim da gravação, como sempre.
        self._pegar_cs2(r, et)
        cod, parei = self._rodar(r, et, ["cs2clip.baixar_demo", "codigo", codigo_partilha, "--baixar"])
        if cod != 0:
            raise self._falha(cod, parei, "O download da demo")
        pronta = next((re.match(r"^Pronta:\s*(.+)$", x.strip()) for x in reversed(self._linhas.get(r["id"], [])) if x.strip().startswith("Pronta:")), None)
        if pronta is None:
            raise ErroEtapa("O download terminou mas não vi o nome do arquivo da demo.")
        arq = Path(pronta.group(1).strip())
        validar_caminho_sem_unc(arq)
        if not arq.is_file():
            raise ErroEtapa(f"A demo baixada não está em {arq}.")
        self._dados(r, demo=str(arq))
        return arq.name

    def _etapa_obter_video(self, r, et):
        from cs2clip.twitch_vod import arquivo_de_destino
        ja = arquivo_de_destino(r["entrada"]["valor"], self.raiz / "entrada")
        if ja.is_file() and ja.stat().st_size > 100_000:               # colou de novo um VOD que já está na pasta entrada: usa o arquivo
            self._dados(r, video=str(ja))
            return f"{ja.name} (já estava baixado)"
        if self.vods is None:
            raise ErroEtapa("O baixador de VOD da Twitch não está disponível.")
        try:
            job = self.vods.iniciar(r["entrada"]["detalhes"]["link"])
        except (ValueError, FileNotFoundError, RuntimeError) as e:
            raise ErroEtapa(str(e)) from e
        self._dados(r, vod_id=job["id"])
        try:
            while True:
                self._checar_cancelada(r["id"])
                item = next((j for j in self.vods.listar()["itens"] if j["id"] == job["id"]), None)
                if item is None:
                    raise ErroEtapa("O download do VOD sumiu da lista.")
                self._etapa(r, et, detalhe=f"{item.get('percentual', 0)}%")
                if item["estado"] == "pronto":
                    self._dados(r, video=str(Path(self.raiz) / "entrada" / item["arquivo"]))
                    return item["arquivo"]
                if item["estado"] in ("erro", "cancelado"):
                    raise ErroEtapa(item.get("erro") or "O download do VOD foi cancelado.")
                self._cancel[r["id"]].wait(self.poll_s)
        finally:
            self._tirar_dados(r, "vod_id")       # G-004: acabou o download desta receita; cancelar depois não pode mexer no baixador (que pode ser de outro pedido)

    PRAZO_DOWNLOAD_S = 6 * 3600

    def _etapa_aguardar_download(self, r, et):
        """D1=A (oficial): o dono baixa pelo YouTube Studio; a receita vigia a pasta Downloads. Achado um vídeo novo que parou de crescer, ele só vale
        depois que o DONO confirma que é esse (pode ser outro download dele): botões "É este arquivo" / "Não é este" (`confirmar`)."""
        inicio = time.time() - 2
        antes = {p.name for p in self._downloads.glob("*") if p.is_file()} if self._downloads.is_dir() else set()
        link = f"https://studio.youtube.com/video/{r['entrada']['detalhes']['id']}/edit"
        self._etapa(r, et, estado="esperando_voce", detalhe=f"Abra {link} → ⋮ → Baixar. Estou vigiando a pasta Downloads.")
        self._marcar(r, estado="esperando_voce")
        self._tirar_dados(r, "candidato", "confirmado")
        ignorados: set[str] = set(r["dados"].get("ignorados", []))
        visto: dict[str, tuple[int, float]] = {}
        candidato: Path | None = None
        prazo = time.monotonic() + self.PRAZO_DOWNLOAD_S
        while True:
            self._checar_cancelada(r["id"])
            if time.monotonic() > prazo:
                raise ErroEtapa("Esperei 6 horas pelo download do vídeo. Baixe pelo YouTube Studio e clique em Retomar.")
            if candidato is None:
                if self._downloads.is_dir():
                    for p in sorted(self._downloads.iterdir()):
                        if not (p.is_file() and p.suffix.lower() in ent.VIDEO_EXT and p.name not in antes and p.name not in ignorados and p.stat().st_mtime >= inicio):
                            continue
                        tam = p.stat().st_size
                        antigo = visto.get(p.name)
                        if antigo and antigo[0] == tam and tam > 100_000 and time.time() - antigo[1] >= max(2 * self.poll_s, 0.05):
                            candidato = p
                            validar_caminho_sem_unc(p)
                            with self._lock:
                                r["dados"]["candidato"] = {"nome": p.name, "caminho": str(p), "mb": round(tam / 1e6, 1)}
                            self._etapa(r, et, detalhe=f"Achei {p.name} ({round(tam / 1e6, 1)} MB) na pasta Downloads. É este o vídeo? Confirme para continuar.")
                            break
                        if not antigo or antigo[0] != tam:
                            visto[p.name] = (tam, time.time())
            else:
                dec = r["dados"].get("confirmado")
                if dec == str(candidato):
                    self._dados(r, video=str(candidato))
                    self._tirar_dados(r, "candidato")
                    self._marcar(r, estado="rodando")
                    return candidato.name
                if dec == "recusado" or not candidato.is_file():       # "não é este" (ou o arquivo sumiu): volta a vigiar e não pergunta desse de novo
                    ignorados.add(candidato.name)
                    with self._lock:
                        r["dados"]["ignorados"] = sorted(ignorados)
                        r["dados"].pop("candidato", None)
                        r["dados"].pop("confirmado", None)
                    candidato = None
                    self._etapa(r, et, detalhe=f"Abra {link} → ⋮ → Baixar. Estou vigiando a pasta Downloads.")
            self._cancel[r["id"]].wait(self.poll_s)

    def confirmar(self, rid: str, aceitar: bool = True) -> dict[str, Any]:
        """O dono respondeu à pergunta "É este o vídeo?" da etapa de download do YouTube."""
        with self._lock:
            r = self._receitas.get(rid)
            if r is None:
                raise FileNotFoundError("Não achei esse trabalho.")
            cand = r["dados"].get("candidato")
            if not cand or r["estado"] not in VIVOS:
                raise RuntimeError("Nenhum arquivo está esperando a sua confirmação.")
            r["dados"]["confirmado"] = cand["caminho"] if aceitar else "recusado"
            self._salvar(r)
            return self._publica(r)

    def _etapa_esperar_conta(self, r, et):
        self._pegar_cs2(r, et)
        cfg = self._config_fn()
        g = cfg.gravacao_demo
        fim = time.monotonic() + self.espera_conta_max_s
        ultimo = None
        while True:
            self._checar_cancelada(r["id"])
            try:
                steam_local.exigir_conta_do_bisturi(g.conta_steamid, g.conta_principal_id, self._conta_logada(),
                                                    apelido_bisturi=g.conta_apelido, apelido_principal=g.conta_principal_apelido)
                self._marcar(r, estado="rodando")
                return "Steam na conta do Bisturi."
            except steam_local.ContaErrada as e:
                bruto = str(e)
                if e.de_configuracao:
                    raise ErroEtapa(bruto) from e                  # configuração errada: esperar não adianta
                texto = bruto.replace("e clique em Continuar", "— o Bisturi continua sozinho assim que você entrar")
                if texto != ultimo:
                    self._etapa(r, et, estado="esperando_voce", detalhe=texto)
                    self._marcar(r, estado="esperando_voce")
                    ultimo = texto
            if time.monotonic() > fim:
                raise ErroEtapa("Esperei 15 minutos pela troca de conta no Steam. Troque e clique em Retomar.")
            self._cancel[r["id"]].wait(self.poll_s)

    def _demo_da_receita(self, r, et=None) -> str:
        if et is not None and et.get("par") is not None:
            return r["dados"]["pares"][int(et["par"])]["demo"]
        return r["dados"].get("demo") or r["entrada"]["valor"]

    def _etapa_gravar(self, r, et):
        self._pegar_cs2(r, et)                                                   # numa retomada, a etapa de conta pode já estar ok
        demo = self._demo_da_receita(r, et)
        validar_caminho_sem_unc(demo)
        args = ["cs2clip.gravador_demo", "--demo", demo]
        if r["opcoes"].get("rounds"):
            args += ["--rounds", r["opcoes"]["rounds"]]
        elif r["opcoes"].get("todos"):
            args += ["--todos"]
        self._etapa(r, et, estado="rodando", detalhe="Abrindo o CS2…")
        cod, parei = self._rodar(r, et, args)
        if cod != 0:
            raise self._falha(cod, parei, "A gravação")
        return "Rounds gravados."

    def _etapa_editar(self, r, et):
        demo = self._demo_da_receita(r, et)
        args = ["cs2clip.editar_demo", "--demo", demo]
        if str(r["dados"].get("jogador") or "").isdigit():   # kit de câmera em outro jogador (A2)
            args.append(f"--jogador={r['dados']['jogador']}")
        aviso_voz = ""
        if et.get("par") is not None:                       # live + demo: a voz da live por cima do jogo limpo
            par = r["dados"]["pares"][int(et["par"])]
            args += ["--live", par["live"], "--analise-da-live", par["analise"]]    # a voz e a webcam da live: o editar_demo decide o que dá para usar e avisa o que não deu
            try:
                if int(self._config_fn().audio.faixa_voz) < 2:
                    aviso_voz = " (sem a sua voz: diga qual faixa do OBS é só o microfone em Configurar → Áudio → faixa_voz)"
            except Exception:  # noqa: BLE001
                pass
        cod, parei = self._rodar(r, et, args)
        if cod != 0:
            raise self._falha(cod, parei, "A edição")
        pronta = next((re.match(r"^Edição pronta em\s+(.+?)\.?$", x.strip()) for x in reversed(self._linhas.get(r["id"], []))
                       if x.strip().startswith("Edição pronta em")), None)
        if pronta:
            self._marcar(r, resultado=pronta.group(1).strip())
        sem_faixa = next((x.strip() for x in reversed(self._linhas.get(r["id"], [])) if x.strip().startswith("[aviso] ") and "SEM a sua voz" in x), "")
        if sem_faixa:
            aviso_voz = " (sem a sua voz: " + sem_faixa.removeprefix("[aviso] ").split(":")[0].strip() + ")"
        return "Kit pronto." + aviso_voz

    def _pasta_da_analise(self, video: str) -> Path | None:
        """A pasta `saida/<id>` da análise desse vídeo (pelo `indice.json`; a mais recente se houver mais de uma).
        G-006 (revisão de 26/11): "mais recente" pela data da análise (`processado_em`), não pela posição na lista (o índice vem do mais novo para o
        mais velho e o código pegava o último, o mais velho). Só vale análise cuja pasta ainda existe."""
        try:
            itens = json.loads((self.raiz / "saida" / "indice.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        alvo = Path(video)
        achadas: list[tuple[str, Path]] = []
        for it in itens if isinstance(itens, list) else []:
            if not isinstance(it, dict):
                continue
            try:
                igual = str(it.get("caminho") or "") and Path(it["caminho"]).resolve() == alvo.resolve()
            except OSError:
                igual = False
            vid = str(it.get("id") or "")
            if (igual or it.get("arquivo") == alvo.name) and vid:
                pasta = self.raiz / "saida" / vid
                if pasta.is_dir():
                    achadas.append((str(it.get("processado_em") or ""), pasta))
        if not achadas:
            return None
        return max(achadas, key=lambda x: x[0])[1]             # empate de data: a primeira da lista (o índice põe a mais nova primeiro)

    def _achar_demo(self, nome: str) -> Path | None:
        for base in [*self._procurar_em(), self.raiz / "demos"]:
            cand = Path(base) / nome
            if cand.is_file():
                return cand
        return None

    def _etapa_parear(self, r, et):
        """Depois da análise da live: a demo que a análise CONFIRMOU pelos abates (sincronia.json) vira regravação em imagem limpa + montagem com a voz."""
        if r["opcoes"].get("sem_demo") or not r["opcoes"].get("limpo", True):
            return "Regravação em imagem limpa desligada."
        video = r["dados"].get("video") or r["entrada"]["valor"]
        pasta = self._pasta_da_analise(video)
        if pasta is None:
            return "Não achei a análise da live; fica só o kit da live."
        try:
            itens = json.loads((pasta / "sincronia.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "A análise não confirmou nenhuma demo para esta live; fica só o kit da live."
        pares = []
        for it in itens if isinstance(itens, list) else []:
            if not (it.get("usada", True) and it.get("deslocamento") is not None and it.get("demo")):
                continue
            demo = self._achar_demo(str(it["demo"]))
            if demo is not None:
                pares.append({"demo": str(demo), "analise": str(pasta), "live": video, "deslocamento": float(it["deslocamento"])})
        pares = pares[:LIMITE_PARES]
        if not pares:
            return "A demo confirmada pela análise não está mais no computador; fica só o kit da live."
        with self._lock:
            r["dados"]["pares"] = pares
            novas = []
            for i in range(len(pares)):
                novas += [_etapa_nova("esperar_conta", i, len(pares)), _etapa_nova("gravar", i, len(pares)), _etapa_nova("editar", i, len(pares))]
            pos = next(k for k, x in enumerate(r["etapas"]) if x is et) + 1
            r["etapas"][pos:pos] = novas
            self._salvar(r)
        return f"Demo confirmada ({len(pares)} partida(s)): vou regravar os rounds bons em imagem limpa e juntar a sua voz."

    def _etapa_analisar(self, r, et):
        if self.fila is None:
            raise ErroEtapa("A fila de análise não está disponível.")
        alvo = r["dados"].get("video") or r["entrada"]["valor"]
        arquivos = [alvo]
        if r["entrada"]["tipo"] == "pasta":
            arquivos = sorted(str(p) for p in Path(alvo).iterdir() if p.is_file() and p.suffix.lower() in ent.VIDEO_EXT)
        for arquivo in arquivos:
            try:
                job = self.fila.adicionar(arquivo, {"demo_auto": not r["opcoes"].get("sem_demo")})
            except (FileNotFoundError, ValueError, RuntimeError) as e:
                raise ErroEtapa(str(e)) from e
            self._dados(r, fila_id=job["id"])
            while True:
                self._checar_cancelada(r["id"])
                item = next((j for j in self.fila.listar()["itens"] if j["id"] == job["id"]), None)
                if item is None:
                    raise ErroEtapa("A análise sumiu da fila.")
                self._etapa(r, et, detalhe=str(item.get("mensagem") or item["estado"])[:200])
                if item["estado"] in ("pronto", "com_problemas"):
                    break
                if item["estado"] == "precisa_jogador":
                    raise ErroEtapa("O programa não achou qual jogador é você na demo. Abra a fila e escolha o jogador.")
                if item["estado"] in ("erro", "cancelado"):
                    raise ErroEtapa(str(item.get("mensagem") or "A análise falhou."))
                self._cancel[r["id"]].wait(self.poll_s)
        return "Análise pronta. Veja em Biblioteca."
