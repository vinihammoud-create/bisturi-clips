"""Configuração: padrões aqui; config.toml (na raiz do projeto) sobrescreve o que quiser."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path


@dataclass
class Deteccao:
    modo: str = "rapido"             # "rapido" (keyframes + janelas) ou "completo" (vídeo todo a 10 fps)
    fps_analise: float = 10.0
    processos: int = 6


@dataclass
class Cortes:
    antes: float = 4.0               # s antes do primeiro abate
    depois: float = 2.5              # s depois do último abate
    juntar_intervalo: float = 8.0    # abates mais próximos que isso viram uma jogada só
    juntar_no_round: float = 120.0   # todos os abates do MESMO round (a menos disso um do outro) vão num vídeo só; 0 = desliga
    pular_parado: float = 6.0        # num vídeo de sequência, tempo parado maior que isso entre abates é cortado fora (s)
    sequencia_max: float = 90.0      # duração máxima do vídeo de uma sequência, já sem o tempo parado (s)
    duracao_minima: float = 7.0
    estender_reacao: bool = True     # inclui a reação do streamer após a jogada
    reacoes_sem_abate: int = 8       # momentos só de grito/chat/falas por vídeo
    so_partidas: bool = True         # cortes, montagem e shorts só de partidas (sem Deathmatch/aquecimento)
    limiar_reacao: float = 0.6       # voz 0–1 (0 = fala normal, 1 = grito) p/ contar como reação
    nota_montagem: float = 20.0      # entra na montagem a partir desta nota (0-99; double kill ~18-26)
    nota_short: float = 30.0         # vira short a partir desta nota (os de maior nota primeiro)


@dataclass
class Shorts:
    gerar: bool = True
    quantidade: int = 8              # por vídeo (as de maior nota)
    largura: int = 1080
    altura: int = 1920
    camera_altura: int = 608         # painel da webcam no topo; resto = gameplay
    topo: int = 160                  # faixa no alto (fica sob as abas do TikTok/Reels): desce a câmera para o rosto aparecer
    marca: str = ""                  # seu @ na faixa do topo, ex.: "@seucanal" (vazio = sem texto)
    zoom_camera: float = 5.0         # largura do recorte da câmera em "larguras de rosto"
    zoom_gameplay: float = 1.25      # >1 aproxima o gameplay (corta HUD das bordas)
    legendas: bool = True
    selo_jogada: bool = True         # "TRIPLE KILL" etc. no momento do abate
    zoom_no_abate: bool = True       # "soco" de zoom no instante de cada abate
    duracao_maxima: float = 60.0     # short nunca passa disso (YouTube Shorts/TikTok): jogada longa vira versão compacta
    antes: float = 2.0               # na versão compacta: segundos antes de cada abate
    gancho: float = 2.0              # o short começa só isto antes do 1º abate (a ação logo de cara)
    versao_efeitos: bool = True      # gera também a versão com efeitos (tremida + replay lento do último abate)
    depois: float = 1.5              # na versão compacta: segundos depois de cada abate
    camera_manual: list[float] = field(default_factory=list)  # [x, y, w, h] relativos (0–1)


@dataclass
class Montagem:
    gerar: bool = True
    ordem: str = "cronologica"       # "cronologica" ou "melhores"
    transicao: float = 0.12          # fade curto entre jogadas (s)


@dataclass
class Transcricao:
    modelo: str = "large-v3-turbo"
    idioma: str = "pt"


@dataclass
class Chat:
    usar: bool = True
    atraso: float = 6.0              # chat reage alguns segundos depois do lance


@dataclass
class Fails:
    gerar: bool = True               # trilha de FAILS/engraçados (morte + frustração ou risada), separada das jogadas
    quantidade: int = 8              # no máximo, por vídeo


@dataclass
class Demo:
    jogador: str = ""                # nome (ou parte do nome) ou steamid de quem você é na demo; vazio = pergunta


@dataclass
class Qualidade:
    encoder: str = "auto"            # auto | h264_nvenc | hevc_nvenc | libx264
    cq: int = 21                     # menor = mais qualidade (19–23 é ótimo; fonte de live ~6 Mbps)
    lufs: float = -14.0              # volume padrão de YouTube/TikTok
    renders_paralelos: int = 2        # NVENC + 6 GB de VRAM: 2 cortes ao mesmo tempo é o seguro


@dataclass
class Config:
    deteccao: Deteccao = field(default_factory=Deteccao)
    cortes: Cortes = field(default_factory=Cortes)
    shorts: Shorts = field(default_factory=Shorts)
    montagem: Montagem = field(default_factory=Montagem)
    transcricao: Transcricao = field(default_factory=Transcricao)
    chat: Chat = field(default_factory=Chat)
    qualidade: Qualidade = field(default_factory=Qualidade)
    demo: Demo = field(default_factory=Demo)
    fails: Fails = field(default_factory=Fails)
    modo_previa: bool = False        # não é opção do usuário: o programa liga sozinho com CS2/OBS abertos


def _merge(obj, data: dict) -> None:
    names = {f.name: f for f in fields(obj)}
    for k, v in data.items():
        if k not in names:
            print(f"  [aviso] config: opção desconhecida '{k}' ignorada")
            continue
        cur = getattr(obj, k)
        if is_dataclass(cur) and isinstance(v, dict):
            _merge(cur, v)
        elif isinstance(cur, bool):
            setattr(obj, k, bool(v))
        elif isinstance(cur, (int, float, str)):
            setattr(obj, k, type(cur)(v))
        else:
            setattr(obj, k, v)


def _validar(cfg: Config) -> None:
    """Valores que quebrariam o programa no meio (ex.: renders_paralelos = 0 derruba o render): erro claro
    já na leitura, que o main mostra como problema no config.toml."""
    minimos = [("qualidade.renders_paralelos", cfg.qualidade.renders_paralelos, 1),
               ("deteccao.processos", cfg.deteccao.processos, 1),
               ("shorts.quantidade", cfg.shorts.quantidade, 0),
               ("fails.quantidade", cfg.fails.quantidade, 0)]
    for nome, valor, minimo in minimos:
        if valor < minimo:
            raise ValueError(f"{nome} = {valor}: o mínimo é {minimo}")
    if cfg.deteccao.fps_analise <= 0:
        raise ValueError(f"deteccao.fps_analise = {cfg.deteccao.fps_analise}: precisa ser maior que 0")
    # opções de texto com lista fechada: valor fora dela só estouraria lá no meio do processamento
    # (o encoder fica de fora de propósito: o render avisa e cai em libx264)
    for nome, valor, aceitos in (("deteccao.modo", cfg.deteccao.modo, ("rapido", "completo")),
                                 ("montagem.ordem", cfg.montagem.ordem, ("cronologica", "melhores"))):
        if valor not in aceitos:
            raise ValueError(f"{nome} = \"{valor}\": use {' ou '.join(aceitos)}")
    s = cfg.shorts
    for nome, valor in (("largura", s.largura), ("altura", s.altura), ("camera_altura", s.camera_altura),
                        ("zoom_camera", s.zoom_camera)):
        if valor <= 0:
            raise ValueError(f"shorts.{nome} = {valor}: precisa ser maior que 0")
    if s.zoom_gameplay < 1:
        raise ValueError(f"shorts.zoom_gameplay = {s.zoom_gameplay}: o mínimo é 1 (1 = sem aproximar)")
    if s.largura % 2 or s.altura % 2:
        raise ValueError("shorts.largura e shorts.altura precisam ser números pares (limite do H.264)")
    if s.camera_altura + max(0, s.topo) >= s.altura:
        raise ValueError(f"shorts: camera_altura ({s.camera_altura}) + topo ({s.topo}) não deixa espaço para o "
                         f"gameplay na altura {s.altura}")
    cm = s.camera_manual
    if cm:
        ok = (len(cm) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in cm)
              and cm[0] >= 0 and cm[1] >= 0 and cm[2] > 0 and cm[3] > 0 and cm[0] + cm[2] <= 1 and cm[1] + cm[3] <= 1)
        if not ok:
            raise ValueError("shorts.camera_manual precisa ser [x, y, largura, altura] com valores de 0 a 1 "
                             "(x + largura e y + altura até 1)")


def load_config(path: Path | None) -> Config:
    cfg = Config()
    if path and path.exists():
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
    _validar(cfg)
    return cfg
