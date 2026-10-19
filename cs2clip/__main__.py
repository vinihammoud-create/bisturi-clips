"""Bisturi Clips — uso:  python -m cs2clip [vídeos...] [opções]

Sem vídeos na linha de comando, processa tudo o que estiver na pasta entrada/.
Código de saída: 0 = tudo certo; 1 = nenhum vídeo foi processado ou configuração inválida;
2 = opção de linha de comando inválida (é o código do argparse); 3 = terminou, mas com problemas
(veja saida\\log.txt). O programa só diz "Pronto" quando nada falhou.
"""
from __future__ import annotations

import argparse
import datetime
import sys
import time
import tomllib
from pathlib import Path

from . import progresso
from .config import load_config
from .refazer import parse_refazer
from .registro import Registro
from .tools import ROOT

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".flv", ".ts", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".mts",
             ".m2ts", ".3gp", ".ogv"}
DEMO_EXT = {".dem"}
LIVE_LONGA_S = 600          # vídeo mais longo que isto sem nenhum abate é quase sempre problema de gravação, não de jogo
SUB_RELATORIO = "Gerado automaticamente — clique na miniatura para assistir ao corte."


def _parece_video(p: Path) -> bool:
    """Arquivo com extensão que não conhecemos (ou sem extensão): vale o conteúdo, não o nome."""
    from .scan import VideoInvalido, probe
    try:
        probe(str(p))
        return True
    except VideoInvalido:
        return False


MODELO_PADRAO, MODELO_LEVE = "large-v3-turbo", "small"


def _modelo_leve(arq: Path, cfg) -> str:
    """Sem placa NVIDIA o Whisper roda no processador, e o modelo padrão levaria horas numa live. Troca só se o
    modelo ainda é o padrão: quem escolheu outro no config.toml não é mexido."""
    import re
    if cfg.transcricao.modelo != MODELO_PADRAO:
        return f"Modelo de voz mantido: {cfg.transcricao.modelo} (escolha sua no config.toml)."
    texto = arq.read_text(encoding="utf-8") if arq.exists() else ""
    linha = f'modelo = "{MODELO_LEVE}"'
    if re.search(r"^modelo\s*=", texto, flags=re.M):
        texto = re.sub(r"^modelo\s*=.*$", linha + "  # modelo leve (PC sem NVIDIA); na GPU use \"large-v3-turbo\"",
                       texto, count=1, flags=re.M)
    elif re.search(r"^\[transcricao\]", texto, flags=re.M):
        texto = re.sub(r"^\[transcricao\][ \t]*\r?$", lambda m: m.group(0) + "\n" + linha, texto, count=1, flags=re.M)
    else:
        texto += ("\n" if texto and not texto.endswith("\n") else "") + f"\n[transcricao]\n{linha}\n"
    arq.write_text(texto, encoding="utf-8")
    return f"Modelo de voz: {MODELO_LEVE} (mais leve para o processador)."


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="cs2clip", description="Cortes automáticos de CS2 (kills, multi-kills, reações).")
    ap.add_argument("videos", nargs="*", help="arquivos de vídeo (ou pastas)")
    ap.add_argument("--saida", default=str(ROOT / "saida"), help="pasta de saída")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--so-analise", action="store_true", help="só analisa e gera o relatório, sem renderizar")
    ap.add_argument("--jogador", default=None, help="(demo .dem) seu nome no jogo, ou parte dele")
    ap.add_argument("--checar", action="store_true",
                    help="confere o ambiente (ffmpeg, GPU, disco, pacotes, CS2/OBS) e sai, sem processar nada")
    ap.add_argument("--baixar-modelo", action="store_true",
                    help="baixa o modelo de voz do config.toml (o instalar.bat usa) e sai")
    ap.add_argument("--modelo-leve", action="store_true",
                    help="PC sem placa NVIDIA (o instalar.bat usa): põe o modelo de voz menor no config.toml e sai")
    ap.add_argument("--forcar-gpu", action="store_true",
                    help="ignora a trava: processa completo mesmo com o CS2 ou o OBS abertos")
    ap.add_argument("--sem-shorts", action="store_true")
    ap.add_argument("--sem-montagem", action="store_true")
    ap.add_argument("--refazer", nargs="?", const="tudo", default=None, metavar="ETAPAS",
                    help="ignora o cache: sozinho refaz tudo; ou liste etapas separadas por vírgula "
                         "(kills, rounds, mortes, voz, falas, chat, webcam, cortes). Ex.: --refazer falas. "
                         "Ponha o vídeo ANTES de --refazer")
    a = ap.parse_args(argv)

    try:
        R = parse_refazer(a.refazer)
    except ValueError as e:
        ap.error(str(e))
    try:
        cfg = load_config(Path(a.config))
    except (tomllib.TOMLDecodeError, ValueError, TypeError) as e:
        print(f"✖ O arquivo de configuração {a.config} tem um problema: {e}\n"
              "  Corrija a linha indicada ou apague o arquivo para voltar aos valores padrão.")
        return 1
    if a.sem_shorts:
        cfg.shorts.gerar = False
    if a.sem_montagem:
        cfg.montagem.gerar = False

    if a.checar:
        from . import checagem
        itens = checagem.checar(ROOT, Path(a.saida))
        print(checagem.texto(itens))
        return 1 if checagem.tem_problema_grave(itens) else 0

    if a.modelo_leve:
        print(_modelo_leve(Path(a.config), cfg))
        return 0

    if a.baixar_modelo:
        from faster_whisper import download_model
        try:
            download_model(cfg.transcricao.modelo)
        except Exception as e:  # noqa: BLE001
            print(f"✖ Não consegui baixar o modelo de voz {cfg.transcricao.modelo}: {e}")
            return 1
        print(f"✔ Modelo de voz {cfg.transcricao.modelo} pronto.")
        return 0

    out_root = Path(a.saida).resolve()  # caminhos absolutos: o ffmpeg dos shorts roda em outra pasta
    out_root.mkdir(parents=True, exist_ok=True)
    registro = Registro(out_root / "log.txt")

    paths: list[Path] = []
    demos: list[Path] = []
    demos_da_pasta: list[Path] = []   # .dem achadas ao varrer uma pasta: só valem se não houver vídeo
    for v in a.videos or [str(ROOT / "entrada")]:
        p = Path(v).resolve()
        if p.is_dir():
            paths += sorted(x for x in p.iterdir() if x.suffix.lower() in VIDEO_EXT)
            demos_da_pasta += sorted(x for x in p.iterdir() if x.suffix.lower() in DEMO_EXT and x.is_file())
        elif not p.exists():
            print(registro.erro(v, "arquivo ou pasta não encontrado."))
        elif p.suffix.lower() in DEMO_EXT:
            demos.append(p)
        elif p.suffix.lower() not in VIDEO_EXT and not _parece_video(p):
            print(registro.erro(p.name, f"formato não aceito ({p.suffix or 'sem extensão'}). "
                                        f"Use: {' '.join(sorted(VIDEO_EXT | DEMO_EXT))}"))
        else:
            paths.append(p)
    quem = a.jogador or cfg.demo.jogador    # sem o seu nome a demo não serve (não adivinha quem é você)
    if demos_da_pasta and not paths and not demos:   # pasta só com demos: analisa as demos
        demos = demos_da_pasta
    elif demos_da_pasta and paths:
        if quem:   # vídeo + demos da mesma pasta: as demos corrigem a análise do vídeo (cs2clip/sincronizar.py)
            demos = demos + demos_da_pasta
        else:
            print(f"  [aviso] {len(demos_da_pasta)} demo(s) na pasta ignorada(s) porque há vídeos: diga o seu nome "
                  "do jogo (--jogador \"nome\" ou [demo] jogador no config.toml) para usá-las com o vídeo.")
    if demos and not paths:
        return _so_demos(demos, a, cfg, out_root, registro, paths)
    if not paths:
        print("Nenhum vídeo para processar. Arraste os vídeos sobre processar.bat ou coloque-os em entrada\\.")
        return 1

    from . import checagem, qc
    from .pipeline import _atomico, _renderizar_e_checar, analyze, render_all
    from .render import concat
    from .report import write_report
    from .scan import VideoInvalido

    # Trava de GPU: com o CS2 ou o OBS abertos a placa é do jogo/da live. Só a prévia (sem Whisper novo, sem render).
    ocupada = [] if a.forcar_gpu else checagem.gpu_ocupada()
    if ocupada:
        print("  ⚠ " + checagem.aviso_trava(ocupada))
        cfg.modo_previa = True
        a.so_analise = True

    lidas: dict[Path, object] = {}   # demo -> (partida, nome) lida uma vez só, ou None se não serviu

    def demos_do_video(video: Path) -> list:
        """Demos a cruzar com este vídeo: as pedidas na linha de comando e, com o seu nome, as .dem ao lado dele."""
        if not quem:
            return []
        candidatas = list(demos) or sorted(x for x in video.parent.iterdir() if x.suffix.lower() in DEMO_EXT)
        saida = []
        for d in candidatas:
            if d not in lidas:
                lidas[d] = _ler_demo_do_video(d, quem)
            if lidas[d] is not None:
                saida.append(lidas[d])
        return saida

    player_dir = ROOT / "dados" / "jogador"
    t0 = time.time()
    all_entries, best_global = [], []
    totals = {"kills": 0, "hs": 0, "moments": 0, "dur": 0.0}
    processados = 0
    progresso.iniciar(out_root, len(paths), "previa" if ocupada else "completo")
    for n, p in enumerate(paths, 1):
        progresso.video(p.name, n)
        try:
            com_demos = demos_do_video(p)
            info, name, out, moments, words, lay = analyze(p, cfg, out_root, player_dir, R,
                                                           **({"demos": com_demos} if com_demos else {}))
        except VideoInvalido as e:
            print("  " + registro.erro(p.name, str(e)))
            continue
        except Exception as e:  # noqa: BLE001
            print("  " + registro.erro(p.name, e) + f"  (detalhes em {registro.arquivo.name})")
            continue
        processados += 1
        totals["dur"] += info.duration
        totals["kills"] += sum(len(m.kills) for m in moments)
        totals["hs"] += sum(e.hs for m in moments for e in m.kills)
        totals["moments"] += len(moments)
        if not any(m.kills for m in moments) and info.duration > LIVE_LONGA_S:
            print("  " + registro.erro(p.name, "Nenhum abate achado neste vídeo longo. Confira: o kill feed aparece no "
                                               "canto de cima à direita? O HUD está visível? O vídeo é do CS2, sem "
                                               "compressão forte (720p)? O jogo está em português, inglês ou espanhol?",
                                     limite=400))
        # relatório deste vídeo (em saida/<pasta>/relatorio.html): a interface abre o relatório certo de cada um
        stats_v = _stats_do_video(moments)
        if a.so_analise:
            from .report import entry
            es = [entry(m, p.name, "", None, None) for m in moments]
            all_entries += es
            _relatorio_do_video(out, es, stats_v)
            continue
        try:   # erro no render de UM vídeo não derruba o lote (os seguintes e o relatório continuam)
            entries, _, keep = render_all(p, cfg, info, name, out, moments, words, lay, registro, R)
        except Exception as e:  # noqa: BLE001
            print("  " + registro.erro(p.name, e) + f"  (detalhes em {registro.arquivo.name})")
            from .report import entry
            es = [entry(m, p.name, "", None, None) for m in moments]   # a análise entra no relatório
            all_entries += es
            _relatorio_do_video(out, es, stats_v)
            continue
        all_entries += entries
        _relatorio_do_video(out, entries, stats_v)
        best_global += keep  # [(momento, arquivo do corte)]: caminhos exatos, sem adivinhar por nome

    # "Melhores de todos": top jogadas de todos os vídeos numa montagem só
    if not a.so_analise and cfg.montagem.gerar and len(paths) > 1 and best_global:
        top = sorted(best_global, key=lambda x: -x[0].score)[:12]
        files = [f for _, f in top if f.is_file()]
        if files:
            try:   # temporário + checagem, como a montagem: cancelado no meio não sobra arquivo pela metade
                _atomico(out_root / "MELHORES_DE_TODOS.mp4", lambda tmp: _renderizar_e_checar(
                    tmp, lambda: concat(files, tmp, cfg), qc.Esperado(1920, 1080, None, False, 60.0),
                    "melhores de todos"))
            except Exception as e:  # noqa: BLE001
                print("  " + registro.erro("MELHORES_DE_TODOS", e))

    if processados == 0:
        progresso.fim("erro", "Nenhum vídeo foi processado.")
        print(f"\n✖ Nenhum vídeo foi processado. Detalhes em {registro.arquivo}.")
        return 1
    hs_pct = f"{100 * totals['hs'] / totals['kills']:.0f}%" if totals["kills"] else "–"
    write_report(out_root / "relatorio.html", all_entries,
                 {"vídeos": str(processados), "horas analisadas": f"{totals['dur'] / 3600:.1f}",
                  "abates": str(totals["kills"]), "headshots": hs_pct, "momentos": str(totals["moments"])},
                 SUB_RELATORIO)
    if registro.n:
        progresso.fim("com_problemas", f"{registro.n} problema(s)")
        print(f"\n⚠ Terminou em {time.time() - t0:.0f}s com {registro.n} problema(s). Detalhes em {registro.arquivo}. "
              f"O relatório ({out_root / 'relatorio.html'}) pode estar incompleto.")
        return 3
    progresso.fim("pronto")
    print(f"\n✔ Pronto em {time.time() - t0:.0f}s. Abra: {out_root / 'relatorio.html'}")
    return 0


def _stats_do_video(moments: list) -> dict[str, str]:
    """Números do cabeçalho do relatório de UM vídeo."""
    abates = sum(len(m.kills) for m in moments)
    hs = sum(e.hs for m in moments for e in m.kills)
    return {"abates": str(abates), "headshots": f"{100 * hs / abates:.0f}%" if abates else "–",
            "momentos": str(len(moments))}


def _relatorio_do_video(out: Path, entradas: list[dict], stats: dict[str, str],
                        subtitulo: str = SUB_RELATORIO) -> None:
    """Grava `<pasta do vídeo>/relatorio.html` só com os momentos dele. Falhar aqui não derruba o lote."""
    from .report import write_report_video
    try:
        write_report_video(out, entradas, stats, subtitulo)
    except OSError as e:
        print(f"  [aviso] não consegui gravar o relatório de {out.name}: {e}")


def _ler_demo_do_video(demo: Path, quem: str):
    """(partida, nome do arquivo) ou None. Demo ilegível ou sem o seu nome não derruba o vídeo: só não é usada."""
    from .demo import DemoInvalida, ler_demo
    try:
        partida = ler_demo(demo, quem)
    except DemoInvalida as e:
        print(f"  [aviso] demo {demo.name} ignorada: {e}")
        return None
    if partida.jogador is None:
        print(f"  [aviso] demo {demo.name} ignorada: não achei o jogador '{quem}' nela.")
        return None
    print(f"  demo {demo.name}: {partida.titulo}, {len(partida.abates)} abates de {partida.nome}")
    return partida, demo.name


def _so_demos(demos: list[Path], a, cfg, out_root: Path, registro: Registro, videos: list[Path]) -> int:
    """Entrada por demo (.dem): lê os eventos exatos do jogo e grava momentos + relatório, sem vídeo.
    (O corte do vídeo a partir da demo depende da sincronização demo↔vídeo, etapa seguinte.)"""
    from .demo import DemoInvalida, analisar_demo
    from .indice import atualizar_indice
    from .report import entry, write_report
    entradas, abates, jogadas, ok = [], 0, 0, 0
    progresso.iniciar(out_root, len(demos))
    for n, d in enumerate(demos, 1):
        progresso.video(d.name, n)
        try:
            partida, momentos, out = analisar_demo(d, cfg, out_root, a.jogador)
        except DemoInvalida as e:
            print("  " + registro.erro(d.name, str(e), limite=1500))   # traz "Jogadores: ..." inteiro: a interface lê
            continue
        except Exception as e:  # noqa: BLE001
            print("  " + registro.erro(d.name, e) + f"  (detalhes em {registro.arquivo.name})")
            continue
        ok += 1
        # entra no índice como os vídeos: é por ele que a interface lista o que já foi analisado
        duracao = partida.rounds[-1].fim if partida.rounds else 0.0
        atualizar_indice(out_root, out.name, d.name, duracao, momentos, [],
                         datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), str(d.resolve()))
        abates += len(partida.abates)
        jogadas += len(momentos)
        es = [entry(m, d.name, "", None, None) for m in momentos]
        entradas += es
        _relatorio_do_video(out, es, {"abates": str(len(partida.abates)), "momentos": str(len(momentos))},
                            "Gerado a partir da demo (tempos em segundos da partida, não do vídeo).")
        print(f"▶ {d.name}\n  {partida.titulo} · {partida.nome}: {len(partida.abates)} abates, "
              f"{len(partida.mortes)} mortes, {len(partida.clutches)} situação(ões) de clutch, "
              f"{len(momentos)} jogadas → {out}")
    if ok == 0:
        progresso.fim("erro", "Nenhuma demo foi processada.")
        print(f"\n✖ Nenhuma demo foi processada. Detalhes em {registro.arquivo}.")
        return 1
    write_report(out_root / "relatorio.html", entradas,
                 {"demos": str(ok), "abates": str(abates), "momentos": str(jogadas)},
                 "Gerado a partir da demo (tempos em segundos da partida, não do vídeo).")
    if registro.n:
        progresso.fim("com_problemas", f"{registro.n} problema(s)")
        print(f"\n⚠ Terminou com {registro.n} problema(s). Detalhes em {registro.arquivo}.")
        return 3
    progresso.fim("pronto")
    print(f"\n✔ Pronto. Abra: {out_root / 'relatorio.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
