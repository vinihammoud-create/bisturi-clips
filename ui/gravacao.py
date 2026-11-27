"""D6 (Plano D): a tela "Gravar pela demo". Lista as demos, mostra o plano e o checklist, baixa por código e grava, tudo em segundo plano.

O trabalho pesado roda como PROCESSO À PARTE (`python -m cs2clip.gravador_demo` / `cs2clip.baixar_demo`), igual à fila de análise: se a tela
fechar, a gravação segue; o cancelar manda CTRL+BREAK (o gravador fecha o jogo e confere os arquivos antes de sair).
Só nomes de demo que o servidor mesmo listou chegam ao processo (nunca um caminho vindo da tela), e o código de partilha é validado antes.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from collections.abc import Callable

from cs2clip import demos_cs2, sharecode, steam_local
from cs2clip.tools import cancelar_processo, grupo_novo, sem_janela
from .seguranca import ambiente_dos_filhos, nome_reservado_windows, python_de_console

LINHAS_MAX = 300
NOME_DEMO = re.compile(r"^match730_\d{21}_\d{10}_\d{1,5}\.dem$")
# Demo de campeonato (HLTV) copiada para `demos\` do Bisturi: nome simples, sem espaço, barra ou ponto no meio (vai na linha do jogo)
NOME_DEMO_LIVRE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.dem$")
JOGADOR_MAX = 60


def _livre_ok(nome: str) -> bool:
    """Nome livre (demo de campeonato) sem nome reservado do Windows (`CON.dem`, `nul.dem`: a pasta da gravação usa o nome)."""
    return bool(NOME_DEMO_LIVRE.fullmatch(nome)) and not nome_reservado_windows(nome)


def _nome_aceito(nome: str, origem: str) -> bool:
    return bool(NOME_DEMO.fullmatch(nome) or (origem == "Bisturi" and _livre_ok(nome)))


def _jogador_valido(jogador: str) -> str:
    """O nome (ou steamid) do jogador observado, como veio da tela. Vazio = você (conta principal)."""
    j = (jogador or "").strip()
    if len(j) > JOGADOR_MAX or any(ord(c) < 32 for c in j):
        raise ValueError(f"O nome do jogador precisa ter até {JOGADOR_MAX} caracteres, sem quebra de linha.")
    return j


CS2_OCUPADO = ("O CS2 (ou o Steam) está sendo usado por um trabalho da tela Início. Espere ele terminar ou cancele em Trabalhos.")


class GerenciadorGravacao:
    def __init__(self, raiz: Path, popen_factory: Callable[..., Any] | None = None, python: str | None = None,
                 pasta_replays: Callable[[], Path | None] | None = None, trava_cs2: Any = None, programa: Path | None = None):
        # P2 (duas bases): `raiz` = a pasta de DADOS (demos/, config.toml, gravacao_demo/); `programa` = de onde os `python -m cs2clip.…` rodam.
        # Sem `programa`, as duas são a mesma pasta (modo portátil e os testes).
        self.raiz = Path(raiz)
        self.programa = Path(programa) if programa is not None else self.raiz
        self._popen = popen_factory or subprocess.Popen
        self._python = python or str(python_de_console())     # NUNCA o pythonw.exe do app (N3): o filho sem console abre janela preta a cada ffmpeg
        self._replays = pasta_replays or demos_cs2.pasta_replays
        self._lock = threading.Lock()
        self._job: dict[str, Any] | None = None
        self._proc: Any = None
        self._linhas: list[str] = []
        # G-003 (revisão de 26/11): a MESMA trava das receitas (o servidor entrega uma só): gravar ou baixar por aqui nunca abre o CS2/Steam junto com um trabalho
        self._trava_cs2 = trava_cs2 if trava_cs2 is not None else threading.Lock()
        self._com_cs2 = False
        self._leitor: threading.Thread | None = None

    def carregar_pasta_replays(self) -> Path | None:
        try:
            rep = self._replays()
            return Path(rep) if rep else None
        except OSError:
            return None

    # ── demos ────────────────────────────────────────────────────────────────────────────────────
    def _pastas(self) -> list[tuple[str, Path]]:
        pastas = [("Bisturi", self.raiz / "demos")]
        rep = self._replays()
        if rep is not None:
            pastas.append(("CS2", Path(rep)))
        return pastas

    def demos(self) -> list[dict[str, Any]]:
        vistos: set[str] = set()
        saida: list[dict[str, Any]] = []
        for origem, pasta in self._pastas():
            for d in demos_cs2.listar_demos(pasta, "*.dem" if origem == "Bisturi" else demos_cs2.PADRAO_DEMO):
                nome = d.caminho.name
                if nome in vistos or not _nome_aceito(nome, origem):
                    continue
                vistos.add(nome)
                try:
                    mb = round(d.caminho.stat().st_size / 1e6, 1)
                except OSError:
                    continue
                saida.append({"nome": nome, "origem": origem, "data": d.quando.strftime("%d/%m/%Y %H:%M"), "aproximada": d.aproximada,
                              "mb": mb, "_ordem": d.quando.timestamp()})
        saida.sort(key=lambda x: -x["_ordem"])
        for x in saida:
            x.pop("_ordem")
        return saida

    def caminho_da_demo(self, nome: str) -> Path:
        """O caminho de uma demo que o servidor listou; qualquer outro nome é recusado (sem barra, sem `..`)."""
        if not isinstance(nome, str) or not (NOME_DEMO.fullmatch(nome) or _livre_ok(nome)):
            raise ValueError("Escolha uma das demos da lista.")
        for origem, pasta in self._pastas():
            arq = Path(pasta) / nome
            if _nome_aceito(nome, origem) and arq.is_file():
                return arq
        if not NOME_DEMO.fullmatch(nome):          # nome livre só vale para a pasta demos\ do Bisturi
            raise ValueError("Escolha uma das demos da lista.")
        raise FileNotFoundError("Não achei essa demo.")

    # ── plano e checklist ────────────────────────────────────────────────────────────────────────
    def _config(self):
        """R-005 (revisão de 26/11): um config.toml com erro NÃO vira os padrões do código calado (conta principal vazia, margens e mínimo de
        abates diferentes dos do dono). O erro sobe como ValueError e a tela mostra a mensagem (as rotas já devolvem 400 com ela)."""
        from cs2clip.config import load_config
        try:
            return load_config(self.raiz / "config.toml")
        except (ValueError, TypeError, OSError) as e:      # tomllib.TOMLDecodeError também é ValueError
            raise ValueError(f"O config.toml tem um erro: {e}. Corrija em Configurar (ou no próprio config.toml) e escolha a demo de novo.") from e

    def jogadores(self, nome: str) -> list[dict[str, Any]]:
        """Os jogadores da demo (mais abates primeiro), para escolher quem a câmera segue (ex.: uma demo de campeonato)."""
        from cs2clip.demo import DemoInvalida, e_falha_do_parser, listar_jogadores
        demo = self.caminho_da_demo(nome)
        try:
            lista = listar_jogadores(demo)
        except DemoInvalida as e:
            raise ValueError(str(e)) from e
        except BaseException as e:  # noqa: BLE001  # pânico do parser vira mensagem
            if not e_falha_do_parser(e):
                raise
            raise ValueError(f"Não consegui ler essa demo ({type(e).__name__}).") from e
        return [{"nome": n, "steamid": str(s), "abates": k} for n, s, k in lista]

    def plano(self, nome: str, jogador: str = "") -> dict[str, Any]:
        from cs2clip.demo import DemoInvalida, ler_demo, rounds_para_gravar
        demo = self.caminho_da_demo(nome)
        jogador = _jogador_valido(jogador)
        cfg = self._config()
        g = cfg.gravacao_demo
        try:
            partida = ler_demo(demo, jogador or g.conta_principal_id or cfg.demo.jogador or None)
        except DemoInvalida as e:
            raise ValueError(str(e)) from e
        except BaseException as e:  # noqa: BLE001  # qualquer falha do parser (inclusive o pânico do Rust) vira mensagem, nunca erro 500 na tela
            from cs2clip.demo import e_falha_do_parser
            if not e_falha_do_parser(e):
                raise
            raise ValueError(f"Não consegui ler essa demo ({type(e).__name__}). Ela pode estar incompleta ou de uma versão que o leitor ainda não conhece.") from e
        if partida.jogador is None:
            if jogador:
                raise ValueError(f"Não achei o jogador \"{jogador}\" nessa demo. Escolha um da lista.")
            raise ValueError(f"Você não aparece nessa demo (confira {steam_local.rotulo_conta('principal', g.conta_principal_apelido)} em Configurar → Contas do Steam). "
                             "Se for a demo de outra pessoa (campeonato), escolha o jogador na lista.")
        todos = rounds_para_gravar(partida, False, 0, g.margem_antes_s, g.margem_depois_s)
        bons = {t.round for t in rounds_para_gravar(partida, True, cfg.cortes.min_abates_round, g.margem_antes_s, g.margem_depois_s)}
        rounds = [{"round": t.round, "abates": t.abates, "duracao_s": round(t.duracao_s()), "bom": t.round in bons} for t in todos]
        return {"demo": nome, "partida": partida.titulo, "jogador": partida.nome, "escolhido": bool(jogador),
                "steamid": str(partida.jogador) if jogador else "", "patch": partida.patch_version,
                "minimo_abates": cfg.cortes.min_abates_round, "rounds": rounds,
                "minutos_bons": round(sum(r["duracao_s"] for r in rounds if r["bom"]) / 60, 1),
                "minutos_todos": round(sum(r["duracao_s"] for r in rounds) / 60, 1)}

    def checklist(self, nome: str | None = None) -> list[dict[str, Any]]:
        from cs2clip import gravador_demo
        demo = self.caminho_da_demo(nome) if nome else None
        return [{"item": i, "ok": bool(ok), "detalhe": str(det)} for i, ok, det in gravador_demo.checklist(demo, self._config())]

    # ── trabalhos em segundo plano ───────────────────────────────────────────────────────────────
    def _ocupado(self) -> bool:
        return self._job is not None and self._job["estado"] == "rodando"

    def ocupado(self) -> bool:
        with self._lock:
            return self._ocupado()

    def editando(self) -> bool:
        """A gravação em andamento foi pedida com "editar em seguida"? (só essa reescreve um kit em `saida/`)"""
        with self._lock:
            return self._ocupado() and bool((self._job or {}).get("edita"))

    def parar(self) -> None:
        """Fim do programa: cancela o trabalho em andamento, se houver e se ainda não foi pedido (o gravador recebe UM pedido educado)."""
        with self._lock:
            ja_pedido = self._job is not None and bool(self._job.get("cancelado"))
        if ja_pedido:
            return
        try:
            self.cancelar()
        except RuntimeError:
            pass                                 # nada em andamento

    def esperar_fim(self, prazo_s: float) -> None:
        """Espera o processo sair (o gravador fecha o jogo e confere os arquivos); passado o prazo, a árvore dele cai."""
        from .coordenador import derrubar_arvore
        with self._lock:
            leitor, proc = self._leitor, self._proc
        if leitor is None or not leitor.is_alive():
            return
        leitor.join(max(0.0, prazo_s))
        if leitor.is_alive():
            derrubar_arvore(proc)
            leitor.join(2.0)

    def _iniciar(self, tipo: str, argumentos: list[str], descricao: str) -> dict[str, Any]:
        with self._lock:
            if self._ocupado():
                raise RuntimeError("Já existe um trabalho em andamento. Espere terminar ou cancele.")
            if not self._trava_cs2.acquire(blocking=False):
                raise RuntimeError(CS2_OCUPADO)
            self._com_cs2 = True
            self._linhas = []
            self._job = {"tipo": tipo, "descricao": descricao, "estado": "rodando", "codigo": None, "inicio": time.time(), "cancelado": False,
                         "edita": "--editar" in argumentos}
            self._proc = None                    # G-014: o processo deste trabalho ainda não existe (o de um trabalho anterior não vale mais)
            job = self._job
        cmd = [self._python, "-m", *argumentos]
        try:
            proc = self._popen(cmd, cwd=str(self.programa), env=ambiente_dos_filhos(self.raiz), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", **sem_janela(grupo_novo()))   # P2: o filho enxerga a mesma pasta de dados
        except OSError as e:
            with self._lock:
                job.update(estado="erro", codigo=-1)
                self._linhas.append(f"Não consegui iniciar: {e}")
                self._soltar_cs2_com_trava()
                return dict(job)
        with self._lock:                         # G-014: o cancelar que chegou durante a abertura vale para o processo novo
            self._proc = proc
            ja_cancelado = bool(job["cancelado"])
            self._leitor = threading.Thread(target=self._ler, args=(proc, job), daemon=True)
            leitor = self._leitor
        if ja_cancelado:
            cancelar_processo(proc)
        leitor.start()
        with self._lock:
            return dict(job)

    def _soltar_cs2_com_trava(self) -> None:
        """Solta a trava do CS2 (quem chama já segura `self._lock`)."""
        if self._com_cs2:
            self._com_cs2 = False
            self._trava_cs2.release()

    def _soltar_cs2(self) -> None:
        with self._lock:
            self._soltar_cs2_com_trava()

    def _ler(self, proc: Any, job: dict[str, Any]) -> None:
        """Lê a saída do processo. G-014: linhas e estado mudam sob a trava (a tela lê os dois pelo `estado()`)."""
        try:
            for linha in proc.stdout or []:
                texto = linha.rstrip("\r\n")
                if texto.strip():
                    with self._lock:
                        self._linhas.append(texto)
                        del self._linhas[:-LINHAS_MAX]
            codigo = proc.wait()
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._linhas.append(f"erro lendo a saída: {e}")
            codigo = -1
        with self._lock:
            self._soltar_cs2_com_trava()         # o processo saiu (o gravador já fechou o jogo): uma receita pode usar o CS2
            job["codigo"] = codigo
            job["estado"] = "cancelado" if job.get("cancelado") else ("pronto" if codigo == 0 else "erro")

    def iniciar_download(self, codigo: str | None = None, ultima: int = 0, so_listar: bool = False) -> dict[str, Any]:
        if codigo:
            if not sharecode.exato(codigo):                       # A2: formato exato (o texto vai como argumento de um processo)
                raise ValueError("O código de partilha não está no formato certo (CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx).")
            return self._iniciar("download", ["cs2clip.baixar_demo", "codigo", codigo.strip(), "--baixar"], "Baixando a partida do código de partilha")
        if so_listar:
            return self._iniciar("download", ["cs2clip.baixar_demo", "ultimas"], "Listando as 8 últimas partidas da conta logada no Steam")
        n = int(ultima)
        if not 1 <= n <= 8:
            raise ValueError("Escolha a partida de 1 a 8.")
        return self._iniciar("download", ["cs2clip.baixar_demo", "ultimas", "--baixar", str(n)], f"Baixando a partida {n} das últimas 8")

    def iniciar_gravacao(self, nome: str, rounds: str = "", todos: bool = False, editar: bool = False, jogador: str = "") -> dict[str, Any]:
        demo = self.caminho_da_demo(nome)
        jogador = _jogador_valido(jogador)
        args = ["cs2clip.gravador_demo", "--demo", str(demo)]
        if jogador:
            args.append(f"--jogador={jogador}")      # com "=": um nome que comece com "-" não vira opção
        if rounds:
            if not re.fullmatch(r"\d{1,3}(,\d{1,3}){0,40}", rounds.strip()):
                raise ValueError("Os rounds precisam ser números separados por vírgula (ex.: 3,8,9).")
            args += ["--rounds", rounds.strip()]
        elif todos:
            args += ["--todos"]
        if editar:
            args += ["--editar"]
        return self._iniciar("gravacao", args, f"Gravando {nome}" + (f" (câmera em {jogador})" if jogador else ""))

    def cancelar(self) -> dict[str, Any]:
        with self._lock:
            if not self._ocupado() or self._job is None:   # (`_ocupado` já implica `_job`)
                raise RuntimeError("Não há trabalho em andamento para cancelar.")
            self._job["cancelado"] = True
            proc = self._proc                  # None = o processo ainda está abrindo: o `_iniciar` para ele logo que ele existir (G-014)
            job = self._job
        if proc is not None:
            cancelar_processo(proc)       # pedido educado (o gravador fecha o jogo) e, se não der, a ÁRVORE do trabalho cai (o ffmpeg neto não sobra; N3b)
        with self._lock:
            return dict(job)

    def estado(self) -> dict[str, Any]:
        with self._lock:
            job = dict(self._job) if self._job else None
            linhas = list(self._linhas[-60:])
        return {"job": job, "linhas": linhas}

    def resultados(self, nome: str, jogador: str = "") -> dict[str, Any]:
        """O `registro.json` da última gravação dessa demo (rounds gravados, velocidade, guarda) e a pasta dos vídeos. `jogador` (steamid) =
        a gravação com a câmera nesse jogador (pasta própria, A2); vazio = a sua."""
        from cs2clip.demo import pasta_da_partida
        jogador = (jogador or "").strip()
        if jogador and not jogador.isdigit():
            raise ValueError("Jogador inválido.")
        pasta = self.raiz / "gravacao_demo" / pasta_da_partida(Path(nome).stem, jogador or None)
        arq = pasta / "registro.json"
        try:
            dados = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"existe": False}
        return {"existe": True, "pasta": str(pasta / "rounds"), "resultados": dados.get("resultados", []), "erro": dados.get("erro"),
                "alertas": dados.get("alertas", []), "guarda": dados.get("guarda")}
