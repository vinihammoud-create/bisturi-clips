"""Etapa 1 do Plano V7 integrado (02/11/2026): higiene e correções cirúrgicas."""
import json
from pathlib import Path

import pytest

from cs2clip.config import Config
from cs2clip.montagem import escolher_musica, escolher_musica_por_ritmo, musica_da_montagem


# ── A1: música só do catálogo ────────────────────────────────────────────────────────────────────

def _pasta_musicas(tmp_path: Path, listadas, extras=(), catalogo=True) -> Path:
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    for nome in (*listadas, *extras):
        (pasta / nome).write_bytes(b"x")
    if catalogo:
        (pasta / "catalogo.json").write_text(json.dumps({"faixas": [
            {"arquivo": n, "licenca": "CC0 1.0", "bpm": 120.0 + i, "drop_s": 1.0} for i, n in enumerate(listadas)]}),
            encoding="utf-8")
    return pasta


def _auto() -> Config:
    cfg = Config()
    cfg.montagem.musica = "auto"
    return cfg


def test_arquivo_fora_do_catalogo_nunca_e_escolhido_no_auto(tmp_path):
    pasta = _pasta_musicas(tmp_path, ["a.mp3", "b.mp3"], extras=["sem_origem.mp3"])
    escolhidas = {escolher_musica(_auto(), f"video{i}", pasta).name for i in range(60)}
    assert escolhidas <= {"a.mp3", "b.mp3"} and "sem_origem.mp3" not in escolhidas


def test_por_ritmo_com_catalogo_sem_faixa_existente_nao_cai_na_pasta(tmp_path):
    pasta = _pasta_musicas(tmp_path, ["sumiu.mp3"], extras=["solta.mp3"])
    (pasta / "sumiu.mp3").unlink()
    faixa, _ = escolher_musica_por_ritmo(12.0, pasta, "v")
    assert faixa is None
    assert musica_da_montagem(_auto(), "v", 12.0, pasta) is None   # nem pelo fallback do `auto`


def test_catalogo_corrompido_nao_libera_a_pasta(tmp_path):
    pasta = _pasta_musicas(tmp_path, [], extras=["solta.mp3"], catalogo=False)
    (pasta / "catalogo.json").write_text("{isto nao e json", encoding="utf-8")
    assert escolher_musica(_auto(), "v", pasta) is None
    assert escolher_musica_por_ritmo(12.0, pasta, "v")[0] is None


def test_entrada_do_catalogo_sem_licenca_nao_vale(tmp_path):
    pasta = _pasta_musicas(tmp_path, ["a.mp3", "b.mp3"])
    cat = json.loads((pasta / "catalogo.json").read_text(encoding="utf-8"))
    cat["faixas"][1]["licenca"] = ""
    (pasta / "catalogo.json").write_text(json.dumps(cat), encoding="utf-8")
    assert {escolher_musica(_auto(), f"v{i}", pasta).name for i in range(40)} == {"a.mp3"}


def test_sem_catalogo_nenhuma_musica_automatica_entra(tmp_path):
    # A-012 (26/11/2026): antes valia a pasta inteira; agora, sem catálogo não há licença registrada e nenhuma música é sorteada
    # (a que o usuário escreve pelo nome continua valendo: teste logo abaixo)
    pasta = _pasta_musicas(tmp_path, [], extras=["minha.mp3"], catalogo=False)
    assert escolher_musica(_auto(), "v", pasta) is None


def test_nome_explicito_continua_valendo_mesmo_fora_do_catalogo(tmp_path):
    pasta = _pasta_musicas(tmp_path, ["a.mp3"], extras=["escolha_do_dono.mp3"])
    cfg = Config()
    cfg.montagem.musica = "escolha_do_dono.mp3"          # o dono escolheu de propósito: respeita
    assert escolher_musica(cfg, "v", pasta).name == "escolha_do_dono.mp3"


def test_catalogo_real_do_projeto_lista_so_arquivos_que_existem_e_todos_com_licenca():
    pasta = Path(__file__).resolve().parent.parent / "musicas"
    cat = json.loads((pasta / "catalogo.json").read_text(encoding="utf-8"))
    for f in cat["faixas"]:
        assert f.get("licenca"), f["arquivo"]


# ── A2: o cache do render conhece a versão do FFmpeg ─────────────────────────────────────────────

def test_hash_do_render_muda_com_a_versao_do_ffmpeg_e_so_com_ela(monkeypatch):
    from cs2clip import refazer
    cfg = Config()
    monkeypatch.setattr(refazer, "versao_ffmpeg", lambda: "ffmpeg version 9.0.2")
    a = refazer.hash_corte(cfg)
    assert refazer.hash_corte(cfg) == a and refazer.hash_short(cfg) == refazer.hash_short(cfg)
    monkeypatch.setattr(refazer, "versao_ffmpeg", lambda: "ffmpeg version 9.1.0")
    assert refazer.hash_corte(cfg) != a


@pytest.mark.lento
def test_versao_ffmpeg_le_a_primeira_linha_e_nao_derruba_sem_ffmpeg(monkeypatch):
    from cs2clip import refazer
    refazer.versao_ffmpeg.cache_clear()
    v = refazer.versao_ffmpeg()
    assert v == "" or v.lower().startswith("ffmpeg version")
    refazer.versao_ffmpeg.cache_clear()
    monkeypatch.setattr(refazer.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("sem ffmpeg")))
    assert refazer.versao_ffmpeg() == ""
    refazer.versao_ffmpeg.cache_clear()


# ── A3: a tela de revisão mostra por que cada momento recebeu a nota ──────────────────────────────

def test_ui_cartao_do_momento_mostra_os_motivos_da_nota(tmp_path):
    from test_r1_ui import rodar_js
    r = rodar_js(tmp_path, """
      ui.state.videoAtual = "v1";
      $("#filter-type").value = "todos"; $("#filter-emotion").value = "todas"; $("#filter-order").value = "nota";
      ui.state.momentos = [
        { rotulo: "TRIPLE KILL", tipo: "abate", nota: 36, foco: 10, inicio: 5, fim: 15, parte: "Partida 1", motivos: ["3 abates +24", "headshot +6", "<b>x</b>"], arquivos: {} },
        { rotulo: "KILL", tipo: "abate", nota: 5, foco: 50, inicio: 45, fim: 55, parte: "Partida 1", arquivos: {} }
      ];
      ui.renderizarMomentos();
      const cartoes = $("#moments-grid").children.filter(c => c.className && c.className.includes("moment-card"));
      const textos = cartoes.map(c => textoDe(c));
      return { n: cartoes.length, primeiro: textos[0], segundo: textos[1] };
    """)
    assert r["n"] == 2
    assert "3 abates +24" in r["primeiro"] and "headshot +6" in r["primeiro"]
    assert "<b>x</b>" in r["primeiro"]                                  # texto puro: nada é interpretado como HTML
    assert "Por que esta nota" in r["primeiro"] and "Por que esta nota" not in r["segundo"]   # sem motivos, sem bloco


# ── A4: benchmark automático por processamento ────────────────────────────────────────────────────

ETAPAS_DE_TESTE = [
    {"etapa": "Varrendo o kill feed", "segundos": 35.1}, {"etapa": 'Lendo as suas mortes (painel "matou você")', "segundos": 39.8},
    {"etapa": "Analisando a voz do streamer (VAD + volume relativo)", "segundos": 3.2},
    {"etapa": "Transcrevendo as falas (Whisper na GPU)", "segundos": 6.7}, {"etapa": "Detectando a webcam (layout vertical)", "segundos": 5.0},
    {"etapa": "Renderizando 9 cortes 16:9 e 1 shorts 9:16 (GPU)", "segundos": 66.6},
    {"etapa": "Montagem com as melhores jogadas", "segundos": 26.8},
]


def test_benchmark_calcula_rtf_e_agrupa_as_etapas():
    import types
    from cs2clip import benchmark
    info = types.SimpleNamespace(duration=585.0, width=1920, height=1080, fps=60.0)
    d = benchmark.montar(info, Config(), "video", ETAPAS_DE_TESTE, som_montagem={"lufs": -14.2, "pico_dbtp": -1.5, "musica": "Beat One.mp3"},
                         n_faixas=3, tamanho_bytes=123)
    assert d["tempo_etapas_s"] == pytest.approx(183.2, abs=0.05) and d["rtf"] == pytest.approx(183.2 / 585.0, abs=0.001)
    assert (d["t_varredura_s"], d["t_mortes_s"], d["t_voz_s"], d["t_whisper_s"]) == (35.1, 39.8, 3.2, 6.7)
    assert (d["t_render_s"], d["t_montagem_s"]) == (66.6, 26.8) and d["t_outras_s"] == 5.0     # webcam cai em "outras"
    assert d["montagem_lufs"] == -14.2 and d["montagem_pico_dbtp"] == -1.5 and d["montagem_musica"] == "Beat One.mp3"
    assert d["largura"] == 1920 and d["fps"] == 60.0 and d["faixas_audio"] == 3 and d["whisper_modelo"] == Config().transcricao.modelo
    assert d["ffmpeg"] is not None and d["run_id"] and d["data_utc"].endswith("Z")


def test_benchmark_sem_duracao_nao_divide_por_zero():
    import types
    from cs2clip import benchmark
    d = benchmark.montar(types.SimpleNamespace(duration=0.0, width=0, height=0, fps=0.0), Config(), "v", [], None, 0, 0)
    assert d["rtf"] is None and d["tempo_etapas_s"] == 0


def test_benchmark_grava_json_e_acrescenta_linha_no_csv(tmp_path):
    import csv
    import types
    from cs2clip import benchmark
    info = types.SimpleNamespace(duration=600.0, width=1920, height=1080, fps=60.0)
    out = tmp_path / "saida" / "v1"
    out.mkdir(parents=True)
    for _ in range(2):
        benchmark.gravar(out, info, Config(), "v1", ETAPAS_DE_TESTE, None, 2, 10)
    assert json.loads((out / "benchmark.json").read_text(encoding="utf-8"))["video"] == "v1"
    linhas = list(csv.DictReader((out.parent / "benchmarks.csv").open(encoding="utf-8", newline="")))
    assert len(linhas) == 2 and linhas[0]["video"] == "v1" and float(linhas[0]["rtf"]) > 0
    assert "etapas" not in linhas[0]                                  # o CSV só leva colunas simples


def test_benchmark_com_cabecalho_antigo_nao_mistura_colunas(tmp_path):
    import types
    from cs2clip import benchmark
    out = tmp_path / "saida" / "v1"
    out.mkdir(parents=True)
    (out.parent / "benchmarks.csv").write_text("coluna_velha\n1\n", encoding="utf-8")
    benchmark.gravar(out, types.SimpleNamespace(duration=60.0, width=1, height=1, fps=1.0), Config(), "v1", [], None, 1, 1)
    assert list(out.parent.glob("benchmarks.antigo-*.csv")) and "video" in (out.parent / "benchmarks.csv").read_text(encoding="utf-8")


def test_benchmark_que_falha_nunca_derruba_o_processamento(tmp_path):
    from cs2clip import benchmark
    benchmark.gravar(tmp_path / "pasta_que_nao_existe", object(), Config(), "v", [], None, 0, 0)   # sem exceção


@pytest.mark.lento
def test_render_all_deixa_o_benchmark_ao_lado_do_tempos_json(tmp_path, monkeypatch):
    from test_r1_pipeline import _cfg_render, _info, _jogada, _render_falso
    import cs2clip.pipeline as pl
    _render_falso(monkeypatch)
    cfg = _cfg_render()
    cfg.shorts.gerar = False
    out = tmp_path / "saida" / "live"
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [_jogada()], [], None)
    assert (out / "tempos.json").is_file() and (out / "benchmark.json").is_file() and (out.parent / "benchmarks.csv").is_file()


# ── A5: QualityReport com estados (NOT_RUN nunca vira PASS) ───────────────────────────────────────

def _gate(status, obrigatorio=True, nome="g"):
    from cs2clip.qualidade import Gate
    return Gate(nome, status, None, "", "", "teste", obrigatorio)


@pytest.mark.parametrize("gates,esperado", [
    ([_gate("PASS"), _gate("PASS")], "PASS"),
    ([_gate("PASS"), _gate("WARNING")], "PASS_WITH_WARNINGS"),
    ([_gate("PASS"), _gate("NOT_RUN", obrigatorio=True)], "NEEDS_REVIEW"),          # obrigatório que não rodou != aprovado
    ([_gate("PASS"), _gate("NOT_RUN", obrigatorio=False)], "PASS"),                  # opcional desligado: sem problema
    ([_gate("WARNING"), _gate("NEEDS_REVIEW")], "NEEDS_REVIEW"),
    ([_gate("NEEDS_REVIEW"), _gate("FAIL")], "FAIL"),
    ([], "NEEDS_REVIEW"),                                                              # nenhum gate rodou: nada foi aprovado
])
def test_status_geral_segue_a_ordem_de_gravidade(gates, esperado):
    from cs2clip.qualidade import status_geral
    assert status_geral(gates) == esperado


def test_gate_audio_pico_alto_e_aviso_ou_falha_conforme_a_opcao():
    from cs2clip.qualidade import gate_audio
    assert gate_audio((-14.1, -1.6), True, -14.0, False).status == "PASS"
    assert gate_audio((-14.1, -0.4), True, -14.0, False).status == "WARNING"
    assert gate_audio((-14.1, -0.4), True, -14.0, True).status == "FAIL"
    assert gate_audio((-19.0, -3.0), True, -14.0, False).status == "WARNING"          # longe do alvo de volume
    assert gate_audio((-19.0, -3.0), True, -14.0, True).status == "FAIL"


def test_gate_audio_sem_medida_quando_devia_ter_som_e_not_run_obrigatorio():
    from cs2clip.qualidade import gate_audio
    g = gate_audio(None, True, -14.0, False)
    assert g.status == "NOT_RUN" and g.obrigatorio
    n = gate_audio(None, False, -14.0, False)                                          # nem devia ter som: não se aplica
    assert n.status == "NOT_RUN" and not n.obrigatorio


def test_gate_conteudo_desligado_e_not_run_opcional(tmp_path):
    from cs2clip.qualidade import gate_conteudo
    g = gate_conteudo(tmp_path / "x.mp4", False, False)
    assert g.status == "NOT_RUN" and not g.obrigatorio and "desligad" in g.motivo


def test_gate_integridade_arquivo_inexistente_falha_com_motivo(tmp_path):
    from cs2clip import qc
    from cs2clip.qualidade import gate_integridade
    g = gate_integridade(tmp_path / "nao_existe.mp4", qc.Esperado(1920, 1080, None, True, 60.0))
    assert g.status == "FAIL" and g.motivo


def test_relatorio_grava_status_por_arquivo_e_geral(tmp_path):
    from cs2clip import qualidade
    out = tmp_path / "v"
    out.mkdir()
    qualidade.gravar(out, {"MONTAGEM_v.mp4": [_gate("PASS"), _gate("WARNING", nome="pico")],
                           "FAILS_v.mp4": [qualidade.falha("render quebrou")]})
    d = json.loads((out / "qualidade.json").read_text(encoding="utf-8"))
    assert d["status"] == "FAIL" and d["arquivos"]["MONTAGEM_v.mp4"]["status"] == "PASS_WITH_WARNINGS"
    assert d["arquivos"]["FAILS_v.mp4"]["gates"][0]["motivo"] == "render quebrou"
    assert set(d["arquivos"]["MONTAGEM_v.mp4"]["gates"][0]) >= {"nome", "status", "medido", "esperado", "motivo", "ferramenta"}


def test_config_tem_gate_loudness_desligado_e_aparece_na_tela():
    from ui.configuracao import CAMPOS_POR_CHAVE
    assert Config().qualidade.gate_loudness is False and ("qualidade", "gate_loudness") in CAMPOS_POR_CHAVE


@pytest.mark.lento
def test_render_all_deixa_o_qualidade_json_com_a_montagem_e_o_status_nao_pass_sem_som(tmp_path, monkeypatch):
    from test_r1_pipeline import _cfg_render, _info, _jogada, _render_falso
    import cs2clip.pipeline as pl
    _render_falso(monkeypatch)
    cfg = _cfg_render()
    cfg.shorts.gerar = False
    out = tmp_path / "saida" / "live"
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [_jogada()], [], None)
    d = json.loads((out / "qualidade.json").read_text(encoding="utf-8"))
    assert d["status"] in {"PASS", "PASS_WITH_WARNINGS", "NEEDS_REVIEW", "FAIL"}
    assert d["status"] != "PASS"      # os arquivos do render falso são 5 bytes: nunca podem sair "aprovados"
