"""Leitura tolerante dos artefatos e edição limitada de momentos.json."""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable

from .seguranca import EXTENSOES_ENTRADA, caminho_dentro, caminho_midia_seguro, id_de_video_suspeito
from cs2clip.tools import substituir_arquivo


def ler_json(caminho: Path, padrao: Any) -> Any:
    try:
        with caminho.open("r", encoding="utf-8-sig") as arquivo:
            return json.load(arquivo)
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError):
        return padrao


def duracao_segundos(valor: Any) -> float:
    try:
        numero = float(valor)
        return numero if numero >= 0 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0



# B6: voto editorial além do "Gostei/Descartar"
MOTIVOS_REJEICAO = ("comum", "repetido", "sem_contexto", "corte_ruim", "hud_ilegivel", "audio_ruim", "falso_positivo", "outro")
CRITERIOS_AVALIACAO = ("impacto", "clareza", "contexto", "ritmo", "audio", "novidade")


def _avaliacao_valida(av: Any) -> dict[str, int]:
    """{critério: 0–3}: só os critérios conhecidos, só inteiros de 0 a 3, pelo menos um."""
    if not isinstance(av, dict) or not av:
        raise ValueError("Avaliação inválida.")
    limpa: dict[str, int] = {}
    for chave, valor in av.items():
        if chave not in CRITERIOS_AVALIACAO or isinstance(valor, bool) or not isinstance(valor, int) or not 0 <= valor <= 3:
            raise ValueError("Avaliação inválida: use os critérios conhecidos com notas inteiras de 0 a 3.")
        limpa[chave] = valor
    return limpa


ESTADOS_QUALIDADE = ("PASS", "PASS_WITH_WARNINGS", "NEEDS_REVIEW", "FAIL")
AJUSTE_MAX = 6.0   # s: quanto o dono pode mexer no início ou no fim de um corte


def _ajuste_valido(aj: Any) -> dict[str, float] | None:
    """{"inicio": s, "fim": s}, cada um entre -6 e +6, arredondado a 0,1 s. Os dois zero = sem ajuste (None)."""
    if not isinstance(aj, dict) or set(aj) != {"inicio", "fim"}:
        raise ValueError("Ajuste inválido.")
    limpo = {}
    for chave, v in aj.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > AJUSTE_MAX:
            raise ValueError(f"Ajuste inválido: use números entre -{AJUSTE_MAX:g} e {AJUSTE_MAX:g} segundos.")
        limpo[chave] = round(float(v), 1) + 0.0
    return None if limpo["inicio"] == 0.0 and limpo["fim"] == 0.0 else limpo


class DadosProjeto:
    def __init__(self, raiz: Path):
        self.raiz = raiz.resolve()
        self.saida = self.raiz / "saida"
        self.entrada = self.raiz / "entrada"
        self._backups_feitos: set[Path] = set()
        self._lock = threading.RLock()
        self.saida.mkdir(parents=True, exist_ok=True)
        self.entrada.mkdir(parents=True, exist_ok=True)
        self._limpar_sobras_de_apagar()

    def _limpar_sobras_de_apagar(self) -> list[Path]:
        """R-009: um "Apagar kit" que falhou no meio (arquivo preso pelo antivírus ou pelo player) deixa `saida/.apagando-<id>-...` com GBs de
        vídeo que o índice já esqueceu. Na abertura e a cada apagar, tenta de novo. Junção ou link nunca é seguido. Devolve o que sobrou."""
        sobras = []
        try:
            candidatas = [p for p in self.saida.iterdir() if p.name.startswith(".apagando-")]
        except OSError:
            return sobras
        for pasta in candidatas:
            try:
                if pasta.is_symlink() or os.path.isjunction(pasta):
                    continue
                if pasta.is_dir():
                    shutil.rmtree(pasta, ignore_errors=True)
                if pasta.exists():
                    sobras.append(pasta)
            except OSError:
                sobras.append(pasta)
        return sobras

    def _arquivo_saida_seguro(self, nome: str) -> Path:
        alvo = caminho_dentro(self.saida, self.saida / nome)
        if not alvo.is_file():
            raise FileNotFoundError(nome)
        return alvo

    def indice(self) -> list[dict[str, Any]]:
        try:
            indice_path = self._arquivo_saida_seguro("indice.json")
        except (FileNotFoundError, PermissionError):
            return []
        dados = ler_json(indice_path, [])
        if not isinstance(dados, list):
            return []
        return [item for item in dados if isinstance(item, dict)]

    def qualidade(self, video_id: str) -> dict[str, Any] | None:
        """Resumo seguro do `qualidade.json` do vídeo (S1.6): estado geral e, por arquivo, só os avisos (texto curto).
        Arquivo ausente, ilegível ou com estado desconhecido = None (a tela não mostra nada, em vez de mostrar errado)."""
        try:
            dados = ler_json(self.pasta_video(video_id) / "qualidade.json", None)
        except (FileNotFoundError, PermissionError):
            return None
        if not isinstance(dados, dict) or dados.get("status") not in ESTADOS_QUALIDADE or not isinstance(dados.get("arquivos"), dict):
            return None
        arquivos = []
        for nome, v in list(dados["arquivos"].items())[:12]:
            if not isinstance(v, dict) or v.get("status") not in ESTADOS_QUALIDADE:
                continue
            avisos = []
            for g in (v.get("gates") or [])[:12]:
                if isinstance(g, dict) and g.get("status") not in ("PASS", "NOT_RUN") and g.get("motivo"):
                    avisos.append(f"{str(g.get('nome', ''))[:30]}: {str(g['motivo'])}"[:300])
            arquivos.append({"nome": str(nome)[:120], "status": v["status"], "avisos": avisos})
        return {"status": dados["status"], "arquivos": arquivos}

    def nada_para_postar(self, video_id: str) -> str | None:
        """E6: a frase que o programa gravou quando nenhum round tem abates suficientes para montagem ou short (`NADA_PARA_POSTAR.txt`)."""
        try:
            texto = (self.pasta_video(video_id) / "NADA_PARA_POSTAR.txt").read_text(encoding="utf-8", errors="replace").strip()
        except (OSError, PermissionError):
            return None
        return texto[:400] or None

    def pasta_video(self, video_id: str) -> Path:
        if not isinstance(video_id, str) or not video_id or video_id in {".", ".."} or any(c in video_id for c in ("/", "\\", ":", "\x00")):
            raise PermissionError("Identificador de vídeo inválido.")
        if id_de_video_suspeito(video_id):      # G-009: `kit.` e `kit ` viram `kit` no Windows (apagaria a pasta e deixaria o índice errado); `.cache` é oculta
            raise PermissionError("Identificador de vídeo inválido.")
        pasta = caminho_dentro(self.saida, self.saida / video_id)
        if not pasta.is_dir():
            raise FileNotFoundError("Vídeo não encontrado.")
        return pasta

    def apagar_kit(self, video_id: str) -> dict[str, Any]:
        """Botão "Apagar kit" da Biblioteca: apaga SÓ a pasta `saida/<id>` e tira o kit do índice.
        A fonte (VOD, gravação, demo) não é tocada. A pasta é renomeada antes de apagar: se algum arquivo estiver
        aberto (o vídeo tocando), o Windows recusa a troca de nome e nada é apagado pela metade."""
        from cs2clip.indice import remover_do_indice
        with self._lock:
            try:
                pasta: Path | None = self.pasta_video(video_id)
            except FileNotFoundError:
                pasta = None                       # só a linha do índice sobrou: tira do índice mesmo assim
            if pasta is None and not any(x.get("id") == video_id for x in self.indice()):
                raise FileNotFoundError("Vídeo não encontrado.")
            liberado = 0
            sobrou = False
            self._limpar_sobras_de_apagar()
            if pasta is not None:
                for arq in pasta.rglob("*"):
                    try:
                        if arq.is_file():
                            liberado += arq.stat().st_size
                    except OSError:
                        pass
                lixo = self.saida / f".apagando-{video_id}-{os.getpid()}-{int(datetime.now().timestamp())}"
                try:
                    pasta.rename(lixo)
                except OSError as exc:
                    raise RuntimeError("Não consegui apagar: algum arquivo deste kit está aberto. Feche o vídeo ou a pasta e tente de novo.") from exc
                shutil.rmtree(lixo, ignore_errors=True)
                sobrou = lixo.exists()           # algo ficou preso: a próxima abertura (ou o próximo apagar) tenta de novo
            remover_do_indice(self.saida, [video_id])
            return {"apagado": video_id, "liberado": liberado, "sobrou": sobrou}

    def momentos(self, video_id: str) -> dict[str, Any]:
        pasta = self.pasta_video(video_id)
        try:
            momentos_path = caminho_dentro(pasta, pasta / "momentos.json")
        except PermissionError:
            return {"formato": 1, "video": video_id, "momentos": []}
        dados = ler_json(momentos_path, {})
        if not isinstance(dados, dict):
            return {"formato": 1, "video": video_id, "momentos": []}
        if not isinstance(dados.get("momentos"), list):
            dados["momentos"] = []
        else:
            dados["momentos"] = [m for m in dados["momentos"] if isinstance(m, dict)]
        return dados

    def partes(self, video_id: str) -> list[dict[str, Any]]:
        pasta = self.pasta_video(video_id)
        dados = ler_json(pasta / "partes.json", [])
        if isinstance(dados, dict):
            # O formato descrito é uma lista. Não inventar uma conversão a partir de objeto.
            return []
        if not isinstance(dados, list):
            return []
        return [p for p in dados if isinstance(p, dict)]

    def demo(self, video_id: str) -> dict[str, Any] | None:
        pasta = self.pasta_video(video_id)
        try:
            caminho = caminho_dentro(pasta, pasta / "demo.json")
        except PermissionError:
            return None
        if not caminho.is_file():
            return None
        dados = ler_json(caminho, None)
        return dados if isinstance(dados, dict) else None

    def publicar(self, video_id: str) -> dict[str, Any] | None:
        """publicar.json do núcleo (título, hashtags e descrição de cada short); None se não houver."""
        pasta = self.pasta_video(video_id)
        try:
            caminho = caminho_dentro(pasta, pasta / "publicar.json")
        except PermissionError:
            return None
        if not caminho.is_file():
            return None
        dados = ler_json(caminho, None)
        if not isinstance(dados, dict):
            return None
        itens = dados.get("itens")
        dados["itens"] = [item for item in itens if isinstance(item, dict)] if isinstance(itens, list) else []
        return dados

    def _miniatura_valida(self, video_id: str, relativo: Any) -> str | None:
        # Mesmas regras de /media (sem .., sem .cache, só dentro da pasta do vídeo), e só imagem.
        if not isinstance(relativo, str) or Path(relativo).suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            return None
        try:
            caminho_midia_seguro(self.saida, video_id, relativo)
        except (FileNotFoundError, PermissionError):
            return None
        return relativo

    def capa(self, video_id: str) -> str | None:
        """Miniatura do cartão da biblioteca: a do melhor short; sem short, a do momento de maior nota."""
        try:
            self.pasta_video(video_id)
            publicar = self.publicar(video_id) or {}
            documento = self.momentos(video_id)
        except (FileNotFoundError, PermissionError):
            return None
        for item in publicar.get("itens", []):
            achada = self._miniatura_valida(video_id, item.get("capa"))
            if achada:
                return achada

        def nota(momento: dict[str, Any]) -> float:
            try:
                return float(momento.get("nota") or 0)
            except (TypeError, ValueError, OverflowError):
                return 0.0

        for momento in sorted(documento.get("momentos", []), key=nota, reverse=True):
            if momento.get("excluido"):
                continue
            arquivos = momento.get("arquivos")
            achada = self._miniatura_valida(video_id, arquivos.get("miniatura")) if isinstance(arquivos, dict) else None
            if achada:
                return achada
        return None

    def progresso(self) -> dict[str, Any]:
        try:
            caminho = self._arquivo_saida_seguro("progresso.json")
        except (FileNotFoundError, PermissionError):
            return {}
        dados = ler_json(caminho, {})
        return dados if isinstance(dados, dict) else {}

    def linhas_log(self, quantidade: int = 30, desde: float | None = None) -> list[str]:
        """Últimas linhas do log.txt, só da execução mais recente: o Registro acrescenta ao arquivo e abre cada
        execução com um cabeçalho '===== data =====' (senão o erro de um vídeo de ontem apareceria no de hoje).
        Com `desde` (instante em que o job começou), uma execução mais velha que ele não é do job: devolve vazio."""
        try:
            caminho = self._arquivo_saida_seguro("log.txt")
            with caminho.open("r", encoding="utf-8", errors="replace") as arquivo:
                linhas = arquivo.readlines()
        except OSError:
            return []
        for i in range(len(linhas) - 1, -1, -1):
            if linhas[i].startswith("===== ") and linhas[i].rstrip().endswith(" ====="):
                linhas = linhas[i:]
                if desde is not None:
                    try:
                        gravado = datetime.strptime(linhas[0].strip().strip("=").strip(), "%Y-%m-%d %H:%M:%S").timestamp()
                    except (ValueError, OverflowError, OSError):
                        break
                    if gravado < desde - 1:
                        return []
                break
        return linhas[-max(1, min(int(quantidade), 200)):]

    def listar_entrada(self) -> list[dict[str, Any]]:
        self.entrada.mkdir(parents=True, exist_ok=True)
        resultado: list[dict[str, Any]] = []
        try:
            itens = sorted(self.entrada.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            return resultado
        for item in itens:
            try:
                if not item.is_symlink() and item.is_file() and item.suffix.lower() in EXTENSOES_ENTRADA:
                    resultado.append({"nome": item.name, "tamanho": item.stat().st_size, "tipo": "demo" if item.suffix.lower() == ".dem" else "video"})
            except OSError:
                continue
        return resultado

    def marcar_momento(
        self,
        video_id: str,
        foco: Any,
        acao: str,
        pode_editar: Callable[[], bool],
        motivo: Any = None,
        avaliacao: Any = None,
        ajuste: Any = None,
    ) -> dict[str, Any]:
        if not pode_editar():
            raise RuntimeError("Espere terminar o processamento e a fila.")
        if acao not in {"aprovar", "desaprovar", "descartar", "restaurar", "motivo", "avaliar", "ajustar"}:
            raise ValueError("Ação não reconhecida.")
        if acao == "motivo" and not (motivo in (None, "") or motivo in MOTIVOS_REJEICAO):
            raise ValueError("Motivo de rejeição não reconhecido.")
        if acao == "avaliar":
            avaliacao = _avaliacao_valida(avaliacao)
        if acao == "ajustar":
            ajuste = _ajuste_valido(ajuste)
        try:
            foco_alvo = float(foco)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Momento inválido.") from exc
        pasta = self.pasta_video(video_id)
        caminho = pasta / "momentos.json"
        with self._lock:
            try:
                original = caminho.read_text(encoding="utf-8-sig")
                documento = json.loads(original)
            except PermissionError as exc:
                raise ValueError("Não consegui ler momentos.json com segurança. Nenhuma alteração foi salva.") from exc
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                raise ValueError("Não consegui ler momentos.json. Nenhuma alteração foi salva.") from exc
            if not isinstance(documento, dict) or not isinstance(documento.get("momentos"), list):
                raise ValueError("O arquivo de momentos está incompleto. Nenhuma alteração foi salva.")
            encontrado = None
            for momento in documento["momentos"]:
                if not isinstance(momento, dict):
                    continue
                bruto = momento.get("foco")
                if bruto is None:
                    continue
                try:
                    foco_momento = float(bruto)
                except (TypeError, ValueError, OverflowError):
                    continue
                if abs(foco_momento - foco_alvo) <= 0.05:
                    encontrado = momento
                    break
            if encontrado is None:
                raise FileNotFoundError("Não encontrei essa jogada. Atualize a tela e tente novamente.")
            if acao == "aprovar":
                encontrado["aprovado"] = True
            elif acao == "desaprovar":
                encontrado["aprovado"] = None   # volta a "não revisado" (os outros leitores já tratam null assim)
            elif acao == "descartar":
                encontrado["excluido"] = True
            elif acao == "restaurar":
                encontrado["excluido"] = False
                encontrado["motivo_rejeicao"] = None
            elif acao == "motivo":
                encontrado["motivo_rejeicao"] = motivo or None
            elif acao == "ajustar":
                encontrado["ajuste"] = ajuste
            else:   # "avaliar"
                encontrado["avaliacao"] = avaliacao

            # Um backup por arquivo durante cada sessão do servidor.
            if caminho not in self._backups_feitos:
                backup = caminho.with_name("momentos.json.bak")
                shutil.copy2(caminho, backup)
                self._backups_feitos.add(caminho)
            conteudo = json.dumps(documento, ensure_ascii=False, indent=1) + "\n"
            fd, temp_nome = tempfile.mkstemp(prefix="momentos-", suffix=".tmp", dir=str(pasta))
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as temporario:
                    temporario.write(conteudo)
                    temporario.flush()
                    os.fsync(temporario.fileno())
                substituir_arquivo(Path(temp_nome), caminho)
            except Exception:
                try:
                    os.unlink(temp_nome)
                except OSError:
                    pass
                raise
        return {"sucesso": True, "momento": encontrado}
