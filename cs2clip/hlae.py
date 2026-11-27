"""D4 (Plano D): como o Bisturi lança o CS2 pelo HLAE e o que manda ao jogo. Só funções PURAS e verificáveis; quem executa é `gravador_demo.py`.

Condições do auditor (`docs/audit/AUDITORIA_PARTE_D.md`) que este módulo garante e os testes provam:
  * a linha de comando do jogo tem `-insecure` e NÃO tem `+connect`, `-allow_third_party_software`, `-trusted`;
  * o `playdemo` só existe aqui, na linha de LANÇAMENTO, com caminho sem aspas, `;` ou quebra de linha;
  * as opções do ffmpeg vêm só de constantes do código (o único número livre é o `cq`, validado de 0 a 51);
  * toda linha enviada depois ao console remoto passa pela lista branca de `netcon.py`.
"""
from __future__ import annotations

import re
from pathlib import Path

from .netcon import validar_linha, validar_valor

NOME_STREAM = "bisturi"
NOME_PRESET = "bisturi_nvenc"
PAINEL_PLAYER_DEMO = "HudDemoController"      # id em panorama/layout/hud/hud.xml (CSGOHudDemoController)
PROIBIDOS_NA_LINHA = ("+connect", "-allow_third_party_software", "-trusted", "-trustedlaunch", "+map", "+exec", "-condebug", "+rcon")
HOOK_DLL = Path("x64") / "AfxHookSource2.dll"


class LinhaInsegura(Exception):
    """A linha de comando do jogo viola as condições do auditor: o jogo NÃO é lançado."""


def argumentos_do_jogo(demo: Path, porta: int, largura: int, altura: int) -> str:
    """Os argumentos do cs2.exe (a parte do `-cmdLine`). A demo vai por caminho ABSOLUTO; aspas só se houver espaço no caminho."""
    demo = Path(demo)
    caminho = str(demo.resolve())
    validar_valor(caminho)
    if not re.fullmatch(r"\d{4,5}", str(int(porta))) or not 1024 <= int(porta) <= 65535:
        raise LinhaInsegura("porta do console remoto inválida")
    if not (largura > 0 and altura > 0):
        raise LinhaInsegura("resolução inválida")
    alvo = f'"{caminho}"' if " " in caminho else caminho
    linha = f"-steam -insecure -novid -console -sw -w {int(largura)} -h {int(altura)} -netconport {int(porta)} +playdemo {alvo}"
    verificar_linha_do_jogo(linha)
    return linha


def verificar_linha_do_jogo(linha: str) -> None:
    """Teste de segurança antes do lançamento: `-insecure` presente e nenhum argumento proibido. Sem isso, abortar."""
    baixo = linha.lower()
    tokens = baixo.split()
    if "-insecure" not in tokens:
        raise LinhaInsegura("a linha de comando do CS2 não tem -insecure")
    for proibido in PROIBIDOS_NA_LINHA:
        if proibido in tokens:
            raise LinhaInsegura(f"a linha de comando do CS2 tem {proibido}, que é proibido")
    if "+playdemo" not in tokens:
        raise LinhaInsegura("a linha de comando do CS2 não tem +playdemo (só se toca demo, nunca se conecta)")


def comando_hlae(hlae_exe: Path, pasta_hlae: Path, cs2_exe: Path, argumentos_jogo: str) -> list[str]:
    """`HLAE.exe -customLoader -noGui -autoStart -hookDllPath ... -programPath ... -cmdLine "<argumentos>"` (sem interface; código 0 = ok)."""
    verificar_linha_do_jogo(argumentos_jogo)
    dll = Path(pasta_hlae) / HOOK_DLL
    return [str(hlae_exe), "-customLoader", "-noGui", "-autoStart", "-hookDllPath", str(dll), "-programPath", str(cs2_exe),
            "-cmdLine", argumentos_jogo + " -afxDisableSteamStorage"]       # recomendação do HLAE: a nuvem do Steam não mexe na config


def comando_cs2_direto(cs2_exe: Path, argumentos_jogo: str) -> list[str]:
    """Teste SEM o HLAE (primeira prova, com o dono presente): o próprio cs2.exe com os mesmos argumentos."""
    verificar_linha_do_jogo(argumentos_jogo)
    return [str(cs2_exe), *_dividir(argumentos_jogo)]


def _dividir(linha: str) -> list[str]:
    partes, atual, aspas = [], [], False
    for c in linha:
        if c == '"':
            aspas = not aspas
        elif c == " " and not aspas:
            if atual:
                partes.append("".join(atual))
                atual = []
        else:
            atual.append(c)
    if atual:
        partes.append("".join(atual))
    return partes


def opcoes_ffmpeg(cq: int) -> str:
    """Opções de SAÍDA do ffmpeg do HLAE (a entrada ele mesmo monta). Constantes; só o `cq` (0-51) é número."""
    cq = int(cq)
    if not 0 <= cq <= 51:
        raise LinhaInsegura("cq fora de 0 a 51")
    return f"-c:v h264_nvenc -preset p5 -tune hq -rc vbr -cq {cq} -b:v 0 -pix_fmt yuv420p {{QUOTE}}{{AFX_OUTPUT_FILE}}{{QUOTE}}"


def comandos_de_preparo(pasta_gravacao: Path, fps: int, cq: int, hud: bool = True) -> list[str]:
    """As linhas do console remoto que deixam o HLAE pronto para gravar (sem raio-x, sem o painel do player, stream, preset NVENC, pasta, fps, áudio)."""
    pasta = validar_valor(str(Path(pasta_gravacao).resolve()))
    if not 1 <= int(fps) <= 240:
        raise LinhaInsegura("fps fora de 1 a 240")
    linhas = [
        "spec_show_xray 0",
        f"mirv_panorama panelStyle panelId={PAINEL_PLAYER_DEMO} opacity=0",      # esconde o painel do player da demo (o `demoui` é um alternador e falhou na prova 7)
        f"mirv_streams add normal {NOME_STREAM}",
        f'mirv_streams settings add ffmpeg {NOME_PRESET} "{opcoes_ffmpeg(cq)}"',
        f"mirv_streams edit {NOME_STREAM} settings {NOME_PRESET}",
    ]
    if not hud:
        linhas.append(f"mirv_streams edit {NOME_STREAM} capture beforeUi")
    linhas += [f'mirv_streams record name "{pasta}"', f"mirv_streams record fps {int(fps)}", "mirv_streams record startMovieWav 1"]
    for linha in linhas:
        validar_linha(linha)
    return linhas


def camera_candidatos(user_id: int | None, entity_id: int | None, accountid: int | None) -> list[list[str]]:
    """Caminhos para travar a câmera no dono, do mais provável ao menos (o `spec_lock_to_accountid` quebrou em 05/2026; o
    `spec_player` com o índice do controlador, user_id + 1, funcionou num teste de 11/2026). O gravador usa o primeiro que passar na verificação."""
    c: list[list[str]] = []
    if user_id is not None:
        c.append(["spec_mode 2", f"spec_player {int(user_id) + 1}"])
        c.append(["spec_mode 2", f"spec_player {int(user_id)}"])
        c.append(['mirv_script_load "mirv_script_spec_lock.js"', "spec_mode 1", f"mirv_script_spec_lock {int(user_id) + 1}"])
    if accountid:
        c.append(["spec_mode 2", f"spec_lock_to_accountid {int(accountid)}"])
    if entity_id is not None:
        c.append(["spec_mode 2", f"spec_player {int(entity_id)}"])
    for linhas in c:
        for linha in linhas:
            validar_linha(linha)
    return c


def comandos_do_trecho(tick_ini: int, tick_fim: int) -> list[str]:
    """Pula para o início do trecho (pausado) e agenda só o FIM por tick; o início é `iniciar_gravacao()` (gravar pausado + retomar).
    Provas reais 6 e 7 (08/11) + código do HLAE (`CommandSystem::OnExecuteCommands`): um comando agendado por tick só dispara quando o tick alvo
    fica ESTRITAMENTE entre o tick do quadro anterior e o atual, ou seja, quando o jogo pula um tick num quadro. Gravando (60 fps forçados,
    64 ticks/s) isso acontece a cada ~15 quadros e o fim dispara; antes de gravar, com fps alto, a demo anda menos de 1 tick por quadro e o
    início nunca disparava."""
    if not (0 <= int(tick_ini) < int(tick_fim)):
        raise LinhaInsegura("ticks do trecho inválidos")
    linhas = [
        "mirv_cmd clear",
        f"demo_goto {int(tick_ini)} 0 1",
        f'mirv_cmd addAtTick {int(tick_fim)} "mirv_streams record end; demo_pause"',
    ]
    for linha in linhas:
        validar_linha(linha)
    return linhas


def iniciar_gravacao(tick_ini: int) -> list[str]:
    """Pula DE NOVO para o tick exato (pausado), começa a gravar e retoma, tudo em seguida. Prova real 8 (08/11): ao retomar, o CS2 pula
    para a frente o tempo que ficou pausado (4,8 s de pausa para a câmera = 4,65 s de demo perdidos, medido pelos abates no vídeo); com o
    pulo refeito logo antes, a pausa cai para décimos de segundo. Os quadros parados que sobrarem são medidos (`congelado_inicio_s`)."""
    if int(tick_ini) < 0:
        raise LinhaInsegura("tick inválido")
    return [f"demo_goto {int(tick_ini)} 0 1", "mirv_streams record start", "demo_resume"]


def parar_gravacao() -> list[str]:
    return ["mirv_streams record end", "demo_pause", "mirv_cmd clear"]
