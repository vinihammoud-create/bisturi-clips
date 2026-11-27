"""A4 (dono, 08/11/2026): resenha = 2+ sinais juntos (Discord alto, várias vozes, você gritando, risadas). Um sinal só é reação comum.

Cada teste tem CONTROLE: com um sinal só (o que o programa fazia: tratar tudo como reação) não aparece resenha.
"""
import numpy as np
import pytest

from cs2clip import pipeline as pl
from cs2clip import resenha as rs
from cs2clip.audio import HOP, SR
from cs2clip.config import Config
from cs2clip.moments import Event, Moment
from cs2clip.transcribe import Word

from test_r1_pipeline import _cfg_render, _info, _render_falso

DUR = 200.0


def _audio_discord(rajadas, dur=DUR, sr=SR):
    """Faixa do Discord sintética: chão de ruído baixo + rajadas de 'fala' (tons de 500-2000 Hz) com amplitude própria."""
    rng = np.random.default_rng(1)
    n = int(dur * sr)
    x = rng.normal(0, 0.0008, n).astype(np.float32)
    t = np.arange(n) / sr
    tom = sum(np.sin(2 * np.pi * f * t) for f in (520, 810, 1250, 1730)) / 4
    for a, b, amp in rajadas:
        i, j = int(a * sr), int(b * sr)
        x[i:j] += (amp * tom[i:j] * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t[i:j]))).astype(np.float32)
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


NORMAL = [(10, 20, 0.04), (40, 50, 0.04), (70, 80, 0.04), (130, 140, 0.04), (160, 170, 0.04)]
GRITARIA = (100, 104, 0.35)           # o Discord explode por 4 s


def _voz(picos, dur=DUR):
    v = np.zeros(int(dur / HOP), np.float32)
    for a, b in picos:
        v[int(a / HOP):int(b / HOP)] = 0.9
    return v


def test_trechos_e_fechamento_de_buracos():
    x = np.zeros(100, bool)
    x[10:25] = True
    x[40:42] = True                    # 0,2 s: curto demais para 1 s
    assert rs.trechos(x, 1.0) == [(1.0, 2.5)]
    y = np.zeros(100, bool)
    y[10:20] = True
    y[22:35] = True                    # buraco de 0,2 s no meio da fala
    assert rs.trechos(rs._fechar(y, 0.5), 2.0) == [(1.0, 3.5)]
    assert rs.trechos(y, 1.2) == [(2.2, 3.5)]            # controle: sem fechar o buraco, só o pedaço de 1,3 s passa de 1,2 s


def test_discord_alto_so_aparece_quando_ha_pico_de_volume():
    com_pico = rs.curva_do_discord(_audio_discord(NORMAL + [GRITARIA]), SR)
    assert com_pico is not None
    ativo, alto, _ = com_pico
    altos = rs.trechos(alto, rs.DISCORD_ALTO_MIN_S)
    assert len(altos) == 1 and 99.0 <= altos[0][0] <= 101.0 and 103.0 <= altos[0][1] <= 105.0
    # controle: Discord sempre no mesmo volume não tem "alto"
    plano = rs.curva_do_discord(_audio_discord(NORMAL), SR)
    assert plano is not None and rs.trechos(plano[1], rs.DISCORD_ALTO_MIN_S) == []
    # faixa muda: sem curva
    assert rs.curva_do_discord(np.zeros(SR * 30, np.int16), SR) is None


def test_gritaria_no_discord_mais_voce_gritando_vira_resenha():
    discord = rs.curva_do_discord(_audio_discord(NORMAL + [GRITARIA]), SR)
    novas, trocadas = rs.achar_resenhas(_voz([(101, 103)]), [], discord, [], DUR, [])
    assert len(novas) == 1 and trocadas == []
    m = novas[0]
    assert m.categoria == "resenha" and m.kind == "reacao" and m.label == "RESENHA"
    assert "Discord alto" in m.reasons[0] and "você gritando" in m.reasons[0]
    assert m.start < 101 and m.end > 103 and m.end - m.start <= rs.DURACAO_MAX and m.score >= 30


def test_um_sinal_so_nao_e_resenha_controle():
    discord = rs.curva_do_discord(_audio_discord(NORMAL + [GRITARIA]), SR)
    assert rs.achar_resenhas(None, [], discord, [], DUR, [])[0] == []                     # só o Discord alto
    assert rs.achar_resenhas(_voz([(101, 103)]), [], None, [], DUR, [])[0] == []           # só o seu grito (era a "reação" de antes)
    assert rs.achar_resenhas(_voz([(101, 103)]), [], discord, [], DUR, [], min_sinais=3)[0] == []   # exigindo 3, 2 não basta


def test_varias_vozes_e_risadas_contam_como_sinais():
    discord = rs.curva_do_discord(_audio_discord(NORMAL), SR)
    # você falando (VAD) em cima do Discord de 130 a 140: várias vozes ao mesmo tempo
    nomes = {s[0] for s in rs.sinais(None, [(131.0, 139.0)], discord, [])}
    assert nomes == {"varias_vozes"}
    # fora do Discord ativo, falar sozinho não conta
    assert rs.sinais(None, [(50.0 + 100, 56.0 + 100)], rs.curva_do_discord(_audio_discord([(10, 20, 0.04), (60, 70, 0.04)]), SR), []) == []
    risos = [Word(132.0, 132.6, "kkkkkk"), Word(134.0, 134.6, "kkkk"), Word(190.0, 190.5, "kkkk")]
    sinais = rs.sinais(None, [(131.0, 139.0)], discord, risos)
    assert {s[0] for s in sinais} == {"varias_vozes", "risadas"}                           # a risada isolada (190 s) não conta
    novas, _ = rs.achar_resenhas(None, [(131.0, 139.0)], discord, risos, DUR, [])
    assert len(novas) == 1 and "risadas" in novas[0].reasons[0]


def test_sem_discord_ainda_acha_grito_mais_risada():
    risos = [Word(101.0, 101.5, "kkkkk"), Word(103.0, 103.5, "hahaha")]
    novas, _ = rs.achar_resenhas(_voz([(100, 102)]), [], None, risos, DUR, [])
    assert len(novas) == 1 and "você gritando" in novas[0].reasons[0] and "risadas" in novas[0].reasons[0]


def test_resenha_substitui_reacao_comum_mas_nunca_pisa_em_jogada_com_abate():
    discord = rs.curva_do_discord(_audio_discord(NORMAL + [GRITARIA]), SR)
    reacao = Moment(98.0, 108.0, "reacao", score=15)
    novas, trocadas = rs.achar_resenhas(_voz([(101, 103)]), [], discord, [], DUR, [reacao])
    assert len(novas) == 1 and trocadas == [reacao]                       # a reação comum sai, a resenha entra no lugar
    jogada = Moment(95.0, 110.0, "abate", kills=[Event(101.0, "kill"), Event(103.0, "kill")], score=40)
    novas, trocadas = rs.achar_resenhas(_voz([(101, 103)]), [], discord, [], DUR, [jogada])
    assert novas == [] and trocadas == []                                 # grito em cima de abate é hype da jogada, não resenha


def test_aviso_quando_nao_ha_faixa_do_discord(tmp_path):
    curva, aviso = rs.carregar_discord(str(tmp_path / "x.mp4"), 0)
    assert curva is None and "faixa_comms" in aviso


def test_risada_dos_amigos_e_um_sinal_do_discord():
    risos_amigos = [Word(133.0, 133.6, "kkkkk")]
    discord = rs.curva_do_discord(_audio_discord(NORMAL), SR)
    sinais = rs.sinais(None, [(131.0, 139.0)], discord, [], risos_amigos)
    assert {s[0] for s in sinais} == {"varias_vozes", "risadas_amigos"}
    novas, _ = rs.achar_resenhas(None, [(131.0, 139.0)], discord, [], DUR, [], falas_amigos=risos_amigos)
    assert len(novas) == 1 and "risadas dos amigos" in novas[0].reasons[0]
    # CONTROLE: sem a risada dos amigos, as várias vozes sozinhas não bastam (padrão)
    assert rs.achar_resenhas(None, [(131.0, 139.0)], discord, [], DUR, [])[0] == []
    # e a risada dos amigos conta como Discord para o discord_basta
    assert len(rs.achar_resenhas(None, [], None, [], DUR, [], discord_basta=True, falas_amigos=risos_amigos)[0]) == 1


def test_trechos_para_transcrever_juntam_falas_vizinhas_ate_o_maximo():
    assert rs._trechos_para_transcrever([(10.0, 12.0), (12.5, 14.0), (20.0, 21.0)]) == [(9.7, 14.3), (19.7, 21.3)]
    longos = rs._trechos_para_transcrever([(float(i), i + 0.9) for i in range(0, 70)], maximo=30.0)
    assert all(b - a <= 30.0 for a, b in longos) and len(longos) >= 3
    unico = rs._trechos_para_transcrever([(0.0, 75.0)], maximo=30.0)                 # um trecho só, de 75 s
    assert all(b - a <= 30.0 + 1e-9 for a, b in unico) and unico[0][0] == 0.0 and abs(unico[-1][1] - 75.3) < 1e-9


def test_transcrever_so_os_trechos_com_fala():
    pcm = np.zeros(SR * 60, np.int16)
    pedidos = []
    falas = rs.transcrever_trechos(pcm, SR, [(5.0, 8.0), (40.0, 40.2)],
                                   lambda audio, ini: pedidos.append((round(ini, 1), len(audio) / SR)) or [Word(ini, ini + 0.5, "kkkk")])
    assert [p[0] for p in pedidos] == [4.7, 39.7] and len(falas) == 2 and pedidos[0][1] == 3.6


def test_discord_mudo_usa_a_fala_da_faixa_do_jogo(monkeypatch):
    # 17/11/2026: nas gravações reais a faixa 4 (Discord) sai muda e a conversa dos amigos vai para a faixa 3 (jogo)
    from cs2clip import audio as aud
    from cs2clip import montagem
    carregadas = []

    def load_audio(src, faixa=None):
        carregadas.append(faixa)
        return np.zeros(SR * 120, np.int16) if faixa == 3 else np.ones(SR * 120, np.int16)
    monkeypatch.setattr(aud, "load_audio", load_audio)
    monkeypatch.setattr(montagem, "n_faixas_audio", lambda src: 4)
    monkeypatch.setattr(aud, "speech_segments", lambda pcm: [(10.0, 30.0), (50.0, 70.0)])
    curva, falas, aviso = rs.carregar_amigos("v.mp4", 4, 3, lambda a, i: [Word(i, i + 0.5, "kkk")], usar_faixa_do_jogo=True, faixa_voz=2)
    assert curva is not None and curva[0][int(15 / HOP)] and not curva[0][int(40 / HOP)] and not curva[1].any()
    assert len(falas) == 2 and "faixa 3 (jogo)" in aviso and "OBS" in aviso and carregadas[-1] == 2
    # sem transcrição: a curva continua, sem falas
    assert rs.carregar_amigos("v.mp4", 4, 3, None, usar_faixa_do_jogo=True, faixa_voz=2)[1] == []
    # CONTROLE (revisão de 17/11): a chave desligada (padrão) não usa a faixa do jogo; sem a faixa do Discord marcada também não
    assert rs.carregar_amigos("v.mp4", 4, 3, None)[0] is None
    assert rs.carregar_amigos("v.mp4", 0, 3, None, usar_faixa_do_jogo=True, faixa_voz=2)[0] is None
    # CONTROLE: pouca fala na faixa do jogo (só rádio do jogo) não vira "amigos falando"
    monkeypatch.setattr(aud, "speech_segments", lambda pcm: [(10.0, 15.0)])
    curva, falas, aviso = rs.carregar_amigos("v.mp4", 4, 3, None, usar_faixa_do_jogo=True, faixa_voz=2)
    assert curva is None and "muda" in aviso


def test_faixa_do_jogo_com_a_sua_voz_ou_sem_microfone_nao_vira_amigos(monkeypatch):
    # revisão de 17/11 (M4): faixa do jogo que é cópia da mistura (tem a SUA voz) faria qualquer grito virar "várias vozes"
    from cs2clip import audio as aud
    from cs2clip import montagem
    monkeypatch.setattr(aud, "load_audio", lambda src, faixa=None: np.zeros(SR * 120, np.int16) if faixa == 3 else np.ones(SR * 120, np.int16))
    monkeypatch.setattr(montagem, "n_faixas_audio", lambda src: 4)
    monkeypatch.setattr(aud, "speech_segments", lambda pcm: [(10.0, 30.0), (50.0, 70.0)])
    meu = [(12.0, 20.0), (55.0, 62.0)]                       # 15 s da sua fala, toda dentro da fala da faixa do jogo
    curva, _, aviso = rs.carregar_amigos("v.mp4", 4, 3, None, usar_faixa_do_jogo=True, faixa_voz=2, segs_mic=meu)
    assert curva is None and "sua voz" in aviso
    # CONTROLE: a sua fala fora da fala da faixa do jogo (os amigos falam em outros momentos) continua valendo
    curva, _, _ = rs.carregar_amigos("v.mp4", 4, 3, None, usar_faixa_do_jogo=True, faixa_voz=2, segs_mic=[(32.0, 45.0), (80.0, 90.0)])
    assert curva is not None
    # sem microfone configurado (faixa_voz 0): o "microfone" sairia da mistura, que já tem a faixa do jogo
    assert rs.carregar_amigos("v.mp4", 4, 3, None, usar_faixa_do_jogo=True, faixa_voz=0)[0] is None


def test_config_da_risada_dos_amigos_nasce_desligada():
    assert Config().resenha.risadas_amigos is False and Config().resenha.conversa_na_faixa_do_jogo is False


def test_pipeline_risada_dos_amigos_respeita_a_trava_da_gpu_e_solta_o_modelo(monkeypatch, tmp_path):
    import cs2clip.resenha as modulo
    import cs2clip.transcribe as tr
    chamadas, soltou = [], []
    monkeypatch.setattr(modulo, "carregar_amigos", lambda src, k, j, t, **kw: chamadas.append((t, kw)) or (None, [], ""))
    monkeypatch.setattr(modulo, "achar_resenhas", lambda *a, **k: ([], []))
    monkeypatch.setattr(pl, "liberar_modelo", lambda: soltou.append(1))
    monkeypatch.setattr(tr, "transcribe", lambda *a, **k: [])
    cfg = Config()
    cfg.resenha.risadas_amigos = True
    cfg.modo_previa = True                       # CS2/OBS abertos: nada de Whisper
    pl._com_resenhas(tmp_path / "v.mp4", cfg, [], [], np.zeros(10, np.float32), [], DUR)
    assert chamadas[-1][0] is None and soltou == [] and chamadas[-1][1]["usar_faixa_do_jogo"] is False
    cfg.modo_previa = False                      # CONTROLE: livre, transcreve e solta o modelo no fim
    cfg.resenha.conversa_na_faixa_do_jogo = True
    pl._com_resenhas(tmp_path / "v.mp4", cfg, [], [], np.zeros(10, np.float32), [], DUR)
    assert chamadas[-1][0] is not None and soltou == [1] and chamadas[-1][1]["usar_faixa_do_jogo"] is True and chamadas[-1][1]["faixa_voz"] == Config().audio.faixa_voz


# ── ligação com nota, short, montagem e a regra dos 3 abates ────────────────────────────────────

def _resenha(idx=1, t=60.0, nota=38.0):
    m = Moment(t - 5, t + 8, "reacao", label="RESENHA", score=nota, idx=idx, parte_tipo="partida", pico=t)
    m.categoria = "resenha"
    m.reasons = ["resenha: Discord alto + você gritando"]
    return m


def test_nota_da_resenha_nao_e_recalculada_como_reacao_comum():
    from cs2clip.nota import pontuar
    m = _resenha(nota=38.0)
    pontuar(m, [])
    assert m.score == 38.0                                           # controle abaixo: sem a categoria vira "reação" (5 pts)
    comum = Moment(55, 68, "reacao", score=38.0, parte_tipo="partida")
    pontuar(comum, [])
    assert comum.score < 10


def test_resenha_vale_destaque_e_reacao_comum_nao():
    assert _resenha().vale_destaque
    assert not Moment(0, 10, "reacao", score=40).vale_destaque
    assert Moment(0, 10, "reacao", score=40, emocao="HYPE").vale_destaque


def _roda(monkeypatch, tmp_path, jogadas, nome, **ajustes):
    _render_falso(monkeypatch)
    cfg = _cfg_render()
    cfg.cortes.min_abates_round = 3
    cfg.cortes.nota_short = 30.0
    for chave, valor in ajustes.items():
        setattr(cfg.montagem, chave, valor)
    out = tmp_path / "saida" / nome
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, jogadas, [], None)
    return out


@pytest.mark.lento
def test_resenha_vira_short_mesmo_com_a_regra_dos_3_abates_e_nao_entra_na_montagem_por_padrao(monkeypatch, tmp_path):
    out = _roda(monkeypatch, tmp_path, [_resenha()], "a")
    assert len(list((out / "shorts").glob("*_vertical.mp4"))) == 1       # o short da resenha existe
    assert not list(out.glob("MONTAGEM_*.mp4"))                          # a música cobriria as vozes: fora por padrão
    assert not (out / "NADA_PARA_POSTAR.txt").exists()                   # há o que postar
    # incluir_resenha liga a exceção da montagem
    com = _roda(monkeypatch, tmp_path, [_resenha()], "b", incluir_resenha=True)
    assert list(com.glob("MONTAGEM_*.mp4"))
    # CONTROLE: a mesma reação sem ser resenha (nem emoção) não vira short: era conversa
    comum = Moment(55, 68, "reacao", label="REAÇÃO", score=38.0, idx=1, parte_tipo="partida")
    sem = _roda(monkeypatch, tmp_path, [comum], "c")
    assert not list((sem / "shorts").glob("*_vertical.mp4"))


def test_config_da_resenha_nasce_ligada_e_valida_os_minimos(tmp_path):
    import pytest

    from cs2clip.config import load_config
    cfg = Config()
    assert cfg.resenha.gerar is True and cfg.resenha.min_sinais == 2 and cfg.montagem.incluir_resenha is False
    toml = tmp_path / "c.toml"
    toml.write_text("[resenha]\nmin_sinais = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="min_sinais"):
        load_config(toml)


def test_com_resenhas_do_pipeline_troca_reacao_comum_e_numera_de_novo(monkeypatch, tmp_path):
    reacao = Moment(98.0, 108.0, "reacao", score=15, idx=1)
    abate = Moment(10.0, 20.0, "abate", kills=[Event(14.0, "kill")], score=20, idx=2)
    nova = _resenha(t=102.0)
    import cs2clip.resenha as modulo
    monkeypatch.setattr(modulo, "carregar_amigos", lambda src, k, j, t, **kw: (None, [], "sem faixa"))
    monkeypatch.setattr(modulo, "achar_resenhas", lambda *a, **k: ([nova], [reacao]))
    saida = pl._com_resenhas(tmp_path / "v.mp4", Config(), [abate, reacao], [], np.zeros(10, np.float32), [], DUR)
    assert saida == [abate, nova] and [m.idx for m in saida] == [1, 2]
    # falha na análise nunca derruba: devolve os momentos como estavam
    monkeypatch.setattr(modulo, "achar_resenhas", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert pl._com_resenhas(tmp_path / "v.mp4", Config(), [abate, reacao], [], np.zeros(10, np.float32), [], DUR) == [abate, reacao]


def test_discord_basta_liga_o_discord_sozinho_mas_o_grito_sozinho_nunca_basta():
    discord = rs.curva_do_discord(_audio_discord(NORMAL + [GRITARIA]), SR)
    padrao, _ = rs.achar_resenhas(None, [], discord, [], DUR, [])
    assert padrao == []                                                                  # padrão: um sinal só = reação comum
    ligado, _ = rs.achar_resenhas(None, [], discord, [], DUR, [], discord_basta=True)
    assert len(ligado) == 1 and "Discord alto" in ligado[0].reasons[0]
    so_grito, _ = rs.achar_resenhas(_voz([(101, 103)]), [], None, [], DUR, [], discord_basta=True)
    assert so_grito == []                                                                # callout: nunca vira resenha sozinho
    from cs2clip.config import Config
    assert Config().resenha.discord_basta is False


def test_e4_volume_medio_em_blocos_da_o_mesmo_numero_e_nao_copia_a_faixa_inteira():
    import tracemalloc
    rng = np.random.default_rng(5)
    pequeno = (rng.normal(0, 0.05, 50_000) * 32767).astype(np.int16)
    antigo = 20 * np.log10(float(np.sqrt(np.mean((pequeno.astype(np.float32) / 32768.0) ** 2))) + 1e-9)    # fórmula da versão anterior
    assert abs(rs.nivel_medio_db(pequeno) - antigo) < 0.01
    assert rs.nivel_medio_db(np.zeros(100, np.int16)) < rs.MUDA_DB and rs.nivel_medio_db(np.zeros(0, np.int16)) == -120.0
    grande = np.ones(SR * 60 * 30, np.int16) * 100                      # 30 min de áudio (~58 MB em int16)
    tracemalloc.start()
    rs.nivel_medio_db(grande)
    _, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert pico < 0.15 * grande.nbytes                                   # a cópia inteira em float32 seria 2x o tamanho da faixa
    # CONTROLE: a fórmula antiga realmente gastava mais que o tamanho da faixa
    tracemalloc.start()
    float(np.sqrt(np.mean((grande.astype(np.float32) / 32768.0) ** 2)))
    _, pico_antigo = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert pico_antigo > grande.nbytes
