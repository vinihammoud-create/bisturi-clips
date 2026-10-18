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
from .config import Config
from . import progresso, qc
from .dedup import resolver_duplicatas
from .sequencia import com_gancho, compactar, remover_repetidos, unir_sequencias
from .emotion import anotar
from .icons import icons_of
from .indice import atualizar_indice, ler_indice
from .layout import Layout, area_cached, cam_crop, detect_layout, ler_area, preview
from .moments import Event, Moment, build_moments, elegiveis, escolher_shorts
from .mortes import mortes_cached
from .fails import achar_fails
from .comando import achar_comandos, aplicar_comandos
from .publicar import gerar_publicar
from .nota import pontuar_todos
from .momentos_io import gravar_momentos, chave_do_momento, chaves_excluidas
from .partes import capitulos_youtube, carregar_partes, dividir_partes, gravar_partes, linhas_capitulos
from .render import concat, render_clip, render_short, thumbnail, render_por_janelas
from .refazer import (CacheRender, hash_corte, hash_short, impressao, parse_refazer, refaz_varredura,
                      render_desatualizado)
from .registro import Registro, resumo_erro
from .report import entry
from .revisao import gerar_revisao, itens_revisao
from .rounds import RoundCollector, carregar_rounds, criar_ocr, salvar_rounds, scan_rounds
from .scan import probe, scan_video, scan_video_fast
from .tools import gravar_atomico, imread, imwrite
from .transcribe import Word, hype_curve, hype_terms, transcribe_full, liberar_modelo


def _fmt(t: float) -> str:
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m:02d}m{s:02d}s"


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


class Step:
    def __init__(self, name: str):
        self.name, self.t0 = name, time.time()
        print(f"  • {name}...", flush=True)
        progresso.etapa(name)

    def done(self, info: str = "") -> None:
        print(f"    ok ({time.time() - self.t0:.0f}s){' — ' + info if info else ''}", flush=True)
        progresso.etapa_feita()


DETECTOR_VERSAO = "6"  # sobe quando detect/scan mudam o que vai para o cache (v5: coletor também lê o banner escuro, 17/10/2026)
# v6 (17/10/2026): regiões tiradas da área do jogo (barras pretas). Só muda vídeo COM barras: sem barras a varredura
# é idêntica, então o cache dessas versões continua valendo
VERSOES_SEM_BARRAS = ("5",)


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
        caixas = _ler_cache(lambda: json.loads(raw.read_text(encoding="utf-8")))
        gravada = _ler_cache(lambda: versao.read_text(encoding="utf-8").strip()) if versao.exists() else None
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
    for tr in tracks:
        f = crops / _nome_recorte(tr.first, usados)
        imwrite(f, tr.best_crop)
        out.append({"t": round(tr.first, 2), "fim": round(tr.last, 2), "hits": tr.hits, "box": list(tr.box),
                    "crop": f.name})
    gravar_atomico(raw, json.dumps(out, indent=1))
    gravar_atomico(versao, _versao_cache(area))
    return out


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
        os.replace(tmp, destino)
    except BaseException:
        _apagar_parcial(tmp)
        raise


REPLAY_ANTES, REPLAY_DEPOIS, REPLAY_VELOCIDADE = 0.8, 0.5, 0.5   # replay lento do último abate (versão com efeitos)


def _tem_efeitos(m: Moment, cfg: Config) -> bool:
    return bool(cfg.shorts.versao_efeitos and m.kills)


def _para_short(m: Moment, cfg: Config, reserva: float = 0.0) -> Moment:
    """Short de uma jogada longa: versão COMPACTA (menos tempo antes/depois de cada abate) para caber em
    `shorts.duracao_maxima`; se mesmo assim não couber, fica o final da jogada. Corte horizontal segue inteiro."""
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


def _renderizar_e_checar(tmp: Path, render, esperado: "qc.Esperado", nome: str) -> None:
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


def _miniatura(src: str, foco: float, duracao: float, tmp: Path) -> None:
    """Quadro logo depois do abate. Abate nos últimos décimos do vídeo pediria um instante depois do fim (o
    ffmpeg não gera quadro nenhum): o instante fica preso antes do fim."""
    t = max(0.0, min(foco + 0.3, duracao - 0.1))
    thumbnail(src, t, tmp)
    if not tmp.is_file():
        raise RuntimeError(f"sem quadro em t={t:.1f}s")


def analyze(path: Path, cfg: Config, out_root: Path, player_dir: Path,
            refazer: frozenset[str] | bool = frozenset()):
    """Análise completa de um vídeo. Retorna (info, nome, pasta, momentos, palavras, layout).
    `refazer`: etapas que ignoram o cache (ver `refazer.py`); True = todas."""
    R = parse_refazer(refazer)
    info = probe(str(path))
    vod = vod_id_from_name(str(path))
    name = _pasta_do_video(path, info, vod, out_root)
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
    for b in boxes:
        crop = imread(cache / "caixas" / b["crop"])
        parsed.append((b, parse_box(crop) if crop is not None else None))
    model = learn_player([p for _, p in parsed if p], PlayerModel.load(player_dir))
    if model:
        model.save(player_dir)
    kinds = [classify(p, model) if (p and model) else "unknown" for _, p in parsed]
    # A mesma linha do kill feed às vezes vira 2-3 trilhas: sem juntar, viram um DOUBLE KILL falso
    instante, descartados = resolver_duplicatas([(b["t"], k, p) for (b, p), k in zip(parsed, kinds)],
                                                [b["hits"] for b, _ in parsed])
    events: list[Event] = []
    for i, ((b, p), kind) in enumerate(zip(parsed, kinds)):
        if i in descartados:
            continue
        events.append(Event(instante.get(i, b["t"]), kind, icons_of(p) if p else [], b["crop"]))
    nk = sum(e.kind == "kill" for e in events)
    na = sum(e.kind == "assist" for e in events)
    nhs = sum(e.kind == "kill" and e.hs for e in events)
    st.done(f"{nk} abates ({nhs} HS), {na} assistências, {len(descartados)} duplicatas juntadas")

    st = Step("Lendo as suas mortes (painel \"matou você\")")
    mortes = mortes_cached(cache, str(path), info, cfg.deteccao.processos, "mortes" in R, area)
    st.done(f"{len(mortes)} mortes")

    audio_pcm: list = []  # carregado só se voz ou falas precisarem (decodificar o áudio leva tempo)

    def audio():
        if not audio_pcm:
            audio_pcm.append(aud.load_audio(str(path)))
        return audio_pcm[0]

    st = Step("Analisando a voz do streamer (VAD + volume relativo)")
    vcache, scache = cache / "voz.npy", cache / "fala_trechos.json"
    voz_cache = (_ler_cache(lambda: (np.load(vcache), json.loads(scache.read_text(encoding="utf-8"))))
                 if tem_som and vcache.exists() and scache.exists() and "voz" not in R else None)
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
    speech_pct = 100 * sum(e - s for s, e in segs) / max(1.0, info.duration)
    st.done(f"fala em {speech_pct:.0f}% do vídeo, {len(aud.find_peaks(voice, 0.6, 0.3, 20.0))} picos de grito")

    st = Step("Transcrevendo as falas (Whisper na GPU)")
    tcache, tmeta = cache / "falas.json", cache / "falas.meta.json"
    meta = {"modelo": cfg.transcricao.modelo, "idioma": cfg.transcricao.idioma}
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
    rounds = carregar_rounds(cache) or []
    abates_t = [e.t for e in events if e.kind == "kill"]
    partes = dividir_partes(rounds, abates_t, info.duration)
    antes_n = len(moments)
    moments = unir_sequencias(moments, partes, cfg.cortes, words, info.duration, mortes)   # abates do round = 1 corte
    unidas = antes_n - len(moments)   # antes de entrarem fails e comandos, que mudam o total
    moments = remover_repetidos(moments, pular=cfg.cortes.pular_parado, maximo=cfg.cortes.sequencia_max)
    moments = aplicar_comandos(moments, achar_comandos(words), info.duration)   # "clipa isso" na live
    if cfg.fails.gerar:   # trilha de FAILS (sua morte + frustração ou risada), separada das jogadas
        moments = sorted(moments + achar_fails(mortes, words, voice, abates_t, moments, info.duration,
                                               cfg.fails.quantidade), key=lambda m: m.start)
        for i, m in enumerate(moments, 1):
            m.idx = i
    for m in moments:
        m.hype_words = _termos_nas_janelas(words, m)   # só as janelas que o render mostra
    anotar(moments, partes, voice, words, abates_t, cfg.cortes.limiar_reacao, mortes)
    pontuar_todos(moments, partes, cfg.cortes.limiar_reacao)   # nota 0-99 explicável + faixa
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
    return info, name, out, moments, words, lay


def render_all(path: Path, cfg: Config, info, name: str, out: Path, moments: list[Moment],
               words: list[Word], lay: Layout | None, registro: Registro | None = None,
               refazer: frozenset[str] = frozenset()) -> tuple[list[dict], list[Path], list[tuple[Moment, Path]]]:
    clips_dir, shorts_dir, thumbs_dir = out / "cortes", out / "shorts", out / "miniaturas"
    for d in (clips_dir, shorts_dir, thumbs_dir):
        d.mkdir(parents=True, exist_ok=True)

    # cortes: todos os momentos | montagem e shorts: só os que passam da nota
    c = cfg.cortes
    todos = moments
    moments = elegiveis(moments, c.so_partidas)  # só partidas competitivas viram corte, montagem e short
    fora_de_partida = len(todos) - len(moments)
    descartados = chaves_excluidas(out / "momentos.json")
    if descartados:   # o que o usuário descartou na revisão (interface ou momentos.json) não é renderizado
        antes = len(moments)
        moments = [m for m in moments if chave_do_momento(m) not in descartados]
        if antes != len(moments):
            print(f"    ({antes - len(moments)} momento(s) que você descartou na revisão não foram renderizados)")
    jogadas = [m for m in moments if m.kind != "fail"]         # fails têm compilação própria
    # montagem: jogadas a partir da nota mínima; reação só com emoção detectada (sem emoção era conversa: gabarito)
    keep = [m for m in jogadas if m.score >= c.nota_montagem and (m.kind == "abate" or m.emocao)]
    best = escolher_shorts(jogadas, c.nota_short, cfg.shorts.quantidade) if cfg.shorts.gerar else []

    def fname(m: Moment) -> str:
        return f"{m.idx:02d}_{_slug(m.label)}_{_fmt(m.start)}"

    cam_box = None
    if lay and lay.face:
        aspect = cfg.shorts.largura / cfg.shorts.camera_altura
        cam_box = cam_crop(lay, 1920, 1080, aspect, cfg.shorts.zoom_camera)
    # área do jogo (gravada pela análise): com barras pretas, o recorte do short segue o jogo, não o quadro
    area_jogo = ler_area(out / ".cache", getattr(info, "width", 0), getattr(info, "height", 0))[1]

    st = Step(f"Renderizando {len(moments)} cortes 16:9 e {len(best)} shorts 9:16 (GPU)")
    errors: list[tuple[str, BaseException]] = []

    forcar = render_desatualizado(out / ".cache", cfg, refazer)
    # --refazer falas só refaz os shorts (têm legenda); o corte 16:9 não usa as falas
    cache_r = CacheRender(out / ".cache", "cortes" in refazer, "falas" in refazer)
    h_corte, h_short = hash_corte(cfg), hash_short(cfg)   # na impressão: config nova refaz só o tipo afetado
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
        imp_clip, imp_thumb = impressao(m, h_corte), f"{m.focus:.1f}"
        try:   # miniatura e corte separados: miniatura que falha não impede o corte
            if not cache_r.pronto(thumb, imp_thumb):
                _atomico(thumb, lambda tmp: _miniatura(str(path), m.focus, info.duration, tmp))
                cache_r.registrar(thumb, imp_thumb)
        except Exception as e:  # noqa: BLE001
            errors.append((f"miniatura {fname(m)}", e))
        try:
            if not cache_r.pronto(clip, imp_clip):
                _atomico(clip, lambda tmp: _renderizar_e_checar(
                    tmp, lambda: render_por_janelas(m, tmp, lambda mm, dest: render_clip(str(path), mm, dest, cfg)),
                    qc.Esperado(1920, 1080, _dur_esperada(m, info), audio_ok, 60.0), f"corte {fname(m)}"))
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
        imp_short = impressao(m, f"{cam_box}|{len(words)}|{sufixo}|{h_short}" + (f"|{area_jogo}" if area_jogo else ""))
        if cache_r.pronto(saida_short, imp_short):
            return
        ass = shorts_dir / f"{fname(m_inteiro)}{sufixo}.ass"

        def short_de_um_trecho(mm: Moment, dest: Path) -> None:
            build_ass(ass, words, mm, mm.start, mm.end, cfg.shorts.largura, cfg.shorts.altura,
                      cfg.shorts.camera_altura + max(0, cfg.shorts.topo) if cam_box else 0, cfg.shorts.legendas,
                      cfg.shorts.selo_jogada, cfg.shorts.marca if cam_box else "", max(0, cfg.shorts.topo) if cam_box else 0)
            render_short(str(path), mm, dest, cfg, cam_box, ass, tremida=efeitos, area_jogo=area_jogo)

        def montar(tmp: Path) -> None:
            if not efeitos or not m.kills:
                render_por_janelas(m, tmp, short_de_um_trecho)
                return
            principal = tmp.with_name(tmp.stem + ".principal" + tmp.suffix)
            replay = tmp.with_name(tmp.stem + ".replay" + tmp.suffix)
            try:
                render_por_janelas(m, principal, short_de_um_trecho)
                ultimo = m.kills[-1].t
                rm = dataclasses.replace(m, start=max(0.0, ultimo - REPLAY_ANTES), end=ultimo + REPLAY_DEPOIS,
                                         segmentos=[])
                render_short(str(path), rm, replay, cfg, cam_box, None, velocidade=REPLAY_VELOCIDADE,
                             area_jogo=area_jogo)
                concat([principal, replay], tmp, cfg)
            finally:
                _apagar_parcial(principal)
                _apagar_parcial(replay)

        extra = (REPLAY_ANTES + REPLAY_DEPOIS) / REPLAY_VELOCIDADE if efeitos and m.kills else 0.0
        try:
            _atomico(saida_short, lambda tmp: _renderizar_e_checar(
                tmp, lambda: montar(tmp),
                qc.Esperado(cfg.shorts.largura, cfg.shorts.altura, _dur_esperada(m, info) + extra, audio_ok, 60.0),
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
    if cfg.montagem.gerar and keep:
        order = keep if cfg.montagem.ordem == "cronologica" else sorted(keep, key=lambda m: -m.score)
        montage_parts = [clips_dir / f"{fname(m)}.mp4" for m in order if (clips_dir / f"{fname(m)}.mp4").exists()]
        if not montage_parts:
            print("    [aviso] montagem: nenhum corte dela foi renderizado (veja os erros acima)")
    # nada para montar: não deixa a montagem de uma análise anterior (com a montagem DESLIGADA, a antiga fica)
    if cfg.montagem.gerar and not montage_parts:
        _apagar_parcial(out / f"MONTAGEM_{name}.mp4")
    if montage_parts:
        st = Step("Montagem com as melhores jogadas")
        try:
            _atomico(out / f"MONTAGEM_{name}.mp4", lambda tmp: _renderizar_e_checar(
                tmp, lambda: concat(montage_parts, tmp, cfg), qc.Esperado(1920, 1080, None, audio_ok, 60.0),
                "montagem"))
            st.done(f"{len(montage_parts)} jogadas, {sum(m.duration for m in keep):.0f}s")
        except Exception as e:  # noqa: BLE001
            print("    " + (registro.erro("montagem", e) if registro else f"[erro] montagem: {resumo_erro(str(e))}"))

    fails = sorted((m for m in moments if m.kind == "fail"), key=lambda m: -m.score)
    partes_fails = [clips_dir / f"{fname(m)}.mp4" for m in fails if (clips_dir / f"{fname(m)}.mp4").exists()]
    if not partes_fails:   # sem fails: não deixa a compilação de uma análise anterior
        _apagar_parcial(out / f"FAILS_{name}.mp4")
    if partes_fails:
        st = Step("Compilação de FAILS")
        try:
            _atomico(out / f"FAILS_{name}.mp4", lambda tmp: _renderizar_e_checar(
                tmp, lambda: concat(partes_fails, tmp, cfg), qc.Esperado(1920, 1080, None, audio_ok, 60.0), "fails"))
            st.done(f"{len(partes_fails)} momentos")
        except Exception as e:  # noqa: BLE001
            print("    " + (registro.erro("fails", e) if registro else f"[erro] fails: {resumo_erro(str(e))}"))

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
    _write_edl(out / f"marcadores_{name}.edl", keep, path.name, fps=round(info.fps) or 60)

    # arquivos de execuções anteriores que deixaram de valer (numeração mudou, momento descartado, short que saiu
    # do top) confundem a pasta e a interface: só os do programa (nome "NN_...") e só os que não valem mais
    validos = ({clips_dir / f"{fname(m)}.mp4" for m in moments} | {thumbs_dir / f"{fname(m)}.jpg" for m in moments}
               | {shorts_dir / f"{fname(m)}_vertical.mp4" for m in best}
               | {shorts_dir / f"{fname(m)}_vertical_efeitos.mp4" for m in best if _tem_efeitos(m, cfg)})
    # shorts desligados (--sem-shorts): os shorts já prontos continuam valendo, nada é apagado em shorts/
    pastas_limpar = (clips_dir, shorts_dir, thumbs_dir) if cfg.shorts.gerar else (clips_dir, thumbs_dir)
    velhos = _limpar_velhos(pastas_limpar, validos)
    if velhos:
        print(f"    ({velhos} arquivo(s) de análises anteriores que não valem mais foram apagados)")

    arquivos = {}
    for m in moments:
        tem_short = m in best or not cfg.shorts.gerar   # desligados: vale o short que já existe
        achados = {"corte": clips_dir / f"{fname(m)}.mp4",
                   "short": shorts_dir / f"{fname(m)}_vertical.mp4" if tem_short else None,
                   "short_efeitos": (shorts_dir / f"{fname(m)}_vertical_efeitos.mp4"
                                     if tem_short and (_tem_efeitos(m, cfg) or not cfg.shorts.gerar) else None),
                   "miniatura": thumbs_dir / f"{fname(m)}.jpg"}
        arquivos[m.idx] = {k: f"{p.parent.name}/{p.name}" if p is not None and p.exists() else None
                           for k, p in achados.items()}
    gravar_momentos(out, todos, name, info.duration, DETECTOR_VERSAO, arquivos)
    com_short = best if cfg.shorts.gerar else sorted((m for m in jogadas if arquivos[m.idx]["short"]),
                                                     key=lambda m: -m.score)
    pub = gerar_publicar(out, name, com_short, arquivos, cfg.shorts.marca)
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
