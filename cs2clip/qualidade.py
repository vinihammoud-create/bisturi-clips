"""Relatório de qualidade da entrega (A5, Plano V7 integrado): um registro por conferência ("gate") de cada arquivo
final (montagem, FAILS), gravado em `saida/<vídeo>/qualidade.json`.

Regras que importam:
  * `NOT_RUN` (não rodou) NUNCA vale como aprovado: se uma conferência obrigatória não rodou, o arquivo fica
    `NEEDS_REVIEW`; só conferência opcional desligada pelo dono (ex.: silêncio) pode ficar `NOT_RUN` sem custo;
  * este relatório só REGISTRA. O que já bloqueava um arquivo ruim (`qc.exigir`, no momento do render) continua
    exatamente igual; a única conferência nova que pode reprovar é a de volume/pico, e só com
    `qualidade.gate_loudness = true` (desligada por padrão).
Estados de uma conferência: PASS, WARNING, NEEDS_REVIEW, FAIL, NOT_RUN. Estado do arquivo/geral: PASS,
PASS_WITH_WARNINGS, NEEDS_REVIEW, FAIL."""
from __future__ import annotations

import dataclasses
import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import qc
from .tools import gravar_atomico

PICO_MAX_DBTP = -1.0       # padrão de YouTube/TikTok/Instagram
DESVIO_LUFS_MAX = 2.0      # LU de diferença para o alvo (`qualidade.lufs`) antes de avisar


@dataclass
class Gate:
    nome: str
    status: str
    medido: Any
    esperado: str
    motivo: str
    ferramenta: str
    obrigatorio: bool = True


def falha(motivo: str, nome: str = "integridade") -> Gate:
    """O arquivo nem foi gerado (ou o render quebrou): fica registrado como FAIL, com o motivo."""
    return Gate(nome, "FAIL", None, "arquivo gerado e conferido", motivo, "render")


def gate_integridade(caminho: Path, esp: qc.Esperado) -> Gate:
    """ffprobe: tamanho, duração, fps, cor e trilha de som (o mesmo `qc.verificar` do render, no arquivo já no lugar
    final). A conferência de conteúdo (preto/mudo) é outro gate."""
    esp = dataclasses.replace(esp, conteudo=False, silencio=False)
    problemas = qc.verificar(Path(caminho), esp)
    if problemas:
        return Gate("integridade", "FAIL", None, "ffprobe dentro do esperado", "; ".join(problemas), "ffprobe")
    return Gate("integridade", "PASS", None, "ffprobe dentro do esperado", "", "ffprobe")


def gate_audio(medida: tuple[float, float] | None, audio_esperado: bool, lufs_alvo: float, rigido: bool) -> Gate:
    esperado = f"{lufs_alvo:.0f} LUFS (±{DESVIO_LUFS_MAX:.0f}) e pico ≤ {PICO_MAX_DBTP:.0f} dBTP depois do AAC"
    if not audio_esperado:
        return Gate("audio", "NOT_RUN", None, esperado, "o arquivo não deveria ter som", "ebur128", obrigatorio=False)
    if medida is None:
        return Gate("audio", "NOT_RUN", None, esperado, "não consegui medir o volume do arquivo", "ebur128")
    lufs, pico = medida
    problemas = []
    if pico > PICO_MAX_DBTP:
        problemas.append(f"pico real {pico:.1f} dBTP acima de {PICO_MAX_DBTP:.0f}")
    if abs(lufs - lufs_alvo) > DESVIO_LUFS_MAX:
        problemas.append(f"volume {lufs:.1f} LUFS longe do alvo {lufs_alvo:.0f}")
    medido = {"lufs": round(lufs, 1), "pico_dbtp": round(pico, 1)}
    if not problemas:
        return Gate("audio", "PASS", medido, esperado, "", "ebur128")
    return Gate("audio", "FAIL" if rigido else "WARNING", medido, esperado, "; ".join(problemas), "ebur128")


def gate_conteudo(caminho: Path, checar: bool, silencio: bool) -> Gate:
    esperado = "vídeo com imagem" + (" e som" if silencio else "")
    if not checar:
        return Gate("conteudo", "NOT_RUN", None, esperado, "conferência de conteúdo desligada (qualidade.checar_conteudo)",
                    "blackdetect", obrigatorio=False)
    problemas = qc.verificar_conteudo(Path(caminho), silencio=silencio)
    if problemas:
        # conferência que não conseguiu rodar (o ffmpeg dela falhou ou travou) não prova defeito no vídeo: pede revisão, não reprova
        so_nao_conferiu = all(p.startswith(qc.NAO_CONFERIU) for p in problemas)
        return Gate("conteudo", "NEEDS_REVIEW" if so_nao_conferiu else "FAIL", None, esperado, "; ".join(problemas), "blackdetect")
    return Gate("conteudo", "PASS", None, esperado, "", "blackdetect")


def status_geral(gates: list[Gate]) -> str:
    """FAIL > NEEDS_REVIEW (inclui conferência obrigatória que não rodou) > PASS_WITH_WARNINGS > PASS.
    Lista vazia = nada foi conferido = NEEDS_REVIEW."""
    if not gates:
        return "NEEDS_REVIEW"
    estados = {g.status for g in gates}
    if "FAIL" in estados:
        return "FAIL"
    if "NEEDS_REVIEW" in estados or any(g.status == "NOT_RUN" and g.obrigatorio for g in gates):
        return "NEEDS_REVIEW"
    if "WARNING" in estados:
        return "PASS_WITH_WARNINGS"
    return "PASS"


_GRAVIDADE = {"PASS": 0, "PASS_WITH_WARNINGS": 1, "NEEDS_REVIEW": 2, "FAIL": 3}


def gravar(out: Path, arquivos: dict[str, list[Gate]]) -> str:
    """Grava `qualidade.json` e devolve o estado geral (o pior entre os arquivos). Nunca derruba o processamento."""
    por_arquivo = {nome: {"status": status_geral(g), "gates": [dataclasses.asdict(x) for x in g]}
                   for nome, g in arquivos.items()}
    geral = max((v["status"] for v in por_arquivo.values()), key=_GRAVIDADE.__getitem__, default="NEEDS_REVIEW")
    dados = {"formato": 1, "gerado_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "status": geral, "arquivos": por_arquivo}
    try:
        gravar_atomico(Path(out) / "qualidade.json", json.dumps(dados, ensure_ascii=False, indent=1))
    except OSError:
        pass
    return geral
