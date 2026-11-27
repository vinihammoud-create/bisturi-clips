"""D4 (Plano D), parte 1: a linha de comando do jogo e os comandos de preparo. As condições do auditor viram testes (com controle)."""
from pathlib import Path

import pytest

from cs2clip import hlae
from cs2clip.hlae import LinhaInsegura
from cs2clip.netcon import ComandoBloqueado, validar_linha

DEMO = Path("C:/Users/jogador/Projetos/bisturi/demos/match730_1_2_3.dem")


def test_linha_do_jogo_tem_insecure_porta_e_playdemo_e_nada_de_conectar():
    linha = hlae.argumentos_do_jogo(DEMO, 2121, 1920, 1080)
    tokens = linha.split()
    assert "-insecure" in tokens and "-novid" in tokens and "-netconport" in tokens and tokens[tokens.index("-netconport") + 1] == "2121"
    assert "+playdemo" in tokens and "-w" in tokens and "1920" in tokens and "1080" in tokens
    for proibido in ("+connect", "-allow_third_party_software", "-trusted", "-trustedlaunch"):
        assert proibido not in tokens
    assert '"' not in linha                                                      # o caminho do projeto não tem espaço: sem aspas


def test_caminho_com_espaco_vai_entre_aspas_e_caminho_perigoso_e_recusado(tmp_path):
    com_espaco = tmp_path / "pasta com espaco"
    com_espaco.mkdir()
    dem = com_espaco / "a.dem"
    dem.write_bytes(b"x")
    assert f'+playdemo "{dem.resolve()}"' in hlae.argumentos_do_jogo(dem, 2121, 1280, 720)
    for ruim in ('a"b.dem', "a;b.dem", "a\nb.dem"):
        with pytest.raises(ComandoBloqueado):
            hlae.argumentos_do_jogo(tmp_path / ruim, 2121, 1280, 720)


def test_verificacao_do_lancamento_recusa_cada_argumento_proibido_e_a_falta_do_insecure():
    bom = "-steam -insecure -novid -console -sw -w 1920 -h 1080 -netconport 2121 +playdemo x.dem"
    hlae.verificar_linha_do_jogo(bom)                                              # CONTROLE: a boa passa
    for extra in ("+connect 1.2.3.4", "-allow_third_party_software", "-trusted", "-trustedlaunch", "+map de_dust2", "+exec x", "+rcon"):
        with pytest.raises(LinhaInsegura):
            hlae.verificar_linha_do_jogo(bom + " " + extra)
    with pytest.raises(LinhaInsegura, match="insecure"):
        hlae.verificar_linha_do_jogo(bom.replace("-insecure ", ""))
    with pytest.raises(LinhaInsegura, match="playdemo"):
        hlae.verificar_linha_do_jogo("-steam -insecure -novid")
    for porta in (80, 70000):
        with pytest.raises(LinhaInsegura):
            hlae.argumentos_do_jogo(DEMO, porta, 1920, 1080)
    with pytest.raises(LinhaInsegura):
        hlae.argumentos_do_jogo(DEMO, 2121, 0, 1080)


def test_comando_do_hlae_sem_interface_com_a_dll_certa():
    args = hlae.argumentos_do_jogo(DEMO, 2121, 1920, 1080)
    cmd = hlae.comando_hlae(Path("C:/b/hlae/HLAE.exe"), Path("C:/b/hlae"), Path("C:/steam/cs2.exe"), args)
    assert cmd[0].endswith("HLAE.exe") and cmd[1:4] == ["-customLoader", "-noGui", "-autoStart"]
    assert cmd[cmd.index("-hookDllPath") + 1].replace("\\", "/").endswith("hlae/x64/AfxHookSource2.dll")
    assert cmd[cmd.index("-programPath") + 1].endswith("cs2.exe") and cmd[cmd.index("-cmdLine") + 1] == args + " -afxDisableSteamStorage"
    with pytest.raises(LinhaInsegura):
        hlae.comando_hlae(Path("h.exe"), Path("h"), Path("c.exe"), args + " +connect 1.2.3.4")          # o HLAE também verifica


def test_teste_direto_sem_hlae_usa_os_mesmos_argumentos_separados():
    args = hlae.argumentos_do_jogo(DEMO, 2121, 1920, 1080)
    cmd = hlae.comando_cs2_direto(Path("C:/steam/cs2.exe"), args)
    assert cmd[0].endswith("cs2.exe") and "-insecure" in cmd and cmd[cmd.index("-netconport") + 1] == "2121" and cmd[-2] == "+playdemo"


def test_opcoes_do_ffmpeg_sao_constantes_e_o_cq_e_validado():
    o = hlae.opcoes_ffmpeg(18)
    assert "h264_nvenc" in o and "-cq 18" in o and "yuv420p" in o and o.endswith("{QUOTE}{AFX_OUTPUT_FILE}{QUOTE}")
    for ruim in (-1, 52, 999):
        with pytest.raises(LinhaInsegura):
            hlae.opcoes_ffmpeg(ruim)
    assert hlae.opcoes_ffmpeg("20") == hlae.opcoes_ffmpeg(20)                       # número como texto vira número; texto livre não passa
    with pytest.raises(ValueError):
        hlae.opcoes_ffmpeg("18; -f null x")


def test_comandos_de_preparo_todos_passam_na_lista_branca_e_cobrem_stream_preset_pasta_fps_audio(tmp_path):
    linhas = hlae.comandos_de_preparo(tmp_path / "gravacao_demo", 60, 18)
    texto = "\n".join(linhas)
    assert "mirv_streams add normal bisturi" in texto and "settings add ffmpeg bisturi_nvenc" in texto
    assert "record fps 60" in texto and "record startMovieWav 1" in texto and f'record name "{(tmp_path / "gravacao_demo").resolve()}"' in texto
    assert "beforeUi" not in texto
    assert "capture beforeUi" in "\n".join(hlae.comandos_de_preparo(tmp_path, 60, 18, hud=False))      # sem HUD: só o mundo
    assert all(validar_linha(linha) for linha in linhas)
    for ruim in (0, 241):
        with pytest.raises(LinhaInsegura):
            hlae.comandos_de_preparo(tmp_path, ruim, 18)
    with pytest.raises(ComandoBloqueado):
        hlae.comandos_de_preparo(tmp_path / 'a"b', 60, 18)                            # nome de pasta com aspas: injeção no console


def test_candidatos_de_camera_na_ordem_certa_e_todos_na_lista_branca():
    c = hlae.camera_candidatos(9, 533, 111111111)
    assert c[0] == ["spec_mode 2", "spec_player 10"] and c[1] == ["spec_mode 2", "spec_player 9"]
    assert c[2][0] == 'mirv_script_load "mirv_script_spec_lock.js"' and c[2][-1] == "mirv_script_spec_lock 10"
    assert c[3] == ["spec_mode 2", "spec_lock_to_accountid 111111111"] and c[4] == ["spec_mode 2", "spec_player 533"]
    assert hlae.camera_candidatos(None, None, None) == []                              # sem identidade: o gravador pede ajuda


def test_comandos_do_trecho_pulam_pausados_e_agendam_so_o_fim_por_tick():
    linhas = hlae.comandos_do_trecho(69481, 76400)
    assert linhas == ["mirv_cmd clear", "demo_goto 69481 0 1", 'mirv_cmd addAtTick 76400 "mirv_streams record end; demo_pause"']
    assert hlae.iniciar_gravacao(69481) == ["demo_goto 69481 0 1", "mirv_streams record start", "demo_resume"]   # provas 6, 7 e 8
    assert all(validar_linha(x) for x in hlae.iniciar_gravacao(69481))
    assert any(x.startswith("mirv_panorama panelStyle panelId=HudDemoController opacity=0") for x in hlae.comandos_de_preparo(Path("C:/x"), 60, 18))
    for ruim in ((100, 100), (200, 100), (-1, 5)):
        with pytest.raises(LinhaInsegura):
            hlae.comandos_do_trecho(*ruim)
    assert all(validar_linha(x) for x in hlae.parar_gravacao())
