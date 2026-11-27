"""A6 (dono, 08/11/2026): música mais emocionante, escolhida pela energia e pelo drop MEDIDOS, e o 1º tiro da montagem no drop.

Cada teste tem CONTROLE: sem a medição (ou com `tiro_no_drop = false`) o comportamento antigo reaparece.
"""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from cs2clip import montagem
from cs2clip import musica_energia as me
from cs2clip.config import Config
from cs2clip.tools import FFMPEG

SR = 11025


def _sinal(trechos):
    """Áudio sintético: [(duração_s, amplitude)] de ruído branco com a amplitude dada."""
    rng = np.random.default_rng(3)
    return np.concatenate([rng.normal(0, amp, int(d * SR)) for d, amp in trechos]).astype(np.float32)


def test_drop_e_achado_onde_a_energia_salta_e_faixa_parelha_nao_tem_drop():
    a = me.analisar(_sinal([(20, 0.01), (30, 0.20)]), SR)             # calmo por 20 s, depois forte
    assert a["drop_s"] is not None and 19.0 <= a["drop_s"] <= 21.0
    assert a["impacto_db"] > 15 and a["energia"] > 0.5 and a["emocao"] > 0.6
    parelha = me.analisar(_sinal([(50, 0.10)]), SR)                    # controle: sempre no mesmo volume
    assert parelha["drop_s"] is None and parelha["impacto_db"] < me.IMPACTO_MIN_DB
    fraca = me.analisar(_sinal([(50, 0.003)]), SR)
    assert fraca["energia"] < 0.1 and fraca["emocao"] < a["emocao"]
    assert me.analisar(np.zeros(SR, np.float32), SR)["drop_s"] is None   # muda/curta: nada a medir, sem erro


def test_pular_a_musica_para_o_drop_cair_no_primeiro_tiro():
    batidas = np.arange(0.25, 90.0, 0.5)
    desloc, motivo = montagem.deslocamento_para_o_drop(30.1, batidas, 90.0, 4.0)
    assert motivo == "" and desloc == pytest.approx(30.25 - 4.0)          # a batida mais perto do drop vai para o tiro
    # nos casos em que não dá, a música toca desde o início e o motivo diz por quê
    assert montagem.deslocamento_para_o_drop(None, batidas, 90.0, 4.0)[0] == 0.0
    assert "não tem um drop" in montagem.deslocamento_para_o_drop(0.0, batidas, 90.0, 4.0)[1]
    desloc, motivo = montagem.deslocamento_para_o_drop(2.0, batidas, 90.0, 4.0)       # drop antes do tiro
    assert desloc == 0.0 and "antes do 1º tiro" in motivo
    assert montagem.deslocamento_para_o_drop(30.0, batidas, 90.0, None) == (0.0, "sem o instante do 1º tiro")
    assert montagem.deslocamento_para_o_drop(80.0, batidas, 90.0, 4.0)[0] == 0.0       # sobraria pouca música


def _catalogo(pasta: Path, faixas: list[dict]):
    pasta.mkdir(parents=True, exist_ok=True)
    for f in faixas:
        (pasta / f["arquivo"]).write_bytes(b"x" * 100)
        f.setdefault("licenca", "CC0")
        f.setdefault("duracao_s", 120.0)
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": faixas}), encoding="utf-8")


def test_escolha_automatica_prefere_a_faixa_mais_emocionante_medida(tmp_path):
    comum = {"arquivo": "calma.mp3", "bpm": 126.0}
    forte = {"arquivo": "forte.mp3", "bpm": 140.0, "emocao": 0.95, "drop_s": 40.0}
    _catalogo(tmp_path / "m", [dict(comum), dict(forte)])
    escolhida, drop = montagem.escolher_musica_por_ritmo(12.0, tmp_path / "m")
    assert escolhida.name == "forte.mp3" and drop == 40.0
    # CONTROLE: o catálogo antigo (sem `emocao`) escolhia só pelo BPM, e a "calma" ganhava
    sem_medida = dict(forte)
    sem_medida.pop("emocao")
    _catalogo(tmp_path / "antigo", [dict(comum), sem_medida])
    assert montagem.escolher_musica_por_ritmo(12.0, tmp_path / "antigo")[0].name == "calma.mp3"
    # faixa sem drop (drop_s nulo, como o medidor grava) não derruba a escolha
    sem_drop = {"arquivo": "parelha.mp3", "bpm": 126.0, "emocao": 0.9, "drop_s": None}
    _catalogo(tmp_path / "nulo", [sem_drop])
    assert montagem.escolher_musica_por_ritmo(12.0, tmp_path / "nulo") == (tmp_path / "nulo" / "parelha.mp3", 0.0)


def test_catalogo_real_tem_as_medidas_licenca_e_varias_faixas_com_drop():
    """Antes (08/11 manhã) só 1 faixa tinha drop forte; o Plano E (E7) trouxe mais: o catálogo precisa de variedade para o rodízio funcionar."""
    cat = json.loads((Path(__file__).resolve().parent.parent / "musicas" / "catalogo.json").read_text(encoding="utf-8"))
    faixas = cat["faixas"]
    assert all("energia" in f and "emocao" in f and "impacto_db" in f for f in faixas)
    assert all(str(f.get("licenca", "")).strip() for f in faixas)                      # nenhuma faixa sem licença registrada
    com_drop = [f for f in faixas if f.get("drop_s") not in (None, 0, 0.0)]
    assert len(com_drop) >= 12, len(com_drop)
    melhor = max(faixas, key=lambda f: f["emocao"])
    assert melhor["arquivo"] == "black-box-legendary-9509.mp3" and "Pixabay" in melhor["licenca"]     # o rótulo certo: Pixabay, não CC0
    assert not any(f.get("exige_credito") for f in faixas)                        # hoje o catálogo só tem faixas que não pedem crédito


def test_config_tiro_no_drop_nasce_ligado():
    assert Config().montagem.tiro_no_drop is True


# ── montagem de verdade (ffmpeg) ─────────────────────────────────────────────────────────────────

def _corte_mudo(dest: Path, dur: float = 4.0) -> Path:
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={dur}",
           "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={dur}", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={dur}",
           "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def _musica_com_drop(dest: Path, drop: float = 30.0, dur: float = 70.0) -> Path:
    """120 BPM: pulsos fracos até `drop` s e fortes depois."""
    t = np.arange(int(dur * SR)) / SR
    sinal = np.zeros_like(t, dtype=np.float32)
    for pulso in np.arange(0.25, dur, 0.5):
        amp = 0.04 if pulso < drop else 0.7
        env = np.exp(-np.maximum(0.0, t - pulso) * 35.0) * (t >= pulso)
        sinal += amp * np.sin(2 * np.pi * 110.0 * t) * env
    pcm = np.clip(sinal * 32767, -32768, 32767).astype(np.int16)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0", "-c:a", "pcm_s16le", str(dest)],
                   input=pcm.tobytes(), capture_output=True, check=True)
    return dest


def _rms_db(video: Path, a: float, b: float) -> float:
    r = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(video), "-vn", "-ac", "1",
                        "-ar", "16000", "-f", "s16le", "pipe:1"], capture_output=True, check=True)
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0
    return 20 * np.log10(float(np.sqrt(np.mean(x ** 2))) + 1e-9)


@pytest.mark.lento
def test_montagem_poe_o_primeiro_tiro_em_cima_do_drop_e_sem_a_opcao_nao(tmp_path):
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    musica = _musica_com_drop(pasta / "com_drop.wav", drop=30.0)
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": [{"arquivo": "com_drop.wav", "licenca": "CC0", "bpm": 120.0,
                                                                  "drop_s": 30.0}]}), encoding="utf-8")
    cortes = [_corte_mudo(tmp_path / "c1.mp4"), _corte_mudo(tmp_path / "c2.mp4")]

    def monta(nome, tiro_no_drop):
        cfg = Config()
        cfg.qualidade.encoder = "libx264"
        cfg.qualidade.garantir_pico = False
        cfg.montagem.tiro_no_drop = tiro_no_drop
        out = tmp_path / nome
        montagem.render_montagem(cortes, out, cfg, musica, tiros_rel=[2.0, 2.0])
        return out, dict(montagem.ULTIMO_RITMO)

    out, ritmo = monta("com.mp4", True)
    assert 27.0 <= ritmo["musica_pula_s"] <= 30.0                         # pulou o começo da música até chegar perto do drop
    assert abs(ritmo["primeiro_tiro_vs_drop_ms"]) <= 500                  # o 1º tiro ficou colado no drop (o kill-sync mexe até 0,45 s)
    tiro = 2.0 - 0.12
    antes, depois = _rms_db(out, 0.2, tiro - 0.3), _rms_db(out, tiro + 0.3, tiro + 1.8)
    assert depois - antes > 12                                            # o drop (som forte) começa onde está o tiro
    # CONTROLE: sem a opção a música toca desde o começo (fraca por 30 s): não há drop em volta do tiro
    out2, ritmo2 = monta("sem.mp4", False)
    assert ritmo2["musica_pula_s"] == 0.0 and ritmo2["primeiro_tiro_vs_drop_ms"] is None
    assert abs(_rms_db(out2, tiro + 0.3, tiro + 1.8) - _rms_db(out2, 0.2, tiro - 0.3)) < 4


@pytest.mark.lento
def test_e3_volume_da_musica_e_medido_no_trecho_que_toca(tmp_path, monkeypatch):
    """RV-04: com `tiro_no_drop` a música toca a partir de `musica_pula_s`; o ganho tem que sair desse trecho, não dos 180 s do começo
    (numa música de intro calma, o começo faria a música sair mais alta que o jogo)."""
    import cs2clip.render as render_mod
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    musica = _musica_com_drop(pasta / "com_drop.wav", drop=30.0)
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": [{"arquivo": "com_drop.wav", "licenca": "CC0", "bpm": 120.0,
                                                                  "drop_s": 30.0}]}), encoding="utf-8")
    cortes = [_corte_mudo(tmp_path / "c1.mp4"), _corte_mudo(tmp_path / "c2.mp4")]
    chamadas = []
    original = render_mod.measure_loudness

    def espia(src, start, dur, target):
        if str(src).endswith("com_drop.wav"):
            chamadas.append((start, dur))
        return original(src, start, dur, target)
    monkeypatch.setattr(render_mod, "measure_loudness", espia)

    def monta(nome, tiro_no_drop):
        chamadas.clear()
        cfg = Config()
        cfg.qualidade.encoder = "libx264"
        cfg.qualidade.garantir_pico = False
        cfg.montagem.tiro_no_drop = tiro_no_drop
        montagem.render_montagem(cortes, tmp_path / nome, cfg, musica, tiros_rel=[2.0, 2.0])
        return list(chamadas), dict(montagem.ULTIMO_RITMO)
    com, ritmo = monta("com.mp4", True)
    assert len(com) == 1 and com[0][0] == pytest.approx(ritmo["musica_pula_s"], abs=0.01) and com[0][0] > 20
    assert 10.0 <= com[0][1] <= 180.0
    sem, _ = monta("sem.mp4", False)                          # controle: sem o pulo, mede do começo (como antes)
    assert sem[0][0] == 0.0
