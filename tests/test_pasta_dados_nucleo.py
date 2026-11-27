"""P2.2 do plano de publicação (26/11/2026): o núcleo (`cs2clip/`) guarda os DADOS da pessoa em `PASTA_DADOS`, não na raiz do programa.

Três coisas, para cada módulo trocado (`baixar_demo`, `demos_cs2`, `steam_api`, `__main__`, `editar_demo`, `gravador_demo`, `config.nascer_do_exemplo`):
  (a) modo portátil (sem `BISTURI_DADOS`, como o dono roda): o caminho é EXATAMENTE o de hoje (`ROOT / "..."`);
  (b) modo instalado (`BISTURI_DADOS` numa pasta temporária, num processo à parte): o dado vai para a pasta de dados e nada nasce na pasta do programa
      (uma cópia pequena do programa, fotografada antes e depois);
  (c) o `config.toml` nasce do exemplo na pasta de dados mesmo com o exemplo na pasta do programa.
Tudo que depende do ambiente da importação roda num Python novo: o processo do pytest pode ter `BISTURI_DADOS` e os módulos já carregados.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cs2clip import caminhos, config
from cs2clip.tools import ROOT, sem_janela

RAIZ = Path(__file__).resolve().parents[1]

# Os valores de segredo são inventados e montados em pedaços (regra 14 do plano): o Gitleaks do envio barra o valor inteiro escrito.
CHAVE_FALSA = "0123456789ABCDEF" * 2
STEAMID_FALSO = "76561198" + "071376839"
AUTENTICACAO_FALSA = "AAAA" + "-" + "BBBBB" + "-" + "CCCC"
CODIGO_DE_PARTILHA = "CSGO-" + "-".join(["GADqf", "jjyJ8", "cSP2r", "smZRo", "TO2xK"])


def _rodar(codigo: str, cwd: Path, extra: dict[str, str] | None = None, args: tuple[str, ...] = (), prazo_s: float = 240) -> dict:
    """Roda `codigo` num Python novo em `cwd`, sem `BISTURI_DADOS` nem `HF_HOME` herdados (só os de `extra`). Devolve o JSON da última linha."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in {"BISTURI_DADOS", "HF_HOME"}}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
    env.update(extra or {})
    r = subprocess.run([sys.executable, "-c", codigo, *args], cwd=str(cwd), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=prazo_s, **sem_janela())
    assert r.returncode == 0, f"o processo saiu com {r.returncode}:\n{r.stderr[-4000:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def _programa_pequeno(destino: Path) -> Path:
    """Uma "pasta do programa" de verdade, como o `app\\` do instalador: o pacote `cs2clip` e o exemplo de configuração."""
    shutil.copytree(RAIZ / "cs2clip", destino / "cs2clip", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(RAIZ / "config.exemplo.toml", destino / "config.exemplo.toml")
    return destino


def _foto(raiz: Path) -> dict[str, tuple[int, int]]:
    """Nome, tamanho e data de tudo dentro de `raiz` (pastas incluídas: a data da pasta muda quando algo nasce ou some nela)."""
    foto: dict[str, tuple[int, int]] = {}
    for pasta, subpastas, arquivos in os.walk(raiz):
        subpastas[:] = [s for s in subpastas if s != "__pycache__"]
        base = Path(pasta)
        foto[str(base.relative_to(raiz)) + os.sep] = (0, base.stat().st_mtime_ns)
        for nome in arquivos:
            st = (base / nome).stat()
            foto[str((base / nome).relative_to(raiz))] = (st.st_size, st.st_mtime_ns)
    return foto


# Imprime, num JSON, onde cada módulo trocado guarda o dado e o que os três pontos de entrada usam como padrão (`--saida`, `--config`):
# o `parse_args` é trocado por um que só devolve os padrões, então nada do programa de verdade roda.
CODIGO_CAMINHOS = r"""
import argparse, json
from cs2clip import caminhos as cm
from cs2clip import baixar_demo, demos_cs2, editar_demo, gravador_demo, steam_api
import cs2clip.__main__ as principal

class Parou(Exception):
    pass

def padroes(modulo):
    original = argparse.ArgumentParser.parse_args
    def captura(self, args=None, namespace=None):
        raise Parou({a.dest: a.default for a in self._actions})
    argparse.ArgumentParser.parse_args = captura
    try:
        modulo.main([])
    except Parou as e:
        return e.args[0]
    finally:
        argparse.ArgumentParser.parse_args = original
    raise AssertionError("main() não chegou ao parse_args")

p_principal, p_editar, p_gravador = padroes(principal), padroes(editar_demo), padroes(gravador_demo)
print(json.dumps({
    "pasta_dados": str(cm.PASTA_DADOS), "instalado": cm.modo_instalado(),
    "constantes": {
        "baixar_demo.PASTA_DEMOS": str(baixar_demo.PASTA_DEMOS), "baixar_demo.CONFIG": str(baixar_demo.CONFIG),
        "demos_cs2.PASTA_DEMOS_PROJETO": str(demos_cs2.PASTA_DEMOS_PROJETO), "demos_cs2.PASTA_CACHE_VOD": str(demos_cs2.PASTA_CACHE_VOD),
        "steam_api.ARQUIVO": str(steam_api.ARQUIVO),
        "editar_demo.PASTA_GRAVACAO": str(editar_demo.PASTA_GRAVACAO), "editar_demo.PASTA_SAIDA": str(editar_demo.PASTA_SAIDA),
        "gravador_demo.PASTA_PADRAO": str(gravador_demo.PASTA_PADRAO),
        "__main__.PASTA_ENTRADA": str(principal.PASTA_ENTRADA), "__main__.PASTA_JOGADOR": str(principal.PASTA_JOGADOR)},
    "padroes": {"__main__.--saida": p_principal["saida"], "__main__.--config": p_principal["config"],
                "editar_demo.--config": p_editar["config"], "gravador_demo.--config": p_gravador["config"]}}))
"""


def _esperado(base: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Os caminhos que cada módulo tem de ter quando os dados moram em `base` (no portátil, `base` é a raiz do programa)."""
    constantes = {
        "baixar_demo.PASTA_DEMOS": base / "demos", "baixar_demo.CONFIG": base / "config.toml",
        "demos_cs2.PASTA_DEMOS_PROJETO": base / "demos", "demos_cs2.PASTA_CACHE_VOD": base / "dados" / "vod_info",
        "steam_api.ARQUIVO": base / "dados" / "segredos_steam.json",
        "editar_demo.PASTA_GRAVACAO": base / "gravacao_demo", "editar_demo.PASTA_SAIDA": base / "saida",
        "gravador_demo.PASTA_PADRAO": base / "gravacao_demo",
        "__main__.PASTA_ENTRADA": base / "entrada", "__main__.PASTA_JOGADOR": base / "dados" / "jogador"}
    padroes = {"__main__.--saida": base / "saida", "__main__.--config": base / "config.toml",
               "editar_demo.--config": base / "config.toml", "gravador_demo.--config": base / "config.toml"}
    return {k: str(v) for k, v in constantes.items()}, {k: str(v) for k, v in padroes.items()}


# ═══ (a) modo portátil: tudo igual a hoje ═════════════════════════════════════════════════════════════════════════════════════════

def test_modo_portatil_os_caminhos_do_nucleo_sao_exatamente_os_de_hoje():
    """Sem `BISTURI_DADOS` e sem `pasta_dados.txt` (como o dono roda), cada caminho é o `ROOT / "..."` de antes da P2.2."""
    assert not (RAIZ / "pasta_dados.txt").exists(), "há um pasta_dados.txt na raiz do projeto: o modo portátil não é o de hoje"
    info = _rodar(CODIGO_CAMINHOS, RAIZ)
    assert Path(info["pasta_dados"]) == ROOT and info["instalado"] is False
    constantes, padroes = _esperado(ROOT)
    assert info["constantes"] == constantes
    assert info["padroes"] == padroes


# ═══ (b) modo instalado: o dado vai para a pasta de dados, o programa não ganha nada ══════════════════════════════════════════════

def test_modo_instalado_os_caminhos_do_nucleo_ficam_na_pasta_de_dados_e_o_programa_nao_muda(tmp_path):
    programa = _programa_pequeno(tmp_path / "Programs" / "app")
    dados = (tmp_path / "Vídeos" / "Bisturi Clips").resolve()
    antes = _foto(programa)
    info = _rodar(CODIGO_CAMINHOS, programa, {"BISTURI_DADOS": str(dados)})
    constantes, padroes = _esperado(dados)
    assert info["instalado"] is True and Path(info["pasta_dados"]) == dados
    assert info["constantes"] == constantes
    assert info["padroes"] == padroes
    assert _foto(programa) == antes, "importar os módulos mexeu na pasta do programa"
    assert not dados.exists(), "importar os módulos criou a pasta de dados (só quem grava cria)"


# Uso de verdade no modo instalado: processar um "vídeo" da pasta entrada\ com os caminhos PADRÃO do CLI, copiar uma demo, gravar os segredos do Steam.
CODIGO_USO = r"""
import json, sys, types
from pathlib import Path

par = json.loads(sys.argv[1])
dados, fonte_dem = Path(par["dados"]).resolve(), Path(par["fonte_dem"])
import cs2clip.pipeline as pl
from cs2clip import caminhos as cm, demos_cs2, steam_api
from cs2clip.__main__ import main

(dados / "entrada").mkdir(parents=True)
(dados / "entrada" / "partida.mp4").write_bytes(b"x")
vistos = {}

def analise(p, cfg, o, pd, R, **extras):
    vistos.update(video=Path(p).resolve(), saida=Path(o).resolve(), jogador=Path(pd).resolve())
    pasta = Path(o) / Path(p).stem
    pasta.mkdir(parents=True, exist_ok=True)
    return types.SimpleNamespace(duration=10.0), Path(p).stem, pasta, [], [], None

pl.analyze = analise
pl.render_all = lambda *a, **k: ([], [], [])
rc = main(["--so-analise", "--forcar-gpu"])

copia = demos_cs2.copiar_para_projeto(fonte_dem)
segredos = steam_api.gravar_segredos(par["chave"], par["steamid"], par["autenticacao"], par["codigo"])
print(json.dumps({"rc": rc, "video": str(vistos.get("video")), "saida": str(vistos.get("saida")), "jogador": str(vistos.get("jogador")),
                  "copia": str(copia), "segredos": str(segredos), "hf_home": __import__("os").environ.get("HF_HOME"), "dados": str(cm.PASTA_DADOS)}))
"""


def test_modo_instalado_processar_copiar_demo_e_gravar_segredos_vao_para_a_pasta_de_dados(tmp_path):
    programa = _programa_pequeno(tmp_path / "Programs" / "app")
    dados = (tmp_path / "Vídeos" / "Bisturi Clips").resolve()
    fonte_dem = tmp_path / "replays" / "partida.dem"
    fonte_dem.parent.mkdir()
    fonte_dem.write_bytes(b"PBDEMS2\x00" + b"0" * 64)
    antes = _foto(programa)

    par = json.dumps({"dados": str(dados), "fonte_dem": str(fonte_dem), "chave": CHAVE_FALSA, "steamid": STEAMID_FALSO,
                      "autenticacao": AUTENTICACAO_FALSA, "codigo": CODIGO_DE_PARTILHA})
    r = _rodar(CODIGO_USO, programa, {"BISTURI_DADOS": str(dados)}, args=(par,), prazo_s=300)

    assert r["rc"] == 0
    assert Path(r["video"]) == dados / "entrada" / "partida.mp4"                       # a pasta entrada\ é a da pessoa
    assert Path(r["saida"]) == dados / "saida" and (dados / "saida" / "progresso.json").is_file()
    assert Path(r["jogador"]) == dados / "dados" / "jogador"
    assert Path(r["copia"]) == dados / "demos" / "partida.dem" and (dados / "demos" / "partida.dem").is_file()
    assert Path(r["segredos"]) == dados / "dados" / "segredos_steam.json" and (dados / "dados" / "segredos_steam.json").is_file()
    assert (dados / "config.toml").read_bytes() == (programa / "config.exemplo.toml").read_bytes()   # (c) nasceu do exemplo, na pasta de dados
    assert Path(r["hf_home"]) == dados / "modelos"
    assert _foto(programa) == antes, "a pasta do programa ganhou, perdeu ou mudou algo"


# ═══ (c) o config.toml nasce do exemplo na pasta de dados ═════════════════════════════════════════════════════════════════════════

@pytest.fixture
def duas_pastas(tmp_path, monkeypatch):
    """O programa (com o exemplo) e a pasta de dados (vazia) separados, como no modo instalado."""
    programa, dados = tmp_path / "app", tmp_path / "dados_da_pessoa"
    programa.mkdir()
    dados.mkdir()
    (programa / "config.exemplo.toml").write_text('[geral]\n# nasceu do exemplo do programa\n', encoding="utf-8")
    monkeypatch.setattr(config, "RAIZ_PROGRAMA", programa)
    monkeypatch.setattr(config, "PASTA_DADOS", dados)
    return programa, dados


def test_config_nasce_do_exemplo_do_programa_na_pasta_de_dados(duas_pastas):
    programa, dados = duas_pastas
    antes = _foto(programa)
    assert config.nascer_do_exemplo(dados / "config.toml") == dados / "config.toml"
    assert (dados / "config.toml").read_bytes() == (programa / "config.exemplo.toml").read_bytes()
    assert sorted(p.name for p in dados.iterdir()) == ["config.toml"]            # nenhum temporário sobrou
    assert _foto(programa) == antes and not (programa / "config.toml").exists()


def test_config_load_config_na_pasta_de_dados_le_o_que_nasceu_do_exemplo(duas_pastas):
    programa, dados = duas_pastas
    config.load_config(dados / "config.toml")
    assert (dados / "config.toml").is_file() and not (programa / "config.toml").exists()


def test_exemplo_ao_lado_do_config_vence_o_do_programa(duas_pastas):
    programa, dados = duas_pastas
    (dados / "config.exemplo.toml").write_text("# o exemplo ao lado\n", encoding="utf-8")
    config.nascer_do_exemplo(dados / "config.toml")
    assert (dados / "config.toml").read_text(encoding="utf-8") == "# o exemplo ao lado\n"


def test_config_que_ja_existe_nunca_e_tocado(duas_pastas):
    _, dados = duas_pastas
    (dados / "config.toml").write_text("# da pessoa\n", encoding="utf-8")
    config.nascer_do_exemplo(dados / "config.toml")
    assert (dados / "config.toml").read_text(encoding="utf-8") == "# da pessoa\n"


def test_config_fora_da_pasta_de_dados_nao_procura_o_exemplo_do_programa(duas_pastas, tmp_path):
    """Só o config da pasta de dados nasce do exemplo do programa: um `config.toml` de teste, numa pasta qualquer, segue sem exemplo (como sempre)."""
    outra = tmp_path / "qualquer"
    outra.mkdir()
    assert config.nascer_do_exemplo(outra / "config.toml") == outra / "config.toml"
    assert list(outra.iterdir()) == []


def test_sem_exemplo_em_lugar_nenhum_o_config_nao_nasce(duas_pastas):
    programa, dados = duas_pastas
    (programa / "config.exemplo.toml").unlink()
    assert config.nascer_do_exemplo(dados / "config.toml") == dados / "config.toml"
    assert not (dados / "config.toml").exists()


def test_no_modo_portatil_programa_e_dados_sao_a_mesma_pasta_e_o_exemplo_ao_lado_basta(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAIZ_PROGRAMA", tmp_path)
    monkeypatch.setattr(config, "PASTA_DADOS", tmp_path)
    (tmp_path / "config.exemplo.toml").write_text("# exemplo\n", encoding="utf-8")
    config.nascer_do_exemplo(tmp_path / "config.toml")
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == "# exemplo\n"


def test_o_nucleo_importa_as_constantes_do_modulo_de_caminhos():
    """`config.PASTA_DADOS` e `config.RAIZ_PROGRAMA` são os do `caminhos.py` (uma fonte só da pasta de dados)."""
    assert config.PASTA_DADOS == caminhos.PASTA_DADOS and config.RAIZ_PROGRAMA == caminhos.RAIZ_PROGRAMA
