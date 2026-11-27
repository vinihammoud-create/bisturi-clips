"""Orquestra: varredura -> classificação -> sinais -> momentos -> transcrição -> render -> relatório."""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import audio as aud
from .captions import build_ass
from .chat import chat_curve, download_chat, estimate_lag, shift, vod_id_from_name
from .classify import PlayerModel, classify, learn_player, parse_box
from .config import Config, retangulo_da_webcam
from . import progresso, qc, qualidade
from .dedup import resolver_duplicatas
from .sequencia import com_gancho, compactar, marcar_morte_no_fim, remover_repetidos, unir_sequencias
from .emotion import anotar
from .icons import icons_of
from .indice import atualizar_indice, ler_indice
from .layout import Layout, area_cached, cam_crop, detect_layout, ler_area, preview
from .moments import CATEGORIAS_FALHA_COM_REACAO, Event, Moment, abates_no_round, build_moments, diversificar_shorts, elegiveis, passa_regra_do_dono
from .mortes import mortes_cached
from .fails import achar_fails, marcar_reacao_na_morte
from .comando import achar_comandos, aplicar_comandos
from .publicar import gerar_publicar
from .nota import pontuar_todos
from .momentos_io import ajustes_dos_momentos, gravar_momentos, chave_do_momento, chaves_excluidas, ler_revisao
from .partes import capitulos_youtube, carregar_partes, dividir_partes, gravar_partes, linhas_capitulos
from .render import concat, geometria_do_short, render_clip, render_short, thumbnail, render_por_janelas
from .faixas import avisos_de_configuracao, comparar_perfis, faixa_de_voz, perfil_de_faixas
from .montagem import ULTIMO_RITMO as _RITMO_MONTAGEM, musica_da_montagem, ordenar_montagem, trilha_jogo
from .refazer import (CacheRender, hash_corte, hash_short, impressao, parse_refazer, refaz_varredura,
                      render_desatualizado)
from .registro import Registro, resumo_erro
from .report import entry
from .revisao import gerar_revisao, itens_revisao
from .rounds import RoundCollector, carregar_rounds, criar_ocr, salvar_rounds, scan_rounds
from .scan import probe, scan_video, scan_video_fast
from .sincronizar import clutch_do_momento, integrar
from .tools import gravar_atomico, imread, imwrite
from .transcribe import Word, hype_curve, hype_terms, transcribe_full, liberar_modelo, troca_de_modelo, zerar_troca_de_modelo
from .tools import substituir_arquivo


def _fmt(t: float) -> str:
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m:02d}m{s:02d}s"


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


_TEMPOS: list[dict] = []   # etapas do vídeo atual (Step.done anota; `tempos.json` grava): onde o tempo vai
_SOM_MONTAGEM: dict = {}   # música, LUFS e pico da montagem do vídeo atual (vai para o benchmark.json)
_GATES: dict[str, list] = {}   # conferências de qualidade por arquivo final do vídeo atual (vai para o qualidade.json)
_VIDEO_DOS_TEMPOS: list[str] = [""]   # de qual vídeo são os _TEMPOS acima (render_all de outro vídeo começa do zero)


class Step:
    def __init__(self, name: str):
        self.name, self.t0 = name, time.time()
        print(f"  • {name}...", flush=True)
        progresso.etapa(name)

    def done(self, info: str = "") -> None:
        dt = time.time() - self.t0
        _TEMPOS.append({"etapa": self.name, "segundos": round(dt, 1)})
        print(f"    ok ({dt:.0f}s){' — ' + info if info else ''}", flush=True)
        progresso.etapa_feita()


def _gravar_tempos(out: Path) -> None:
    """`saida/<vídeo>/tempos.json`: o tempo de cada etapa da última vez que o vídeo foi processado."""
    dados: dict[str, object] = {"etapas": _TEMPOS, "total_s": round(sum(e["segundos"] for e in _TEMPOS), 1)}
    troca = troca_de_modelo()
    if troca:   # P3.9: a GPU falhou e a fala desta análise veio do modelo leve (ou não veio): o dono precisa saber
        dados["fala_modelo"] = troca
    try:
        gravar_atomico(out / "tempos.json", json.dumps(dados, ensure_ascii=False, indent=1))
    except OSError:
        pass   # medir o tempo nunca pode derrubar o processamento


def _faixa_do_cache_confere(meta: Path, faixa_voz: int | None) -> bool:
    """O cache de voz foi feito lendo a mesma faixa que agora? Sem `voz.meta.json` (cache antigo) vale como "mistura"."""
    guardada = None
    if meta.exists():
        dados = _ler_cache(lambda: json.loads(meta.read_text(encoding="utf-8")))
        guardada = dados.get("faixa") if isinstance(dados, dict) else "ilegivel"
    return guardada == faixa_voz


def fails_para_compilar(fails: list, revisao: dict, exigir_aprovacao: bool) -> list:
    """C9: com `fails.exigir_aprovacao`, só entram na compilação os fails que o dono marcou "Gostei" (revisão do
    momentos.json, pela chave do momento). Desligado (padrão): entram todos, como sempre."""
    if not exigir_aprovacao:
        return list(fails)
    return [m for m in fails if (revisao.get(chave_do_momento(m)) or {}).get("aprovado") is True]


TRECHO_MIN = 1.0   # s: um trecho de sequência nunca fica menor que isto por causa do ajuste do dono


def aplicar_ajustes(moments: list, ajustes: dict, duracao: float, minimo: float) -> None:
    """C10: aplica o ajuste de início/fim que o dono fez na revisão (segundos, por momento). Nunca sai do vídeo e nunca
    deixa o corte menor que `minimo`. Em sequência de round, mexe no começo do 1º trecho e no fim do último."""
    for m in moments:
        aj = ajustes.get(chave_do_momento(m))
        if not aj:
            continue
        novo_fim = min(duracao, m.end + aj[1])
        novo_ini = min(max(0.0, m.start + aj[0]), novo_fim - minimo)
        if m.segmentos:   # sequência de round: o 1º e o último trecho precisam continuar com pelo menos TRECHO_MIN s
            novo_ini = min(novo_ini, m.segmentos[0][1] - TRECHO_MIN)
            novo_fim = max(novo_fim, m.segmentos[-1][0] + TRECHO_MIN)
        if novo_ini < 0 or novo_fim - novo_ini < minimo - 1e-9:
            continue   # corte curto demais para o ajuste caber: fica como estava
        if m.segmentos:
            segs = list(m.segmentos)
            segs[0] = (novo_ini, segs[0][1])
            segs[-1] = (segs[-1][0], novo_fim)
            if any(b - a < TRECHO_MIN - 1e-9 for a, b in segs):
                continue   # o ajuste engoliria um trecho inteiro: fica como estava
            m.segmentos = segs
        m.start, m.end = novo_ini, novo_fim


def registrar_perfil_de_faixas(faixas_do_video, audio, cache: Path, saida: Path) -> list[str]:
    """S1.3: grava o perfil de faixas do vídeo (`.cache/faixas.json`) e o último perfil conhecido (`saida/perfil_faixas.json`),
    comparando com o do vídeo anterior. Devolve os avisos. Nunca derruba o processamento."""
    try:
        atual = perfil_de_faixas(faixas_do_video, audio)
        global_ = saida / "perfil_faixas.json"
        anterior = None
        if global_.is_file():
            anterior = _ler_cache(lambda: json.loads(global_.read_text(encoding="utf-8")))
            anterior = anterior if isinstance(anterior, dict) else None
        avisos = comparar_perfis(anterior, atual)
        texto = json.dumps(atual, ensure_ascii=False, indent=1)
        cache.mkdir(parents=True, exist_ok=True)
        gravar_atomico(cache / "faixas.json", texto)
        saida.mkdir(parents=True, exist_ok=True)
        gravar_atomico(global_, texto)
        return avisos
    except Exception:  # noqa: BLE001
        return []


def _gravar_benchmark(out: Path, path: Path, info, cfg: Config, name: str) -> None:
    """`benchmark.json` + linha em `saida/benchmarks.csv` (cs2clip/benchmark.py). Nunca derruba o processamento."""
    try:
        from . import benchmark
        from .montagem import n_faixas_audio
        existe = Path(path).is_file()
        benchmark.gravar(out, info, cfg, name, list(_TEMPOS), dict(_SOM_MONTAGEM) or None,
                         n_faixas_audio(str(path)) if existe else 0, Path(path).stat().st_size if existe else 0)
    except Exception:  # noqa: BLE001
        pass


def _gates_do_arquivo(arquivo: Path, cfg: Config, esperado: qc.Esperado, medida) -> list:
    """As conferências do `qualidade.json` para um arquivo final. Nunca levanta erro: se algo falhar aqui, o arquivo
    fica NEEDS_REVIEW (não conferido), jamais "aprovado" nem derrubado."""
    try:
        q = cfg.qualidade
        return [qualidade.gate_integridade(arquivo, esperado),
                qualidade.gate_audio(medida, esperado.audio, q.lufs, q.gate_loudness),
                qualidade.gate_conteudo(arquivo, q.checar_conteudo, q.checar_silencio and esperado.audio)]
    except Exception as e:  # noqa: BLE001
        return [qualidade.Gate("conferencia", "NEEDS_REVIEW", None, "conferência concluída",
                               f"não consegui conferir ({str(e)[:100]})", "qualidade")]


DETECTOR_VERSAO = "7"  # sobe quando detect/scan mudam o que vai para o cache (v5: coletor também lê o banner escuro, 17/10/2026)
# v7 (18/10/2026): o coletor lê também a VITÓRIA azul do CT (antes ~1 em cada 6 rounds ficava sem banner)
# v6 (17/10/2026): regiões tiradas da área do jogo (barras pretas). Só muda vídeo COM barras: sem barras a varredura
# é idêntica, então o cache dessas versões continua valendo
VERSOES_SEM_BARRAS: tuple[str, ...] = ()   # v5 e v6 não leem a vitória azul: varrem de novo


def _nome_recorte(t: float, usados: set[str]) -> str:
    """<t com 8 dígitos e 2 casas>.png; duas trilhas no mesmo instante (abate + assistência no
    mesmo quadro) ganham sufixo _2, _3... para não sobrescrever o recorte uma da outra."""
    nome, k = f"{t:08.2f}.png", 1
    while nome in usados:
        k += 1
        nome = f"{t:08.2f}_{k}.png"
    usados.add(nome)
    return nome


def _versao_cache(area) -> str:
    """O que vai em `detector.versao`: com barras leva a área junto (área medida de outro jeito = varrer de novo)."""
    return DETECTOR_VERSAO if area is None else f"{DETECTOR_VERSAO}|{','.join(str(v) for v in area)}"


def _versao_vale(gravada: str, area) -> bool:
    return gravada == _versao_cache(area) or (area is None and gravada in VERSOES_SEM_BARRAS)


def _scan_cached(info, cache: Path, cfg: Config, refazer: bool, area=None) -> list[dict]:
    """`area`: área do jogo (layout.area_cached); None = jogo na tela toda."""
    raw = cache / "caixas.json"
    crops = cache / "caixas"
    versao = cache / "detector.versao"
    if raw.exists() and not refazer:
        caixas, gravada = _ler_kill_feed(raw, versao)
        rounds_ok = not (cache / "rounds.json").exists() or carregar_rounds(cache) is not None
        if caixas is None or not rounds_ok:
            print("    cache do kill feed ilegível (gravação interrompida?) — varrendo de novo")
        elif gravada is not None and _versao_vale(gravada, area):
            return caixas
        else:
            print("    cache do kill feed de uma versão antiga do detector — varrendo de novo")
    crops.mkdir(parents=True, exist_ok=True)
    done = [0.0]
    lock = threading.Lock()
    last = [0]

    def prog(sec: float) -> None:
        with lock:
            done[0] += sec
            pct = int(100 * done[0] / max(1.0, info.duration))
            if pct >= last[0] + 10:
                last[0] = pct - pct % 10
                print(f"    {last[0]}%", flush=True)

    coletor = RoundCollector(area=area)  # banner de fim de round, lido na mesma passada de quadros-chave
    tracks = None
    if cfg.deteccao.modo == "rapido":
        tracks = scan_video_fast(info, fps=cfg.deteccao.fps_analise, workers=cfg.deteccao.processos, progress=prog,
                                 on_frame=coletor.feed, area=area)
        if tracks is None:
            print("    keyframes muito espaçados neste vídeo — usando a varredura completa")
    if tracks is not None:
        salvar_rounds(cache, coletor.result())
    else:
        tracks = scan_video(info, fps=cfg.deteccao.fps_analise, workers=cfg.deteccao.processos, progress=prog,
                            area=area)
        print("    lendo os banners de fim de round (varredura lenta, uns minutos)...")
        salvar_rounds(cache, scan_rounds(info.path, criar_ocr(), area=area))
    out = []
    usados: set[str] = set()
    sem_recorte = 0
    for tr in tracks:
        f = crops / _nome_recorte(tr.first, usados)
        try:
            imwrite(f, tr.best_crop)
        except OSError as e:     # recorte que não foi gravado não entra no cache: o nome dele apontaria para o vazio (R-028)
            sem_recorte += 1
            print(f"    [aviso] caixa em {tr.first:.1f}s sem recorte gravado ({e}); ela fica fora do cache")
            continue
        out.append({"t": round(tr.first, 2), "fim": round(tr.last, 2), "hits": tr.hits, "box": list(tr.box),
                    "crop": f.name})
    # R-020/A-006: caixas e versão do detector numa gravação só (formato 2); nunca em dois arquivos, em nenhuma ordem
    gravar_atomico(raw, json.dumps({"formato": 2, "versao": _versao_cache(area), "caixas": out}, indent=1))
    try:
        versao.unlink(missing_ok=True)     # sobra do formato antigo: o `caixas.json` novo já leva a própria versão
    except OSError:
        pass
    return out


def _ler_kill_feed(raw: Path, versao: Path) -> tuple[list[dict] | None, str | None]:
    """(caixas, versão do detector) do cache do kill feed. Formato 2: um JSON só `{"formato", "versao", "caixas"}`.
    Formato antigo (de antes do R-020): `caixas.json` com a lista + `detector.versao` ao lado, ainda lido para não
    invalidar cache que já existe. Caixas `None` = ilegível; versão `None` = sem versão gravada."""
    bruto = _ler_cache(lambda: json.loads(raw.read_text(encoding="utf-8")))
    if isinstance(bruto, dict):
        caixas, gravada = bruto.get("caixas"), bruto.get("versao")
        if not isinstance(caixas, list) or not isinstance(gravada, str):
            return None, None
        return caixas, gravada.strip()
    gravada = _ler_cache(lambda: versao.read_text(encoding="utf-8").strip()) if versao.exists() else None
    return (bruto if isinstance(bruto, list) else None), gravada


def _gravar_planos(moments: list[Moment], cfg: Config, pasta: Path, nome_de) -> int:
    """Grava o `EditPlan` de cada momento. Um momento estranho só perde o plano dele (R-024): os outros saem.
    Devolve quantos planos foram gravados."""
    from .plano import criar_plano_momento
    gravados = 0
    for m in moments:
        try:
            criar_plano_momento(m, cfg, formato="16:9").salvar(pasta / f"{nome_de(m)}.json")
            gravados += 1
        except Exception as e_plano:  # noqa: BLE001  (o plano é registro auditável: não pode derrubar a análise)
            print(f"    (aviso ao gravar o plano do momento {getattr(m, 'idx', '?')}: {(str(e_plano).splitlines() or [repr(e_plano)])[0]})")
    return gravados


def _ler_cache(ler):
    """Lê um arquivo do .cache. Ilegível (gravação interrompida por Cancelar ou queda de energia, de antes da
    gravação atômica) = como se não existisse: a etapa é refeita em vez de derrubar a análise em toda execução."""
    try:
        return ler()
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        return None


def _npy(arr) -> bytes:
    buf = io.BytesIO()
    np.save(buf, arr)
    return buf.getvalue()


def _atomico(destino: Path, fazer) -> None:
    """Escreve em `<nome>.tmp.<ext>` e só troca pelo destino no fim: se o programa for interrompido no meio,
    nunca sobra um arquivo pela metade com o nome final (que seria reaproveitado como "pronto")."""
    tmp = destino.with_name(f"{destino.stem}.tmp{destino.suffix}")
    _apagar_parcial(tmp)
    try:
        fazer(tmp)
        substituir_arquivo(tmp, destino)
    except BaseException:
        _apagar_parcial(tmp)
        raise


REPLAY_ANTES, REPLAY_DEPOIS, REPLAY_VELOCIDADE = 0.8, 0.5, 0.5   # replay lento do último abate (versão com efeitos)


def _tem_efeitos(m: Moment, cfg: Config) -> bool:
    return bool(cfg.shorts.versao_efeitos and m.kills)


def _para_short(m: Moment, cfg: Config, reserva: float = 0.0) -> Moment:
    """Short de uma jogada longa: versão COMPACTA (menos tempo antes/depois de cada abate) para caber em
    `shorts.duracao_maxima`; se mesmo assim não couber, ficam TODOS os abates (regra de ouro do dono) e só a folga em volta deles
    encolhe, até o mínimo. Corte horizontal segue inteiro."""
    curto = compactar(m, cfg.shorts.duracao_maxima - reserva, cfg.shorts.antes, cfg.shorts.depois,
                      cfg.cortes.pular_parado)
    return com_gancho(curto, cfg.shorts.gancho)


_NOME_DO_PROGRAMA = re.compile(r"^\d{2,}_.+(_vertical)?\.(mp4|jpg)$")


def _limpar_velhos(pastas, validos: set[Path]) -> int:
    """Apaga das pastas de saída os arquivos gerados pelo programa que não fazem parte do resultado atual.
    Nunca toca em arquivo com outro padrão de nome (algo que o usuário tenha posto ali)."""
    apagados = 0
    for pasta in pastas:
        for f in pasta.iterdir():
            if f.is_file() and _NOME_DO_PROGRAMA.match(f.name) and f not in validos:
                try:
                    f.unlink()
                    apagados += 1
                except OSError:
                    pass
    return apagados


def _dur_esperada(m: Moment, info) -> float:
    """Duração que o corte deve ter: a do momento, ou menos se ele termina além do fim do vídeo."""
    return max(0.1, min(m.duration, info.duration - m.janelas[0][0]))


FALA_MIN_VOZ = 0.01   # fração do vídeo com fala abaixo da qual a gravação é considerada sem voz


def _mistura_sem_voz(out: Path, info, words: list, cfg: Config) -> bool:
    """A gravação não tem voz nenhuma (nem a sua nem a do Discord)? Exige DOIS sinais independentes: o detector de
    fala (VAD) achou menos de 1% de fala E o Whisper não reconheceu nenhuma palavra. Na prévia (sem Whisper) ou sem a
    análise de fala guardada, não dá para saber: False (a montagem segue a regra de sempre)."""
    if cfg.modo_previa or words:
        return False
    try:
        segs = json.loads((out / ".cache" / "fala_trechos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    fala = sum(float(fim) - float(ini) for ini, fim in segs)
    return fala < FALA_MIN_VOZ * max(1.0, float(info.duration))


def _dur_corte(m: Moment, info, cfg: Config) -> float:
    """Duração do corte 16:9: a de `_dur_esperada`, ou a que sobra depois dos efeitos de tempo (speed-ramp encurta,
    congelamento alonga) quando eles estão ligados."""
    base = _dur_esperada(m, info)
    if not (cfg.cortes.speed_ramp or cfg.cortes.freeze_headshot):
        return base
    from .efeitos import duracao_do_corte
    total = sum(duracao_do_corte(dataclasses.replace(m, start=a, end=b, segmentos=[]), cfg) for a, b in m.janelas)
    return max(0.1, total - (m.duration - base))   # o que passa do fim do vídeo continua não contando


def duracao_da_montagem(arquivo: Path, soma_dos_momentos: float) -> float:
    """Duração REAL do arquivo da montagem, para o resumo da etapa. A soma da duração dos momentos passa do arquivo (cada corte perde as bordas
    da transição e há mistura entre cortes: F5, 63 s no texto × 58,98 s no arquivo). Arquivo ilegível: cai para a soma."""
    try:
        real = float(probe(str(arquivo)).duration)
    except Exception:  # noqa: BLE001  (VideoInvalido, ffprobe ausente...: o resumo nunca derruba a montagem)
        return soma_dos_momentos
    return real if real > 0 else soma_dos_momentos


def _esperado(cfg: Config, largura: int, altura: int, duracao: float | None, audio_ok: bool) -> qc.Esperado:
    """O que o arquivo gerado deve ter (ffprobe) + a checagem de conteúdo (tela preta, e silêncio se ligado)."""
    q = cfg.qualidade
    return qc.Esperado(largura, altura, duracao, audio_ok, 60.0,
                       conteudo=q.checar_conteudo, silencio=q.checar_silencio and audio_ok)


def _renderizar_e_checar(tmp: Path, render, esperado: qc.Esperado, nome: str) -> None:
    """Gera o arquivo temporário e o confere (ffprobe). Se reprovar, levanta erro: o `_atomico` descarta o
    temporário e o arquivo bom que já existia continua no lugar."""
    render()
    qc.exigir(tmp, esperado, nome)


def _apagar_parcial(f: Path) -> None:
    """Apaga o arquivo que um render deixou pela metade. Nunca levanta erro: falhar na limpeza não pode
    derrubar o programa nem esconder o erro de verdade do render."""
    try:
        if f.is_file():
            f.unlink()
    except OSError:
        pass


def _origem(path: Path, info) -> dict:
    """Impressão do vídeo de origem: tamanho em bytes + duração + resolução. Sem a data de modificação:
    copiar o arquivo muda a data e forçaria uma reanálise cara à toa."""
    return {"tamanho": Path(path).stat().st_size, "duracao": round(info.duration, 2),
            "largura": info.width, "altura": info.height}


def _ler_origem(cache: Path) -> dict | None:
    try:
        d = json.loads((cache / "origem.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _hash6(path: Path) -> str:
    return hashlib.sha1(os.path.normcase(str(Path(path).resolve())).encode("utf-8")).hexdigest()[:6]


def _pasta_do_video(path: Path, info, vod: str | None, out_root: Path) -> str:
    """Nome da pasta de saída: o VOD ou o nome do arquivo (as pastas que já existem continuam valendo).
    Ganha '-<hash6 do caminho>' só quando o nome vem vazio (só caracteres não latinos) ou quando a pasta já
    é de OUTRO vídeo que ainda existe (mesmo começo de nome, duas partes do mesmo VOD)."""
    base = vod or _slug(path.stem)[:40]
    if not base:
        return f"video-{_hash6(path)}"
    gravada = _ler_origem(out_root / base / ".cache")
    if gravada is None or gravada == _origem(path, info):
        return base
    dono = next((x.get("caminho") for x in ler_indice(out_root) if x.get("id") == base), "")
    eu = os.path.normcase(str(Path(path).resolve()))
    if dono and os.path.normcase(str(Path(dono))) != eu and Path(dono).is_file():
        return f"{base}-{_hash6(path)}"
    return base


def _conferir_origem(cache: Path, origem: dict) -> None:
    """O arquivo foi trocado por outro vídeo com o mesmo nome: apaga o .cache inteiro (kill feed, mortes, voz,
    falas, chat, webcam, render_meta) antes de usar. Sem origem.json (análise antiga), o cache é mantido."""
    gravada = _ler_origem(cache)
    if gravada is not None and gravada != origem:
        print("    vídeo diferente com o mesmo nome: refazendo a análise")
        try:
            shutil.rmtree(cache)
        except OSError as e:   # cache pela metade misturaria os dois vídeos: melhor parar
            raise RuntimeError(f"não consegui apagar o cache antigo em {cache}: feche o que estiver usando "
                               "essa pasta e rode de novo") from e
    cache.mkdir(parents=True, exist_ok=True)
    gravar_atomico(cache / "origem.json", json.dumps(origem))


def _termos_nas_janelas(words: list[Word], m: Moment) -> list[str]:
    """Termos de empolgação só do que aparece no vídeo: numa sequência, o tempo parado entre os trechos é
    cortado pelo render e não pode dar rótulo nem nota."""
    termos: list[str] = []
    for a, b in m.janelas:
        for t in hype_terms(words, a, b):
            if t not in termos:
                termos.append(t)
    return termos


def _resumo_sincronia(relatorio: list[dict]) -> str:
    """Uma linha por demo para o terminal: usada (e o quanto mudou) ou por que ficou de fora."""
    partes = []
    for r in relatorio:
        if not r.get("usada"):
            partes.append(f"{r['demo'][-24:]}: não usada ({r.get('motivo', '')})")
            continue
        partes.append(f"{r['mapa']} {r['placar'][0]}x{r['placar'][1]}: {r['casados']}/{r['total_demo']} abates casados, "
                      f"deslocamento {r['deslocamento']:+.1f} s, rounds {r['rounds_video']}→{r['rounds_demo']}, "
                      f"{r['abates_recuperados']} abate(s) recuperado(s), {r['abates_falsos_removidos']} falso(s) removido(s)")
    return "; ".join(partes)


def _marcar_clutches(moments: list[Moment], clutches: list) -> None:
    for m in moments:
        if m.kind != "abate" or not m.kills:
            continue
        c = clutch_do_momento(m.kills, clutches)
        if c is None:
            continue
        m.clutch = c
        if c[1] and len(m.kills) < 5:   # ACE continua ACE; o resto vira CLUTCH 1vX
            m.label = f"CLUTCH 1v{c[0]}"


def _miniatura(src: str, foco: float, duracao: float, tmp: Path) -> None:
    """Quadro logo depois do abate. Abate nos últimos décimos do vídeo pediria um instante depois do fim (o
    ffmpeg não gera quadro nenhum): o instante fica preso antes do fim."""
    t = max(0.0, min(foco + 0.3, duracao - 0.1))
    thumbnail(src, t, tmp)
    if not tmp.is_file():
        raise RuntimeError(f"sem quadro em t={t:.1f}s")


def caixa_da_webcam_inteira(s, lay: Layout | None) -> tuple[int, int, int, int] | None:
    """23/11/2026 (layout `webcam_inteira`): a caixa da webcam em pixels de 1920x1080, INTEIRA (com o letreiro), sem zoom no rosto. O retângulo dado em
    Configurar (`shorts.webcam_retangulo`) vale mais que o achado pela análise (`lay.cam`); sem nenhum dos dois, None (o short sai sem painel)."""
    if lay is None or lay.face is None:     # revisão de 23/11: sem webcam no vídeo (kit limpo, OBS só do jogo), o retângulo fixo viraria um pedaço do jogo
        return None
    dado = retangulo_da_webcam(s.webcam_retangulo)
    if dado is not None:
        return dado
    if lay.cam is None:
        return None
    x, y, w, h = lay.cam
    par = lambda v: int(round(v / 2) * 2)  # noqa: E731  (dimensões pares para o yuv420)
    return par(x * 1920), par(y * 1080), par(w * 1920), par(h * 1080)


def _posicao_da_caixa(b: dict) -> tuple[float, float, float] | None:
    """(fim, y, largura) de uma caixa do `caixas.json` (cache antigo sem esses campos: None, e a regra de posição não vale)."""
    try:
        return float(b["fim"]), float(b["box"][1]), float(b["box"][2])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _com_resenhas(path: Path, cfg: Config, moments: list[Moment], segs: list, voice, words: list, duracao: float) -> list[Moment]:
    """A4: acrescenta os momentos de RESENHA (cs2clip/resenha.py). Nunca derruba a análise."""
    from .resenha import achar_resenhas, carregar_amigos
    try:
        transcrever = None
        if cfg.resenha.risadas_amigos and not cfg.modo_previa:   # com CS2/OBS abertos (prévia) nada de Whisper na GPU (CONSTITUICAO §3.5)
            from .transcribe import transcribe
            modelo, idioma = cfg.transcricao.modelo, cfg.transcricao.idioma
            transcrever = lambda audio, inicio: transcribe(audio, inicio, modelo, idioma)   # noqa: E731
        try:
            discord, falas_amigos, aviso = carregar_amigos(str(path), cfg.audio.faixa_comms, cfg.audio.faixa_jogo, transcrever,
                                                           usar_faixa_do_jogo=cfg.resenha.conversa_na_faixa_do_jogo,
                                                           faixa_voz=cfg.audio.faixa_voz, segs_mic=segs)
        finally:
            if transcrever is not None:
                liberar_modelo()   # a memória de vídeo fica livre para o render (a transcrição principal já tinha soltado o modelo)
        if aviso:
            print(f"    [aviso] {aviso}")
        novas, substituidas = achar_resenhas(voice, segs, discord, words, duracao, moments,
                                             cfg.resenha.quantidade, cfg.resenha.min_sinais, cfg.resenha.discord_basta,
                                             falas_amigos=falas_amigos)
    except Exception as e:  # noqa: BLE001
        print(f"    [aviso] resenha: não consegui analisar ({resumo_erro(str(e))}); seguindo sem ela")
        return moments
    if not novas:
        print("    resenha: nenhum momento com 2 ou mais sinais juntos (Discord alto, várias vozes, você gritando, risadas)")
        return moments
    fora = {id(m) for m in substituidas}
    moments = sorted([m for m in moments if id(m) not in fora] + novas, key=lambda m: m.start)
    for i, m in enumerate(moments, 1):
        m.idx = i
    print(f"    resenha: {len(novas)} momento(s) engraçado(s): "
          + "; ".join(f"{_fmt(m.start)} ({m.reasons[0].removeprefix('resenha: ')})" for m in novas))
    return moments


def _resolver_demos_auto(candidatas: list[Path], events: list[Event], quem: str) -> list[tuple]:
    """B1: de cada demo candidata, descobre quem é o dono (ou usa o nome dado) comparando os abates com o kill feed deste vídeo.
    Demo sem sincronia confiável com ninguém é descartada, com o motivo. Devolve [(PartidaDemo, nome do arquivo)]."""
    from .demo import DemoInvalida, ler_demo
    from .demos_cs2 import escolher_dono
    tempos = [e.t for e in events if e.kind == "kill"]
    saida: list[tuple] = []
    for dem in candidatas:
        motivos: list[str] = []
        dono = escolher_dono(dem, tempos, quem or None, motivos=motivos)
        if dono is None:
            if motivos:      # a demo nem abriu: dizer isso, e não "não é desta gravação"
                print(f"    [aviso] {motivos[0].rstrip('. ')}. Demo não usada.")           # o motivo já traz o nome da demo
            else:
                print(f"    [aviso] demo {dem.name}: não é desta gravação (nenhum jogador dela tem abates que casem com os do vídeo). Não usada.")
            continue
        try:
            partida = ler_demo(dem, dono.steamid)
        except DemoInvalida as e:
            print(f"    [aviso] demo {dem.name} ignorada: {e}")
            continue
        dica = "" if quem else f" Para não precisar descobrir de novo, ponha [demo] jogador = \"{dono.nome}\" no config.toml."
        print(f"    demo {dem.name}: você é '{dono.nome}' ({dono.casados} de {dono.total_demo} abates casam com o vídeo, "
              f"confiança {dono.confianca}).{dica}")
        saida.append((partida, dem.name))
    return saida


def analyze(path: Path, cfg: Config, out_root: Path, player_dir: Path,
            refazer: frozenset[str] | bool = frozenset(), demos: list | None = None,
            demos_auto: list[Path] | None = None, dono: str = ""):
    """Análise completa de um vídeo. Retorna (info, nome, pasta, momentos, palavras, layout).
    `refazer`: etapas que ignoram o cache (ver `refazer.py`); True = todas.
    `demos`: [(PartidaDemo, nome do arquivo)] das partidas que estão no vídeo (cs2clip/sincronizar.py).
    `demos_auto`: arquivos .dem candidatos (B1, achados na pasta do CS2): só valem as que casam com os abates deste vídeo;
    `dono` é o seu nome na demo (vazio = descobre pelos abates)."""
    R = parse_refazer(refazer)
    _TEMPOS.clear()   # tempos.json conta só as etapas deste vídeo
    zerar_troca_de_modelo()
    _SOM_MONTAGEM.clear()
    _GATES.clear()
    info = probe(str(path))
    vod = vod_id_from_name(str(path))
    name = _pasta_do_video(path, info, vod, out_root)
    _VIDEO_DOS_TEMPOS[0] = name
    out = out_root / name
    cache = out / ".cache"
    print(f"\n▶ {path.name}  ({_fmt(info.duration)}, {info.width}x{info.height} @ {info.fps:.0f}fps)")
    _conferir_origem(cache, _origem(path, info))
    tem_som = qc.tem_audio(str(path))   # sem trilha de som: o ffmpeg -vn falharia e derrubaria a análise

    st = Step("Varrendo o kill feed")
    # área do jogo: com barras pretas (4:3 numa live 16:9), kill feed/banner/painel são frações dela
    area = area_cached(cache, info, refaz_varredura(R))
    if area:
        print(f"    jogo com barras pretas: área {area[2]}x{area[3]} em x={area[0]}, y={area[1]}")
    boxes = _scan_cached(info, cache, cfg, refaz_varredura(R), area)
    st.done(f"{len(boxes)} caixas destacadas")

    st = Step("Classificando abates/assistências")
    parsed = []
    sem_recorte = 0
    for b in boxes:
        crop = imread(cache / "caixas" / b["crop"])
        sem_recorte += crop is None
        parsed.append((b, parse_box(crop) if crop is not None else None))
    if sem_recorte:    # antes isso era invisível: o abate virava "unknown" calado (R-028)
        print(f"    [aviso] {sem_recorte} caixa(s) sem o recorte no cache; não dá para classificar. "
              "Reanalise com --refazer kills se faltar abate.")
    model = learn_player([p for _, p in parsed if p], PlayerModel.load(player_dir))
    if model:
        try:
            model.save(player_dir)
        except OSError as e:      # o treino do nome é um atalho para o próximo vídeo: falhar ao gravá-lo não pode derrubar a análise deste
            print(f"    [aviso] não consegui guardar o treino do nome do jogador ({e}); a análise segue")
    kinds = [classify(p, model) if (p and model) else "unknown" for _, p in parsed]
    # A mesma linha do kill feed às vezes vira 2-3 trilhas: sem juntar, viram um DOUBLE KILL falso
    instante, descartados = resolver_duplicatas([(b["t"], k, p) for (b, p), k in zip(parsed, kinds)],
                                                [b["hits"] for b, _ in parsed],
                                                posicoes=[_posicao_da_caixa(b) for b, _ in parsed])
    events: list[Event] = []
    for i, ((b, p), kind) in enumerate(zip(parsed, kinds)):
        if i in descartados:
            continue
        events.append(Event(instante[i] if i in instante else b["t"], kind, icons_of(p) if p else [], b["crop"]))
    nk = sum(e.kind == "kill" for e in events)
    na = sum(e.kind == "assist" for e in events)
    nhs = sum(e.kind == "kill" and e.hs for e in events)
    st.done(f"{nk} abates ({nhs} HS), {na} assistências, {len(descartados)} duplicatas juntadas")

    st = Step("Lendo as suas mortes (painel \"matou você\")")
    mortes = mortes_cached(cache, str(path), info, cfg.deteccao.processos, "mortes" in R, area)
    st.done(f"{len(mortes)} mortes")

    rounds = carregar_rounds(cache) or []
    fusao = None
    if demos_auto and not demos:   # B1: a demo achada na pasta do CS2 só vale se os abates dela casam com os deste vídeo
        demos = _resolver_demos_auto(demos_auto, events, dono)
    if demos:   # a demo da partida corrige abates, mortes e rounds com os fatos exatos (nada é gravado no cache)
        st = Step("Cruzando com a demo da partida")
        fusao = integrar(demos, events, mortes, rounds, info.duration)
        events, mortes, rounds = fusao.events, fusao.mortes, fusao.rounds
        gravar_atomico(out / "sincronia.json", json.dumps(fusao.relatorio, ensure_ascii=False, indent=1))
        st.done(_resumo_sincronia(fusao.relatorio))

    audio_pcm: list = []  # carregado só se voz ou falas precisarem (decodificar o áudio leva tempo)
    # B2: com `audio.faixa_voz` válida, voz/Whisper/emoção leem a faixa do MICROFONE em vez da mistura (sem tiros nem Discord)
    faixa_voz, aviso_voz = faixa_de_voz(str(path), cfg.audio.faixa_voz) if tem_som else (None, "")
    if tem_som:   # S1.2: confere as faixas do [audio] contra o vídeo e avisa (uma vez por vídeo)
        from .montagem import n_faixas_audio
        for aviso in avisos_de_configuracao(cfg.audio, n_faixas_audio(str(path))):
            print(f"    [aviso] {aviso}")
        try:   # S1.3: perfil de faixas deste vídeo (rápido, sem medir fala) e comparação com o do vídeo anterior
            from .faixas import analisar as _analisar_faixas
            for aviso in registrar_perfil_de_faixas(_analisar_faixas(str(path), com_fala=False), cfg.audio, cache, out_root):
                print(f"    [aviso] {aviso}")
        except Exception:  # noqa: BLE001
            pass
    if aviso_voz:
        print(f"    [aviso] {aviso_voz}")

    def audio():
        if not audio_pcm:
            audio_pcm.append(aud.load_audio(str(path), faixa=faixa_voz))
        return audio_pcm[0]

    st = Step("Analisando a voz do streamer (VAD + volume relativo)")
    vcache, scache = cache / "voz.npy", cache / "fala_trechos.json"
    voz_cache = (_ler_cache(lambda: (np.load(vcache), json.loads(scache.read_text(encoding="utf-8"))))
                 if tem_som and vcache.exists() and scache.exists() and "voz" not in R
                 and _faixa_do_cache_confere(cache / "voz.meta.json", faixa_voz) else None)
    if not tem_som:   # curva zerada do tamanho que voice_curve daria; nada vai para o cache
        segs, voice = [], np.zeros(int(info.duration / aud.HOP), np.float32)
        print("    vídeo sem som: só abates (sem voz, falas e risadas)")
    elif voz_cache is not None:
        voice, segs = voz_cache
    else:
        segs = aud.speech_segments(audio())
        voice = aud.voice_curve(audio(), segs)
        gravar_atomico(vcache, _npy(voice))
        gravar_atomico(scache, json.dumps(segs))
        gravar_atomico(cache / "voz.meta.json", json.dumps({"faixa": faixa_voz}))
    speech_pct = 100 * sum(e - s for s, e in segs) / max(1.0, info.duration)
    origem_voz = f", lida da faixa {faixa_voz + 1} (microfone)" if faixa_voz is not None else ""
    st.done(f"fala em {speech_pct:.0f}% do vídeo, {len(aud.find_peaks(voice, 0.6, 0.3, 20.0))} picos de grito{origem_voz}")

    st = Step("Transcrevendo as falas (Whisper na GPU)")
    tcache, tmeta = cache / "falas.json", cache / "falas.meta.json"
    meta: dict[str, str | int] = {"modelo": cfg.transcricao.modelo, "idioma": cfg.transcricao.idioma}
    if faixa_voz is not None:   # só entra quando usada: quem lê a mistura (o de sempre) mantém o cache que já tem
        meta["faixa"] = faixa_voz
    meta_ok = tmeta.exists() and _ler_cache(lambda: json.loads(tmeta.read_text(encoding="utf-8"))) == meta
    falas_cache = (_ler_cache(lambda: [Word(**w) for w in json.loads(tcache.read_text(encoding="utf-8"))])
                   if tem_som and tcache.exists() and meta_ok and "falas" not in R else None)
    if not tem_som:
        words = []   # sem Whisper: em silêncio ele inventa frases
    elif falas_cache is not None:
        words = falas_cache
    elif cfg.modo_previa:
        words = []   # trava de GPU: sem Whisper novo enquanto o jogo/OBS usam a placa
        print("    (prévia: sem transcrição guardada e a GPU está ocupada; falas e risadas ficam de fora)")
    else:
        words = transcribe_full(audio(), cfg.transcricao.modelo, cfg.transcricao.idioma)
        liberar_modelo()   # a memória de vídeo fica livre para o render
        troca = troca_de_modelo()
        if troca:   # a GPU falhou e a fala veio do modelo leve (ou nenhum): o cache não vale como o modelo pedido, a próxima análise tenta de novo
            meta["modelo"] = troca["modelo_usado"] or "nenhum"
        gravar_atomico(tcache, json.dumps([w.__dict__ for w in words], ensure_ascii=False))
        gravar_atomico(tmeta, json.dumps(meta))
    talk = hype_curve(words, info.duration, aud.HOP)
    st.done("pulado (vídeo sem som)" if not tem_som
            else f"{len(words)} palavras" if words or not cfg.modo_previa else "pulado (prévia)")

    chat = None
    if cfg.chat.usar and vod:
        st = Step(f"Chat da Twitch (VOD {vod})")
        cj = cache / "chat.json"
        if "chat" in R:
            cj.unlink(missing_ok=True)
        raw = None
        if download_chat(vod, cj):
            try:
                raw, n = chat_curve(cj, info.duration)
            except (ValueError, AttributeError):   # chat.json corrompido (download interrompido): apaga e segue
                cj.unlink(missing_ok=True)
                st.done("arquivo do chat estava corrompido: apagado (baixa de novo na próxima análise)")
            else:
                lag = estimate_lag(raw, [e.t for e in events if e.kind == "kill"], cfg.chat.atraso)
                chat = shift(raw, lag) if n else None
                st.done(f"{n} mensagens, atraso do chat {lag:.1f}s")
        else:
            st.done("indisponível")

    st = Step("Montando e pontuando as jogadas")
    moments = build_moments(events, voice, chat, talk, words, info.duration, cfg)
    for m in moments:
        m.hype_words = _termos_nas_janelas(words, m)
    st.done(f"{sum(m.kind == 'abate' for m in moments)} jogadas, {sum(m.kind == 'reacao' for m in moments)} reações")

    st = Step("Dividindo em partidas e rounds, e lendo a emoção")
    abates_t = [e.t for e in events if e.kind == "kill"]
    partes = dividir_partes(rounds, abates_t, info.duration, exatos=fusao.exatos if fusao else None)
    duvidosas = [pt for pt in partes if pt.precisa_confirmar]
    if duvidosas:   # B7: nada é descartado em silêncio: o que ficou incerto aparece marcado na revisão
        print(f"    [aviso] {len(duvidosas)} trecho(s) com classificação incerta (partida curta, Deathmatch/aquecimento ou sem round lido): "
              "confira na revisão os momentos marcados CONFIRMAR PARTE")
    antes_n = len(moments)
    moments = unir_sequencias(moments, partes, cfg.cortes, words, info.duration, mortes)   # abates do round = 1 corte
    unidas = antes_n - len(moments)   # antes de entrarem fails e comandos, que mudam o total
    moments = remover_repetidos(moments, cortes=cfg.cortes, maximo=cfg.cortes.sequencia_max)
    moments = aplicar_comandos(moments, achar_comandos(words), info.duration)   # "clipa isso" na live
    marcar_morte_no_fim(moments, mortes, info.duration)   # R3: morreu logo depois do último abate: o corte mostra e o kit diz
    if cfg.cortes.falha_com_reacao:   # 23/11: e se você reagiu (raiva/risada) depois, o corte vai até o fim da reação
        if marcar_reacao_na_morte(moments, words, info.duration):
            moments = remover_repetidos(moments, cortes=cfg.cortes, maximo=cfg.cortes.sequencia_max)   # revisão de 23/11: a reação não fica em 2 cortes
    if cfg.resenha.gerar and tem_som:   # A4: momentos engraçados (2+ sinais juntos); a reação comum que eles cobrem sai
        moments = _com_resenhas(path, cfg, moments, segs, voice, words, info.duration)
    if cfg.fails.gerar:   # trilha de FAILS (sua morte + frustração ou risada), separada das jogadas
        moments = sorted(moments + achar_fails(mortes, words, voice, abates_t, moments, info.duration,
                                               cfg.fails.quantidade), key=lambda m: m.start)
        for i, m in enumerate(moments, 1):
            m.idx = i
    for m in moments:
        m.hype_words = _termos_nas_janelas(words, m)   # só as janelas que o render mostra
    if fusao is not None:   # clutch 1vX: só a demo sabe
        _marcar_clutches(moments, fusao.clutches)
    anotar(moments, partes, voice, words, abates_t, cfg.cortes.limiar_reacao, mortes)
    pontuar_todos(moments, partes, cfg.cortes.limiar_reacao, cfg.cortes.falha_com_reacao)   # nota 0-99 explicável + faixa
    gravar_partes(out, partes, name, info.duration)
    gravar_momentos(out, moments, name, info.duration, DETECTOR_VERSAO)
    atualizar_indice(out_root, name, path.name, info.duration, moments, partes,
                     datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), str(Path(path).resolve()))
    n_part = sum(p.tipo == "partida" for p in partes)
    st.done(f"{len(rounds)} rounds lidos, {n_part} partida(s) ("
            + ", ".join(p.placar for p in partes if p.tipo == "partida") + "), "
            + f"{sum(m.emocao is not None for m in moments)} momentos com emoção"
            + (f", {unidas} corte(s) unidos em sequências do mesmo round" if unidas else ""))

    lay = None
    if cfg.shorts.gerar:
        st = Step("Detectando a webcam (layout vertical)")
        lc = cache / "layout.json"

        def do_cache() -> Layout:
            d = json.loads(lc.read_text(encoding="utf-8"))
            return Layout(tuple(d["cam"]) if d["cam"] else None, tuple(d["face"]) if d["face"] else None, d["conf"])

        lay_cache = _ler_cache(do_cache) if lc.exists() and "webcam" not in R else None
        if cfg.shorts.camera_manual and len(cfg.shorts.camera_manual) == 4:
            x, y, w, h = cfg.shorts.camera_manual
            lay = Layout((x, y, w, h), (x + w * 0.4, y + h * 0.25, w * 0.2, h * 0.35), 1.0)
        elif lay_cache is not None:
            lay = lay_cache
        else:
            lay = detect_layout(str(path), info.duration)
            gravar_atomico(lc, json.dumps({"cam": lay.cam, "face": lay.face, "conf": lay.confidence}))
            try:   # é só a imagem de conferência: falhar aqui não pode derrubar a análise
                preview(str(path), info.duration, lay, str(out / "layout_detectado.png"))
            except Exception as e:  # noqa: BLE001
                print(f"    [aviso] não consegui gerar layout_detectado.png ({e})")
        st.done("webcam encontrada" if lay.face else "sem webcam — short só com gameplay")
    _gravar_tempos(out)
    return info, name, out, moments, words, lay


def _avisar_nada_para_postar(out: Path, jogadas: list[Moment], aptas: list[Moment], por_round: dict[int, int], c, keep: list[Moment],
                             best: list[Moment], gerar_shorts: bool, tem_so_short: bool = False) -> None:
    """Diz ao dono, em palavras, por que a montagem ou os shorts não saíram (nunca fica em silêncio) e grava/apaga `NADA_PARA_POSTAR.txt`."""
    aviso = out / "NADA_PARA_POSTAR.txt"
    if not jogadas:
        aviso.unlink(missing_ok=True)
        return
    if c.min_abates_round > 0 and not aptas and not (tem_so_short and best):
        maximo = max(por_round.values(), default=0)
        frase = (f"Nenhuma jogada com {c.min_abates_round} ou mais abates seus nesta partida (a maior teve {maximo}). Nada para postar. "
                 "Os cortes de cada jogada continuam na pasta cortes. (regra cortes.min_abates_round)")
        print(f"    [aviso] {frase}", flush=True)
        try:
            aviso.write_text(frase + chr(10), encoding="utf-8")
        except OSError:
            pass
        return
    aviso.unlink(missing_ok=True)
    if gerar_shorts and aptas and not best:   # sem isto o dono vê "0 shorts" e não sabe se quebrou
        regra = f" que passam na regra dos {c.min_abates_round} abates" if c.min_abates_round > 0 else ""
        print(f"    [aviso] nenhum short: a maior nota entre as jogadas{regra} foi {max(m.score for m in aptas):.0f} e o corte para "
              f"virar short é {c.nota_short:g} (cortes.nota_short). As jogadas continuam em cortes/ e as melhores na montagem.", flush=True)
    elif gerar_shorts and not best:
        print("    [aviso] nenhum short: nenhuma jogada passou na regra do round.", flush=True)
    if not keep and aptas:
        print(f"    [aviso] nenhuma montagem: a maior nota entre as jogadas que passam na regra foi {max(m.score for m in aptas):.0f} "
              f"e o corte da montagem é {c.nota_montagem:g} (cortes.nota_montagem).", flush=True)


def _assinatura_cam(cam_video: Path) -> str:
    """Entra na impressão do short: trocar a origem da webcam (outra live, outro recorte, outro deslocamento) refaz o short; a mesma origem reaproveita. Vale a assinatura
    que a edição grava ao lado (`PARTIDA_WEBCAM.json`); a data do arquivo NÃO serve, porque ela muda a cada edição (R2 da revisão de 13/11)."""
    try:
        return "cam:" + str(json.loads(Path(cam_video).with_suffix(".json").read_text(encoding="utf-8"))["assinatura"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        st = Path(cam_video).stat()
        return f"cam:{Path(cam_video).name}:{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return "cam:?"


def render_all(path: Path, cfg: Config, info, name: str, out: Path, moments: list[Moment],
               words: list[Word], lay: Layout | None, registro: Registro | None = None,
               refazer: frozenset[str] = frozenset(), cam_video: Path | None = None) -> tuple[list[dict], list[Path], list[tuple[Moment, Path]]]:
    clips_dir, shorts_dir, thumbs_dir = out / "cortes", out / "shorts", out / "miniaturas"
    for d in (clips_dir, shorts_dir, thumbs_dir):
        d.mkdir(parents=True, exist_ok=True)
    # estado da render anterior não vaza para esta (G-G05): conferências, som da montagem e ritmo são só do render.
    # Os tempos da análise ficam (a mesma execução analisa e renderiza), a não ser que sejam de outro vídeo.
    _GATES.clear()
    _SOM_MONTAGEM.clear()
    _RITMO_MONTAGEM.clear()
    if _VIDEO_DOS_TEMPOS[0] != name:
        _TEMPOS.clear()
        _VIDEO_DOS_TEMPOS[0] = name

    # cortes: todos os momentos | montagem e shorts: só os que passam da nota
    c = cfg.cortes
    todos = moments
    bases = {m.idx: (m.start, m.end, list(m.segmentos)) for m in todos}   # tempos da análise, antes do ajuste do dono
    moments = elegiveis(moments, c.so_partidas)  # só partidas competitivas viram corte, montagem e short
    fora_de_partida = len(todos) - len(moments)
    descartados = chaves_excluidas(out / "momentos.json")
    descartados_m: list[Moment] = []   # fora de tudo, mas o corte e a miniatura que já existem ficam (dá para rever/restaurar)
    if descartados:   # o que o usuário descartou na revisão (interface ou momentos.json) não é renderizado
        antes = len(moments)
        descartados_m = [m for m in moments if chave_do_momento(m) in descartados]
        moments = [m for m in moments if chave_do_momento(m) not in descartados]
        if antes != len(moments):
            print(f"    ({antes - len(moments)} momento(s) que você descartou na revisão não foram renderizados)")
    aplicar_ajustes(moments, ajustes_dos_momentos(out / "momentos.json"), float(getattr(info, "duration", 0.0) or 1e9),
                    c.duracao_minima)   # C10: início/fim acertados pelo dono na revisão
    jogadas = [m for m in moments if m.kind != "fail"]         # fails têm compilação própria
    # montagem: jogadas a partir da nota mínima; reação só com emoção detectada (sem emoção era conversa: gabarito)
    # A2/A3 (dono, 08/11/2026): montagem e short só com round de N+ abates seus (ou resenha/pedido); o corte 16:9 de cada jogada continua
    por_round = abates_no_round(jogadas)
    aptas = [m for m in jogadas if passa_regra_do_dono(m, por_round[id(m)], c.min_abates_round)]
    # 23/11 (opção `cortes.falha_com_reacao`, desligada): "começou bem e terminou mal, com a reação" (basta 1 abate) e FAILS com raiva ou risada vão SÓ para os
    # shorts: a música da montagem cobriria a reação, que é a graça (mesma razão da resenha)
    so_short: list[Moment] = []
    if c.falha_com_reacao:
        ja = {id(m) for m in aptas}
        so_short = [m for m in jogadas if id(m) not in ja and passa_regra_do_dono(m, por_round[id(m)], c.min_abates_round, True)]
        so_short += [m for m in moments if m.kind == "fail" and m.categoria in CATEGORIAS_FALHA_COM_REACAO]
    keep = [m for m in aptas if m.score >= c.nota_montagem and m.vale_destaque
            and (m.categoria != "resenha" or cfg.montagem.incluir_resenha)]
    best = diversificar_shorts(aptas + so_short, c.nota_short, cfg.shorts.quantidade) if cfg.shorts.gerar else []
    _avisar_nada_para_postar(out, jogadas, aptas, por_round, c, keep, best, cfg.shorts.gerar, tem_so_short=bool(so_short))

    def fname(m: Moment) -> str:
        return f"{m.idx:02d}_{_slug(m.label)}_{_fmt(m.start)}"

    cam_box = None
    if cfg.shorts.layout == "webcam_inteira":      # 23/11: a webcam INTEIRA (o retângulo dado em Configurar, ou o achado pela análise), sem zoom no rosto
        cam_box = caixa_da_webcam_inteira(cfg.shorts, lay)
    elif lay and lay.face:
        aspect = cfg.shorts.largura / cfg.shorts.camera_altura
        cam_box = cam_crop(lay, 1920, 1080, aspect, cfg.shorts.zoom_camera)
    # área do jogo (gravada pela análise): com barras pretas, o recorte do short segue o jogo, não o quadro
    area_jogo = ler_area(out / ".cache", getattr(info, "width", 0), getattr(info, "height", 0))[1]
    painel = bool(cam_box or cam_video)      # S5: há painel de cima no short (recorte da webcam do vídeo, ou o vídeo só da webcam da live)

    st = Step(f"Renderizando {len(moments)} cortes 16:9 e {len(best)} shorts 9:16 (GPU)")
    errors: list[tuple[str, BaseException]] = []

    forcar = render_desatualizado(out / ".cache", cfg, refazer)
    # --refazer falas só refaz os shorts (têm legenda); o corte 16:9 não usa as falas
    cache_r = CacheRender(out / ".cache", "cortes" in refazer, "falas" in refazer)
    h_corte, h_short = hash_corte(cfg), hash_short(cfg)   # na impressão: config nova refaz só o tipo afetado
    # A1: faixa só com o jogo (gravação multipista) vai junto no corte, para a montagem com música tirar a voz
    trilha, motivo_trilha = trilha_jogo(str(path), cfg, mistura_sem_voz=_mistura_sem_voz(out, info, words, cfg))
    if motivo_trilha:
        print(f"    (áudio: {motivo_trilha})")
    cfg._trilha_jogo = trilha
    h_clip = h_corte + (f"|jogo{trilha}" if trilha is not None else "")
    for d in (clips_dir, shorts_dir, thumbs_dir, out):  # sobras de uma execução interrompida
        for sobra in d.glob("*.tmp.*"):
            _apagar_parcial(sobra)
    if not forcar and any((clips_dir / f"{fname(m)}.mp4").exists() for m in moments):
        print("    (reaproveitando os cortes e shorts já renderizados que continuam valendo; "
              "use --refazer cortes para refazer tudo)")
    if fora_de_partida > 0:
        print(f"    ({fora_de_partida} momentos fora de partida ficaram só na análise; "
              "para incluí-los use so_partidas = false no config.toml)")

    def job_clip(m: Moment) -> None:
        clip, thumb = clips_dir / f"{fname(m)}.mp4", thumbs_dir / f"{fname(m)}.jpg"
        imp_clip, imp_thumb = impressao(m, h_clip), f"{m.focus:.1f}"
        try:   # miniatura e corte separados: miniatura que falha não impede o corte
            if not cache_r.pronto(thumb, imp_thumb):
                _atomico(thumb, lambda tmp: _miniatura(str(path), m.focus, info.duration, tmp))
                cache_r.registrar(thumb, imp_thumb)
        except Exception as e:  # noqa: BLE001
            errors.append((f"miniatura {fname(m)}", e))
        try:
            if not cache_r.pronto(clip, imp_clip):
                _atomico(clip, lambda tmp: _renderizar_e_checar(
                    tmp, lambda: render_por_janelas(m, tmp, lambda mm, dest: render_clip(str(path), mm, dest, cfg), cfg),
                    _esperado(cfg, 1920, 1080, _dur_corte(m, info, cfg), audio_ok), f"corte {fname(m)}"))
                cache_r.registrar(clip, imp_clip)
        except Exception as e:  # noqa: BLE001
            errors.append((f"corte {fname(m)}", e))

    def fazer_short(m_inteiro: Moment, efeitos: bool) -> None:
        """Versão limpa (legenda + selo + zoom) ou com efeitos (+ tremida no abate e replay lento do último)."""
        # jogada longa: short compacto; na versão com efeitos, sobra espaço para o replay dentro do limite
        replay = (REPLAY_ANTES + REPLAY_DEPOIS) / REPLAY_VELOCIDADE if efeitos and m_inteiro.kills else 0.0
        try:   # erro aqui subiria pelo ex.map e derrubaria o render do vídeo inteiro
            m = _para_short(m_inteiro, cfg, reserva=replay)
        except Exception as e:  # noqa: BLE001
            errors.append((f"short {fname(m_inteiro)}{' (efeitos)' if efeitos else ''}", e))
            return
        sufixo = "_vertical_efeitos" if efeitos else "_vertical"
        saida_short = shorts_dir / f"{fname(m_inteiro)}{sufixo}.mp4"
        # webcam, legendas, versão e a config dos shorts mudam o short
        imp_short = impressao(m, f"{cam_box}|{len(words)}|{sufixo}|{h_short}" + (f"|{area_jogo}" if area_jogo else "") + (f"|{_assinatura_cam(cam_video)}" if cam_video else ""))
        if cache_r.pronto(saida_short, imp_short):
            return
        ass = shorts_dir / f"{fname(m_inteiro)}{sufixo}.ass"

        def short_de_um_trecho(mm: Moment, dest: Path) -> None:
            geo = geometria_do_short(cfg.shorts, cam_box, bool(cam_video))
            build_ass(ass, words, mm, mm.start, mm.end, cfg.shorts.largura, cfg.shorts.altura,
                      geo["jogo_y"] if painel else 0, cfg.shorts.legendas,
                      cfg.shorts.selo_jogada, cfg.shorts.marca if painel and not geo["inteira"] else "", geo["topo"] if painel else 0,   # webcam_inteira: nada nos 150 px de cima (§7)
                      gancho=None if cfg.shorts.gancho_titulo else False,
                      selo_a_partir=max(2, cfg.cortes.min_abates_round))
            render_short(str(path), mm, dest, cfg, cam_box, ass, tremida=efeitos, area_jogo=area_jogo, cam_video=str(cam_video) if cam_video else None)

        def montar(tmp: Path) -> None:
            if not efeitos or not m.kills:
                render_por_janelas(m, tmp, short_de_um_trecho, cfg)
                return
            principal = tmp.with_name(tmp.stem + ".principal" + tmp.suffix)
            replay = tmp.with_name(tmp.stem + ".replay" + tmp.suffix)
            try:
                janelas_m = m.janelas
                m._janela_loudness = (janelas_m[0][0], janelas_m[-1][1])
                m._cache_loudness = {}
                render_por_janelas(m, principal, short_de_um_trecho, cfg)
                ultimo = m.kills[-1].t
                rm = dataclasses.replace(m, start=max(0.0, ultimo - REPLAY_ANTES), end=ultimo + REPLAY_DEPOIS,
                                         segmentos=[])
                rm._janela_loudness = m._janela_loudness
                rm._cache_loudness = m._cache_loudness
                render_short(str(path), rm, replay, cfg, cam_box, None, velocidade=REPLAY_VELOCIDADE,
                             area_jogo=area_jogo, cam_video=str(cam_video) if cam_video else None)
                concat([principal, replay], tmp, cfg)
            finally:
                _apagar_parcial(principal)
                _apagar_parcial(replay)

        extra = (REPLAY_ANTES + REPLAY_DEPOIS) / REPLAY_VELOCIDADE if efeitos and m.kills else 0.0
        try:
            _atomico(saida_short, lambda tmp: _renderizar_e_checar(
                tmp, lambda: montar(tmp),
                _esperado(cfg, cfg.shorts.largura, cfg.shorts.altura, _dur_esperada(m, info) + extra, audio_ok),
                f"short {fname(m_inteiro)}{' (efeitos)' if efeitos else ''}"))
            cache_r.registrar(saida_short, imp_short)
        except Exception as e:  # noqa: BLE001
            errors.append((f"short {fname(m_inteiro)}{' (efeitos)' if efeitos else ''}", e))
        finally:
            _apagar_parcial(ass)

    def job_short(m_inteiro: Moment) -> None:
        fazer_short(m_inteiro, efeitos=False)
        if _tem_efeitos(m_inteiro, cfg):   # sem abate, a versão "com efeitos" seria igual à limpa
            fazer_short(m_inteiro, efeitos=True)

    audio_ok = qc.tem_audio(str(path))   # sem som na entrada não dá para cobrar som do corte
    total_jobs, feitos, trava = len(moments) + len(best), [0], threading.Lock()

    def contado(job):
        def run(m):
            job(m)
            with trava:
                feitos[0] += 1
                progresso.detalhe(f"{feitos[0]} de {total_jobs}")
        return run

    with ThreadPoolExecutor(cfg.qualidade.renders_paralelos) as ex:
        list(ex.map(contado(job_clip), moments))
        list(ex.map(contado(job_short), best))
    for contexto, exc in errors:
        print("    " + (registro.erro(contexto, exc) if registro else f"[erro] {contexto}: {resumo_erro(str(exc))}"))
    cache_r.salvar()
    st.done(f"{len(errors)} com erro" if errors else "")

    montage_parts: list[Path] = []
    order: list[Moment] = []            # a ordem da montagem (só preenchida com a montagem ligada)
    na_montagem: list[Moment] = []      # os cortes que de fato entraram nela
    tiros_rel: list[float | None] = []
    abates_rel: list[list[float]] = []
    musica_montagem: Path | None = None   # decidida uma vez: o vídeo e o .fcpxml usam a mesma faixa
    if cfg.montagem.gerar and keep:
        order = ordenar_montagem(keep, cfg.montagem.ordem)
        na_montagem = [m for m in order if (clips_dir / f"{fname(m)}.mp4").exists()]
        montage_parts = [clips_dir / f"{fname(m)}.mp4" for m in na_montagem]
        tiros_rel = [(float(m.kills[0].t - m.start) if m.kills else None) for m in na_montagem]
        abates_rel = [[float(k.t - m.start) for k in m.kills if k.kind == "kill"] for m in na_montagem]
        minutos = sum(m.duration for m in na_montagem) / 60.0
        musica_montagem = musica_da_montagem(cfg, f"MONTAGEM_{name}",
                                             len(na_montagem) / minutos if minutos > 0 else 12.0,
                                             duracao_montagem=minutos * 60.0)
        if not montage_parts:
            print("    [aviso] montagem: nenhum corte dela foi renderizado (veja os erros acima)")
    # nada para montar: não deixa a montagem de uma análise anterior (com a montagem DESLIGADA, a antiga fica)
    if cfg.montagem.gerar and not montage_parts:
        _apagar_parcial(out / f"MONTAGEM_{name}.mp4")
    if montage_parts:
        st = Step("Montagem com as melhores jogadas")
        resumo_montagem: list[str] = []
        try:
            _atomico(out / f"MONTAGEM_{name}.mp4", lambda tmp: _renderizar_e_checar(
                tmp, lambda: resumo_montagem.append(
                    concat(montage_parts, tmp, cfg, tiros_rel=tiros_rel, musica=musica_montagem,
                           abates_rel=abates_rel) or ""),
                _esperado(cfg, 1920, 1080, None, audio_ok), "montagem"))
            # o que o dono precisa saber sem abrir o arquivo: música (ou por que ficou sem), volume e pico real
            medida = qc.medir_loudness(out / f"MONTAGEM_{name}.mp4") if audio_ok else None
            som = f", {medida[0]:.1f} LUFS, pico {medida[1]:.1f} dBTP" if medida else ""
            musica_txt = f" — {resumo_montagem[-1]}" if resumo_montagem and resumo_montagem[-1] else ""
            _SOM_MONTAGEM.update({"musica": musica_montagem.name if musica_montagem else "",
                                  "lufs": round(medida[0], 1) if medida else None,
                                  "pico_dbtp": round(medida[1], 1) if medida else None,
                                  "ritmo": dict(_RITMO_MONTAGEM)})
            st.done(f"{len(montage_parts)} jogadas, {duracao_da_montagem(out / f'MONTAGEM_{name}.mp4', sum(m.duration for m in na_montagem)):.0f}s{musica_txt}{som}")
            if medida and medida[1] > -1.0:
                print(f"    [aviso] montagem: pico real {medida[1]:.1f} dBTP acima de -1 (o YouTube pode distorcer)")
            _GATES[f"MONTAGEM_{name}.mp4"] = _gates_do_arquivo(out / f"MONTAGEM_{name}.mp4", cfg,
                                                               _esperado(cfg, 1920, 1080, None, audio_ok), medida)
        except Exception as e:  # noqa: BLE001
            print("    " + (registro.erro("montagem", e) if registro else f"[erro] montagem: {resumo_erro(str(e))}"))
            _GATES[f"MONTAGEM_{name}.mp4"] = [qualidade.falha(resumo_erro(str(e)))]

    # C8: a mesma montagem, com a voz do dono e sem música (outro modo de saída; a musical continua como está)
    arq_voz = out / f"MONTAGEM_VOZ_{name}.mp4"
    if cfg.montagem.gerar and not montage_parts:
        _apagar_parcial(arq_voz)   # sem jogadas, não deixa a versão com voz de uma análise anterior (montagem desligada: fica)
    elif montage_parts and cfg.montagem.versao_com_voz:
        st = Step("Montagem com a sua voz")
        try:
            _atomico(arq_voz, lambda tmp: _renderizar_e_checar(
                tmp, lambda: concat(montage_parts, tmp, cfg, tiros_rel=tiros_rel, musica=None),
                _esperado(cfg, 1920, 1080, None, audio_ok), "montagem com voz"))
            medida_v = qc.medir_loudness(arq_voz) if audio_ok else None
            st.done(f"{len(montage_parts)} jogadas, com o microfone e sem música"
                    + (f", {medida_v[0]:.1f} LUFS, pico {medida_v[1]:.1f} dBTP" if medida_v else ""))
            a_ = cfg.audio
            if a_.faixa_comms and not (a_.faixa_voz and a_.faixa_jogo):   # a promessa "sem Discord" só vale com microfone + jogo
                print("    [aviso] a montagem com voz usa a mistura da live (faixa 1), que pode ter o Discord dentro; "
                      "marque audio.faixa_voz e audio.faixa_jogo para ele ficar de fora")
            _GATES[arq_voz.name] = _gates_do_arquivo(arq_voz, cfg, _esperado(cfg, 1920, 1080, None, audio_ok), medida_v)
        except Exception as e:  # noqa: BLE001
            print("    " + (registro.erro("montagem_voz", e) if registro else f"[erro] montagem com voz: {resumo_erro(str(e))}"))
            _GATES[arq_voz.name] = [qualidade.falha(resumo_erro(str(e)))]

    fails = sorted((m for m in moments if m.kind == "fail"), key=lambda m: -m.score)
    todos_fails = len(fails)
    fails = fails_para_compilar(fails, ler_revisao(out / "momentos.json"), cfg.fails.exigir_aprovacao)
    if cfg.fails.exigir_aprovacao and todos_fails > len(fails):
        print(f"    [aviso] FAILS: {todos_fails - len(fails)} de {todos_fails} aguardam a sua revisão (marque Gostei) para entrar na compilação")
    partes_fails = [clips_dir / f"{fname(m)}.mp4" for m in fails if (clips_dir / f"{fname(m)}.mp4").exists()]
    if not partes_fails:   # sem fails: não deixa a compilação de uma análise anterior
        _apagar_parcial(out / f"FAILS_{name}.mp4")
    if partes_fails:
        st = Step("Compilação de FAILS")
        try:
            _atomico(out / f"FAILS_{name}.mp4", lambda tmp: _renderizar_e_checar(
                tmp, lambda: concat(partes_fails, tmp, cfg), _esperado(cfg, 1920, 1080, None, audio_ok), "fails"))
            st.done(f"{len(partes_fails)} momentos")
            _GATES[f"FAILS_{name}.mp4"] = _gates_do_arquivo(
                out / f"FAILS_{name}.mp4", cfg, _esperado(cfg, 1920, 1080, None, audio_ok),
                qc.medir_loudness(out / f"FAILS_{name}.mp4") if audio_ok else None)
        except Exception as e:  # noqa: BLE001
            print("    " + (registro.erro("fails", e) if registro else f"[erro] fails: {resumo_erro(str(e))}"))
            _GATES[f"FAILS_{name}.mp4"] = [qualidade.falha(resumo_erro(str(e)))]

    # exportações: JSON, capítulos para YouTube, EDL para DaVinci/Premiere
    # capítulos do YouTube: as PARTES do vídeo (válidos: 1º em 00:00, 3 ou mais, 10 s ou mais cada);
    # se não der para montar, cai na lista de jogadas (antes era sempre assim)
    caps = capitulos_youtube(carregar_partes(out), info.duration)
    if caps:
        chap = linhas_capitulos(caps)
    else:
        chap = ["00:00 Início"] + [f"{_fmt(m.start).replace('h', ':').replace('m', ':').rstrip('s')} {m.label}"
                                   for m in keep]
    (out / "capitulos_youtube.txt").write_text("\n".join(chap) + "\n", encoding="utf-8")

    # V9: Capa do YouTube com a melhor jogada
    if keep and Path(path).is_file():
        try:
            from .capa import gerar_capa, gerar_capas_candidatas
            cam_capa = caixa_da_webcam_inteira(cfg.shorts, lay) if cfg.capa.estilo == "chamativa" else None   # 23/11: a webcam INTEIRA no canto da capa chamativa
            gerar_capa(str(path), keep, out / f"CAPA_{name}.jpg", getattr(info, "duration", 0.0), cfg.capa.estilo, cam_box=cam_capa)
            if len(keep) > 1 or cfg.capa.alternativas_da_mesma_jogada:   # C10: alternativas de jogadas diferentes, com o motivo de cada uma (capas\capas.json); 23/11: com 1 jogada só, só se a opção estiver ligada
                gerar_capas_candidatas(str(path), keep, out / "capas", 3, getattr(info, "duration", 0.0), cfg.capa.estilo,
                                       mesma_jogada=cfg.capa.alternativas_da_mesma_jogada, cam_box=cam_capa)
        except Exception as e_capa:
            print(f"    (aviso ao gerar capa: {e_capa})")

    # M1: Planos de edição estruturados dos momentos em out / planos
    _gravar_planos(moments, cfg, out / "planos", fname)

    _write_edl(out / f"marcadores_{name}.edl", keep, path.name, fps=round(info.fps) or 60)
    if keep and montage_parts:
        from .davinci import exportar_fcpxml
        from .montagem import batidas
        musica_xml = musica_montagem   # a mesma faixa do vídeo da montagem
        batidas_xml: list[float] | None = None
        if musica_xml and musica_xml.is_file():
            try:
                b_arr, _ = batidas(musica_xml)
                batidas_xml = [float(x) for x in b_arr]
            except Exception:
                batidas_xml = None
        exportar_fcpxml(out / f"marcadores_{name}.fcpxml", order if cfg.montagem.gerar else keep,
                        montage_parts, fps=round(info.fps) or 60, musica=musica_xml, batidas_s=batidas_xml)

    # arquivos de execuções anteriores que deixaram de valer (numeração mudou, momento descartado, short que saiu
    # do top) confundem a pasta e a interface: só os do programa (nome "NN_...") e só os que não valem mais
    validos = ({clips_dir / f"{fname(m)}.mp4" for m in moments + descartados_m}
               | {thumbs_dir / f"{fname(m)}.jpg" for m in moments + descartados_m}
               | {shorts_dir / f"{fname(m)}_vertical.mp4" for m in best}
               | {shorts_dir / f"{fname(m)}_vertical_efeitos.mp4" for m in best if _tem_efeitos(m, cfg)})
    # shorts desligados (--sem-shorts): os shorts já prontos continuam valendo, nada é apagado em shorts/
    pastas_limpar = (clips_dir, shorts_dir, thumbs_dir) if cfg.shorts.gerar else (clips_dir, thumbs_dir)
    velhos = _limpar_velhos(pastas_limpar, validos)
    if velhos:
        print(f"    ({velhos} arquivo(s) de análises anteriores que não valem mais foram apagados)")

    arquivos = {}
    ids_descartados = {id(m) for m in descartados_m}
    for m in moments + descartados_m:
        # descartado não tem short (os dele são apagados acima); desligados: vale o short que já existe
        tem_short = id(m) not in ids_descartados and (m in best or not cfg.shorts.gerar)
        achados = {"corte": clips_dir / f"{fname(m)}.mp4",
                   "short": shorts_dir / f"{fname(m)}_vertical.mp4" if tem_short else None,
                   "short_efeitos": (shorts_dir / f"{fname(m)}_vertical_efeitos.mp4"
                                     if tem_short and (_tem_efeitos(m, cfg) or not cfg.shorts.gerar) else None),
                   "miniatura": thumbs_dir / f"{fname(m)}.jpg"}
        arquivos[m.idx] = {k: f"{p.parent.name}/{p.name}" if p is not None and p.exists() else None
                           for k, p in achados.items()}
    gravar_momentos(out, todos, name, info.duration, DETECTOR_VERSAO, arquivos, bases)
    com_short = best if cfg.shorts.gerar else sorted((m for m in jogadas if arquivos[m.idx]["short"]),
                                                     key=lambda m: -m.score)
    musica_usada = None
    if _SOM_MONTAGEM.get("musica") and (out / f"MONTAGEM_{name}.mp4").is_file():     # a faixa que está NO vídeo da montagem (crédito sai no publicar.txt)
        from .montagem import PASTA_MUSICAS
        from .musicas_catalogo import credito_da_faixa
        musica_usada = credito_da_faixa(PASTA_MUSICAS, str(_SOM_MONTAGEM["musica"]))
    pub = gerar_publicar(out, name, com_short, arquivos, cfg.shorts.marca, musica_usada)
    if pub:
        print(f"    Títulos e hashtags prontos para postar: {pub}")
    itens = itens_revisao(moments, arquivos)
    if itens:
        print(f"    Revisão dos momentos (assistir e clicar): {gerar_revisao(out, name, itens)}")
    else:   # nenhum corte: não deixa a revisão de uma análise anterior apontando para arquivos apagados
        (out / "revisao.html").unlink(missing_ok=True)

    entries = []
    for m in todos:  # a análise mostra todos; só os de partida têm corte/short/miniatura
        clip = clips_dir / f"{fname(m)}.mp4"
        short = shorts_dir / f"{fname(m)}_vertical.mp4"
        rel = lambda p: f"{name}/{p.parent.name}/{p.name}"  # noqa: E731
        entries.append(entry(m, path.name, rel(thumbs_dir / f"{fname(m)}.jpg") if (thumbs_dir / f"{fname(m)}.jpg").exists() else "",
                             rel(clip) if clip.exists() else None, rel(short) if short.exists() else None))
    # (momento, arquivo do corte) dos que entram na montagem: caminhos exatos, sem adivinhar por nome
    keep_arquivos = [(m, clips_dir / f"{fname(m)}.mp4") for m in keep if (clips_dir / f"{fname(m)}.mp4").exists()]
    _gravar_tempos(out)
    _gravar_benchmark(out, path, info, cfg, name)
    if _GATES:
        geral = qualidade.gravar(out, dict(_GATES))
        if geral != "PASS":
            print(f"    [qualidade] {geral} — veja {out / 'qualidade.json'}")
    return entries, montage_parts, keep_arquivos


def _tc(t: float, fps: int) -> str:
    f = int(round(t * fps))
    h, f = divmod(f, 3600 * fps)
    m, f = divmod(f, 60 * fps)
    s, f = divmod(f, fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _write_edl(path: Path, moments: list[Moment], src_name: str, fps: int) -> None:
    lines = [f"TITLE: Highlights {path.stem}", "FCM: NON-DROP FRAME", ""]
    rec = 0.0
    n = 0
    for m in moments:
        for a, b in m.janelas:                      # sequência com trechos cortados: um evento por trecho
            n += 1
            lines.append(f"{n:03d}  AX       AA/V  C        {_tc(a, fps)} {_tc(b, fps)} "
                         f"{_tc(rec, fps)} {_tc(rec + (b - a), fps)}")
            lines.append(f"* FROM CLIP NAME: {src_name}")
            lines.append(f"* COMMENT: {m.label} (nota {m.score:.0f})")
            lines.append("")
            rec += b - a
    path.write_text("\n".join(lines), encoding="utf-8")
