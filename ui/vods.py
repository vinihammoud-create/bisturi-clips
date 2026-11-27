"""C4: baixar o VOD da Twitch pelo link, em segundo plano, para a pasta entrada/ (um download de cada vez).

E4 (revisão do Opus, 08/11/2026): o download tem tempo máximo (`TEMPO_MAXIMO_S`, depois disso a API travou) e pode ser cancelado."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any
from collections.abc import Callable

from cs2clip.twitch_vod import CLI, arquivo_de_destino, comando_download, linha_de_erro, mensagem_de_erro, percentual_da_linha, vod_id_de_link
from cs2clip.tools import sem_janela, substituir_arquivo

from .coordenador import derrubar_arvore

TEMPO_MAXIMO_S = 6 * 3600     # VOD de várias horas; passou disso a API travou


class GerenciadorVod:
    def __init__(self, entrada: Path, popen_factory: Callable[..., Any] | None = None, cli: Path | None = None,
                 tempo_maximo: float = TEMPO_MAXIMO_S):
        self.entrada = Path(entrada)
        self._popen = popen_factory or subprocess.Popen
        self._cli = Path(cli) if cli else CLI
        self.tempo_maximo = tempo_maximo
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._procs: dict[str, Any] = {}
        self._motivo_parada: dict[str, str] = {}
        self._threads: dict[str, threading.Thread] = {}

    def iniciar(self, link: str) -> dict[str, Any]:
        """Valida o link e começa a baixar. Levanta ValueError (link inválido), FileNotFoundError (sem o baixador) ou
        RuntimeError (já há um download em andamento ou o mesmo VOD já foi pedido)."""
        vod = vod_id_de_link(link)
        if vod is None:
            raise ValueError("Cole o link de um VOD da Twitch, no formato twitch.tv/videos/123456789.")
        if not self._cli.is_file():
            raise FileNotFoundError("O baixador da Twitch (bin/TwitchDownloaderCLI.exe) não está no projeto.")
        with self._lock:
            if any(j["estado"] == "baixando" for j in self._jobs.values()):
                raise RuntimeError("Já existe um download em andamento. Espere terminar ou cancele.")
            destino = arquivo_de_destino(vod, self.entrada)
            if destino.is_file():
                raise RuntimeError(f"O VOD {vod} já está na pasta entrada ({destino.name}).")
            self._limpar_restos(destino)   # RF-04: resto de um download interrompido (o baixador usa --collision Rename e gravaria noutro nome)
            job = {"id": uuid.uuid4().hex[:8], "vod": vod, "estado": "baixando", "percentual": 0, "arquivo": destino.name, "erro": ""}
            self._jobs[job["id"]] = job
            fio = threading.Thread(target=self._executar, args=(job, destino), daemon=True)
            self._threads[job["id"]] = fio
        fio.start()
        return dict(job)

    def _limpar_restos(self, destino: Path) -> None:
        """Sem download em andamento (quem chama já conferiu): apaga o `.parcial` deste VOD e a pasta temporária do baixador."""
        destino.with_name(destino.name + ".parcial").unlink(missing_ok=True)
        shutil.rmtree(self.entrada / ".baixando", ignore_errors=True)

    def _marcar_parada(self, job_id: str, motivo: str) -> Any:
        self._motivo_parada[job_id] = motivo
        return self._procs.get(job_id)

    def _parar(self, job_id: str, motivo: str) -> None:
        """Pede ao baixador para parar (cancelamento ou tempo esgotado). Quem trata o resultado é `_executar`.
        R-012: cai a ÁRVORE do baixador (o TwitchDownloaderCLI junta os pedaços com o FFmpeg, um neto que o `terminate()` deixava gravando)."""
        derrubar_arvore(self._marcar_parada(job_id, motivo))

    def cancelar(self, job_id: str | None = None) -> dict[str, Any]:
        """Cancela o download em andamento. Com `job_id`, só se for ESSE (G-004: a receita cancela o próprio VOD, nunca o de outro pedido).
        Levanta RuntimeError se não há nenhum (ou se o em andamento é outro)."""
        with self._lock:
            ativo = next((j for j in self._jobs.values() if j["estado"] == "baixando"), None)
            if ativo is None:
                raise RuntimeError("Não há download em andamento para cancelar.")
            if job_id is not None and ativo["id"] != job_id:
                raise RuntimeError("Esse download não está mais em andamento.")
            proc = self._marcar_parada(ativo["id"], "cancelado")
            copia = dict(ativo)
        derrubar_arvore(proc)                      # o taskkill (até 10 s) fora da trava: a tela segue perguntando o progresso
        return copia

    def _executar(self, job: dict[str, Any], destino: Path) -> None:
        # M2: o baixador decide o formato pela extensão do `-o` e só aceita .mp4/.m4a (com ".mp4.parcial" ele recusava em 6 s e nunca baixou nada). O parcial é
        # um .mp4 DENTRO da pasta escondida `.baixando` (a lista de vídeos da tela só olha `entrada\` e não a enxerga); o cache do baixador fica noutra subpasta.
        temporaria = self.entrada / ".baixando"
        parcial = temporaria / destino.name
        cache = temporaria / "cache"
        relogio = threading.Timer(self.tempo_maximo, self._parar, args=(job["id"], "tempo"))
        relogio.daemon = True
        resultado: dict[str, Any] = {}      # o estado final só é publicado DEPOIS da limpeza: "pronto" nunca aparece com restos na pasta
        try:
            cache.mkdir(parents=True, exist_ok=True)
            cmd = comando_download(job["vod"], parcial, cache)
            cmd[0] = str(self._cli)
            proc = self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                              **sem_janela())
            self._procs[job["id"]] = proc
            relogio.start()
            if self._motivo_parada.get(job["id"]):   # cancelou antes de o processo existir
                derrubar_arvore(proc)
            recentes: deque[str] = deque(maxlen=15)
            primeira_de_erro = ""        # a linha que explica vem no COMEÇO do rastro: as 15 últimas não bastam (um rastro longo a empurra para fora)
            for linha in proc.stdout or []:
                if linha.strip():
                    recentes.append(linha.strip())
                    if not primeira_de_erro and linha_de_erro(linha):
                        primeira_de_erro = linha.strip()
                    # (a linha de progresso não conta: só exceção ou [ERROR])
                p = percentual_da_linha(linha)
                if p is not None:
                    job["percentual"] = p
            codigo = proc.wait()
            motivo = self._motivo_parada.get(job["id"])
            if motivo == "cancelado":
                resultado = {"estado": "cancelado", "erro": ""}
            elif motivo == "tempo":
                resultado = {"estado": "erro", "erro": f"o download passou do tempo máximo ({self.tempo_maximo / 3600:g} h) e foi encerrado"}
            elif codigo == 0 and parcial.is_file() and parcial.stat().st_size > 100_000:
                substituir_arquivo(parcial, destino)
                resultado = {"estado": "pronto", "percentual": 100}
            else:
                # a primeira linha de erro só entra se as 15 últimas NÃO têm nenhuma: senão uma `[ERROR] … retrying` passageira do começo vence a falha de verdade do fim
                vistas = ([primeira_de_erro] if primeira_de_erro and not any(linha_de_erro(x) for x in recentes) else []) + list(recentes)
                resultado = {"estado": "erro", "erro": mensagem_de_erro(vistas, codigo)}
        except Exception as e:  # noqa: BLE001
            resultado = {"estado": "erro", "erro": str(e)[:300]}
        finally:
            relogio.cancel()
            self._procs.pop(job["id"], None)
            try:
                parcial.unlink(missing_ok=True)
            except OSError:
                pass                                         # antivírus/ffmpeg segurando o arquivo: a limpeza falhar NUNCA pode deixar o job preso em "baixando"
            shutil.rmtree(temporaria, ignore_errors=True)    # RF-04: a pasta temporária do baixador não fica para trás (cancelou, deu erro ou terminou)
            job.update(resultado or {"estado": "erro", "erro": "o download foi interrompido"})

    def listar(self) -> dict[str, Any]:
        with self._lock:
            return {"itens": [dict(j) for j in self._jobs.values()]}

    def baixando(self) -> bool:
        with self._lock:
            return any(j["estado"] == "baixando" for j in self._jobs.values())

    def parar(self) -> None:
        """Fim do programa: cancela o download em andamento, se houver e se ainda não foi pedido."""
        with self._lock:
            ativo = next((j for j in self._jobs.values() if j["estado"] == "baixando"), None)
            if ativo is None or self._motivo_parada.get(ativo["id"]):
                return
            proc = self._marcar_parada(ativo["id"], "cancelado")
        derrubar_arvore(proc)

    def esperar_fim(self, prazo_s: float) -> None:
        """Espera o download parar e a pasta temporária ser limpa; passado o prazo, a árvore do baixador cai."""
        fim = time.monotonic() + max(0.0, prazo_s)
        with self._lock:
            fios = list(self._threads.items())
        for job_id, fio in fios:
            fio.join(max(0.0, fim - time.monotonic()))
            if fio.is_alive():
                derrubar_arvore(self._procs.get(job_id))
                fio.join(2.0)
