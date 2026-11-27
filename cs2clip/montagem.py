"""Montagem 16:9 estilo esports (C2 + A1 + A5 + M6 Kill-Sync + M8 Ordem em Arco + V6 SFX CC0).

* Junta os cortes já renderizados SEM o fade preto de cada um (a borda de `montagem.transicao` sai) e emenda com
  corte seco ou com uma mistura curta (`montagem.transicao_tipo`). O som leva micro-fade de 15 ms na emenda
  (sem estalo) e o arquivo final passa por loudnorm em 2 passadas: volume único do começo ao fim.
* Ordem (`montagem.ordem`):
  - `"cronologica"`: na ordem da partida;
  - `"melhores"`: da maior nota para a menor;
  - `"arco"` (M8, estilo BLAST / Virre): abre com a Top 2 (gancho forte logo nos primeiros 3 s), cresce em tensão
    no miolo e fecha o vídeo com a Top 1 (o grande Clutch / Ace).
* Música (A5) + Kill-Sync (M6): quando todos os cortes têm a faixa só do jogo (A1, gravação multipista do OBS),
  entra a música + som do jogo, cortando na batida (`alinhar_na_batida`) e ajustando o início do corte em até
  0,45 s (`alinhar_tiro_na_batida`) para o 1º abate cair cravado na batida.
"""
from __future__ import annotations

import hashlib
import wave
import json
import os
import subprocess
from pathlib import Path

import numpy as np

from .config import Config
from .musicas_catalogo import caminho_da_faixa, catalogo_efetivo, pasta_da_pessoa, tem_catalogo
from .tools import FFMPEG, FFPROBE, ROOT
from .tools import substituir_arquivo
from .tools import sem_janela

PASTA_MUSICAS = ROOT / "musicas"
EXTENSOES_MUSICA = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus")
FADE_MISTURA = 0.25      # duração da mistura no modo "fade"
MICRO_FADE = 0.015       # anti-estalo na emenda seca
MAX_AJUSTE = 0.75        # no máximo isto sai do fim de um corte para a emenda cair na batida (s)
MINIMO_CORTE = 1.5       # corte não fica menor que isto por causa da batida
MAX_RECUO_TIRO = 0.45    # M6 Kill-Sync: quanto pode avançar o início do corte para o 1º tiro cair na batida (s)
MIN_ANTES_TIRO = 1.2     # nunca deixa menos que isto antes do 1º abate no corte


# ── ordem narrativa (M8) ─────────────────────────────────────────────────────────────────────────

def ordenar_montagem(keep: list, ordem: str) -> list:
    """Ordena as jogadas da montagem: `cronologica`, `melhores` ou `arco` (abre com Top 2 e fecha com Top 1)."""
    if not keep or ordem == "cronologica":
        return list(keep)
    por_nota = sorted(keep, key=lambda m: (-getattr(m, "score", 0.0), getattr(m, "start", 0.0)))
    if ordem == "melhores" or len(por_nota) < 2:
        return por_nota
    if len(por_nota) == 2:   # arco com 2 jogadas: abre na menor e fecha na maior (G2 do Gemini: terminava na fraca)
        return [por_nota[1], por_nota[0]]
    # Arco narrativo (estilo BLAST / Virre):
    # 1º clipe = Top 2 (gancho imediato); miolo = crescente até a Top 3; último clipe = Top 1 (clímax)
    gancho = por_nota[1]
    climax = por_nota[0]
    miolo = list(reversed(por_nota[2:]))
    return [gancho, *miolo, climax]


# ── faixas de áudio (A1) ─────────────────────────────────────────────────────────────────────────

def n_faixas_audio(arquivo: str | Path) -> int:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(arquivo)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", **sem_janela())
    return len([ln for ln in r.stdout.splitlines() if ln.strip()])


def _duracao(arquivo: str | Path) -> float:
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(arquivo)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _pcm(arquivo: str, faixa: int, ini: float, dur: float, sr: int = 8000) -> np.ndarray:
    r = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-ss", f"{ini:.3f}", "-i", arquivo, "-t", f"{dur:.3f}",
                        "-map", f"0:a:{faixa}", "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1"],
                       capture_output=True, **sem_janela())
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def trilha_jogo(src: str, cfg: Config, mistura_sem_voz: bool = False) -> tuple[int | None, str]:
    """Índice (0 = 1ª faixa de áudio) da faixa só com o jogo, ou None e o motivo em português simples.
    Confere de verdade: o OBS com todas as fontes em todas as faixas grava a MESMA mistura em cada uma.
    `mistura_sem_voz`: a análise não achou fala nenhuma na gravação (ver `pipeline._mistura_sem_voz`); aí a faixa 1
    já é só o jogo e serve para a montagem com música quando não houver faixa separada que preste."""
    indice, motivo = _trilha_configurada(src, cfg)
    if indice is None and mistura_sem_voz and n_faixas_audio(src) >= 1:
        return 0, ("a gravação não tem voz (nenhuma fala detectada): a faixa 1 serve de som do jogo "
                   "e a montagem leva música")
    return indice, motivo


def _trilha_configurada(src: str, cfg: Config) -> tuple[int | None, str]:
    k = cfg.audio.faixa_jogo
    if k <= 0:
        return None, ""
    if k == 1:
        return None, "audio.faixa_jogo = 1 é a mistura da live (tem voz); use a faixa só do jogo (ex.: 3)"
    n = n_faixas_audio(src)
    if n < k:
        return None, f"o vídeo tem {n} faixa(s) de áudio e não tem a faixa {k}: a montagem fica sem música"
    dur = _duracao(src)
    ini = max(0.0, dur / 2 - 15.0)
    mix, jogo = _pcm(src, 0, ini, 30.0), _pcm(src, k - 1, ini, 30.0)
    if not len(jogo) or float(np.sqrt(np.mean(jogo ** 2))) < 1e-4:
        return None, f"a faixa {k} está muda: a montagem fica sem música"
    if len(mix) == len(jogo) and float(np.max(np.abs(mix - jogo))) < 1e-3 and not getattr(cfg.audio, "fonte_limpa", False):
        return None, (f"a faixa {k} é igual à faixa 1 (o OBS grava a mesma mistura em todas as faixas): "
                      "a montagem fica sem música até separar o jogo no OBS")
    return k - 1, ""


# ── música, batida (A5) e Kill-Sync (M6) ─────────────────────────────────────────────────────────

def musicas_liberadas(pasta: Path = PASTA_MUSICAS, pasta_pessoa: Path | None = None) -> list[Path]:
    """As músicas que a escolha AUTOMÁTICA pode usar. Com catálogo, só as que ele lista (o efetivo: fábrica com os ajustes da pessoa,
    mais as faixas da pessoa), existem (na pasta da pessoa ou na de fábrica) e têm licença registrada: um arquivo solto na pasta, sem
    origem conhecida, nunca é sorteado. Catálogo de fábrica ilegível = nenhuma (não dá para saber o que é seguro). Sem catálogo nenhum
    também nenhuma, com aviso (A-012, 26/11/2026): música só com licença registrada; apagar o `catalogo.json` não pode ser um jeito de
    contornar isso."""
    pessoa = pasta_da_pessoa(pasta, pasta_pessoa)
    if not pasta.is_dir() and not pessoa.is_dir():
        return []
    if not tem_catalogo(pasta, pessoa):
        print("    [aviso] musicas/catalogo.json não existe: a música automática fica desligada (só entra música com a licença registrada no catálogo; "
              "veja musicas/COMO_ADICIONAR_MUSICAS.md)")
        return []
    try:
        faixas = catalogo_efetivo(pasta, pessoa)["faixas"]
        nomes = {str(f["arquivo"]) for f in faixas if str(f.get("licenca", "")).strip() and not f.get("desligada")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print("    [aviso] musicas/catalogo.json ilegível: a música automática fica desligada até ele ser corrigido")
        return []
    caminhos = (caminho_da_faixa(n, pasta, pessoa) for n in nomes)
    return sorted(p for p in caminhos if p is not None and p.suffix.lower() in EXTENSOES_MUSICA)


def escolher_musica(cfg: Config, chave: str, pasta: Path = PASTA_MUSICAS, pasta_pessoa: Path | None = None) -> Path | None:
    """`montagem.musica`: "" = sem música; "auto" = uma da pasta musicas/ (a mesma para o mesmo vídeo, outra para
    outro vídeo); senão o nome do arquivo (na pasta de músicas da pessoa, na de fábrica ou caminho completo)."""
    escolha = (cfg.montagem.musica or "").strip()
    if not escolha:
        return None
    pessoa = pasta_da_pessoa(pasta, pasta_pessoa)
    if escolha.lower() != "auto":
        for p in (pessoa / escolha, pasta / escolha, Path(escolha)):
            if p.is_file():
                return p
        print(f"    [aviso] montagem.musica: '{escolha}' não encontrada em {pasta}; montagem sem música")
        return None
    arquivos = musicas_liberadas(pasta, pessoa)
    if not arquivos:
        return None
    i = int(hashlib.sha1(chave.encode("utf-8")).hexdigest(), 16) % len(arquivos)
    return arquivos[i]


PESO_EMOCAO = 1.5           # A6: peso da emoção medida no critério antigo (catálogo sem drop medido): 25 BPM de distância = 1 ponto
EVITAR_ULTIMAS = 2          # E7: um vídeo NOVO evita as faixas dadas aos 2 últimos vídeos (variedade); o mesmo vídeo mantém a sua
HISTORICO_MUSICA = "historico.json"
HISTORICO_MAX = 60
EMPATE_EMOCAO = 0.10        # R1: faixas a até isto da melhor emoção contam como empate e dividem a montagem pelo sorteio
FOLGA_PULO_S = 6.0          # R1: tiro do 1º corte previsto a ~6 s da montagem, para saber se a faixa "cabe" antes de escolher
DURACAO_MIN_MUSICA = 30.0   # s: vinheta curta (feita para Short) nunca é sorteada para a montagem 16:9


def _tem_drop(f: dict) -> bool:
    return f.get("drop_s") not in (None, 0, 0.0)   # o medidor grava null quando não há drop (salto < 4 dB)


def _cabe_sem_loop(f: dict, duracao_montagem: float) -> bool:
    """A faixa toca a montagem inteira a partir do pulo até o drop, sem voltar ao começo (R2)."""
    if duracao_montagem <= 0:
        return True
    pulo = max(0.0, float(f.get("drop_s") or 0.0) - FOLGA_PULO_S)
    return pulo + duracao_montagem <= float(f.get("duracao_s") or 1e9)


def faixas_do_rodizio(faixas: list[dict]) -> list[dict]:
    """Quais das faixas LIGADAS (já liberadas: com licença, arquivo e não desligadas) disputam a montagem, da MAIS preferida para a menos: a mesma regra de
    `escolher_musica_por_ritmo` (vinheta curta fora; só as que têm drop; vence a de maior emoção). A tela Músicas usa isto para dizer ao dono quais tocam de fato."""
    longas = [f for f in faixas if float(f.get("duracao_s") or DURACAO_MIN_MUSICA) >= DURACAO_MIN_MUSICA]
    pool = longas or list(faixas)
    if any(f.get("emocao") is not None for f in pool):
        pool = [f for f in pool if _tem_drop(f)] or pool
    return sorted(pool, key=lambda f: (-float(f.get("emocao") or 0.0), str(f.get("arquivo"))))


def arquivo_historico(pasta: Path) -> Path:
    """Onde fica o rodízio das músicas: na pasta de músicas da pessoa (`pasta_da_pessoa`; no modo portátil, a mesma `musicas/`).
    `BISTURI_HISTORICO_MUSICA` (caminho de arquivo) troca o lugar: os testes usam, para nunca tocar no histórico real."""
    env = os.environ.get("BISTURI_HISTORICO_MUSICA", "").strip()
    return Path(env) if env else pasta_da_pessoa(Path(pasta)) / HISTORICO_MUSICA


def _ler_historico(pasta: Path) -> dict:
    """Quem ficou com qual faixa: {"por_chave": {vídeo: faixa}, "ordem": [vídeos, do mais antigo ao mais novo]}. Ilegível = vazio (nunca derruba a montagem)."""
    try:
        h = json.loads(arquivo_historico(pasta).read_text(encoding="utf-8"))
        if isinstance(h.get("por_chave"), dict) and isinstance(h.get("ordem"), list):
            return h
    except (OSError, ValueError, AttributeError):
        pass
    return {"por_chave": {}, "ordem": []}


def _gravar_historico(pasta: Path, h: dict) -> bool:
    try:
        alvo = arquivo_historico(pasta)
        alvo.parent.mkdir(parents=True, exist_ok=True)       # modo instalado: a pasta de músicas da pessoa pode ainda não existir
        tmp = alvo.with_name(alvo.name + ".tmp")
        tmp.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
        substituir_arquivo(tmp, alvo)
        return True
    except OSError:
        return False              # sem permissão de escrita: só perde a rotação, a montagem sai igual


def _registrar_uso(pasta: Path, chave: str, arquivo: str) -> None:
    h = _ler_historico(pasta)
    if chave in h["ordem"]:
        h["ordem"].remove(chave)
    h["ordem"].append(chave)
    h["por_chave"][chave] = arquivo
    for velha in h["ordem"][:-HISTORICO_MAX]:
        h["por_chave"].pop(velha, None)
        if isinstance(h.get("evitar"), dict):
            h["evitar"].pop(velha, None)
    h["ordem"] = h["ordem"][-HISTORICO_MAX:]
    _gravar_historico(pasta, h)


EVITAR_NO_VIDEO_MAX = 8     # S1: quantas faixas o dono já recusou naquele vídeo (a próxima escolha as evita)


def _evitadas_do_video(pasta: Path, chave: str) -> tuple[str, ...]:
    """As faixas que o dono mandou trocar NESTE vídeo (botão "Trocar a música"): a próxima escolha para ele as evita."""
    evitar = _ler_historico(pasta).get("evitar")
    lista = evitar.get(chave) if isinstance(evitar, dict) else None
    return tuple(str(x) for x in lista) if isinstance(lista, list) else ()


def trocar_musica_do_video(pasta: Path, chave: str, atual: str = "") -> dict:
    """S1: solta a faixa presa ao vídeo `chave` (`MONTAGEM_<id>`) e a põe na lista do que evitar NELE; só este vídeo muda (as outras montagens, o rodízio e a
    lista da tela Músicas ficam como estão). A montagem nova só sai quando o vídeo for refeito. `atual`: a faixa que o arquivo pronto usa de fato (o `benchmark.json`
    guarda), para o caso de o histórico não lembrar dela. Devolve `{"anterior": nome ou "", "trocada": bool}`; `trocada` False = não havia faixa conhecida."""
    h = _ler_historico(pasta)
    anterior = h["por_chave"].pop(chave, None) or (atual or "")
    if not anterior:
        return {"anterior": "", "trocada": False}
    evitar = h.get("evitar")
    if not isinstance(evitar, dict):
        evitar = {}
    lista = [x for x in (evitar.get(chave) or []) if x != anterior] + [anterior]
    evitar[chave] = lista[-EVITAR_NO_VIDEO_MAX:]
    while len(evitar) > HISTORICO_MAX:
        evitar.pop(next(iter(evitar)))
    h["evitar"] = evitar
    if not _gravar_historico(pasta, h):
        raise OSError("Não consegui gravar o histórico das músicas (musicas/historico.json).")
    return {"anterior": anterior, "trocada": True}


def _faixas_recentes(pasta: Path, chave: str, quantas: int = EVITAR_ULTIMAS) -> tuple[str, ...]:
    """As faixas dadas aos últimos vídeos (sem contar este)."""
    h = _ler_historico(pasta)
    recentes: list[str] = []
    for k in reversed([x for x in h["ordem"] if x != chave]):
        f = h["por_chave"].get(k)
        if f and f not in recentes:
            recentes.append(f)
        if len(recentes) >= quantas:
            break
    return tuple(recentes)


def escolher_musica_por_ritmo(cortes_por_min: float = 12.0, pasta: Path = PASTA_MUSICAS,
                              chave: str = "", duracao_montagem: float = 0.0,
                              evitar: tuple[str, ...] = (), pasta_pessoa: Path | None = None) -> tuple[Path | None, float]:
    """Escolhe no catálogo efetivo (`musicas_catalogo.catalogo_efetivo`) a faixa para a montagem, devolvendo `(caminho_musica, segundo_do_drop)`.
    Só entram faixas liberadas (`musicas_liberadas`); sem catálogo nenhum, nenhuma (A-012).

    R1 (revisão de 09/11/2026: o sorteio entre as 3 melhores ainda caía em faixa calma): com a energia e o drop MEDIDOS
    (`tools/medir_musicas.py`), (1) só as faixas COM drop disputam (e, se houver alguma que caiba na montagem sem voltar ao
    começo, `duracao_montagem` > 0, só essas); (2) vence a de maior `emocao`; (3) só o empate real (até `EMPATE_EMOCAO`) é sorteado
    pelo hash do nome, na ordem do BPM mais perto do ritmo. Catálogo sem as medidas (antigo): o critério de antes (BPM, top 3)."""
    pessoa = pasta_da_pessoa(pasta, pasta_pessoa)
    liberadas = {p.name: p for p in musicas_liberadas(pasta, pessoa)}
    if tem_catalogo(pasta, pessoa):
        if not liberadas:
            return None, 0.0
        try:
            cat = catalogo_efetivo(pasta, pessoa)
            faixas = [f for f in cat.get("faixas", []) if f.get("arquivo") in liberadas]
            longas = [f for f in faixas if float(f.get("duracao_s") or DURACAO_MIN_MUSICA) >= DURACAO_MIN_MUSICA]
            faixas = longas or faixas   # só vinhetas curtas no catálogo: melhor elas (em loop) que sem música
            bpm_alvo = max(95.0, min(145.0, 90.0 + cortes_por_min * 3.0))

            def distancia(f: dict) -> float:
                return abs(float(f.get("bpm", 120.0)) - bpm_alvo)
            if any(f.get("emocao") is not None for f in faixas):
                com_drop = [f for f in faixas if _tem_drop(f)]
                pool = [f for f in com_drop if _cabe_sem_loop(f, duracao_montagem)] or com_drop or faixas
                pool = [f for f in pool if f.get("arquivo") not in evitar] or pool       # E7: variedade, sem esvaziar o pool
                melhor = max(float(f.get("emocao") or 0.0) for f in pool)
                empate = sorted((f for f in pool if float(f.get("emocao") or 0.0) >= melhor - EMPATE_EMOCAO),
                                key=lambda f: (distancia(f), str(f.get("arquivo"))))
                idx = int(hashlib.sha1(chave.encode("utf-8")).hexdigest(), 16) % len(empate) if chave else 0
                escolhida = empate[idx]
            else:
                faixas = [f for f in faixas if f.get("arquivo") not in evitar] or faixas
                faixas.sort(key=distancia)
                top = faixas[: min(3, len(faixas))]
                idx = int(hashlib.sha1(chave.encode("utf-8")).hexdigest(), 16) % len(top) if chave else 0
                escolhida = top[idx]
            return liberadas[escolhida["arquivo"]], float(escolhida.get("drop_s") or 0.0)
        except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError):
            return None, 0.0
    arquivos = sorted(liberadas.values())
    if not arquivos:
        return None, 0.0
    idx = int(hashlib.sha1(chave.encode("utf-8")).hexdigest(), 16) % len(arquivos) if chave else 0
    return arquivos[idx], 0.0


def musica_da_montagem(cfg: Config, chave: str, cortes_por_min: float = 12.0,
                       pasta: Path = PASTA_MUSICAS, duracao_montagem: float = 0.0, pasta_pessoa: Path | None = None) -> Path | None:
    """A música de uma montagem, decidida UMA vez só: o vídeo e o `.fcpxml` do DaVinci têm que usar a mesma faixa
    (revisão de 31/10/2026: com chaves diferentes, 13 de 20 nomes davam músicas diferentes). Com `musica = "auto"`
    escolhe pelo ritmo REAL da montagem (`cortes_por_min`); com um nome de arquivo, usa esse; vazio = sem música.
    O rodízio (`historico.json`) mora na pasta da pessoa."""
    pessoa = pasta_da_pessoa(pasta, pasta_pessoa)
    if (cfg.montagem.musica or "").strip().lower() == "auto":
        liberadas = {p.name: p for p in musicas_liberadas(pasta, pessoa)}
        fixa = _ler_historico(pessoa)["por_chave"].get(chave) if chave else None
        evitar_a_fixa: tuple[str, ...] = ()
        if fixa and fixa in liberadas:                 # o mesmo vídeo sempre fica com a mesma faixa (refazer não troca a música)...
            entrada = entrada_do_catalogo_segura(liberadas[fixa], pasta, pessoa)
            if not duracao_montagem or entrada is None or _cabe_sem_loop(entrada, duracao_montagem):
                return liberadas[fixa]
            evitar_a_fixa = (fixa,)                    # ...a menos que a montagem tenha crescido e a faixa já não toque inteira sem voltar ao começo (R2)
        faixa, _ = escolher_musica_por_ritmo(cortes_por_min, pasta, chave, duracao_montagem,
                                             evitar=(_faixas_recentes(pessoa, chave) + _evitadas_do_video(pessoa, chave) if chave else ()) + evitar_a_fixa,
                                             pasta_pessoa=pessoa)
        faixa = faixa or escolher_musica(cfg, chave, pasta, pessoa)
        if evitar_a_fixa:                              # a presa já não cabe: só troca se a nova CABE; se nenhuma cabe, mantém a que o vídeo já tinha
            entrada_nova = entrada_do_catalogo_segura(faixa, pasta, pessoa) if faixa is not None else None
            if entrada_nova is None or not _cabe_sem_loop(entrada_nova, duracao_montagem):
                return liberadas[evitar_a_fixa[0]]
        if faixa is not None and chave and pasta.is_dir():
            _registrar_uso(pessoa, chave, faixa.name)
        return faixa
    return escolher_musica(cfg, chave, pasta, pessoa)


def batidas_do_audio(a: np.ndarray, sr: int) -> np.ndarray:
    """Tempos (s) das batidas: fluxo espectral -> período por autocorrelação (70-180 BPM, preferência perto de 125)
    -> fase que mais coincide com os ataques."""
    hop, nfft = 256, 1024
    if len(a) < nfft * 4:
        return np.zeros(0)
    n = 1 + (len(a) - nfft) // hop
    janela = np.hanning(nfft).astype(np.float32)
    env = np.zeros(n, np.float32)
    anterior = None
    for b in range(0, n, 2048):                       # em blocos: música longa não estoura memória
        idx = np.arange(b, min(n, b + 2048))[:, None] * hop + np.arange(nfft)[None, :]
        mag = np.log1p(100.0 * np.abs(np.fft.rfft(a[idx] * janela, axis=1)))
        ant = np.vstack([anterior[None, :], mag[:-1]]) if anterior is not None else np.vstack([mag[:1], mag[:-1]])
        env[b:b + len(mag)] = np.maximum(mag - ant, 0).sum(axis=1)
        anterior = mag[-1]
    k = max(1, int(0.5 * sr / hop))
    env = np.maximum(env - np.convolve(env, np.ones(k) / k, mode="same"), 0)
    if env.max() <= 0:
        return np.zeros(0)
    fps = sr / hop
    lag_min, lag_max = int(fps * 60 / 180), int(fps * 60 / 70)
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(lag_min, min(lag_max, len(ac) - 1) + 1)
    if not len(lags):
        return np.zeros(0)
    bpm = 60 * fps / lags
    peso = np.exp(-0.5 * (np.log2(bpm / 125.0) / 0.9) ** 2)
    melhor = int(lags[np.argmax(ac[lags] * peso)])
    if 1 <= melhor < len(ac) - 1:                     # refina o período entre quadros (parábola)
        y0, y1, y2 = ac[melhor - 1], ac[melhor], ac[melhor + 1]
        den = y0 - 2 * y1 + y2
        periodo = (melhor + (0.5 * (y0 - y2) / den if den else 0.0)) / fps
    else:
        periodo = melhor / fps
    dur = len(a) / sr
    fases = np.linspace(0, periodo, 48, endpoint=False)
    notas = [env[np.clip(np.round(np.arange(f, dur, periodo) * fps).astype(int), 0, n - 1)].sum() for f in fases]
    return np.arange(fases[int(np.argmax(notas))], dur, periodo)


def batidas(musica: Path, sr: int = 11025) -> tuple[np.ndarray, float]:
    """(batidas, duração da música)."""
    r = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-i", str(musica), "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "pipe:1"], capture_output=True, **sem_janela())
    a = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0
    return batidas_do_audio(a, sr), len(a) / sr


def batidas_e_compasso(musica: Path, sr: int = 11025) -> tuple[np.ndarray, float, np.ndarray, str]:
    """(batidas, duração, tempos fortes "1" do compasso, de onde veio o compasso). Decodifica a música uma vez só.
    Sem compasso confiável, os tempos fortes vêm vazios e a montagem usa só a grade de batidas."""
    from .compasso import grade_de_compasso
    r = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-i", str(musica), "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "pipe:1"], capture_output=True, **sem_janela())
    a = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0
    b = batidas_do_audio(a, sr)
    down, fonte = grade_de_compasso(Path(musica), a, sr, b)
    return b, len(a) / sr, down, fonte


def alinhar_na_batida(duracoes: list[float], batidas_s: np.ndarray, mistura: float = 0.0,
                      max_ajuste: float = MAX_AJUSTE, minimo: float = MINIMO_CORTE) -> list[float]:
    """Tira um pouco do FIM de cada corte (nunca acrescenta) para a emenda seguinte cair numa batida. O fim do
    corte é o respiro depois do último abate (`cortes.depois` = 2,5 s), então perder até ~0,75 s é seguro."""
    saida, inicio = [], 0.0
    for i, d in enumerate(duracoes):
        if i < len(duracoes) - 1 and len(batidas_s):
            emenda = inicio + d - mistura
            cands = batidas_s[(batidas_s <= emenda + 1e-6) & (batidas_s >= emenda - max_ajuste)]
            if len(cands) and d - (emenda - cands[-1]) >= minimo:
                d -= emenda - float(cands[-1])
        saida.append(d)
        inicio += d - mistura
    return saida


def alinhar_tiros_e_emendas(
    inicios: list[float],
    duracoes: list[float],
    tiros_rel: list[float | None],
    batidas_s: np.ndarray,
    mistura: float = 0.0,
    max_recuo: float = MAX_RECUO_TIRO,
    max_ajuste_fim: float = MAX_AJUSTE,
    minimo: float = MINIMO_CORTE,
    fortes: np.ndarray | None = None,
) -> tuple[list[float], list[float]]:
    """M6 (Kill-Sync) + A5:
    1) Para cada corte que tem `tiro_rel` (instante do 1º abate relativo ao começo do arquivo do corte), avança o
       `ini` em até `max_recuo` s para que o tiro caia exatamente numa batida da música.
    2) Em seguida, ajusta o fim (`d`) em até `max_ajuste_fim` s para a emenda seguinte também cair na batida.
    `fortes` (opcional): os "1" do compasso. Dentro da mesma janela de ajuste eles têm preferência sobre as batidas
    comuns; se nenhum cair na janela, vale a batida de sempre. Os limites de contexto (MIN_ANTES_TIRO, `minimo`,
    `max_recuo`) são os mesmos nos dois casos."""
    if not len(batidas_s):
        return list(inicios), list(duracoes)

    def candidatas(lo: float, hi: float) -> np.ndarray:
        if fortes is not None and len(fortes):
            f = fortes[(fortes <= hi + 1e-6) & (fortes >= lo)]
            if len(f):
                return f
        return batidas_s[(batidas_s <= hi + 1e-6) & (batidas_s >= lo)]
    novos_ini: list[float] = []
    novas_dur: list[float] = []
    pos_montagem = 0.0
    for i, (ini, d, tiro) in enumerate(zip(inicios, duracoes, tiros_rel)):
        if tiro is not None and tiro - ini > MIN_ANTES_TIRO:
            lim_recuo = min(max_recuo, tiro - ini - MIN_ANTES_TIRO, max(0.0, d - minimo))
            if lim_recuo > 0.02:
                # Se avançarmos o início em `delta` (0 <= delta <= lim_recuo), o tiro na montagem cai em:
                # t_tiro = pos_montagem + (tiro - ini - delta)
                t_tiro_atual = pos_montagem + (tiro - ini)
                cands = candidatas(t_tiro_atual - lim_recuo, t_tiro_atual)
                if len(cands):
                    delta = t_tiro_atual - float(cands[-1])
                    ini += delta
                    d -= delta
        if i < len(duracoes) - 1:
            emenda = pos_montagem + d - mistura
            cands_fim = candidatas(emenda - max_ajuste_fim, emenda)
            if len(cands_fim) and d - (emenda - float(cands_fim[-1])) >= minimo:
                d -= emenda - float(cands_fim[-1])
        novos_ini.append(ini)
        novas_dur.append(d)
        pos_montagem += d - mistura
    return novos_ini, novas_dur


FOLGA_MUSICA_S = 20.0   # a música precisa ter pelo menos isto depois do ponto onde começa a tocar


def deslocamento_para_o_drop(drop_s: float | None, batidas_s: np.ndarray, duracao_musica: float,
                             primeiro_tiro: float | None, total_montagem: float | None = None) -> tuple[float, str]:
    """A6: quantos segundos pular do começo da música para que o drop dela caia no 1º tiro da montagem (no tempo da montagem).
    Usa a batida mais perto do drop (a montagem corta na batida). (0, motivo) quando não dá: sem drop medido, sem tiro,
    o drop vem ANTES do tiro, ou (R2, revisão de 09/11/2026) a música não dura a montagem inteira a partir do pulo: ela
    voltaria à introdução calma no meio do vídeo. Sem `total_montagem` vale só a folga mínima de `FOLGA_MUSICA_S`."""
    if not drop_s or drop_s <= 0:
        return 0.0, "a música não tem um drop medido"
    if primeiro_tiro is None:
        return 0.0, "sem o instante do 1º tiro"
    alvo = float(batidas_s[int(np.argmin(np.abs(batidas_s - drop_s)))]) if len(batidas_s) else float(drop_s)
    desloc = alvo - primeiro_tiro
    if desloc < 0.0:
        return 0.0, f"o drop ({alvo:.1f} s) vem antes do 1º tiro ({primeiro_tiro:.1f} s): música tocada desde o início"
    if total_montagem is not None and total_montagem > 0:
        if desloc + total_montagem > duracao_musica:
            return 0.0, (f"a música ({duracao_musica:.0f} s) não dura a montagem ({total_montagem:.0f} s) a partir do pulo ({desloc:.0f} s): "
                         "voltaria à introdução; tocada desde o início")
    elif desloc > duracao_musica - FOLGA_MUSICA_S:
        return 0.0, "o drop fica perto demais do fim da música"
    return round(desloc, 3), ""


def entrada_do_catalogo_segura(musica: Path, pasta: Path | None = None, pasta_pessoa: Path | None = None) -> dict | None:
    from .compasso import entrada_do_catalogo
    try:
        return entrada_do_catalogo(musica, pasta, pasta_pessoa)
    except Exception:  # noqa: BLE001
        return None


ULTIMO_RITMO: dict = {}   # métricas de ritmo da última montagem renderizada (o pipeline guarda no benchmark.json)
TOLERANCIA_BATIDA = 0.04  # s: "caiu na batida" = a menos de 40 ms dela


def _fracao_perto(instantes: list[float], grade: np.ndarray | None) -> float | None:
    if grade is None or not len(grade) or not instantes:
        return None
    g = np.asarray(grade)
    return round(sum(float(np.min(np.abs(g - x))) <= TOLERANCIA_BATIDA for x in instantes) / len(instantes), 2)


def metricas_ritmo(inicios: list[float], durs: list[float], tiros_rel: list[float | None] | None, mistura: float,
                   batidas_s: np.ndarray | None, fortes: np.ndarray | None = None,
                   frases: np.ndarray | None = None) -> dict:
    """C5: números do ritmo da montagem, para comparar estilos (não provam que ficou bom; quem julga é o dono):
    cortes por minuto, duração dos planos (P10/P50/P90) e quanto das emendas e dos primeiros tiros caiu na batida,
    no "1" do compasso e no começo de frase. Campos que não se aplicam (sem música) ficam None."""
    n = len(durs)
    total = sum(durs) - mistura * max(0, n - 1)
    emendas, tiros, pos = [], [], 0.0
    for i, (ini, d) in enumerate(zip(inicios, durs)):
        rel = tiros_rel[i] if tiros_rel and i < len(tiros_rel) else None
        if rel is not None:
            tiros.append(pos + (rel - ini))
        pos += d - mistura
        if i < n - 1:
            emendas.append(pos)
    ordenados = sorted(durs)

    def pct(q: float) -> float:
        return round(ordenados[min(len(ordenados) - 1, int(q * len(ordenados)))], 2) if ordenados else 0.0
    return {"cortes": n, "cortes_por_min": round(60.0 * n / total, 1) if total > 0 else None,
            "plano_s": {"p10": pct(0.10), "p50": pct(0.50), "p90": pct(0.90)},
            "emendas_na_batida": _fracao_perto(emendas, batidas_s), "tiros_na_batida": _fracao_perto(tiros, batidas_s),
            "emendas_no_compasso": _fracao_perto(emendas, fortes), "tiros_no_compasso": _fracao_perto(tiros, fortes),
            "emendas_na_frase": _fracao_perto(emendas, frases)}


def envelope_ducking(abates_s: list[float], total: float, profundidade_db: float, sr: int = 48000,
                     ataque: float = 0.008, sustenta: float = 0.04, solta: float = 0.15) -> np.ndarray:
    """C7: ganho linear (1,0 = sem mudança) para a MÚSICA: a cada abate cai `profundidade_db` dB em `ataque` s,
    segura `sustenta` s e volta em `solta` s. Determinístico: a redução é exatamente a pedida (um compressor com
    `ratio` não garante teto). Abates próximos (rajada) não se somam: vale o maior corte."""
    n = max(1, int(total * sr))
    ganho = np.ones(n, np.float32)
    fundo = 10.0 ** (-abs(profundidade_db) / 20.0)
    if profundidade_db == 0 or not abates_s:
        return ganho
    for t in abates_s:
        ini, fim = t - ataque, t + sustenta + solta
        a, b = max(0, int(ini * sr)), min(n, int(fim * sr) + 1)
        if b <= a:
            continue
        x = np.arange(a, b) / sr
        forma = np.clip((x - ini) / ataque, 0.0, 1.0) * np.clip((fim - x) / solta, 0.0, 1.0)
        forma = np.minimum(forma, 1.0)
        ganho[a:b] = np.minimum(ganho[a:b], (1.0 - (1.0 - fundo) * forma).astype(np.float32))
    return ganho


def gravar_envelope(ganho: np.ndarray, destino: Path, sr: int = 48000) -> Path:
    """O envelope como WAV mono de 16 bits (o ganho vira a amplitude da amostra), para o `amultiply` do ffmpeg."""
    pcm = np.clip(np.rint(ganho * 32767.0), 0, 32767).astype("<i2")
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return destino


def abates_na_montagem(inicios: list[float], durs: list[float], abates_rel: list[list[float]] | None,
                       mistura: float) -> list[float]:
    """Instante (s da montagem) de cada abate que ficou dentro do pedaço mantido de cada corte."""
    saida, pos = [], 0.0
    for i, (ini, d) in enumerate(zip(inicios, durs)):
        for rel in (abates_rel[i] if abates_rel and i < len(abates_rel) else []):
            t = pos + (rel - ini)
            if pos - 1e-6 <= t <= pos + d + 1e-6:
                saida.append(t)
        pos += d - mistura
    return saida


def _grade_em_loop(b: np.ndarray, dur_musica: float, total: float) -> np.ndarray:
    """A música toca em loop se a montagem for maior: repete a grade de batidas a cada volta."""
    if not len(b) or dur_musica <= 0:
        return b
    voltas = int(total // dur_musica) + 1
    return np.concatenate([b + k * dur_musica for k in range(voltas)])


# ── render ───────────────────────────────────────────────────────────────────────────────────────

def render_montagem(files: list[Path], out: Path, cfg: Config, musica: Path | None = None,
                    tiros_rel: list[float | None] | None = None, abates_rel: list[list[float]] | None = None) -> str:
    """Monta `files` (cortes 1920x1080 60 fps) em `out`. Devolve um resumo curto ("com música X", "sem música: ...")."""
    from .render import (AUDIO_ARGS, BT709_FILTRO, FPS, H16, W16, _run, _run_enc, audio_filter, measure_loudness,
                         video_args)
    if not files:
        raise ValueError("nada para juntar: nenhum trecho foi renderizado")
    borda = max(0.0, cfg.montagem.transicao)
    mistura = FADE_MISTURA if cfg.montagem.transicao_tipo == "fade" else 0.0
    brutos = [_duracao(f) for f in files]
    inicio = [borda if d > 2 * borda + 1.0 else 0.0 for d in brutos]
    durs = [max(0.5, d - 2 * b) for d, b in zip(brutos, inicio)]
    n_aud = [n_faixas_audio(f) for f in files]
    resumo = ""
    if musica is not None and not all(n >= 2 for n in n_aud):
        resumo = "sem música: algum corte não tem a faixa só do jogo"
        musica = None
    ULTIMO_RITMO.clear()
    pulo_musica = 0.0       # só ganha valor com música; o bloco do filtro abaixo também só roda com música
    if musica is not None:
        compasso = getattr(cfg.montagem, "grade_de_corte", "batida") == "compasso"   # C1-C3: opt-in; padrão = batida
        if compasso:
            b, dur_m, down, fonte_compasso = batidas_e_compasso(musica)
        else:
            (b, dur_m), down, fonte_compasso = batidas(musica), np.zeros(0), ""
        # Se tiros_rel não veio explícito, usa `cfg.cortes.antes` nos cortes longos o bastante
        rels = tiros_rel if tiros_rel and len(tiros_rel) == len(files) else [
            cfg.cortes.antes if br >= cfg.cortes.antes + 1.5 else None for br in brutos
        ]
        # A6 (dono, 08/11/2026): o 1º tiro da montagem cai no drop da música. A música começa `pulo_musica` s adiante e todas as
        # grades de batida/compasso andam juntas (a montagem continua cortando na batida, só que na da música já deslocada).
        i_tiro = next((k for k, r in enumerate(rels) if r is not None), None)
        rel_tiro = None if i_tiro is None else rels[i_tiro]
        primeiro_tiro = (None if i_tiro is None or rel_tiro is None
                         else sum(durs[:i_tiro]) - mistura * i_tiro + (rel_tiro - inicio[i_tiro]))
        pulo_musica, motivo_drop = 0.0, ""
        if getattr(cfg.montagem, "tiro_no_drop", True):
            drop = (entrada_do_catalogo_segura(musica) or {}).get("drop_s")
            pulo_musica, motivo_drop = deslocamento_para_o_drop(drop, b, dur_m, primeiro_tiro,
                                                                sum(durs) - mistura * max(0, len(durs) - 1) + 1.0)
        grade = _grade_em_loop(b, dur_m, sum(durs) + 10 + pulo_musica) - pulo_musica
        grade = grade[grade >= 0]
        if len(down):
            fortes = _grade_em_loop(down, dur_m, sum(durs) + 10 + pulo_musica) - pulo_musica
            fortes = fortes[fortes >= 0]
        else:
            fortes = None
        if getattr(cfg.montagem, "kill_sync", True):
            inicio, durs = alinhar_tiros_e_emendas(inicio, durs, rels, grade, mistura, fortes=fortes)
        elif getattr(cfg.montagem, "corte_na_batida", True):
            durs = alinhar_na_batida(durs, grade, mistura)
        frases = None
        if fortes is not None and len(fortes):
            from .compasso import inicios_de_frase
            frases = inicios_de_frase(fortes, int((entrada_do_catalogo_segura(musica) or {}).get("phrase_bars") or 4))
        ULTIMO_RITMO.update(metricas_ritmo(inicio, durs, rels, mistura, grade, fortes, frases))
        ULTIMO_RITMO["compasso"] = fonte_compasso if compasso else "grade de batidas"
        if i_tiro is not None and rel_tiro is not None:   # A6: onde o 1º tiro ficou em relação ao drop (0 = em cima); None = a faixa não tem drop alinhável
            tiro_final = sum(durs[:i_tiro]) - mistura * i_tiro + (rel_tiro - inicio[i_tiro])
            drop_na_montagem = (float((entrada_do_catalogo_segura(musica) or {}).get("drop_s") or 0.0) - pulo_musica) if pulo_musica > 0 else None
            ULTIMO_RITMO["primeiro_tiro_vs_drop_ms"] = (round(1000 * (tiro_final - drop_na_montagem))
                                                       if drop_na_montagem is not None else None)
            ULTIMO_RITMO["musica_pula_s"] = pulo_musica
            if motivo_drop:
                ULTIMO_RITMO["drop_motivo"] = motivo_drop
        resumo = f"com música {musica.name}" + (f" (~{60 / float(np.median(np.diff(b))):.0f} BPM)" if len(b) > 2 else "")
        if compasso:
            resumo += f"; {fonte_compasso}"
    total = sum(durs) - mistura * (len(files) - 1)
    com_audio = musica is not None or any(n_aud)
    trilha = 1 if musica is not None else 0

    fil: list[str] = []
    for i, (f, ini, d) in enumerate(zip(files, inicio, durs)):
        fil.append(f"[{i}:v]trim=start={ini:.3f}:duration={d:.3f},setpts=PTS-STARTPTS,"
                   f"scale={W16}:{H16}:force_original_aspect_ratio=decrease,pad={W16}:{H16}:(ow-iw)/2:(oh-ih)/2,"
                   f"setsar=1,fps={FPS},format=yuv420p,settb=AVTB[v{i}]")
        if not com_audio:
            continue
        if n_aud[i] > trilha:
            fil.append(f"[{i}:a:{trilha}]atrim=start={ini:.3f}:duration={d:.3f},asetpts=PTS-STARTPTS,"
                       f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                       f"afade=t=in:d={MICRO_FADE},afade=t=out:st={max(0.0, d - MICRO_FADE):.3f}:d={MICRO_FADE}[a{i}]")
        else:   # corte mudo: silêncio do mesmo tamanho (o som dos outros não pode ser descartado)
            fil.append(f"aevalsrc=0|0:d={d:.3f}:s=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
    n = len(files)
    if mistura > 0 and n > 1:
        ant, pos = "v0", durs[0]
        for i in range(1, n):
            fil.append(f"[{ant}][v{i}]xfade=transition=fade:duration={mistura}:offset={pos - mistura:.3f}[x{i}]")
            ant, pos = f"x{i}", pos + durs[i] - mistura
        fil.append(f"[{ant}]{BT709_FILTRO}[v]")
        if com_audio:
            ant = "a0"
            for i in range(1, n):
                fil.append(f"[{ant}][a{i}]acrossfade=d={mistura}:c1=tri:c2=tri[y{i}]")
                ant = f"y{i}"
            fil.append(f"[{ant}]anull[aj0]")
    else:
        fil.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,{BT709_FILTRO}[v]")
        if com_audio:
            fil.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[aj0]")

    entradas = [x for f in files for x in ("-i", str(f))]
    idx_in = n
    # V6: SFX CC0 sutil nas emendas quando `montagem.sfx = true` e há mais de 1 corte
    if com_audio and getattr(cfg.montagem, "sfx", False) and n > 1:
        from .sfx import garantir_sfx
        whoosh = garantir_sfx()["whoosh"]
        entradas += ["-i", str(whoosh)]
        idx_sfx = idx_in
        idx_in += 1
        # Posições de cada emenda em milissegundos
        emendas_ms: list[int] = []
        acum = 0.0
        for d in durs[:-1]:
            acum += d - mistura
            emendas_ms.append(max(0, int(round((acum - 0.07) * 1000))))
        ns = len(emendas_ms)
        fil.append(f"[{idx_sfx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=-16dB"
                   + (f",asplit={ns}" + "".join(f"[sw{k}]" for k in range(ns)) if ns > 1 else "[sw0]"))
        for k, ms in enumerate(emendas_ms):
            fil.append(f"[sw{k}]adelay={ms}|{ms}[sd{k}]")
        fil.append("[aj0]" + "".join(f"[sd{k}]" for k in range(ns)) + f"amix=inputs={ns + 1}:duration=first:normalize=0[aj]")
    elif com_audio:
        fil.append("[aj0]anull[aj]")

    if musica is not None:
        meas = measure_loudness(str(musica), pulo_musica, min(180.0, max(10.0, total)), cfg.qualidade.lufs)   # E3: o volume do trecho que TOCA, não dos 180 s do começo
        ganho = (cfg.qualidade.lufs + cfg.montagem.volume_musica - float(meas["input_i"])) if meas else 0.0
        entradas += ["-stream_loop", "-1", "-i", str(musica)]
        duck_db = float(getattr(cfg.montagem, "ducking_tiros_db", 0.0) or 0.0)
        abates_s = abates_na_montagem(inicio, durs, abates_rel, mistura) if duck_db > 0 else []
        com_duck = bool(abates_s)
        fil.append(f"[{idx_in}:a]atrim=start={pulo_musica:.3f}:duration={total:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                   f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={ganho:.2f}dB,"
                   f"afade=t=in:d=0.3,afade=t=out:st={max(0.0, total - 2.0):.3f}:d=2[{'m0' if com_duck else 'm'}]")
        if com_duck:   # C7: a música abaixa `duck_db` dB a cada abate (envelope pronto, multiplicado sobre a música)
            arq_duck = gravar_envelope(envelope_ducking(abates_s, total, duck_db), out.with_name(out.stem + ".duck.wav"))
            entradas += ["-i", str(arq_duck)]
            fil.append(f"[{idx_in + 1}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,"
                       f"pan=stereo|c0=c0|c1=c0[env];[m0][env]amultiply[m]")
            ULTIMO_RITMO["ducking_db"] = duck_db
            ULTIMO_RITMO["abates_com_ducking"] = len(abates_s)
        fil.append("[aj][m]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[a]")
    elif com_audio:
        fil.append("[aj]anull[a]")

    bruto = out.with_name(out.stem + ".mix" + out.suffix)
    try:
        _run_enc(lambda: [FFMPEG, "-y", "-v", "error", "-nostdin", *entradas, "-filter_complex", ";".join(fil),
                          "-map", "[v]", *(["-map", "[a]"] if com_audio else []), "-t", f"{total:.3f}",
                          *video_args(cfg), *(AUDIO_ARGS if com_audio else []), "-movflags", "+faststart",
                          str(bruto)])
        # 2ª passada do volume no arquivo inteiro: jogadas, emendas e música no mesmo nível (sem pulo)
        meas = measure_loudness(str(bruto), 0.0, total + 1.0, cfg.qualidade.lufs) if com_audio else None
        if meas:
            _run([FFMPEG, "-y", "-v", "error", "-nostdin", "-i", str(bruto), "-map", "0:v:0", "-map", "0:a:0",
                  "-c:v", "copy", "-af", audio_filter(meas, cfg.qualidade.lufs, total, 0.0), *AUDIO_ARGS,
                  "-movflags", "+faststart", str(out)])
        else:
            substituir_arquivo(bruto, out)
        from .render import garantir_pico
        garantir_pico(out, cfg)   # S1.4b: pico real <= -1 dBTP garantido no arquivo final
    finally:
        bruto.unlink(missing_ok=True)
        out.with_name(out.stem + ".duck.wav").unlink(missing_ok=True)
    return resumo
