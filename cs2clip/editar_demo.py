"""D7 (Plano D): edita a partida a partir do vídeo LIMPO gravado pela demo: cortes, shorts, montagem, capa e qualidade, tudo com o código de sempre.

Fluxo: lê a demo (os momentos do dono, a regra dos 3 abates) -> junta os rounds gravados num vídeo só -> mapeia os momentos para esse vídeo ->
`render_all`. Opcional: a voz da live (microfone do OBS) por cima do jogo, alinhada pelo deslocamento descoberto quando a live foi analisada
junto da demo (`python -m cs2clip <live.mp4> --demo auto`; a pasta dessa análise é o `--analise-da-live`).
"""
from __future__ import annotations

import copy
import json
import datetime
from pathlib import Path

from . import caminhos  # noqa: F401  (P2.2) PRIMEIRO import do pacote: o HF_HOME do modo instalado tem de existir antes de qualquer huggingface_hub
from . import steam_local, video_limpo
from .caminhos import PASTA_DADOS
from .config import Config
from .demo import ler_demo, momentos_da_demo, pasta_da_partida
from .sequencia import regra_do_corte
from .video_limpo import ErroVideoLimpo

PASTA_GRAVACAO = PASTA_DADOS / "gravacao_demo"
PASTA_SAIDA = PASTA_DADOS / "saida"


def config_do_video_limpo(cfg: Config) -> Config:
    """Cópia da configuração para a fonte limpa: o vídeo tem 2 faixas (1 = jogo ou jogo + voz, 2 = só o jogo) e não tem webcam nem Discord
    (a webcam do short, quando há live junto, vem do vídeo da live: `_webcam_dos_rounds`)."""
    c = copy.deepcopy(cfg)
    c.audio.faixa_jogo, c.audio.faixa_voz, c.audio.faixa_comms = 2, 0, 0
    c.audio.tratar_voz = False
    c.audio.separar_jogo_faixa_unica = False
    c.audio.fonte_limpa = True                             # as duas faixas são o jogo puro: a conferência de igualdade com a faixa 1 não vale aqui
    c.shorts.webcam_retangulo = ""                       # o vídeo limpo não tem a webcam da live (a dela vem do vídeo só da webcam)
    c.capa.alternativas_da_mesma_jogada = True             # 23/11: a edição limpa costuma ter 1 jogada só, e todo kit sai com `capas\capas.json`
    if not cfg.gravacao_demo.hud:
        c.shorts.mostrar_killfeed = False                  # sem HUD não há kill feed para recortar
    return c


def _webcam_dos_rounds(rounds, live: Path, analise_da_live: Path, desloc: float, cfg: Config, out: Path, imprimir) -> Path | None:
    """S5: o vídeo SÓ da webcam da live, na mesma linha do tempo da partida limpa (`PARTIDA_WEBCAM.mp4`), para o painel de cima dos shorts. O recorte é o mesmo que o
    short da live usaria (layout achado na análise da live). Qualquer problema vira aviso e os shorts saem sem o painel: a webcam nunca derruba a edição."""
    caixa = video_limpo.caixa_da_webcam(analise_da_live, live, cfg)
    if caixa is None:
        imprimir("[aviso] Não achei a webcam na análise da live (ou a live não é 16:9): os shorts saem sem o painel da webcam.")
        return None
    pasta = out / "_webcam"
    destino, lado = out / "PARTIDA_WEBCAM.mp4", out / "PARTIDA_WEBCAM.json"
    assinatura = video_limpo.assinatura_da_webcam(live, caixa, desloc, rounds, cfg.shorts.largura, cfg.shorts.camera_altura)
    try:                                                      # R2: nada mudou desde a última edição → reaproveita (e os shorts, que dependem dela, também ficam como estão)
        if destino.is_file() and json.loads(lado.read_text(encoding="utf-8")).get("assinatura") == assinatura:
            imprimir(f"Webcam da live reaproveitada ({len(rounds)} round(s)): nada mudou desde a última edição.")
            return destino
    except (OSError, ValueError, AttributeError):
        pass
    try:
        pasta.mkdir(exist_ok=True)
        cams = {r.round: video_limpo.webcam_da_live(r.arquivo, live, r.t0 + desloc, caixa, pasta / f"round_{r.round:02d}.mp4",
                                                    cfg.shorts.largura, cfg.shorts.camera_altura) for r in rounds}
        arquivo = video_limpo.montar_webcam(rounds, cams, destino)
        lado.write_text(json.dumps({"assinatura": assinatura}), encoding="utf-8")
    except (video_limpo.ErroVideoLimpo, OSError) as e:
        imprimir(f"[aviso] Os shorts saem sem a webcam da live: {e}")
        return None
    imprimir(f"Webcam da live no painel de cima dos shorts ({len(rounds)} round(s)).")
    return arquivo


def editar(demo: Path, cfg: Config, *, jogador=None, pasta_gravacao: Path | None = None, saida_raiz: Path | None = None,
           live: Path | None = None, analise_da_live: Path | None = None, imprimir=print) -> Path:
    """Gera a edição em `saida/<nome da demo>_limpo/`. Devolve a pasta. Levanta `ErroVideoLimpo` com a explicação quando não dá."""
    from .indice import atualizar_indice
    from .momentos_io import gravar_momentos
    from .pipeline import render_all
    from .report import entry, write_report_video
    from .scan import probe
    demo = Path(demo)
    g = cfg.gravacao_demo
    partida = ler_demo(demo, jogador or g.conta_principal_id or cfg.demo.jogador or None)
    if partida.jogador is None:
        raise ErroVideoLimpo(f"Você não aparece nessa demo (confira {steam_local.rotulo_conta('principal', g.conta_principal_apelido)} em Configurar → Contas do Steam).")
    pasta = pasta_da_partida(demo.stem, partida.jogador if jogador else None)      # câmera em outro jogador: pasta própria (A2)
    grav = Path(pasta_gravacao) if pasta_gravacao else PASTA_GRAVACAO / pasta
    rounds = video_limpo.rounds_gravados(grav)
    nome = f"{pasta}_limpo"
    out_root = Path(saida_raiz) if saida_raiz else PASTA_SAIDA
    out = out_root / nome
    out.mkdir(parents=True, exist_ok=True)
    imprimir(f"{len(rounds)} round(s) gravado(s): {', '.join(str(r.round) for r in rounds)}")

    cam_video: Path | None = None
    if live is not None:                                   # a voz da live por cima do jogo, round a round
        if analise_da_live is None:
            raise ErroVideoLimpo("Para juntar a live, diga também a pasta da análise dela (--analise-da-live).")
        desloc = video_limpo.deslocamento_da_analise(analise_da_live, demo.name)
        faixa_voz = cfg.audio.faixa_voz
        tem_faixas = video_limpo.faixas_de_audio(live)
        if faixa_voz < 2:
            # audio.faixa_voz = 0 (não configurado) ou 1 (a mistura da live, com o Discord): não há faixa só do microfone. Antes isto levantava ErroVideoLimpo no meio da edição;
            # agora o kit sai sem a voz (e, se houver webcam, com ela: a webcam não depende do áudio).
            imprimir("[aviso] O microfone da live não está configurado (audio.faixa_voz, de 2 a 6; em Configurar diga qual faixa do OBS é só o microfone): sigo SEM a sua voz.")
        elif faixa_voz > tem_faixas:
            # VOD da Twitch e vídeo do YouTube têm 1 faixa só: a faixa do microfone do OBS (audio.faixa_voz) não existe neles. Antes isto quebrava só no fim,
            # depois de regravar tudo (achado A1 da revisão de 11/11); agora o kit sai sem a voz e o aviso diz por quê.
            imprimir(f"[aviso] A live tem só {tem_faixas} faixa(s) de áudio e o microfone está na faixa {faixa_voz} (audio.faixa_voz): sigo SEM a sua voz.")
        else:
            trab = out / "_com_voz"
            trab.mkdir(exist_ok=True)
            novos = []
            for r in rounds:
                destino = trab / r.arquivo.name
                video_limpo.juntar_voz_da_live(r.arquivo, live, r.t0 + desloc, faixa_voz, destino)
                novos.append(video_limpo.RoundGravado(r.round, destino, r.t0, r.t1, r.duracao))
            rounds = novos
            imprimir(f"Voz da live (faixa {faixa_voz}) junta em {len(rounds)} round(s), deslocamento {desloc:+.1f} s")
        if g.webcam_no_short and cfg.shorts.gerar:         # a webcam da live no painel de cima dos shorts (vale também para VOD/YouTube, que não têm a faixa do microfone)
            cam_video = _webcam_dos_rounds(rounds, live, analise_da_live, desloc, cfg, out, imprimir)

    limpa, deslocamentos = video_limpo.montar_partida_limpa(rounds, out / "PARTIDA_LIMPA.mp4")
    c = cfg.cortes
    momentos = momentos_da_demo(partida, c.antes, c.depois, **regra_do_corte(c))
    mapeados = video_limpo.mapear_momentos(momentos, rounds, deslocamentos)
    imprimir(f"{len(mapeados)} jogada(s) do dono nos rounds gravados (de {len(momentos)} na demo)")
    if not mapeados:
        raise ErroVideoLimpo("Nenhuma jogada sua cai dentro dos rounds gravados.")
    info = probe(str(limpa))
    cfg2 = config_do_video_limpo(cfg)
    gravar_momentos(out, mapeados, nome, info.duration, "demo-limpo")
    entradas, _, _ = render_all(limpa, cfg2, info, nome, out, mapeados, [], None, cam_video=cam_video)
    try:
        abates = sum(len(m.kills) for m in mapeados)
        hs = sum(e.hs for m in mapeados for e in m.kills)
        write_report_video(out, entradas or [entry(m, limpa.name, "", None, None) for m in mapeados],
                           {"abates": str(abates), "headshots": f"{100 * hs / abates:.0f}%" if abates else "–", "momentos": str(len(mapeados))},
                           "Editado a partir do vídeo limpo gravado pela demo.")
    except OSError as e:
        imprimir(f"[aviso] não consegui gravar o relatório: {e}")
    from .mapas import data_da_demo, titulo_da_partida
    titulo = titulo_da_partida(partida.mapa, partida.placar if partida.placar != (0, 0) else None, data_da_demo(demo))
    atualizar_indice(out_root, nome, limpa.name, info.duration, mapeados, [], datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), str(limpa.resolve()),
                     titulo=f"{titulo} (imagem limpa)" if titulo else "")
    return out


def main(argv: list[str] | None = None) -> int:
    """`python -m cs2clip.editar_demo --demo <.dem> [--live <video> --analise-da-live <pasta>]`: edita uma gravação pela demo que já existe."""
    import argparse
    import sys
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    from .config import load_config
    ap = argparse.ArgumentParser(prog="cs2clip.editar_demo", description=main.__doc__)
    ap.add_argument("--demo", required=True)
    ap.add_argument("--config", default=str(PASTA_DADOS / "config.toml"))
    ap.add_argument("--live", default="")
    ap.add_argument("--analise-da-live", default="")
    ap.add_argument("--jogador", default="", help="quem a câmera seguiu na gravação (nome ou steamid); vazio = você")
    a = ap.parse_args(argv)
    try:
        pasta = editar(Path(a.demo), load_config(Path(a.config)), jogador=a.jogador.strip() or None, live=Path(a.live) if a.live else None,
                       analise_da_live=Path(a.analise_da_live) if a.analise_da_live else None)
    except ErroVideoLimpo as e:
        print(f"[parei] {e}")
        return 1
    print(f"Edição pronta em {pasta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
