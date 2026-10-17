"""Leitura tolerante dos artefatos e edição limitada de momentos.json."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from .seguranca import caminho_dentro, caminho_midia_seguro


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


class DadosProjeto:
    def __init__(self, raiz: Path):
        self.raiz = raiz.resolve()
        self.saida = self.raiz / "saida"
        self.entrada = self.raiz / "entrada"
        self._backups_feitos: set[Path] = set()
        self._lock = threading.RLock()
        self.saida.mkdir(parents=True, exist_ok=True)
        self.entrada.mkdir(parents=True, exist_ok=True)

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

    def pasta_video(self, video_id: str) -> Path:
        if not isinstance(video_id, str) or not video_id or video_id in {".", ".."} or any(c in video_id for c in ("/", "\\", ":", "\x00")):
            raise PermissionError("Identificador de vídeo inválido.")
        pasta = caminho_dentro(self.saida, self.saida / video_id)
        if not pasta.is_dir():
            raise FileNotFoundError("Vídeo não encontrado.")
        return pasta

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

    def linhas_log(self, quantidade: int = 30) -> list[str]:
        try:
            caminho = self._arquivo_saida_seguro("log.txt")
            with caminho.open("r", encoding="utf-8", errors="replace") as arquivo:
                return arquivo.readlines()[-max(1, min(int(quantidade), 200)):]
        except OSError:
            return []

    def listar_entrada(self) -> list[dict[str, Any]]:
        self.entrada.mkdir(parents=True, exist_ok=True)
        resultado: list[dict[str, Any]] = []
        try:
            itens = sorted(self.entrada.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            return resultado
        for item in itens:
            try:
                if not item.is_symlink() and item.is_file() and item.suffix.lower() in {".mp4", ".mkv", ".mov", ".flv", ".ts", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".mts", ".m2ts", ".3gp", ".ogv", ".dem"}:
                    resultado.append({"nome": item.name, "tamanho": item.stat().st_size, "tipo": "demo" if item.suffix.lower() == ".dem" else "video"})
            except OSError:
                continue
        return resultado

    def marcar_momento(
        self,
        video_id: str,
        foco: float,
        acao: str,
        pode_editar: Callable[[], bool],
    ) -> dict[str, Any]:
        if not pode_editar():
            raise RuntimeError("Espere terminar o processamento e a fila.")
        if acao not in {"aprovar", "descartar", "restaurar"}:
            raise ValueError("Ação não reconhecida.")
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
                try:
                    foco_momento = float(momento.get("foco"))
                except (TypeError, ValueError, OverflowError):
                    continue
                if abs(foco_momento - foco_alvo) <= 0.05:
                    encontrado = momento
                    break
            if encontrado is None:
                raise FileNotFoundError("Não encontrei essa jogada. Atualize a tela e tente novamente.")
            if acao == "aprovar":
                encontrado["aprovado"] = True
            elif acao == "descartar":
                encontrado["excluido"] = True
            else:
                encontrado["excluido"] = False

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
                os.replace(temp_nome, caminho)
            except Exception:
                try:
                    os.unlink(temp_nome)
                except OSError:
                    pass
                raise
        return {"sucesso": True, "momento": encontrado}
