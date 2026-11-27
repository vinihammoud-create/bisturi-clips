"""Motor formal `EditPlan` validado com Pydantic (M1 - ADR 0005).

Representa de forma estruturada, determinística e auditável o plano de edição de qualquer corte, short ou montagem
antes de chamar o FFmpeg:
  * `formato`: `"16:9"`, `"9:16"` ou `"montagem_16:9"` (lista fechada)
  * `trechos`: lista de intervalos `[inicio, fim]` com `velocidade` (0.25 a 2.0) e `motivo`
  * `transicao`: `"corte"` ou `"fade"` (lista fechada)
  * `audio`: faixa de origem, LUFS alvo, música opcional, `kill_sync` e `sfx`
  * Os limites de LUFS, volume da música e duração da transição são os MESMOS de `config.FAIXAS` (R-024/A-010): um
    `config.toml` válido nunca deixa o kit sem plano.
  * Nunca aceita comando livre de FFmpeg: garante que toda edição gerada por regras ou IA passe por validação.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .config import FAIXAS, Config
from .moments import Moment

def _faixa(secao: str, campo: str) -> tuple[float, float]:
    """(mínimo, máximo) que o `config._validar` aceita para `secao.campo`: a fonte única dos limites do plano."""
    for s, c, minimo, maximo, _incluso in FAIXAS:
        if (s, c) == (secao, campo):
            return float(minimo), float(maximo)
    raise KeyError(f"{secao}.{campo} não está em config.FAIXAS")


LUFS_MIN, LUFS_MAX = _faixa("qualidade", "lufs")
VOLUME_MUSICA_MIN, VOLUME_MUSICA_MAX = _faixa("montagem", "volume_musica")
TRANSICAO_MAX = _faixa("montagem", "transicao")[1]

FormatoPlano = Literal["16:9", "9:16", "montagem_16:9"]
TransicaoPlano = Literal["corte", "fade"]
MotivoTrecho = Literal["jogada", "abate", "replay_lento", "reacao", "clutch", "fail"]


class TrechoPlano(BaseModel):
    inicio: float = Field(ge=0.0)
    fim: float = Field(gt=0.0)
    velocidade: float = Field(default=1.0, ge=0.25, le=2.0)
    motivo: MotivoTrecho = "jogada"
    primeiro_tiro_s: float | None = None

    @model_validator(mode="after")
    def _fim_maior_que_inicio(self) -> TrechoPlano:
        if self.fim <= self.inicio:
            raise ValueError(f"fim ({self.fim}) precisa ser maior que inicio ({self.inicio})")
        return self

    @property
    def duracao_saida(self) -> float:
        return (self.fim - self.inicio) / self.velocidade


class CamadaTexto(BaseModel):
    texto: str = Field(min_length=1, max_length=80)
    inicio_rel: float = Field(default=0.0, ge=0.0)
    duracao: float = Field(default=1.8, gt=0.1, le=10.0)
    posicao: Literal["topo", "centro", "rodape_esquerdo"] = "rodape_esquerdo"


class PlanoAudio(BaseModel):
    lufs: float = Field(default=-14.0, ge=LUFS_MIN, le=LUFS_MAX)
    faixa_jogo: int = Field(default=0, ge=0, le=6)
    musica: str = ""
    volume_musica_db: float = Field(default=-4.0, ge=VOLUME_MUSICA_MIN, le=VOLUME_MUSICA_MAX)
    kill_sync: bool = True
    sfx: bool = False


class EditPlan(BaseModel):
    versao: int = 1
    id_momento: str
    formato: FormatoPlano
    largura: int = Field(default=1920, ge=2)     # o config só pede par e maior que 0 (sem teto)
    altura: int = Field(default=1080, ge=2)
    fps: int = Field(default=60, ge=24, le=120)
    transicao: TransicaoPlano = "corte"
    duracao_transicao: float = Field(default=0.12, ge=0.0, le=TRANSICAO_MAX)
    trechos: list[TrechoPlano] = Field(min_length=1)
    camadas_texto: list[CamadaTexto] = Field(default_factory=list)
    audio: PlanoAudio = Field(default_factory=PlanoAudio)

    @property
    def duracao_total(self) -> float:
        return round(sum(t.duracao_saida for t in self.trechos), 3)

    def salvar(self, destino: Path) -> Path:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(self.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return destino


def criar_plano_momento(m: Moment, cfg: Config, formato: FormatoPlano = "16:9") -> EditPlan:
    """Gera o `EditPlan` validado de um `Moment` segundo a configuração ativa."""
    motivo: MotivoTrecho = "fail" if m.kind == "fail" else ("clutch" if m.clutch else ("abate" if m.kills else "reacao"))
    primeiro_tiro = m.kills[0].t if m.kills else None
    trechos = [
        TrechoPlano(
            inicio=round(a, 3),
            fim=round(b, 3),
            velocidade=1.0,
            motivo=motivo,
            primeiro_tiro_s=round(primeiro_tiro, 3) if (i == 0 and primeiro_tiro is not None) else None,
        )
        for i, (a, b) in enumerate(m.janelas)
    ]
    camadas: list[CamadaTexto] = []
    if m.label:
        sub = f"ROUND {m.round_n} · {m.label}" if m.round_n else m.label
        camadas.append(CamadaTexto(texto=sub, inicio_rel=0.0, duracao=1.8,
                                   posicao="centro" if formato == "9:16" else "rodape_esquerdo"))
    w, h = (cfg.shorts.largura, cfg.shorts.altura) if formato == "9:16" else (1920, 1080)
    return EditPlan(
        id_momento=f"{m.idx:02d}_{m.label or m.kind}",
        formato=formato,
        largura=w,
        altura=h,
        fps=60,
        transicao=cfg.montagem.transicao_tipo if cfg.montagem.transicao_tipo in ("corte", "fade") else "corte",
        duracao_transicao=cfg.montagem.transicao,
        trechos=trechos,
        camadas_texto=camadas,
        audio=PlanoAudio(
            lufs=cfg.qualidade.lufs,
            faixa_jogo=cfg.audio.faixa_jogo,
            musica=cfg.montagem.musica if formato == "montagem_16:9" else "",
            volume_musica_db=cfg.montagem.volume_musica,
            kill_sync=cfg.montagem.kill_sync,
            sfx=cfg.montagem.sfx,
        ),
    )

