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
import subprocess
import threading
from functools import lru_cache
from dataclasses import asdict
from pathlib import Path

from .tools import gravar_atomico
from .tools import sem_janela

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
# cortes.antes/depois/estender_reacao e as 3 chaves da regra do pulo (depois_de_cada_abate, antes_do_proximo, pulo_minimo)
# já mudam início/fim e os trechos do momento, que estão na `impressao` (os trechos entram um a um em `janelas`).
_CAMPOS_SHORT = ("largura", "altura", "camera_altura", "topo", "marca", "zoom_camera", "zoom_gameplay", "legendas",
                 "selo_jogada", "gancho_titulo", "zoom_no_abate", "duracao_maxima", "antes", "depois", "gancho", "camera_manual")


def _sha(dados) -> str:
    return hashlib.sha1(json.dumps(dados, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]


RENDER_VERSAO = 3   # 3: guarda de pico real <= -1 dBTP (05/11/2026)
# 2: 2: tags e matriz de cor BT.709, loudness unico por momento e minterpolate em replay <25 fps


@lru_cache(maxsize=1)
def versao_ffmpeg() -> str:
    """Primeira linha de `ffmpeg -version` ("ffmpeg version 9.0.2-full_build..."), ou "" se não der para ler.
    Trocar de FFmpeg pode mudar o pixel/som do arquivo (encoder, filtros): entra na impressão do render."""
    try:
        from .tools import FFMPEG
        r = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=15, **sem_janela())
        return (r.stdout or "").splitlines()[0].strip() if r.returncode == 0 and r.stdout else ""
    except (OSError, SystemExit, subprocess.SubprocessError, IndexError):
        return ""


def _hash_base(cfg) -> str:
    return _sha({"v": RENDER_VERSAO, "encoder": cfg.qualidade.encoder, "cq": cfg.qualidade.cq, "lufs": cfg.qualidade.lufs,
                 "transicao": cfg.montagem.transicao, "ffmpeg": versao_ffmpeg()})


# Opções avançadas que mudam o arquivo (efeitos do corte, voz tratada, faixa do jogo). Só entram na impressão quando
# LIGADAS: assim os cortes e shorts que já existem continuam valendo para quem não usa nenhuma, e ligar uma delas
# refaz exatamente o que ela muda.
def _efeitos_do_corte(cfg) -> dict:
    ligados = {n: True for n in ("speed_ramp", "freeze_headshot", "cartao_tatico") if getattr(cfg.cortes, n, False)}
    ligados.update(_efeitos_de_voz(cfg))
    if getattr(cfg.audio, "separar_jogo_faixa_unica", False):
        ligados["jogo_unico"] = True
    return ligados


def _efeitos_de_voz(cfg) -> dict:
    a = cfg.audio
    if getattr(a, "tratar_voz", False):
        return {"voz": [a.faixa_voz, a.faixa_jogo]}
    if getattr(a, "faixa_comms", 0):   # D-G1: cortes sem o Discord (microfone + jogo, mistura simples)
        return {"voz_sem_discord": [a.faixa_voz, a.faixa_jogo]}
    return {}


def hash_corte(cfg) -> str:
    """Impressão da configuração que muda o corte 16:9 (encoder, qualidade, volume, o fade entre trechos e os
    efeitos avançados ligados)."""
    ef = _efeitos_do_corte(cfg)
    return _hash_base(cfg) if not ef else _sha({"base": _hash_base(cfg), "efeitos": ef})


def hash_short(cfg) -> str:
    """Impressão da configuração que muda o short 9:16: a do corte + os campos de `[shorts]` que mudam o pixel."""
    s = asdict(cfg.shorts)
    dados = {k: s[k] for k in _CAMPOS_SHORT}
    dados["selo_a_partir"] = max(2, cfg.cortes.min_abates_round)   # E1: o selo de multi-kill do short começa neste abate; mudar invalida o short
    if s.get("pan_suave"):
        dados["pan_suave"] = True
    if s.get("mostrar_killfeed", True) is False:
        dados["sem_killfeed"] = True
    if s.get("layout", "classico") != "classico":      # 23/11: o formato novo muda o pixel; o padrão não mexe na impressão de antes
        dados["layout"] = s.get("layout")
        dados["webcam_retangulo"] = s.get("webcam_retangulo", "")
    voz = _efeitos_de_voz(cfg)
    return _sha({"corte": _hash_base(cfg), "shorts": dados, **({"voz": voz} if voz else {})})


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
        with self._lock:    # `registrar` mexe no mesmo dict de outra thread (renders em paralelo): leitura sob a trava (G-019)
            gravada = self.meta.get(f.name)
        return f.is_file() and f.stat().st_size > 0 and gravada == imp

    def registrar(self, f: Path, imp: str) -> None:
        with self._lock:
            self.meta[f.name] = imp

    def salvar(self) -> None:
        self.arq.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            gravar_atomico(self.arq, json.dumps(self.meta, ensure_ascii=False))
