"""D4 (Plano D): grava a partida pelo CS2 a partir da demo (HLAE + console remoto), sem tocar na pasta do jogo.

Roteiro (cada passo é uma função pequena; o registro de tudo vai para `registro.json`, gravado a cada passo):
  1. pré-checagens: sessão NÃO elevada, CS2/OBS fechados, Steam na conta do Bisturi (bloqueio duro da principal), HLAE instalado, demo legível e não mais nova que o jogo;
  2. FOTO dos arquivos do jogo e da conta principal (antes);
  3. lança o CS2 pelo HLAE (`-insecure`, só `playdemo`), conecta no console remoto (127.0.0.1), confere que a porta é só local;
  4. prepara o HLAE (stream, preset NVENC, pasta, fps, áudio);
  5. para cada trecho: pula (pausado), trava a câmera no dono, agenda início/fim por tick, retoma, espera o arquivo, junta vídeo + áudio, confere;
  6. fecha o jogo, FOTO (depois) e compara: mudou algo crítico = alerta vermelho.
Tudo que fala com o mundo (processos, rede local, relógio) entra por `Dependencias`, para o roteiro inteiro ser testado com um jogo falso.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable

from . import caminhos  # noqa: F401  (P2.2) PRIMEIRO import do pacote: o HF_HOME do modo instalado tem de existir antes de qualquer huggingface_hub
from . import hlae, steam_local, terceiros
from .caminhos import PASTA_DADOS
from .config import Config
from .demo import PartidaDemo, TrechoGravacao, pasta_da_partida, rounds_para_gravar, trechos_por_ticks
from .netcon import ClienteNetcon, SemConexao, escuta_so_em_local, estado_do_firewall, linhas_da_porta
from .tools import FFMPEG, FFPROBE, substituir_arquivo
from .tools import sem_janela

PASTA_PADRAO = PASTA_DADOS / "gravacao_demo"
TICKRATE = 64.0
SONDA_CONSOLE = "spec_show_xray"      # consulta de convar (sem valor): o jogo devolve o nome quando o console está de pé; não muda nada
AVISO_TABELA_TCP = ("não achei a porta do console na tabela TCP (leitura instável neste Windows: estado 0 em sockets vivos); "
                    "a proteção fica por conta da regra do Firewall, já conferida antes de abrir o jogo")


def comando_regra_firewall(porta: int) -> str:
    """A regra que o DONO cria uma vez (PowerShell como administrador; o Bisturi nunca roda elevado)."""
    return (f"New-NetFirewallRule -DisplayName 'Bisturi bloqueia {int(porta)}' -Direction Inbound -Action Block -Protocol TCP "
            f"-LocalPort {int(porta)} -Profile Any")


def mensagem_firewall(porta: int, detalhe: str) -> str:
    return (f"O console remoto do CS2 abre a porta {int(porta)} para TODA a rede e não tem senha ({detalhe}). Antes de abrir o jogo, crie UMA VEZ "
            f"a regra de bloqueio no Firewall do Windows, num PowerShell como administrador:\n    {comando_regra_firewall(porta)}\n"
            f"(para desfazer: Remove-NetFirewallRule -DisplayName 'Bisturi bloqueia {int(porta)}')")


class ErroGravacao(Exception):
    """A gravação parou (mensagem pronta, em português). `etapa` diz em que passo."""

    def __init__(self, mensagem: str, etapa: str = ""):
        super().__init__(mensagem)
        self.etapa = etapa


@dataclass
class Roteiro:
    """Tempos de espera (s). Ajustáveis porque o primeiro teste real (com o dono) mede o que o jogo precisa de verdade."""
    espera_carregar_s: float = 25.0     # depois de abrir o console remoto, até a demo estar carregada
    espera_pulo_s: float = 3.0          # depois do `demo_goto`
    espera_em_jogo_s: float = 60.0      # depois de a demo carregar, até o jogo sair da tela de carregamento (17/11: saltar antes travou o CS2)
    espera_assentar_s: float = 3.0      # folga depois de entrar no jogo, antes do 1º salto
    espera_repulo_s: float = 0.3        # depois do 2º `demo_goto`, logo antes de gravar (prova 8: cada segundo pausado aqui é demo perdida)
    espera_camera_s: float = 1.0
    poll_s: float = 1.0
    folga_relogio_s: float = 60.0       # além do esperado, quanto esperar o arquivo antes de forçar o fim
    espera_fechar_s: float = 30.0
    tentativas_camera: int = 5


@dataclass
class Dependencias:
    lancar: Callable[[list[str]], Any]
    netcon: Callable[[int], Any]
    escuta_so_em_local: Callable[[int], tuple[bool, list[str]]]
    tirar_foto: Callable[[Path | None, Path | None], Any]
    comparar: Callable[[Any, Any], Any]
    jogo_aberto: Callable[[], list[str]]
    conta_logada: Callable[[], int]
    steam_rodando: Callable[[], bool]
    dormir: Callable[[float], None]
    agora: Callable[[], float]
    duracao_do_video: Callable[[Path], float | None]
    quadro_escuro: Callable[[Path], bool]
    juntar: Callable[[Path, Path | None, Path], bool]
    verificar_camera: Callable[[Path, TrechoGravacao], tuple[bool | None, str]]
    processo_do_jogo_vivo: Callable[[], bool]
    encerrar_jogo: Callable[[], None]
    sem_admin: Callable[[], None]
    # 08/11: com padrão, para os testes antigos continuarem válidos
    firewall: Callable[[int], tuple[bool, str]] = lambda porta: (True, "ok")
    linhas_da_porta: Callable[[int], list] = lambda porta: []
    pids_do_jogo: Callable[[], list[int]] = lambda: []
    congelado_no_inicio: Callable[[Path], float] = lambda arq: 0.0
    som_mudo: Callable[[Path], bool] = lambda arq: False                    # 17/11: o som do jogo gravado saiu mudo


# ── implementações reais (usadas fora dos testes) ─────────────────────────────────────────────────

def _duracao_ffprobe(arq: Path) -> float | None:
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(arq)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def _congelado_no_inicio(arq: Path, janela_s: float = 6.0) -> float:
    """Quantos segundos o vídeo fica PARADO no começo (a gravação começa com a demo pausada; o `demo_resume` vem logo depois).
    `freezedetect` do ffmpeg nos primeiros segundos; 0.0 se não há congelamento começando em ~0."""
    r = subprocess.run([FFMPEG, "-v", "info", "-nostdin", "-t", f"{janela_s:.1f}", "-i", str(arq), "-an", "-vf", "freezedetect=n=0.01:d=0.08",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    inicio = fim = None
    for linha in (r.stderr or "").splitlines():
        if "freeze_start:" in linha and inicio is None:
            try:
                inicio = float(linha.rsplit("freeze_start:", 1)[1].strip())
            except ValueError:
                inicio = None
        elif "freeze_end:" in linha and inicio is not None and fim is None:
            try:
                fim = float(linha.rsplit("freeze_end:", 1)[1].strip())
            except ValueError:
                fim = None
    if inicio is None or inicio > 0.15:
        return 0.0
    return round(fim if fim is not None else janela_s, 2)


SOM_MUDO_DB = -70.0     # pico abaixo disto: o som do jogo saiu mudo


def _som_mudo(arq: Path) -> bool:
    """True se a faixa de som do vídeo gravado é silêncio (pico ≤ SOM_MUDO_DB; silêncio digital dá −91 dB no `volumedetect`). Prova real de 17/11:
    com o Windows sem nenhuma saída de som ligada (o fone USB desligado), os 3 rounds saíram mudos e os kits ficaram sem som e sem música."""
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(arq), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", r.stderr or "")
    if not m:
        return False                         # não deu para medir: não acusa
    return m.group(1) == "-inf" or float(m.group(1)) <= SOM_MUDO_DB


def _quadro_escuro(arq: Path, amostras: int = 5) -> bool:
    """True se algum dos quadros amostrados é quase todo preto (luma média < 8)."""
    import numpy as np
    dur = _duracao_ffprobe(arq) or 0.0
    for k in range(amostras):
        t = dur * (k + 0.5) / amostras
        r = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", str(arq), "-frames:v", "1", "-vf", "scale=160:90",
                            "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"], capture_output=True, **sem_janela())
        if len(r.stdout) >= 160 * 90 and float(np.frombuffer(r.stdout[:160 * 90], np.uint8).mean()) < 8.0:
            return True
    return False


def _juntar_av(video: Path, wav: Path | None, destino: Path) -> bool:
    """Junta vídeo e áudio do HLAE no formato dos cortes (2 faixas de áudio iguais: mistura e jogo). True se havia áudio; sem WAV o arquivo
    sai com duas faixas mudas (assim a montagem e os shorts continuam funcionando) e devolve False."""
    parcial = destino.with_name(destino.stem + ".parcial.mp4")
    if wav is not None and wav.is_file() and wav.stat().st_size > 1000:
        cmd = [FFMPEG, "-y", "-v", "error", "-nostdin", "-i", str(video), "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0", "-map", "1:a:0",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(parcial)]
        tem_audio = True
    else:
        dur = _duracao_ffprobe(video) or 1.0
        cmd = [FFMPEG, "-y", "-v", "error", "-nostdin", "-i", str(video), "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
               "-map", "0:v:0", "-map", "1:a:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(parcial)]
        tem_audio = False
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    if r.returncode != 0:
        parcial.unlink(missing_ok=True)
        raise ErroGravacao(f"o ffmpeg não conseguiu juntar o vídeo e o áudio: {r.stderr[-300:]}", "juntar")
    substituir_arquivo(parcial, destino)
    return tem_audio


def jogadores_ambiguos(jogadores: dict[int, str], escolhido: str) -> list[str]:
    """Os nomes que casam com `escolhido` quando ele é só uma parte de nome que vale para vários (nem steamid, nem nome exato)."""
    q = (escolhido or "").strip().lower()
    if not q or q.isdigit() or any(n.lower() == q for n in jogadores.values()):
        return []
    achados = sorted(n for n in jogadores.values() if q in n.lower())
    return achados if len(achados) > 1 else []


TOL_CAMERA_S = 1.0      # s: folga entre o instante de um abate (pela demo) e o aparecimento da caixa destacada no vídeo


def abates_que_batem(inicios: list[float], abates: list[float], tol: float = TOL_CAMERA_S) -> int:
    """Quantos abates do jogador têm uma caixa destacada no mesmo ritmo: procura o deslocamento (o vídeo pode começar um pouco antes ou
    depois do tick previsto) que casa mais abates com caixas, cada caixa no máximo uma vez. Caixa sobrando (assistência, a própria morte)
    não atrapalha."""
    if not abates or not inicios:
        return 0
    melhor = 0
    for c in inicios:
        desloc = c - abates[0]
        livres = sorted(inicios)
        casados = 0
        for a in abates:
            alvo = a + desloc
            perto = [x for x in livres if abs(x - alvo) <= tol]
            if perto:
                livres.remove(min(perto, key=lambda x: abs(x - alvo)))
                casados += 1
        melhor = max(melhor, casados)
    return melhor


def _verificar_camera_por_kill_feed(clip: Path, trecho: TrechoGravacao) -> tuple[bool | None, str]:
    """A câmera está no jogador? Com o HUD ligado, o kill feed destaca (borda) as linhas do jogador observado; o detector do Bisturi acha essas
    caixas. Revisão de 17/11 (M3): não basta UMA caixa (outro jogador que também matou no round passava); cada abate do jogador (instantes da
    demo) precisa de uma caixa no mesmo ritmo. None = não dá para saber."""
    try:
        from .scan import probe, scan_video, scan_video_fast
        info = probe(str(clip))
        trilhas = scan_video_fast(info) or scan_video(info)
    except Exception as e:  # noqa: BLE001
        return None, f"não consegui verificar ({e})"
    n = len(trilhas)
    if trecho.abates <= 0:
        return None, f"{n} caixa(s) no kill feed; o round não tem abate do jogador para comparar"
    if not trecho.ticks_abates:          # trecho antigo sem os ticks: só a contagem
        return (n >= trecho.abates), f"{n} caixa(s) destacadas no kill feed para {trecho.abates} abate(s) do jogador"
    abates = [(t - trecho.tick_ini) / TICKRATE for t in sorted(trecho.ticks_abates)]
    casados = abates_que_batem(sorted(tr.first for tr in trilhas), abates)
    detalhe = f"{casados} de {len(abates)} abate(s) do jogador com caixa destacada no mesmo instante ({n} caixa(s) no kill feed)"
    precisa = len(abates) if len(abates) <= 2 else len(abates) - 1      # o detector pode perder 1 caixa num round grande
    if casados >= precisa:
        return True, detalhe
    if casados == 0:
        return False, detalhe            # nenhuma caixa no ritmo dos abates: câmera em outro lugar (tenta a próxima)
    return None, detalhe                 # só uma parte bate: fica o vídeo e sai o aviso "confira" (antes, 1 caixa qualquer já "confirmava")


def _pids(nome: str) -> list[int]:
    if sys.platform != "win32":
        return []
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {nome}", "/NH", "/FO", "CSV"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **sem_janela())
    pids = []
    for linha in r.stdout.splitlines():
        partes = [p.strip('"') for p in linha.split('","')]
        if len(partes) >= 2 and partes[0].lower().strip('"') == nome.lower() and partes[1].strip('"').isdigit():
            pids.append(int(partes[1].strip('"')))
    return pids


def _encerrar_cs2() -> None:
    """Último recurso: encerra SÓ os `cs2.exe` abertos depois do lançamento (pelo PID, nunca pelo nome; a checagem inicial garante que
    não havia nenhum antes)."""
    for pid in _pids("cs2.exe"):
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, **sem_janela())


def _linhas_da_porta_para_registro(porta: int) -> list[dict]:
    try:
        return [c._asdict() for c in linhas_da_porta(porta)]
    except OSError as e:
        return [{"erro": str(e)}]


def dependencias_reais() -> Dependencias:
    return Dependencias(
        lancar=lambda cmd: subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL),   # janela-propria: o HLAE/CS2 abrem a própria janela
        netcon=lambda porta: ClienteNetcon(porta),
        escuta_so_em_local=lambda porta: escuta_so_em_local(porta, pids_do_jogo=_pids("cs2.exe")),
        firewall=estado_do_firewall, linhas_da_porta=_linhas_da_porta_para_registro, pids_do_jogo=lambda: _pids("cs2.exe"),
        congelado_no_inicio=_congelado_no_inicio,
        tirar_foto=steam_local.tirar_foto, comparar=steam_local.comparar, jogo_aberto=steam_local.jogo_aberto,
        conta_logada=steam_local.conta_logada, steam_rodando=steam_local.steam_rodando,
        dormir=time.sleep, agora=time.monotonic, duracao_do_video=_duracao_ffprobe, quadro_escuro=_quadro_escuro, juntar=_juntar_av,
        verificar_camera=_verificar_camera_por_kill_feed, processo_do_jogo_vivo=lambda: bool(_pids("cs2.exe")), som_mudo=_som_mudo,
        encerrar_jogo=_encerrar_cs2, sem_admin=terceiros.exigir_sem_admin)


# ── registro ──────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Registro:
    caminho: Path
    dados: dict = field(default_factory=dict)

    def gravar(self, **campos) -> None:
        self.dados.update(campos)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.caminho.with_name(self.caminho.name + ".tmp")
        tmp.write_text(json.dumps(self.dados, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        substituir_arquivo(tmp, self.caminho)      # tolera a recusa momentânea do Windows (já derrubou teste sob carga)

    def somar(self, lista: str, item: Any) -> None:
        self.dados.setdefault(lista, []).append(item)
        self.gravar()


def _numero_do_patch(texto: str) -> int | None:
    """`1.41.9.6` -> 14196 e `14196` -> 14196 (o cabeçalho da demo guarda o número sem pontos). Mesma leitura do leitor de demos (`demo.numero_do_patch`)."""
    from .demo import numero_do_patch
    return numero_do_patch(texto)


# ── o roteiro ─────────────────────────────────────────────────────────────────────────────────────

def prechecagens(demo: Path, partida: PartidaDemo, cfg: Config, dep: Dependencias, reg: Registro, imprimir=print) -> dict:
    """Tudo o que precisa estar certo ANTES de abrir o jogo. Levanta `ErroGravacao` na primeira falha."""
    g = cfg.gravacao_demo
    try:
        dep.sem_admin()
    except terceiros.ErroTerceiros as e:
        raise ErroGravacao(str(e), "prechecagem") from e
    abertos = dep.jogo_aberto()
    if abertos:
        raise ErroGravacao(f"Feche antes de gravar: {', '.join(abertos)} está aberto.", "prechecagem")
    ok_fw, det_fw = dep.firewall(g.netcon_porta)
    if not ok_fw:
        raise ErroGravacao(mensagem_firewall(g.netcon_porta, det_fw), "prechecagem")
    if not demo.is_file():
        raise ErroGravacao(f"Não achei a demo: {demo}", "prechecagem")
    hlae_exe, pasta_hlae = terceiros.caminho_hlae(), terceiros.PASTA_BIN / terceiros.HLAE.pasta
    if hlae_exe is None:
        raise ErroGravacao(f"O HLAE não está instalado ({terceiros.dica_instalar('hlae')}).", "prechecagem")
    if not (pasta_hlae / "ffmpeg" / "bin" / "ffmpeg.exe").is_file():
        raise ErroGravacao("O HLAE está sem o ffmpeg.exe em bin\\hlae\\ffmpeg\\bin.", "prechecagem")
    cs2 = steam_local.cs2_exe()
    if cs2 is None:
        raise ErroGravacao("Não achei o cs2.exe do Steam.", "prechecagem")
    if not dep.steam_rodando():
        raise ErroGravacao(f"O Steam está fechado. Abra o Steam e entre {steam_local.rotulo_conta('bisturi', g.conta_apelido, 'na')}.", "conta")
    try:
        conta = steam_local.exigir_conta_do_bisturi(g.conta_steamid, g.conta_principal_id, dep.conta_logada(),
                                               apelido_bisturi=g.conta_apelido, apelido_principal=g.conta_principal_apelido)
    except steam_local.ContaErrada as e:
        raise ErroGravacao(str(e), "conta") from e
    patch_jogo = _numero_do_patch(steam_local.versao_cs2().get("PatchVersion", ""))
    patch_demo = _numero_do_patch(partida.patch_version)
    avisos = []
    if patch_jogo and patch_demo and patch_demo > patch_jogo:
        raise ErroGravacao(f"A demo é mais nova ({patch_demo}) que o seu CS2 ({patch_jogo}). Atualize o jogo pelo Steam.", "prechecagem")
    if patch_jogo and patch_demo and patch_jogo - patch_demo > 30:
        avisos.append(f"demo antiga (jogo {patch_jogo}, demo {patch_demo}): pode haver diferença na reprodução")
    if partida.user_id is None and partida.accountid is None and partida.entity_id is None:
        raise ErroGravacao("A demo não trouxe a identidade do jogador (user_id/accountid): não dá para travar a câmera nele.", "prechecagem")
    info = {"conta_logada": conta, "hlae": str(hlae_exe), "cs2": str(cs2), "patch_jogo": patch_jogo, "patch_demo": patch_demo, "avisos": avisos,
            "firewall": det_fw}
    for a in avisos:
        imprimir(f"[aviso] {a}")
    reg.gravar(prechecagem=info)
    return {"hlae_exe": hlae_exe, "pasta_hlae": pasta_hlae, "cs2": cs2, **info}


def _takes(pasta: Path) -> list[Path]:
    return sorted(p for p in pasta.glob("take*") if p.is_dir()) if pasta.is_dir() else []


def apagar_brutos_juntados(pasta_hlae: Path, juntados: list[tuple[str, Path]]) -> tuple[int, int]:
    """R-026: apaga o material bruto (`take*`) dos rounds que JÁ viraram `rounds/round_NN.mp4`. `juntados`: (nome do take, arquivo pronto do
    round). Só mexe em pasta chamada `takeNNNN` que está direto dentro de `pasta_hlae` (nada fora dela, nenhum atalho) e só se o arquivo
    pronto existe. Quem chama decide QUANDO (só com a gravação inteira sem erro). Falha ao apagar não é erro: o take fica no disco.
    Devolve (quantos takes apagou, bytes liberados)."""
    raiz = Path(pasta_hlae).resolve()
    apagados = liberado = 0
    for nome, pronto in juntados:
        if not re.fullmatch(r"take\d+", str(nome)) or not Path(pronto).is_file():
            continue
        pasta = Path(pasta_hlae) / nome
        try:
            if pasta.is_symlink() or not pasta.is_dir() or pasta.resolve().parent != raiz:
                continue
            tamanho = sum(f.stat().st_size for f in pasta.rglob("*") if f.is_file())
            shutil.rmtree(pasta)
        except OSError:
            continue
        apagados += 1
        liberado += tamanho
    return apagados, liberado


def _achar_arquivos_do_take(take: Path) -> tuple[Path | None, Path | None]:
    video = next(iter(sorted(take.rglob("video.mp4"))), None)
    wav = take / "audio.wav"
    return video, wav if wav.is_file() else None


def _esperar_take_novo(pasta: Path, antes: set[str], dep: Dependencias, r: Roteiro, limite_s: float) -> Path | None:
    fim = dep.agora() + limite_s
    while dep.agora() < fim:
        novos = [t for t in _takes(pasta) if t.name not in antes]
        if novos:
            return novos[-1]
        dep.dormir(r.poll_s)
    return None


def _esperar_video_pronto(take: Path, dep: Dependencias, r: Roteiro, limite_s: float) -> tuple[Path | None, Path | None]:
    """O vídeo só é legível (tem o `moov`) quando o ffmpeg do HLAE termina; é o sinal de que a gravação acabou."""
    fim = dep.agora() + limite_s
    while dep.agora() < fim:
        video, wav = _achar_arquivos_do_take(take)
        if video is not None and dep.duracao_do_video(video):
            return video, wav
        dep.dormir(r.poll_s)
    return None, None


def gravar_um_trecho(net, trecho: TrechoGravacao, camera: list[str], pasta_hlae_saida: Path, saida_rounds: Path, dep: Dependencias,
                     r: Roteiro, reg: Registro, cfg: Config) -> dict:
    """Grava UM trecho com UMA opção de câmera. Devolve o resultado (arquivo, duração, velocidade, verificações)."""
    esperado = trecho.duracao_s()
    antes = {t.name for t in _takes(pasta_hlae_saida)}
    diag: dict = {"round": trecho.round, "camera": camera}                            # o que o jogo respondeu em cada passo (prova 5)
    _texto_console(net)
    for linha in hlae.comandos_do_trecho(trecho.tick_ini, trecho.tick_fim)[:2]:     # limpa e pula (pausado)
        net.enviar(linha)
    dep.dormir(r.espera_pulo_s)
    diag["apos_pulo"] = _texto_console(net, 1500)
    for linha in camera:
        net.enviar(linha)
        dep.dormir(0.2)
    dep.dormir(r.espera_camera_s)
    diag["apos_camera"] = _texto_console(net, 800)
    for linha in hlae.comandos_do_trecho(trecho.tick_ini, trecho.tick_fim)[2:]:     # agenda início e fim por tick
        net.enviar(linha)
    net.enviar("mirv_cmd print")
    dep.dormir(0.3)
    diag["agenda"] = _texto_console(net, 800)
    t0 = dep.agora()
    inicio = hlae.iniciar_gravacao(trecho.tick_ini)
    net.enviar(inicio[0])                                                             # pulo refeito: a pausa longa (câmera) não entra na gravação
    dep.dormir(r.espera_repulo_s)
    for linha in camera:                                                              # a câmera de novo, caso o pulo a tenha derrubado
        net.enviar(linha)
    for linha in inicio[1:]:                                                          # gravar e retomar, em seguida
        net.enviar(linha)
    take = _esperar_take_novo(pasta_hlae_saida, antes, dep, r, limite_s=min(10.0, esperado + r.folga_relogio_s))
    if take is None:
        diag["apos_retomar_sem_take"] = _texto_console(net, 1500)
        reg.somar("alertas", f"round {trecho.round}: sem take 10 s depois do início; mandei `record start` de novo")
        net.enviar("mirv_streams record start")
        take = _esperar_take_novo(pasta_hlae_saida, antes, dep, r, limite_s=esperado + r.folga_relogio_s)
    if take is None:
        diag["final"] = _texto_console(net, 1500)
        reg.somar("console_rounds", diag)
        for linha in hlae.parar_gravacao():
            net.enviar(linha)
        raise ErroGravacao(f"O HLAE não começou a gravar o round {trecho.round} (nenhuma pasta take nova, mesmo mandando `record start` duas vezes). "
                           "Veja 'console_rounds' no registro.json.", "gravar")
    reg.somar("console_rounds", diag)
    video, wav = _esperar_video_pronto(take, dep, r, limite_s=esperado + r.folga_relogio_s)
    if video is None:
        for linha in hlae.parar_gravacao():                                         # o agendamento por tick não disparou: força o fim
            net.enviar(linha)
        video, wav = _esperar_video_pronto(take, dep, r, limite_s=30.0)
        reg.somar("alertas", f"round {trecho.round}: o fim agendado por tick não disparou; o fim foi forçado")
    if video is None:
        raise ErroGravacao(f"O vídeo do round {trecho.round} não ficou pronto.", "gravar")
    relogio = dep.agora() - t0
    saida_rounds.mkdir(parents=True, exist_ok=True)
    destino = saida_rounds / f"round_{trecho.round:02d}.mp4"
    tinha_audio = dep.juntar(video, wav, destino)
    duracao = dep.duracao_do_video(destino) or 0.0
    problemas = []
    if abs(duracao - esperado) > 3.0:                                                 # até ~1,5 s parado no começo + o fim por tick (±0,3 s)
        problemas.append(f"duração {duracao:.1f} s, esperado {esperado:.1f} s")
    if dep.quadro_escuro(destino):
        problemas.append("há quadro preto")
    if not tinha_audio:
        problemas.append("sem áudio do jogo (WAV ausente)")
    elif dep.som_mudo(destino):
        problemas.append("o som do jogo saiu MUDO: confira se há uma saída de som ligada no Windows (fone ou caixa; sem ela o CS2 não gera som) "
                              "e grave de novo esse round")
    ok_cam, det_cam = dep.verificar_camera(destino, trecho)
    congelado = dep.congelado_no_inicio(destino)                                      # quadros parados do começo (gravação começa pausada)
    return {"round": trecho.round, "arquivo": str(destino), "esperado_s": round(esperado, 2), "duracao_s": round(duracao, 2),
            "congelado_inicio_s": round(congelado, 2),
            "relogio_s": round(relogio, 1), "velocidade_x": round(duracao / relogio, 2) if relogio > 0 else None,
            "tamanho_mb": round(destino.stat().st_size / 1e6, 1) if destino.is_file() else None, "camera_ok": ok_cam, "camera_detalhe": det_cam,
            "problemas": problemas, "take": take.name}


def _rounds_ja_gravados(registro: Path, rounds_novos: set[int], jogador: int | None = None) -> dict:
    """Do `registro.json` anterior (se houver), os trechos e resultados dos rounds que NÃO vão ser regravados e cujo vídeo ainda existe.
    Prova real 10: regravar só o round 16 (`--rounds 16`) apagava os rounds 3, 8 e 9 do registro, e a edição ficava só com um round."""
    vazio = {"trechos": [], "resultados": []}
    try:
        dados = json.loads(Path(registro).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return vazio
    anterior = dados.get("jogador")
    if jogador is not None and anterior is not None and int(anterior) != int(jogador):   # câmera em outro jogador: nada é reaproveitado (A2)
        return vazio
    validos = {int(r.get("round", -1)) for r in dados.get("resultados", [])
               if int(r.get("round", -1)) not in rounds_novos and Path(str(r.get("arquivo", ""))).is_file()}
    return {"trechos": [t for t in dados.get("trechos", []) if int(t.get("round", -1)) in validos],
            "resultados": [r for r in dados.get("resultados", []) if int(r.get("round", -1)) in validos]}


def gravar(demo: Path, partida: PartidaDemo, cfg: Config, saida: Path | None = None, *, trechos: list[TrechoGravacao] | None = None,
           dep: Dependencias | None = None, roteiro: Roteiro | None = None, imprimir=print) -> dict:
    """Grava os trechos (por padrão os rounds bons da regra do dono). Devolve o registro. Em qualquer erro, fecha o jogo, tira a foto
    "depois" e confere antes de propagar."""
    dep = dep or dependencias_reais()
    r = roteiro or Roteiro()
    g = cfg.gravacao_demo
    saida = Path(saida) if saida else PASTA_PADRAO / Path(demo).stem
    reg = Registro(saida / "registro.json")
    trechos = trechos if trechos is not None else rounds_para_gravar(
        partida, g.so_rounds_bons, cfg.cortes.min_abates_round, g.margem_antes_s, g.margem_depois_s)
    anteriores = _rounds_ja_gravados(reg.caminho, {t.round for t in trechos}, partida.jogador)      # regravar só um round não apaga os outros
    reg.gravar(demo=str(demo), jogador=partida.jogador, jogador_nome=partida.nome, inicio=time.strftime("%Y-%m-%d %H:%M:%S"), trechos=anteriores["trechos"] + [asdict(t) for t in trechos],
               resultados=anteriores["resultados"], alertas=[],
               aviso_autoexec="o CS2 aberto pela conta do Bisturi lê o autoexec.cfg compartilhado do jogo (fps_max, binds): isso pode alterar a gravação; o arquivo NÃO é mexido")
    if not trechos:
        raise ErroGravacao("Nenhum round para gravar (nenhum round com os abates mínimos nesta demo).", "plano")
    ok = prechecagens(demo, partida, cfg, dep, reg, imprimir)
    cs2_pasta = steam_local.pasta_cs2()
    csgo = cs2_pasta / "game" / "csgo" if cs2_pasta else None
    principal = steam_local.pasta_userdata_cs2(steam_local.accountid_do_steamid64(g.conta_principal_id)) if g.conta_principal_id else None
    foto_antes = dep.tirar_foto(csgo, principal)
    reg.gravar(foto_antes={"arquivos": len(foto_antes), "ausentes": foto_antes.ausentes})
    argumentos = hlae.argumentos_do_jogo(demo, g.netcon_porta, g.largura, g.altura)
    comando = hlae.comando_hlae(ok["hlae_exe"], ok["pasta_hlae"], ok["cs2"], argumentos)
    reg.gravar(comando=comando)
    pasta_hlae_saida = saida / "hlae"
    net = None
    erro: Exception | None = None
    resultados: list[dict] = list(anteriores["resultados"])            # os rounds mantidos entram antes dos novos
    juntados: list[tuple[str, Path]] = []                              # R-026: (take, round pronto) dos rounds que ficaram BONS nesta execução
    try:
        imprimir("Abrindo o CS2 pelo HLAE...")
        dep.lancar(comando)
        net = dep.netcon(g.netcon_porta)
        try:
            net.conectar(60.0)
        except Exception as e:  # noqa: BLE001
            raise ErroGravacao(f"Não consegui falar com o console do CS2: {e}", "netcon") from e
        local, enderecos = dep.escuta_so_em_local(g.netcon_porta)
        reg.gravar(netcon_ok=local, netcon_enderecos=enderecos, tcp=dep.linhas_da_porta(g.netcon_porta))
        if not local and enderecos:
            raise ErroGravacao(f"O console remoto do CS2 não está protegido ({'; '.join(enderecos)}). Parei por segurança.", "netcon")
        if not local:
            reg.somar("alertas", AVISO_TABELA_TCP)
            imprimir(f"[aviso] {AVISO_TABELA_TCP}")
        imprimir(f"Console remoto conectado ({'; '.join(enderecos) or 'porta protegida pelo Firewall'}). "
                 f"Esperando a demo carregar ({r.espera_carregar_s:.0f} s)...")
        pronto, em_s = esperar_console_pronto(net, dep, r.espera_carregar_s)
        reg.gravar(console_responde=pronto, console_respondeu_em_s=em_s)
        if not pronto:
            imprimir("[aviso] o console não devolveu texto (o jogo pode não ecoar pelo netcon); seguindo pelo tempo fixo")
        carregou, carregou_s = esperar_demo_carregada(net, dep, max(r.espera_carregar_s, 120.0))
        reg.gravar(demo_carregou=carregou, demo_carregou_em_s=carregou_s)
        if carregou:
            imprimir(f"Demo carregada ({carregou_s:.0f} s).")
        else:
            reg.somar("alertas", "o jogo não confirmou a demo carregada (demo_info sem playback_ticks); seguindo mesmo assim")
            imprimir("[aviso] o jogo não confirmou a demo carregada; seguindo")
        em_jogo, em_jogo_s = esperar_em_jogo(net, dep, r.espera_em_jogo_s)
        reg.gravar(em_jogo=em_jogo, em_jogo_em_s=em_jogo_s)
        if em_jogo:
            imprimir(f"O jogo entrou no mapa ({em_jogo_s:.0f} s depois).")
        else:
            reg.somar("alertas", f"o jogo não avisou que saiu da tela de carregamento em {r.espera_em_jogo_s:.0f} s; seguindo")
            imprimir("[aviso] o jogo não avisou que saiu da tela de carregamento; seguindo")
        dep.dormir(r.espera_assentar_s)
        _texto_console(net)                                                            # descarta o que veio antes do preparo
        console: dict = {"preparo": []}
        for linha in hlae.comandos_de_preparo(pasta_hlae_saida, g.fps, g.cq, g.hud):
            net.enviar(linha)
            dep.dormir(0.2)
            console["preparo"].append({"linha": linha, "resposta": _texto_console(net, 600)})
        net.enviar("mirv_streams print")
        dep.dormir(0.5)
        console["mirv_streams_print"] = _texto_console(net, 1500)
        reg.gravar(console=console)
        respostas = " ".join([console["mirv_streams_print"]] + [x["resposta"] for x in console["preparo"]]).lower()
        if "unknown command" in respostas:
            raise ErroGravacao("O jogo não reconhece os comandos mirv_*: o HLAE não está carregado no CS2 (veja 'console' no registro.json).", "hlae")
        cameras = hlae.camera_candidatos(partida.user_id, partida.entity_id, partida.accountid)
        if not cameras:
            raise ErroGravacao("Sem identidade do jogador na demo para travar a câmera.", "camera")
        camera_atual = 0
        for trecho in trechos:
            melhor = None
            takes_do_round: list[str] = []
            for tentativa in range(min(r.tentativas_camera, len(cameras) - camera_atual)):
                idx = camera_atual + tentativa
                imprimir(f"Round {trecho.round}: gravando ({trecho.duracao_s():.0f} s de jogo, câmera #{idx + 1})...")
                res = gravar_um_trecho(net, trecho, cameras[idx], pasta_hlae_saida, saida / "rounds", dep, r, reg, cfg)
                takes_do_round.append(str(res["take"]))
                res["camera_candidato"] = idx + 1
                res["camera_linhas"] = cameras[idx]
                reg.somar("tentativas", {k: res[k] for k in ("round", "camera_candidato", "camera_ok", "camera_detalhe", "duracao_s", "relogio_s")})
                # o laço para na 1ª tentativa que não é False e toda tentativa grava no MESMO round_NN.mp4: o resultado
                # guardado é sempre o da última, que é o vídeo que ficou no disco (R-022: um None depois de um False
                # mantinha o False, parava a gravação e o registro descrevia outra câmera)
                melhor = res
                if res["camera_ok"] is not False:
                    camera_atual = idx
                    break
                imprimir(f"  câmera #{idx + 1} não parece estar no jogador ({res['camera_detalhe']}); tentando a próxima")
            if melhor is None:      # `tentativas_camera` zerada: nenhum take foi gravado (antes dava TypeError aqui)
                raise ErroGravacao(f"Nenhuma tentativa de câmera foi feita no round {trecho.round} (tentativas_camera = {r.tentativas_camera}).", "camera")
            resultados.append(melhor)
            reg.gravar(resultados=resultados)
            if melhor["camera_ok"] is True and not melhor["problemas"]:
                # R-026 (revisão final): o bruto só sai do round que ficou BOM (câmera confirmada e nenhum problema), junto com os takes das
                # câmeras descartadas dele. Round com aviso (duração estranha, mudo, quadro preto) ou câmera "não confirmada" guarda o bruto:
                # é a única cópia inteira (vídeo e WAV sem perda) para conferir ou juntar de novo.
                juntados += [(take, Path(melhor["arquivo"])) for take in takes_do_round]
            for p in melhor["problemas"]:
                imprimir(f"  [aviso] round {trecho.round}: {p}")
            if melhor["velocidade_x"]:
                imprimir(f"  round {trecho.round} pronto: {melhor['duracao_s']:.0f} s em {melhor['relogio_s']:.0f} s ({melhor['velocidade_x']:.2f}x o tempo real)")
            if melhor["camera_ok"] is None:
                reg.somar("alertas", f"round {trecho.round}: não deu para confirmar a câmera ({melhor['camera_detalhe']}); confira o vídeo")
            if melhor["camera_ok"] is False:
                raise ErroGravacao(f"Nenhuma das câmeras testadas parece estar no jogador (round {trecho.round}). Os vídeos ficaram em "
                                   f"{saida / 'rounds'} para você conferir; parei para não gastar tempo.", "camera")
    except Exception as e:  # noqa: BLE001
        erro = e
        reg.gravar(erro=f"{type(e).__name__}: {e}", etapa=getattr(e, "etapa", ""))
    finally:
        _fechar_jogo(net, dep, r, reg, imprimir, porta=g.netcon_porta)
        foto_depois = dep.tirar_foto(csgo, principal)
        dif = dep.comparar(foto_antes, foto_depois)
        reg.gravar(guarda={"ok": dif.ok, "vermelho": dif.vermelho, "avisos": dif.avisos, "ignorados": len(dif.ignorados)}, fim=time.strftime("%Y-%m-%d %H:%M:%S"))
        if not dif.ok:
            imprimir("ALERTA VERMELHO: arquivos do jogo ou da conta principal mudaram:\n" + dif.texto())
    if erro is not None:
        raise erro
    if not dif.ok:
        return reg.dados                                                          # ALERTA VERMELHO da guarda: nada é apagado; o dono precisa de tudo para conferir
    apagados, liberado = apagar_brutos_juntados(pasta_hlae_saida, juntados)       # R-026: só aqui, com a gravação inteira sem erro
    if apagados:
        reg.gravar(bruto_apagado={"takes": apagados, "mb": round(liberado / 1e6, 1)})
        imprimir(f"Liberei {liberado / 1e9:.2f} GB: apaguei o material bruto de {apagados} take(s) já juntados em rounds/ (hlae/).")
    return reg.dados


def _texto_console(net, limite: int = 4000) -> str:
    """O que o jogo escreveu desde a última leitura (vai para o registro: é a única prova do que o HLAE respondeu)."""
    try:
        return (net.lido() or "")[-limite:]
    except Exception:  # noqa: BLE001
        return ""


def esperar_demo_carregada(net, dep: Dependencias, tempo_maximo_s: float, intervalo_s: float = 3.0) -> tuple[bool, float]:
    """Manda `demo_info` até o jogo listar a demo (`playback_ticks`, visto na prova 4): só então `demo_goto` vale. (carregou, segundos)."""
    inicio = dep.agora()
    while True:
        try:
            net.enviar("demo_info")
        except SemConexao:
            return False, round(dep.agora() - inicio, 1)
        if net.esperar("playback_ticks", intervalo_s):
            return True, round(dep.agora() - inicio, 1)
        if dep.agora() - inicio >= tempo_maximo_s:
            return False, round(dep.agora() - inicio, 1)
        dep.dormir(0.5)


MARCA_EM_JOGO = "CSGO_GAME_UI_STATE_INGAME"    # "ChangeGameUIState: CSGO_GAME_UI_STATE_LOADINGSCREEN -> CSGO_GAME_UI_STATE_INGAME"


def esperar_em_jogo(net, dep: Dependencias, tempo_maximo_s: float, intervalo_s: float = 2.0) -> tuple[bool, float]:
    """Espera o jogo SAIR da tela de carregamento antes do 1º `demo_goto`. Prova real de 17/11 (demo de campeonato, 375 MB): o
    `demo_info` respondeu em 4 s, o salto saiu com a demo ainda parada ("Current playback is 0") e o CS2 caiu com
    `FATAL ERROR: CopyNewEntity: invalid class index (221), out of range 0`; nas gravações boas de 08/11 o salto saía com a demo já andando.
    (entrou, segundos). Sem a marca no tempo: segue, e quem chamou registra o alerta."""
    inicio = dep.agora()
    while True:
        if getattr(net, "viu_em_jogo", False):        # a marca passou antes, numa espera que consumiu o texto (revisão de 17/11)
            return True, round(dep.agora() - inicio, 1)
        try:
            if net.esperar(MARCA_EM_JOGO, intervalo_s):
                return True, round(dep.agora() - inicio, 1)
        except SemConexao:
            return False, round(dep.agora() - inicio, 1)
        if not dep.processo_do_jogo_vivo():           # o jogo caiu no carregamento: não espera os 60 s à toa
            return False, round(dep.agora() - inicio, 1)
        if dep.agora() - inicio >= tempo_maximo_s:
            return False, round(dep.agora() - inicio, 1)
        dep.dormir(intervalo_s)


def esperar_console_pronto(net, dep: Dependencias, tempo_maximo_s: float, intervalo_s: float = 3.0) -> tuple[bool, float]:
    """Manda a sonda (`spec_show_xray`, só consulta) até o jogo devolver o texto dela, ou acabar o tempo. (respondeu, segundos).
    O CS2 ignora comandos enquanto ainda sobe; sem isso, `quit` e os preparos do HLAE podem cair no vazio."""
    inicio = dep.agora()
    while True:
        try:
            net.enviar(SONDA_CONSOLE)
        except SemConexao:
            return False, round(dep.agora() - inicio, 1)
        if net.esperar(SONDA_CONSOLE, intervalo_s):
            return True, round(dep.agora() - inicio, 1)
        if dep.agora() - inicio >= tempo_maximo_s:
            return False, round(dep.agora() - inicio, 1)
        dep.dormir(0.5)


def _fechar_jogo(net, dep: Dependencias, r: Roteiro, reg: Registro, imprimir, porta: int | None = None) -> None:
    """Pede `quit` pelo console e espera o processo sumir. O CS2 pode ignorar o `quit` enquanto carrega (visto em 08/11): reenvia a cada 8 s,
    numa conexão NOVA (a anterior pode ter morrido sem avisar), até `espera_fechar_s`; só então encerra pelo PID e registra o alerta."""
    inicio = dep.agora()
    fim = inicio + r.espera_fechar_s
    ultimo_quit: float | None = None
    quits = 0
    while dep.processo_do_jogo_vivo() and dep.agora() < fim:
        if ultimo_quit is None or dep.agora() - ultimo_quit >= 8.0:
            if ultimo_quit is not None and porta is not None and net is not None:
                try:
                    net.fechar()
                except Exception:  # noqa: BLE001
                    pass
                net = None
            if net is None and porta is not None:
                net = dep.netcon(porta)
                try:
                    net.conectar(3.0)
                except Exception:  # noqa: BLE001
                    net = None
            if net is not None:
                try:
                    net.enviar("quit")
                    quits += 1
                except Exception:  # noqa: BLE001
                    try:
                        net.fechar()
                    except Exception:  # noqa: BLE001
                        pass
                    net = None
            ultimo_quit = dep.agora()
        dep.dormir(1.0)
    vivo = dep.processo_do_jogo_vivo()
    reg.gravar(fechamento={"quits_enviados": quits, "fechou_sozinho": not vivo, "segundos": round(dep.agora() - inicio, 1)})
    if vivo:
        reg.somar("alertas", "o CS2 não fechou sozinho; encerrado pelo PID")
        dep.encerrar_jogo()
    if net is not None:
        try:
            net.fechar()
        except Exception:  # noqa: BLE001
            pass


# ── linha de comando (o dono roda por aqui, numa sessão NORMAL, não elevada) ──────────────────────

def esperar_conta(cfg: Config, dep: Dependencias, imprimir=print, tempo_maximo_s: float = 900.0, intervalo_s: float = 3.0) -> int:
    """Espera o dono trocar o Steam para a conta do Bisturi (o Bisturi nunca troca sozinho nem guarda senha). Levanta `ErroGravacao` ao esgotar o tempo."""
    g = cfg.gravacao_demo
    fim = dep.agora() + tempo_maximo_s
    ultimo_aviso = -1e9
    while True:
        try:
            if not dep.steam_rodando():
                raise steam_local.ContaErrada(f"O Steam está fechado. Abra o Steam e entre {steam_local.rotulo_conta('bisturi', g.conta_apelido, 'na')}.")
            return steam_local.exigir_conta_do_bisturi(g.conta_steamid, g.conta_principal_id, dep.conta_logada(),
                                                   apelido_bisturi=g.conta_apelido, apelido_principal=g.conta_principal_apelido)
        except steam_local.ContaErrada as e:
            if dep.agora() - ultimo_aviso > 15.0:
                imprimir(f"\n>>> {e}\n    Esperando (o Bisturi confere sozinho a cada poucos segundos; Ctrl+C cancela)...")
                ultimo_aviso = dep.agora()
            if dep.agora() >= fim:
                raise ErroGravacao("Esperei a troca de conta e ela não aconteceu.", "conta") from e
            dep.dormir(intervalo_s)


def checklist(demo: Path | None, cfg: Config, dep: Dependencias | None = None) -> list[tuple[str, bool, str]]:
    """O que está pronto e o que falta, sem abrir nada: (item, ok, detalhe)."""
    dep = dep or dependencias_reais()
    g = cfg.gravacao_demo
    itens: list[tuple[str, bool, str]] = []
    hl = terceiros.caminho_hlae()
    itens.append(("HLAE instalado", hl is not None, str(hl) if hl else terceiros.dica_instalar("hlae")))
    itens.append(("ffmpeg do HLAE", (terceiros.PASTA_BIN / "hlae" / "ffmpeg" / "bin" / "ffmpeg.exe").is_file(), "bin/hlae/ffmpeg/bin"))
    cs2 = steam_local.cs2_exe()
    itens.append(("cs2.exe do Steam", cs2 is not None, str(cs2) if cs2 else "não achei"))
    itens.append(("versão do CS2", True, steam_local.versao_cs2().get("PatchVersion", "?")))
    elevada = terceiros.rodando_como_admin()
    itens.append(("sessão normal (não administrador)", not elevada, "feche o Bisturi e abra de novo pelo atalho, sem escolher 'Executar como administrador'" if elevada else "ok"))
    ok_fw, det_fw = dep.firewall(g.netcon_porta)
    itens.append((f"Firewall bloqueia a porta {g.netcon_porta} para fora", ok_fw,
                  det_fw if ok_fw else f"{det_fw}; crie (PowerShell como administrador): {comando_regra_firewall(g.netcon_porta)}"))
    abertos = dep.jogo_aberto()
    itens.append(("CS2 e OBS fechados", not abertos, ", ".join(abertos) or "ok"))
    rodando = dep.steam_rodando()
    itens.append(("Steam aberto", rodando, "ok" if rodando else "abra o Steam"))
    try:
        steam_local.exigir_conta_do_bisturi(g.conta_steamid, g.conta_principal_id, dep.conta_logada(),
                                               apelido_bisturi=g.conta_apelido, apelido_principal=g.conta_principal_apelido)
        itens.append(("Steam na 2ª conta (conta do Bisturi)", True, "ok"))
    except steam_local.ContaErrada as e:
        itens.append(("Steam na 2ª conta (conta do Bisturi)", False, str(e)))
    itens.append(("demo", bool(demo and Path(demo).is_file()), str(demo) if demo else "diga --demo"))
    return itens


def teste_sem_hlae(demo: Path, cfg: Config, dep: Dependencias | None = None, roteiro: Roteiro | None = None, imprimir=print) -> dict:
    """PRIMEIRA PROVA (com o dono presente): abre o CS2 SEM o HLAE, só `playdemo`, confere a porta do console, mede se o console responde,
    pede o `demo_info` e fecha. Mostra se a demo abre sem travar (o bug do CS2 de 11/2026) e quanto o jogo leva. Nada é gravado.
    Tudo vai para `gravacao_demo/teste_sem_hlae.json`, com fotos da tabela TCP em vários momentos (para entender este Windows)."""
    dep = dep or dependencias_reais()
    r = roteiro or Roteiro()
    g = cfg.gravacao_demo
    dep.sem_admin()
    abertos = dep.jogo_aberto()
    if abertos:
        raise ErroGravacao(f"Feche antes: {', '.join(abertos)} está aberto.", "prechecagem")
    ok_fw, det_fw = dep.firewall(g.netcon_porta)
    if not ok_fw:
        raise ErroGravacao(mensagem_firewall(g.netcon_porta, det_fw), "prechecagem")
    steam_local.exigir_conta_do_bisturi(g.conta_steamid, g.conta_principal_id, dep.conta_logada(),
                                           apelido_bisturi=g.conta_apelido, apelido_principal=g.conta_principal_apelido)
    cs2 = steam_local.cs2_exe()
    if cs2 is None:
        raise ErroGravacao("Não achei o cs2.exe do Steam.", "prechecagem")
    cs2_pasta = steam_local.pasta_cs2()
    csgo = cs2_pasta / "game" / "csgo" if cs2_pasta else None
    principal = steam_local.pasta_userdata_cs2(steam_local.accountid_do_steamid64(g.conta_principal_id)) if g.conta_principal_id else None
    antes = dep.tirar_foto(csgo, principal)
    comando = hlae.comando_cs2_direto(cs2, hlae.argumentos_do_jogo(demo, g.netcon_porta, 1280, 720))
    resultado: dict = {"quando": time.strftime("%Y-%m-%d %H:%M:%S"), "comando": comando, "firewall": det_fw, "abriu_console": False,
                       "porta_so_local": None, "tcp": [], "alertas": []}
    registro = Registro(PASTA_PADRAO / "teste_sem_hlae.json", resultado)
    net = None
    t0 = dep.agora()

    def foto_tcp(rotulo: str) -> None:
        resultado["tcp"].append({"quando": rotulo, "t_s": round(dep.agora() - t0, 1), "pids_cs2": dep.pids_do_jogo(),
                                 "linhas": dep.linhas_da_porta(g.netcon_porta)})
        registro.gravar()
    try:
        imprimir("Abrindo o CS2 (sem HLAE) com a demo...")
        proc = dep.lancar(comando)
        resultado["pid_lancado"] = getattr(proc, "pid", None)
        net = dep.netcon(g.netcon_porta)
        net.conectar(90.0)
        resultado["abriu_console"] = True
        resultado["segundos_ate_o_console"] = round(dep.agora() - t0, 1)
        foto_tcp("logo após conectar")
        local, ends = dep.escuta_so_em_local(g.netcon_porta)
        resultado["porta_so_local"], resultado["enderecos"] = local, ends
        imprimir(f"Console remoto abriu em {resultado['segundos_ate_o_console']} s; porta protegida: {local} {ends}")
        if not local and ends:
            raise ErroGravacao(f"O console remoto do CS2 não está protegido ({'; '.join(ends)}). Parei por segurança.", "netcon")
        if not local:
            resultado["alertas"].append(AVISO_TABELA_TCP)
            imprimir(f"[aviso] {AVISO_TABELA_TCP}")
        dep.dormir(2.0)
        foto_tcp("2 s depois")
        pronto, em_s = esperar_console_pronto(net, dep, r.espera_carregar_s)
        resultado["console_responde"], resultado["console_respondeu_em_s"] = pronto, em_s
        imprimir(f"O console respondeu em {em_s:.0f} s." if pronto else f"O console não devolveu texto em {em_s:.0f} s (seguindo).")
        dep.dormir(max(0.0, r.espera_carregar_s - em_s))
        foto_tcp("depois da espera")
        net.enviar("demo_info")
        dep.dormir(2.0)
        resultado["console_saida"] = net.lido()[-3000:]
        net.enviar("demo_pause")
        resultado["jogo_vivo_apos_espera"] = dep.processo_do_jogo_vivo()
        imprimir("Esperei a demo carregar. Se a tela do CS2 mostrou a partida, o teste passou. Fechando...")
    finally:
        _fechar_jogo(net, dep, r, registro, imprimir, porta=g.netcon_porta)
        foto_tcp("depois de fechar")
        dif = dep.comparar(antes, dep.tirar_foto(csgo, principal))
        resultado["guarda_ok"] = dif.ok
        if not dif.ok:
            imprimir("ALERTA VERMELHO: arquivos mudaram:\n" + dif.texto())
        elif dif.avisos:
            resultado["guarda_avisos"] = dif.avisos
        registro.gravar()
    return resultado


def _quebra_vira_interrupcao(*_args) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    import argparse
    if sys.platform == "win32":
        import signal
        signal.signal(signal.SIGBREAK, _quebra_vira_interrupcao)   # o cancelar da tela manda CTRL+BREAK
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="cs2clip.gravador_demo",
                                 description="Grava a partida pelo CS2 a partir da demo (HLAE). Rode numa sessão NORMAL (não administrador).")
    ap.add_argument("--demo", required=True, help="caminho da .dem (ou 'auto' para a mais recente da pasta de demos do CS2)")
    ap.add_argument("--config", default=str(PASTA_DADOS / "config.toml"))
    ap.add_argument("--jogador", default="", help="quem a câmera segue: nome (ou parte) ou steamid. Vazio = você (conta principal). "
                                                  "Serve para demo de campeonato (ex.: --jogador=NomeDoJogador)")
    ap.add_argument("--listar-jogadores", action="store_true", help="só mostra os jogadores da demo (nome, steamid, abates) e sai")
    ap.add_argument("--ticks", default="", help="grava só estes trechos, em ticks da demo: INICIO-FIM[,INICIO-FIM] (ex.: 106500-110100; 64 ticks = 1 s). "
                                               "Vale mais que --rounds; um trecho por round")
    ap.add_argument("--rounds", default="", help="rounds a gravar, separados por vírgula (padrão: os rounds bons da regra do dono)")
    ap.add_argument("--todos", action="store_true", help="grava a partida inteira")
    ap.add_argument("--so-checar", action="store_true", help="mostra o plano e o que falta, sem abrir nada")
    ap.add_argument("--teste-sem-hlae", action="store_true", help="primeira prova: abre o CS2 sem o HLAE só para ver se a demo abre")
    ap.add_argument("--saida", default="", help="pasta de saída (padrão: gravacao_demo/<nome da demo>)")
    ap.add_argument("--editar", action="store_true", help="depois de gravar, já monta cortes, shorts e montagem a partir do vídeo limpo")
    ap.add_argument("--live", default="", help="(com --editar) a gravação da live (OBS): a voz dela entra por cima do jogo")
    ap.add_argument("--analise-da-live", default="", help="(com --live) a pasta saida/<live> da análise feita junto da demo (tem o sincronia.json)")
    a = ap.parse_args(argv)
    from .config import load_config
    from .demo import DemoInvalida, ler_demo
    cfg = load_config(Path(a.config))
    g = cfg.gravacao_demo
    demo = Path(a.demo)
    escolhido = a.jogador.strip()
    quem = escolhido or g.conta_principal_id or cfg.demo.jogador or None
    partida = None
    try:
        if a.listar_jogadores:
            from .demo import listar_jogadores
            for nome, sid, abates in listar_jogadores(demo):
                print(f"  {nome}  (steamid {sid}, {abates} abates)")
            return 0
        if a.demo == "auto":
            from . import demos_cs2
            for cand in reversed(demos_cs2.listar_demos()):          # da mais nova para a mais velha: a primeira em que você jogou
                p_ = ler_demo(cand.caminho, quem)
                if p_.jogador is not None and p_.abates:
                    demo, partida = cand.caminho, p_
                    break
            if partida is None:
                quem_txt = f"o jogador \"{escolhido}\"" if escolhido else f"você ({steam_local.rotulo_conta('principal', g.conta_principal_apelido)}, em Configurar → Contas do Steam)"
                print(f"Não achei, na pasta de demos do CS2, nenhuma demo em que {quem_txt} tenha jogado.")
                return 1
        else:
            partida = ler_demo(demo, quem)
            if partida.jogador is None and escolhido:
                print(f"Não achei o jogador \"{escolhido}\" nessa demo. Jogadores: "
                      + ", ".join(partida.jogadores.values()))
                return 1
            ambiguos = jogadores_ambiguos(partida.jogadores, escolhido)
            if ambiguos:            # parte de nome que casa com vários: antes escolhia calado quem mais matou (revisão de 17/11, B4)
                print(f"\"{escolhido}\" casa com mais de um jogador ({', '.join(ambiguos)}). Use o nome inteiro ou o steamid (--listar-jogadores).")
                return 1
            if partida.jogador is None:
                print(f"Não achei o seu jogador nessa demo (confira {steam_local.rotulo_conta('principal', g.conta_principal_apelido)} em Configurar → Contas do Steam).")
                return 1
    except DemoInvalida as e:
        print(f"[erro] {e}")
        return 1
    if a.todos:
        g.so_rounds_bons = False
    trechos = rounds_para_gravar(partida, g.so_rounds_bons, cfg.cortes.min_abates_round, g.margem_antes_s, g.margem_depois_s)
    if a.rounds:
        quer = {int(x) for x in a.rounds.split(",") if x.strip().isdigit()}
        trechos = [t for t in rounds_para_gravar(partida, False, 0, g.margem_antes_s, g.margem_depois_s) if t.round in quer]
    if a.ticks:
        try:
            trechos = trechos_por_ticks(partida, a.ticks)
        except ValueError as e:
            print(f"[erro] ticks: {e}")
            return 1
    print(f"\nDemo: {demo.name} | {partida.titulo} | jogador: {partida.nome} (user_id {partida.user_id}, conta {partida.accountid}) "
          f"| patch da demo {partida.patch_version}")
    total = sum(t.duracao_s() for t in trechos)
    print(f"Rounds a gravar ({len(trechos)}): " + ", ".join(f"{t.round} ({t.abates} abates, {t.duracao_s():.0f} s)" for t in trechos)
          + f" | total {total / 60:.1f} min de jogo")
    dep = dependencias_reais()
    print("\nChecklist:")
    for item, ok, det in checklist(demo, cfg, dep):
        print(f"  [{'ok' if ok else 'FALTA'}] {item}: {det}")
    if a.so_checar:
        return 0
    na_conta_do_bisturi = False        # R-021: o lembrete de trocar de volta só vale depois que o dono chegou na conta do Bisturi
    try:
        esperar_conta(cfg, dep)
        na_conta_do_bisturi = True
        if a.teste_sem_hlae:
            res = teste_sem_hlae(demo, cfg, dep)
            print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
            fch = res.get("fechamento") or {}
            print(f"\nRESUMO: console em {res.get('segundos_ate_o_console')} s | respondeu: {res.get('console_responde')} "
                  f"({res.get('console_respondeu_em_s')} s) | porta: {res.get('enderecos')} | jogo vivo após a espera: {res.get('jogo_vivo_apos_espera')} "
                  f"| fechou sozinho: {fch.get('fechou_sozinho')} ({fch.get('quits_enviados')} quit) | guarda: {'ok' if res.get('guarda_ok') else 'ALERTA'}")
            print("Me diga também o que você VIU na tela do CS2 (abriu a partida? travou? quanto demorou?).")
        else:
            if not trechos:
                print("Nenhum round para gravar.")
                return 1
            saida = Path(a.saida) if a.saida else PASTA_PADRAO / pasta_da_partida(demo.stem, partida.jogador if escolhido else None)
            reg = gravar(demo, partida, cfg, saida, trechos=trechos, dep=dep)
            print(f"\nPronto. Vídeos em {saida / 'rounds'}; relatório em registro.json.")
            print("Guarda da configuração:", "tudo igual" if reg.get("guarda", {}).get("ok") else "ALERTA, veja registro.json")
            if a.editar:
                from .editar_demo import editar
                from .video_limpo import ErroVideoLimpo
                try:
                    pasta = editar(demo, cfg, jogador=escolhido or None, pasta_gravacao=saida, live=Path(a.live) if a.live else None,
                                   analise_da_live=Path(a.analise_da_live) if a.analise_da_live else None)
                    print(f"\nEdição pronta em {pasta} (cortes, shorts, montagem, capa e relatório).")
                except ErroVideoLimpo as e:
                    print(f"\n[a edição parou] {e}")
    except (ErroGravacao, steam_local.ContaErrada, terceiros.ErroTerceiros, hlae.LinhaInsegura) as e:
        print(f"\n[parei] {e}")
        return 1
    except KeyboardInterrupt:
        print("\nCancelado.")
        return 1
    finally:                           # R-021: também com erro ou Ctrl+C (justamente quando o dono mais precisa do lembrete)
        if na_conta_do_bisturi:
            print(f"\nAGORA: no Steam, troque de volta para {steam_local.rotulo_conta('principal', g.conta_principal_apelido)}, se quiser jogar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
