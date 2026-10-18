"""`--refazer`: quais etapas da análise ignoram o cache e são feitas de novo.

    --refazer                 tudo (como era)
    --refazer falas           só a transcrição (Whisper)
    --refazer kills,voz       kill feed + voz

Etapas: kills (kill feed), rounds (banner de fim de round), mortes (painel "matou você"), voz, falas, chat,
webcam, cortes.
`kills` e `rounds` saem da MESMA passada de quadros-chave: pedir uma das duas refaz a varredura.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict
from pathlib import Path

from .tools import gravar_atomico

ETAPAS =("kills", "rounds", "mortes", "voz", "falas", "chat", "webcam", "cortes")


def parse_refazer(valor: str | bool | frozenset[str] | set[str] | None) -> frozenset[str]:
    """None/False -> nada; True, "", "tudo" -> todas; "falas,voz" -> {falas, voz}; um conjunto já pronto
    passa direto (o `analyze` recebe o que o `main` já converteu). Nome desconhecido levanta ValueError
    com a lista do que vale."""
    if isinstance(valor, (set, frozenset)):
        return frozenset(valor)
    if valor is None or valor is False:
        return frozenset()
    if valor is True:
        return frozenset(ETAPAS)
    nomes = [n.strip().lower() for n in str(valor).split(",") if n.strip()]
    if not nomes or "tudo" in nomes:
        return frozenset(ETAPAS)
    ruins = [n for n in nomes if n not in ETAPAS]
    if ruins:
        raise ValueError(f"etapa(s) desconhecida(s) em --refazer: {', '.join(ruins)}. "
                         f"Use: tudo, {', '.join(ETAPAS)}")
    return frozenset(nomes)


def refaz_varredura(refazer: frozenset[str]) -> bool:
    """O kill feed e os rounds são lidos juntos: qualquer um dos dois pede a varredura de novo."""
    return "kills" in refazer or "rounds" in refazer


# ── render: quando reaproveitar cortes e shorts que já existem ──────────────────────────────

# Só o que muda o PIXEL/SOM do arquivo. Fora de propósito: gerar, quantidade, versao_efeitos, ordem e
# renders_paralelos (ligar/desligar shorts ou montagem não pode mandar todos os cortes de volta para a GPU).
# cortes.antes/depois/estender_reacao já mudam início/fim do momento, que estão na `impressao`.
_CAMPOS_SHORT = ("largura", "altura", "camera_altura", "topo", "marca", "zoom_camera", "zoom_gameplay", "legendas",
                 "selo_jogada", "zoom_no_abate", "duracao_maxima", "antes", "depois", "gancho", "camera_manual")


def _sha(dados) -> str:
    return hashlib.sha1(json.dumps(dados, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]


def hash_corte(cfg) -> str:
    """Impressão da configuração que muda o corte 16:9 (encoder, qualidade, volume e o fade entre trechos)."""
    return _sha({"encoder": cfg.qualidade.encoder, "cq": cfg.qualidade.cq, "lufs": cfg.qualidade.lufs,
                 "transicao": cfg.montagem.transicao})


def hash_short(cfg) -> str:
    """Impressão da configuração que muda o short 9:16: a do corte + os campos de `[shorts]` que mudam o pixel."""
    s = asdict(cfg.shorts)
    return _sha({"corte": hash_corte(cfg), "shorts": {k: s[k] for k in _CAMPOS_SHORT}})


def render_desatualizado(cache: Path, cfg, refazer: frozenset[str]) -> bool:
    """True = refazer todos os cortes e shorts: `--refazer cortes` ou `--refazer falas` (a legenda dos shorts
    vem das falas). Mudança de configuração NÃO entra aqui: cada arquivo leva `hash_corte`/`hash_short` na
    própria impressão e se invalida sozinho (só o tipo de arquivo afetado é refeito)."""
    return bool({"cortes", "falas"} & refazer)


# ── render: o arquivo velho ainda vale para ESTE momento? ──────────────────────────────────


def impressao(m, extra: str = "") -> str:
    """Identifica o conteúdo de um corte/short: início, fim, nº de abates e rótulo (+ `extra`: hash da
    configuração, webcam...). O nome do arquivo não traz o fim; se qualquer um desses mudar numa nova
    análise, o arquivo velho NÃO serve."""
    janelas = ",".join(f"{a:.2f}-{b:.2f}" for a, b in m.janelas)
    return f"{m.start:.2f}|{m.end:.2f}|{len(m.kills)}|{m.label}|{janelas}|{extra}"


class CacheRender:
    """Lembra, por arquivo (`.cache/render_meta.json`), de qual momento ele foi renderizado. Um arquivo
    só é reaproveitado se existe, não está vazio e a impressão do momento é a mesma."""

    def __init__(self, cache: Path, forcar: bool, forcar_shorts: bool = False):
        self.arq = cache / "render_meta.json"
        self.forcar = forcar
        self.forcar_shorts = forcar_shorts   # --refazer falas: a legenda vem das falas, o corte 16:9 não usa
        self._lock = threading.Lock()
        try:
            self.meta: dict[str, str] = {} if forcar else json.loads(self.arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.meta = {}

    def pronto(self, f: Path, imp: str) -> bool:
        if self.forcar or (self.forcar_shorts and "_vertical" in f.name):
            return False
        return f.is_file() and f.stat().st_size > 0 and self.meta.get(f.name) == imp

    def registrar(self, f: Path, imp: str) -> None:
        with self._lock:
            self.meta[f.name] = imp

    def salvar(self) -> None:
        self.arq.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            gravar_atomico(self.arq, json.dumps(self.meta, ensure_ascii=False))
