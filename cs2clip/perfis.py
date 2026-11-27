"""Perfis prontos de edição e Questionário Rápido de Pedido de Vídeo (Q1).

Transforma as 6 respostas do Briefing do Vídeo (ou o nome de um perfil pronto) em ajustes diretos no `Config`.
Perfis disponíveis:
  * `esports_blast`: corte seco, foco na mira, montagem com música na batida (em vídeos multipista), sem slow artificial
  * `fragmovie_musical`: ritmo rápido, ordem em arco narrativo, Kill-Sync na batida + SFX sutis de transição
  * `criador_live`: mantém voz/reação, shorts com webcam e legenda animada, montagem sem música por cima da fala
  * `jogo_puro`: apenas o som original do jogo, sem música, sem efeitos visuais (ideal para editar depois no DaVinci)
  * `shorts_viral`: foco em Shorts 9:16 rápidos (gancho de 1,5 s, legenda, selo da jogada e zoom no abate)
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config


@dataclass(frozen=True)
class PerfilEdicao:
    nome: str
    titulo: str
    descricao: str
    ajustes: dict[str, dict[str, object]]


PERFIS: dict[str, PerfilEdicao] = {
    "esports_blast": PerfilEdicao(
        nome="esports_blast",
        titulo="Esports Limpo (estilo BLAST / HLTV)",
        descricao="Corte seco, resumo da partida round a round (ordem cronológica), música na batida sem voz e sem câmera lenta artificial.",
        ajustes={
            "cortes": {"pular_parado": 5.0, "estender_reacao": False, "so_partidas": True},
            "shorts": {"versao_efeitos": False, "legendas": False, "selo_jogada": True, "zoom_no_abate": True,
                       "gancho_titulo": False},   # D-G2 (08/11): esports limpo não anuncia a jogada antes dela
            "montagem": {"gerar": True, "ordem": "cronologica", "transicao_tipo": "corte", "musica": "auto", "kill_sync": True, "sfx": False},   # dono 08/11/2026: resumo por round, em ordem
        },
    ),
    "fragmovie_musical": PerfilEdicao(
        nome="fragmovie_musical",
        titulo="Fragmovie Musical (estilo Virre CS2)",
        descricao="Ritmo acelerado, tiros e cortes cravados na batida (Kill-Sync), efeitos sonoros sutis (SFX CC0) e arco narrativo.",
        ajustes={
            "cortes": {"antes": 3.0, "depois": 2.0, "pular_parado": 3.5, "estender_reacao": False, "so_partidas": True},
            "shorts": {"versao_efeitos": False, "selo_jogada": True, "zoom_no_abate": True},
            "montagem": {"gerar": True, "ordem": "arco", "transicao_tipo": "corte", "musica": "auto", "kill_sync": True, "sfx": True},
        },
    ),
    "criador_live": PerfilEdicao(
        nome="criador_live",
        titulo="Criador / Streamer com Reações",
        descricao="Mantém sua voz e reações, estende o corte enquanto você comemora, gera Shorts com webcam e legenda palavra por palavra.",
        ajustes={
            "cortes": {"antes": 4.0, "depois": 2.5, "pular_parado": 6.0, "estender_reacao": True, "reacoes_sem_abate": 8},
            "shorts": {"gerar": True, "legendas": True, "selo_jogada": True, "zoom_no_abate": True, "versao_efeitos": False},
            "montagem": {"gerar": True, "ordem": "melhores", "transicao_tipo": "fade", "musica": "", "kill_sync": False, "sfx": False},
            "audio": {"faixa_jogo": 0},
        },
    ),
    "jogo_puro": PerfilEdicao(
        nome="jogo_puro",
        titulo="Jogo Puro (sem música e sem efeitos)",
        descricao="Somente o áudio limpo do jogo e cortes 16:9 em 60 fps BT.709, sem música nem sobreposições.",
        ajustes={
            "cortes": {"pular_parado": 6.0, "estender_reacao": False},
            "shorts": {"versao_efeitos": False, "legendas": False},
            "montagem": {"gerar": True, "ordem": "cronologica", "transicao_tipo": "corte", "musica": "", "kill_sync": False, "sfx": False},
        },
    ),
    "shorts_viral": PerfilEdicao(
        nome="shorts_viral",
        titulo="Pacote de Shorts / TikTok / Reels 9:16",
        descricao="Foco total nos Shorts verticais: gancho de 1,5 s antes do 1º abate, zoom no abate, selo e legenda dinâmica.",
        ajustes={
            "shorts": {"gerar": True, "quantidade": 10, "gancho": 1.5, "legendas": True, "selo_jogada": True, "zoom_no_abate": True, "versao_efeitos": False},
            "montagem": {"gerar": False},
        },
    ),
}

NOMES_PERFIS = tuple(PERFIS.keys())


def aplicar_perfil(cfg: Config, nome: str) -> Config:
    """Aplica os ajustes do perfil `nome` sobre `cfg` e devolve `cfg`."""
    chave = (nome or "").strip().lower()
    if not chave:
        return cfg
    if chave not in PERFIS:
        raise ValueError(f"perfil '{nome}' desconhecido: use {', '.join(NOMES_PERFIS)}")
    perfil = PERFIS[chave]
    for secao, campos in perfil.ajustes.items():
        alvo = getattr(cfg, secao, None)
        if alvo is None:
            continue
        for k, v in campos.items():
            if hasattr(alvo, k):
                setattr(alvo, k, v)
    return cfg


def aplicar_questionario(
    cfg: Config,
    *,
    destino: str = "C",       # "A" = só montagem 16:9 | "B" = só shorts 9:16 | "C" = completo
    estilo: int = 1,          # 1 = esports_blast | 2 = fragmovie_musical | 3 = criador_live
    audio: int = 1,           # 1 = só jogo + música | 2 = só som puro do jogo | 3 = manter voz + jogo
    musica: str = "auto",     # "auto", "" (sem música) ou nome do arquivo em musicas/
    ritmo: int = 1,           # 1 = rápido (3,5 s) | 2 = tático/completo (6,0 s)
    ordem: int = 1,           # 1 = arco | 2 = melhores | 3 = cronologica
) -> Config:
    """Converte as 6 respostas do Questionário Rápido de Pedido de Vídeo em configuração pronta."""
    mapa_estilo = {1: "esports_blast", 2: "fragmovie_musical", 3: "criador_live"}
    aplicar_perfil(cfg, mapa_estilo.get(estilo, "esports_blast"))

    dest = (destino or "C").strip().upper()
    if dest == "A":
        cfg.montagem.gerar = True
        cfg.shorts.gerar = False
    elif dest == "B":
        cfg.montagem.gerar = False
        cfg.shorts.gerar = True
    else:
        cfg.montagem.gerar = True
        cfg.shorts.gerar = True

    if audio == 1:
        if cfg.audio.faixa_jogo == 0:
            cfg.audio.faixa_jogo = 3
        cfg.montagem.musica = musica or "auto"
    elif audio == 2:
        cfg.montagem.musica = ""
    elif audio == 3:
        cfg.audio.faixa_jogo = 0
        cfg.montagem.musica = ""
        cfg.cortes.estender_reacao = True

    cfg.cortes.pular_parado = 3.5 if ritmo == 1 else 6.0
    cfg.montagem.ordem = {1: "arco", 2: "melhores", 3: "cronologica"}.get(ordem, "arco")
    return cfg

