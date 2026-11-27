"""D2 (Plano D): Steam e CS2 neste PC + a guarda da configuração (foto antes/depois) + o bloqueio duro da conta principal.

Tudo com pastas e registro FALSOS (nada do Steam real é tocado). Cada teste tem CONTROLE.
"""
import os
from pathlib import Path

import pytest

from cs2clip import steam_local as sl

PRINCIPAL = "76561198071376839"      # conta principal (falsa)
SEGUNDA = "76561198182487950"        # conta do Bisturi (falsa)
ID_PRINCIPAL, ID_SEGUNDA = 111111111, 222222222


def _jogo(tmp_path):
    csgo = tmp_path / "Steam" / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo"
    (csgo / "cfg").mkdir(parents=True)
    (csgo / "replays").mkdir()
    (csgo / "gameinfo.gi").write_text("SearchPaths { Game csgo }", encoding="utf-8")
    (csgo / "steam.inf").write_text("PatchVersion=1.41.9.6\nVersionDate=Nov 04 2026\n", encoding="utf-8")
    (csgo / "cfg" / "autoexec.cfg").write_text("fps_max 240\n", encoding="utf-8")
    (csgo / "pak01_000.vpk").write_bytes(b"P" * 5000)
    (csgo / "console.log").write_text("log\n", encoding="utf-8")
    (csgo / "replays" / "match730_1.dem").write_bytes(b"DEMO")
    ud = tmp_path / "Steam" / "userdata" / str(ID_PRINCIPAL) / "730"
    (ud / "local" / "cfg").mkdir(parents=True)
    (ud / "local" / "cfg" / "cs2_video.txt").write_text("setting.fullscreen 1\n", encoding="utf-8")
    (ud / "socache.dt").write_bytes(b"cache")
    return csgo, ud


def _toca(arq: Path, conteudo):
    """Muda o conteúdo mantendo o tamanho e empurra a data (como um programa que reescreve o arquivo)."""
    arq.write_bytes(conteudo if isinstance(conteudo, bytes) else conteudo.encode("utf-8"))
    st = arq.stat()
    os.utime(arq, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_duas_fotos_seguidas_sao_iguais_e_so_os_pequenos_criticos_levam_hash(tmp_path):
    csgo, ud = _jogo(tmp_path)
    f = sl.tirar_foto(csgo, ud)
    assert f.arquivos["csgo/gameinfo.gi"].sha256 and f.arquivos["csgo/cfg/autoexec.cfg"].sha256 and f.arquivos["csgo/steam.inf"].sha256
    assert f.arquivos["csgo/pak01_000.vpk"].sha256 is None                       # grande e não crítico: só tamanho e data
    assert f.arquivos["userdata/local/cfg/cs2_video.txt"].sha256
    d = sl.comparar(f, sl.tirar_foto(csgo, ud))
    assert d.ok and not d.avisos and "Nenhum arquivo" in d.texto()


def test_cfg_do_jogo_mudado_e_alerta_vermelho_e_o_controle_sem_mudanca_nao(tmp_path):
    csgo, ud = _jogo(tmp_path)
    antes = sl.tirar_foto(csgo, ud)
    assert sl.comparar(antes, sl.tirar_foto(csgo, ud)).ok                       # CONTROLE
    _toca(csgo / "cfg" / "autoexec.cfg", "fps_max 999\n")
    d = sl.comparar(antes, sl.tirar_foto(csgo, ud))
    assert not d.ok and any("autoexec.cfg" in x and "conteúdo mudou" in x for x in d.vermelho)
    assert "ALERTA" in d.texto()


def test_so_a_data_mudou_com_o_mesmo_conteudo_nao_e_alerta(tmp_path):
    csgo, ud = _jogo(tmp_path)
    antes = sl.tirar_foto(csgo, ud)
    st = (csgo / "cfg" / "autoexec.cfg").stat()
    os.utime(csgo / "cfg" / "autoexec.cfg", ns=(st.st_atime_ns, st.st_mtime_ns + 9_000_000_000))
    assert sl.comparar(antes, sl.tirar_foto(csgo, ud)).ok


def test_video_txt_da_conta_principal_mudado_e_arquivo_novo_sao_vermelhos(tmp_path):
    """O caso que o dono temia: o CS2 reescreve cs2_video.txt (visto no teste de outro projeto). E arquivo NOVO em game\\csgo (ex.: um plugin)."""
    csgo, ud = _jogo(tmp_path)
    antes = sl.tirar_foto(csgo, ud)
    _toca(ud / "local" / "cfg" / "cs2_video.txt", "setting.fullscreen 0\n")
    (csgo / "csdm").mkdir()
    (csgo / "csdm" / "server.dll").write_bytes(b"MZ")
    d = sl.comparar(antes, sl.tirar_foto(csgo, ud))
    assert any("cs2_video.txt" in x for x in d.vermelho)
    assert any("NOVO" in x and "csdm" in x for x in d.vermelho)
    (ud / "local" / "cfg" / "cs2_video.txt").unlink()
    d2 = sl.comparar(antes, sl.tirar_foto(csgo, ud))
    assert any("SUMIU" in x and "cs2_video.txt" in x for x in d2.vermelho)


def test_o_que_o_jogo_escreve_no_uso_normal_nao_e_alerta_mas_aparece_nos_ignorados(tmp_path):
    csgo, ud = _jogo(tmp_path)
    antes = sl.tirar_foto(csgo, ud)
    _toca(csgo / "console.log", "mais log\n")
    (csgo / "backup_round01.txt").write_text("x", encoding="utf-8")
    (csgo / "replays" / "match730_2.dem").write_bytes(b"OUTRA")
    (csgo / "replays" / "match730_2.dem.info").write_bytes(b"i")
    (csgo / "logs").mkdir()
    (csgo / "logs" / "a.txt").write_text("x", encoding="utf-8")
    _toca(ud / "socache.dt", b"CACHE")
    d = sl.comparar(antes, sl.tirar_foto(csgo, ud))
    assert d.ok and len(d.ignorados) >= 5 and any("socache.dt" in x for x in d.avisos)
    # CONTROLE: um .vpk grande que muda de tamanho NÃO é ignorado (seria atualização ou escrita indevida)
    (csgo / "pak01_000.vpk").write_bytes(b"P" * 5001)
    assert not sl.comparar(antes, sl.tirar_foto(csgo, ud)).ok


def test_pasta_da_conta_principal_ausente_e_registrada_e_nao_quebra(tmp_path):
    csgo, _ = _jogo(tmp_path)
    f = sl.tirar_foto(csgo, None)
    assert f.ausentes == ["userdata"] and any(k.startswith("csgo/") for k in f.arquivos)


# ── conta logada: o bloqueio duro ─────────────────────────────────────────────────────────────────

def test_ids_da_conta_batem_com_os_da_pasta_userdata_real_do_dono():
    assert sl.accountid_do_steamid64(PRINCIPAL) == ID_PRINCIPAL and sl.accountid_do_steamid64(SEGUNDA) == ID_SEGUNDA
    assert sl.steamid64_da_conta(ID_SEGUNDA) == int(SEGUNDA) and sl.steamid64_da_conta(0) == 0


def test_mensagens_de_conta_sem_nome_de_login_nem_arquivo_e_com_a_marca_de_configuracao_errada():
    """C1 (14/11): as frases falam da "conta do Bisturi" e mandam para Configurar (nada de editar arquivo); só os erros de configuração levam a marca."""
    casos = [("", PRINCIPAL, ID_SEGUNDA, True), (PRINCIPAL, PRINCIPAL, ID_PRINCIPAL, True), (SEGUNDA, PRINCIPAL, 0, False),
             (SEGUNDA, PRINCIPAL, ID_PRINCIPAL, False), (SEGUNDA, PRINCIPAL, 123456, False)]
    for segunda, principal, logada, configuracao in casos:
        with pytest.raises(sl.ContaErrada) as e:
            sl.exigir_conta_do_bisturi(segunda, principal, logada=logada)
        texto = str(e.value)
        assert e.value.de_configuracao is configuracao
        assert "config.toml" not in texto and "gravacao_demo" not in texto and "2ª conta" not in texto
        assert "conta do Bisturi" in texto
    with pytest.raises(sl.ContaErrada, match="^Falta dizer qual"):                   # o JS e o passo "Primeiros passos" reconhecem esse começo
        sl.exigir_conta_do_bisturi("", "", logada=ID_SEGUNDA)


def test_a_conta_principal_logada_e_bloqueada_e_a_segunda_passa():
    assert sl.exigir_conta_do_bisturi(SEGUNDA, PRINCIPAL, logada=ID_SEGUNDA) == ID_SEGUNDA                  # CONTROLE: a 2ª conta passa
    with pytest.raises(sl.ContaErrada, match="PRINCIPAL"):
        sl.exigir_conta_do_bisturi(SEGUNDA, PRINCIPAL, logada=ID_PRINCIPAL)
    with pytest.raises(sl.ContaErrada, match="fechado"):
        sl.exigir_conta_do_bisturi(SEGUNDA, PRINCIPAL, logada=0)
    with pytest.raises(sl.ContaErrada, match="não é a conta do Bisturi"):
        sl.exigir_conta_do_bisturi(SEGUNDA, PRINCIPAL, logada=123456)
    with pytest.raises(sl.ContaErrada, match="Falta dizer"):
        sl.exigir_conta_do_bisturi("", PRINCIPAL, logada=ID_SEGUNDA)
    with pytest.raises(sl.ContaErrada, match="igual à sua conta principal"):
        sl.exigir_conta_do_bisturi(PRINCIPAL, PRINCIPAL, logada=ID_PRINCIPAL)
    # sem a principal configurada o bloqueio ainda vale para qualquer conta que não seja a 2ª
    with pytest.raises(sl.ContaErrada):
        sl.exigir_conta_do_bisturi(SEGUNDA, "", logada=ID_PRINCIPAL)


def test_conta_logada_vem_do_registro_e_steam_fechado_da_zero(monkeypatch):
    valores = {("Software\\Valve\\Steam\\ActiveProcess", "ActiveUser"): ID_SEGUNDA, ("Software\\Valve\\Steam\\ActiveProcess", "pid"): 4242,
               ("Software\\Valve\\Steam", "SteamPath"): "c:/program files (x86)/steam"}
    monkeypatch.setattr(sl, "_ler_registro", lambda sub, nome: valores.get((sub, nome)))
    assert sl.conta_logada() == ID_SEGUNDA
    monkeypatch.setattr(sl, "_pid_vivo", lambda pid: pid == 4242)
    assert sl.steam_rodando() is True
    monkeypatch.setattr(sl, "_pid_vivo", lambda pid: False)
    assert sl.steam_rodando() is False
    monkeypatch.setattr(sl, "_ler_registro", lambda sub, nome: None)             # CONTROLE: sem a chave
    assert sl.conta_logada() == 0 and sl.steam_rodando() is False


# ── achar o CS2 e a versão ───────────────────────────────────────────────────────────────────────

def test_acha_o_cs2_pela_biblioteca_e_le_a_versao(tmp_path, monkeypatch):
    csgo, _ = _jogo(tmp_path)
    steam = tmp_path / "Steam"
    (steam / "steamapps" / "libraryfolders.vdf").write_text('"libraryfolders" { "0" { "path" "' + str(steam).replace("\\", "\\\\") + '" } }',
                                                            encoding="utf-8")
    exe = steam / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "bin" / "win64" / "cs2.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    assert sl.pasta_cs2(steam) == steam / "steamapps" / "common" / "Counter-Strike Global Offensive"
    assert sl.cs2_exe(steam) == exe
    assert sl.versao_cs2(sl.pasta_cs2(steam))["PatchVersion"] == "1.41.9.6"
    assert sl.pasta_userdata_cs2(ID_PRINCIPAL, steam) == steam / "userdata" / str(ID_PRINCIPAL) / "730"
    assert sl.pasta_userdata_cs2(ID_SEGUNDA, steam) is None                                   # essa conta não tem pasta aqui
    assert sl.pasta_cs2(tmp_path / "vazio") is None and sl.versao_cs2(tmp_path / "vazio") == {}


@pytest.mark.skipif(sl.pasta_cs2() is None, reason="CS2 não está nesta máquina")
def test_leitura_real_somente_leitura_acha_cs2_e_foto_estavel():
    """[medido neste PC] só leitura: nada é escrito. Duas fotos seguidas do jogo e da pasta da conta principal são iguais."""
    csgo = sl.pasta_cs2() / "game" / "csgo"
    assert sl.cs2_exe() is not None and sl.versao_cs2().get("PatchVersion")
    userdata = sl.pasta_userdata_cs2(ID_PRINCIPAL)
    f1 = sl.tirar_foto(csgo, userdata)
    assert len(f1) > 100 and any(k.endswith("gameinfo.gi") for k in f1.arquivos)
    assert sl.comparar(f1, sl.tirar_foto(csgo, userdata)).ok
