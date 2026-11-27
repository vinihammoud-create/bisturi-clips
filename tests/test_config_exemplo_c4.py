"""C4 (14/11/2026): o config.toml do dono sai do git; quem instala parte do config.exemplo.toml, e o programa funciona sem o arquivo.

Nenhum teste aqui lê o config.toml da raiz (ele é do dono): tudo usa o exemplo ou uma pasta temporária.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cs2clip.config import Config, load_config

RAIZ = Path(__file__).resolve().parents[1]
EXEMPLO = RAIZ / "config.exemplo.toml"


def _git(*args: str) -> subprocess.CompletedProcess:
    if shutil.which("git") is None:
        pytest.skip("git não instalado")
    return subprocess.run(["git", "-C", str(RAIZ), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


# ── o exemplo ────────────────────────────────────────────────────────────────────────────────────

def test_o_exemplo_so_difere_do_codigo_nas_escolhas_de_produto():
    """O que o exemplo entrega de diferente do padrão do código está travado, valor por valor, em tests/test_config_de_fabrica.py."""
    from test_config_de_fabrica import ESCOLHAS_DE_PRODUTO, _diferencas
    assert _diferencas(load_config(EXEMPLO)) == ESCOLHAS_DE_PRODUTO                                 # nenhum valor do dono; só as escolhas de estilo


def test_as_linhas_comentadas_do_exemplo_mostram_o_padrao_do_codigo(tmp_path):
    """Tirar o # de cada opção comentada (`# chave = valor`) leva essa opção ao padrão do CÓDIGO: as comentadas mostram o padrão, não uma escolha.
    Sobram diferentes só as escolhas escritas sem # (o perfil) e o que o perfil decide sem ter linha no arquivo."""
    from test_config_de_fabrica import _diferencas
    linhas = []
    for linha in EXEMPLO.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^# ([a-z_]+ = \S.*)$", linha)
        linhas.append(m.group(1) if m else linha)
    texto = "\n".join(linhas)
    assert len(re.findall(r"^# [a-z_]+ = ", EXEMPLO.read_text(encoding="utf-8"), flags=re.M)) >= 15       # CONTROLE: o exemplo tem opções comentadas
    p = tmp_path / "config_descomentado.toml"
    p.write_text(texto, encoding="utf-8")
    assert _diferencas(load_config(p)) == {("montagem", "perfil"): "esports_blast", ("shorts", "gancho_titulo"): False}


def test_o_exemplo_nao_tem_nada_do_dono():
    texto = EXEMPLO.read_text(encoding="utf-8")
    assert "[gravacao_demo]" not in texto                                                           # as contas ficam em Configurar → Contas do Steam
    assert not re.search(r"(?<!\d)7656119\d{10}(?!\d)", texto)                                      # nenhum SteamID64
    assert re.search(r'^marca = ""', texto, flags=re.M) and re.search(r'^jogador = ""', texto, flags=re.M)
    assert not re.search(r"^(faixa_jogo|faixa_voz|faixa_comms) = [1-9]", texto, flags=re.M)         # nenhuma faixa de áudio do OBS do dono
    assert not re.search(r'^\s*(marca|jogador|conta\w*)\s*=\s*"[^"]', texto, flags=re.M)             # nenhum texto pessoal preenchido
    assert "@" not in re.sub(r"#.*", "", texto)                                                      # nenhum @ fora de comentário


def test_o_config_do_dono_esta_ignorado_e_fora_do_git_mas_o_exemplo_entra():
    assert _git("check-ignore", "-q", "config.toml").returncode == 0                                # o .gitignore cobre o config.toml da raiz
    assert _git("check-ignore", "-q", "config.exemplo.toml").returncode == 1                         # e não o exemplo
    assert _git("ls-files", "--", "config.toml").stdout.strip() == ""                                # não está versionado
    assert _git("ls-files", "--", "config.exemplo.toml").stdout.strip() == "config.exemplo.toml" or not (RAIZ / ".git").exists()


# ── o instalador ─────────────────────────────────────────────────────────────────────────────────

BAT = RAIZ / "instalar.bat"


def test_instalar_bat_continua_em_ascii_com_crlf_e_copia_o_exemplo_antes_do_modelo_leve():
    bruto = BAT.read_bytes()
    bruto.decode("ascii")                                                                            # só ASCII (o cmd.exe não gosta de acento)
    assert b"\n" not in bruto.replace(b"\r\n", b"")                                                  # todo fim de linha é CRLF
    texto = bruto.decode("ascii")
    assert texto.count("config.exemplo.toml") >= 2 and texto.index("config.exemplo.toml") < texto.index("--modelo-leve")


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe só existe no Windows")
def test_o_trecho_do_instalador_cria_o_config_so_quando_nao_existe(tmp_path):
    trecho = [linha for linha in BAT.read_text(encoding="ascii").splitlines() if "config.exemplo.toml" in linha and not linha.lstrip().lower().startswith("rem")]
    assert 1 <= len(trecho) <= 2
    (tmp_path / "trecho.bat").write_bytes(("@echo off\r\ncd /d \"%~dp0\"\r\n" + "\r\n".join(trecho) + "\r\n").encode("ascii"))
    shutil.copy(EXEMPLO, tmp_path / "config.exemplo.toml")

    def rodar():
        r = subprocess.run(["cmd", "/c", str(tmp_path / "trecho.bat")], cwd=tmp_path, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
    rodar()
    assert (tmp_path / "config.toml").read_bytes() == EXEMPLO.read_bytes()                           # sem config.toml: nasce do exemplo
    (tmp_path / "config.toml").write_text('[shorts]\nmarca = "@meu"\n', encoding="utf-8")
    rodar()
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == '[shorts]\nmarca = "@meu"\n'    # CONTROLE: o que já existe nunca é sobrescrito
    (tmp_path / "config.toml").unlink()
    (tmp_path / "config.exemplo.toml").unlink()
    rodar()                                                                                          # sem o exemplo, não quebra nem cria nada
    assert not (tmp_path / "config.toml").exists()


# ── tudo funciona sem o config.toml ──────────────────────────────────────────────────────────────

def test_load_config_sem_arquivo_e_o_padrao(tmp_path):
    assert load_config(tmp_path / "nao_existe.toml") == Config() and load_config(None) == Config()


def test_checar_pela_linha_de_comando_sem_config(tmp_path, monkeypatch, capsys):
    """`--checar` com o config ausente chega até a checagem (aqui trocada por uma falsa, para não depender do PC) e sai 0."""
    import cs2clip.checagem as checagem
    from cs2clip.__main__ import main
    chamadas = []
    monkeypatch.setattr(checagem, "checar", lambda raiz, saida: chamadas.append(raiz) or [])
    monkeypatch.setattr(checagem, "texto", lambda itens: "checagem falsa")
    monkeypatch.setattr(checagem, "tem_problema_grave", lambda itens: False)
    ausente = tmp_path / "config.toml"
    assert main(["--checar", "--config", str(ausente), "--saida", str(tmp_path / "s")]) == 0
    saida = capsys.readouterr().out
    assert chamadas and "checagem falsa" in saida and "problema" not in saida and not ausente.exists()      # nem cria o arquivo


def test_modelo_leve_sem_config_cria_o_arquivo_so_com_o_modelo(tmp_path, capsys):
    from cs2clip.__main__ import main
    arq = tmp_path / "config.toml"
    assert not arq.exists()
    assert main(["--modelo-leve", "--config", str(arq)]) == 0
    assert load_config(arq).transcricao.modelo == "small" and "small" in capsys.readouterr().out
    depois = load_config(arq)
    depois.transcricao.modelo = Config().transcricao.modelo
    assert depois == Config()                                                                        # o resto continua o padrão


def test_tela_configurar_le_e_salva_sem_o_config_e_o_salvar_cria_o_arquivo(tmp_path):
    from ui.configuracao import ConfiguracaoProjeto
    cfg = ConfiguracaoProjeto(tmp_path)
    assert not (tmp_path / "config.toml").exists()
    lido = {(c["secao"], c["chave"]): c for c in cfg.ler()["campos"]}
    assert all(c["origem"] == "padrão do programa" for c in lido.values())                           # tudo vem do padrão
    assert lido[("shorts", "marca")]["valor"] == "" and lido[("qualidade", "renders_paralelos")]["valor"] == Config().qualidade.renders_paralelos
    assert not (tmp_path / "config.toml").exists()                                                   # ler não cria nada
    cfg.salvar({"shorts.marca": "@meucanal"})
    assert (tmp_path / "config.toml").is_file() and load_config(tmp_path / "config.toml").shorts.marca == "@meucanal"
    depois = {(c["secao"], c["chave"]): c for c in cfg.ler()["campos"]}
    assert depois[("shorts", "marca")]["origem"] == "config.toml" and depois[("cortes", "antes")]["origem"] == "padrão do programa"


def test_servidor_sem_config_responde_a_tela_configurar(tmp_path):
    from test_ui_dados_seguranca import cliente, criar_projeto, token_headers
    criar_projeto(tmp_path)
    assert not (tmp_path / "config.toml").exists()
    with cliente(tmp_path) as c:
        r = c.get("/api/config", headers=token_headers(c))
        assert r.status_code == 200 and r.json()["campos"]
        assert c.post("/api/config", json={"alteracoes": {"shorts.marca": "@x"}}, headers=token_headers(c)).status_code == 200
    assert (tmp_path / "config.toml").is_file()
