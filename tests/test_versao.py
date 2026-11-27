"""P4.1: o número de versão vive numa fonte só (cs2clip.__version__) e aparece na linha de comando e na tela.

O `CHANGELOG.md` e o `instalador/*.iss` ainda não existem (P5 e P7): o teste que os confere pula até eles aparecerem.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

import cs2clip
import ui
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers

RAIZ = Path(__file__).resolve().parent.parent
INDEX_HTML = RAIZ / "ui" / "static" / "index.html"
APP_JS = RAIZ / "ui" / "static" / "app.js"
FORMATO = re.compile(r"^\d+\.\d+\.\d+$")


def test_as_duas_fontes_do_numero_sao_a_mesma():
    assert ui.__version__ == cs2clip.__version__


def test_formato_maior_menor_patch_numerico():
    assert FORMATO.fullmatch(cs2clip.__version__), cs2clip.__version__


def _python(*args: str, cwd: Path = RAIZ) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_linha_de_comando_versao_imprime_o_numero_e_sai_com_zero(tmp_path):
    r = _python("-m", "cs2clip", "--versao")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"Bisturi Clips {cs2clip.__version__}"
    # sem ler o config: um config.toml quebrado não atrapalha, e nada é processado
    quebrado = tmp_path / "config.toml"
    quebrado.write_text("isto = [não é toml", encoding="utf-8")
    r = _python("-m", "cs2clip", "--versao", "--config", str(quebrado), "--saida", str(tmp_path / "saida"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == f"Bisturi Clips {cs2clip.__version__}"
    assert not (tmp_path / "saida").exists()


def test_versao_nao_carrega_o_whisper():
    codigo = ("import sys; from cs2clip.__main__ import main; codigo = main(['--versao']); "
              "print(codigo, any(m.split('.')[0] in ('faster_whisper', 'ctranslate2', 'torch') for m in sys.modules))")
    r = _python("-c", codigo)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "0 False"


def test_rota_versao_pede_token_e_devolve_o_numero(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        assert c.get("/api/versao").status_code == 403
        r = c.get("/api/versao", headers=token_headers(c))
        assert r.status_code == 200
        assert r.json() == {"versao": cs2clip.__version__}


def test_tela_nao_traz_o_numero_escrito_a_mao():
    """O número vem de /api/versao; escrito no HTML ou no JS ele ficaria velho no primeiro aumento de versão."""
    numero = re.escape(cs2clip.__version__)
    for arquivo in (INDEX_HTML, APP_JS):
        texto = arquivo.read_text(encoding="utf-8")
        assert not re.search(numero, texto), f"{arquivo.name} tem o número da versão escrito à mão"
        assert not re.search(r"Vers[ãa]o\s+v?\d", texto), f"{arquivo.name} tem 'Versão <número>' escrito à mão"


def test_tela_le_a_versao_da_rota():
    js = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert '"/api/versao"' in js
    assert 'id="rodape-versao"' in html and 'id="ajuda-versao"' in html
    assert ".innerHTML" not in js


def _numero_em(arquivo: Path, padroes: list[str]) -> str | None:
    for linha in arquivo.read_text(encoding="utf-8-sig").splitlines():
        for p in padroes:
            m = re.search(p, linha)
            if m:
                return m.group(1)
    return None


def test_changelog_e_instalador_trazem_o_mesmo_numero():
    """Confere os arquivos que a P5 e a P7 vão criar. Enquanto não existem, o teste pula."""
    changelog = RAIZ / "CHANGELOG.md"
    isss = sorted((RAIZ / "instalador").glob("*.iss")) if (RAIZ / "instalador").is_dir() else []
    if not changelog.is_file() and not isss:
        pytest.skip("CHANGELOG.md e instalador/*.iss ainda não existem (criados na P5 e na P7)")
    if changelog.is_file():
        achado = _numero_em(changelog, [r"^##\s*\[?v?(\d+\.\d+\.\d+)\]?"])
        assert achado == cs2clip.__version__, f"CHANGELOG.md: {achado!r} != {cs2clip.__version__!r}"
    for iss in isss:
        achado = _numero_em(iss, [r'^\s*AppVersion\s*=\s*"?(\d+\.\d+\.\d+)', r'^\s*#define\s+VersaoApp\s+"(\d+\.\d+\.\d+)"'])
        assert achado == cs2clip.__version__, f"{iss.name}: {achado!r} != {cs2clip.__version__!r}"


def test_js_mostra_a_versao_lida_da_rota_e_manda_o_token(tmp_path):
    from test_r1_ui import rodar_js
    r = rodar_js(tmp_path, """
      cfg.fetch = async (url) => ({ ok: true, headers: { get: () => "application/json" }, json: async () => ({ versao: "7.8.9" }) });
      await ui.mostrarVersao();
      return { rodape: $("#rodape-versao").textContent, ajuda: $("#ajuda-versao").textContent,
               escondidoRodape: $("#rodape-versao").classList.contains("hidden"), escondidoAjuda: $("#ajuda-versao").classList.contains("hidden"),
               url: fetches[fetches.length - 1] };
    """)
    assert r == {"rodape": " · Versão 7.8.9", "ajuda": "Versão 7.8.9", "escondidoRodape": False, "escondidoAjuda": False, "url": "/api/versao"}


def test_js_sem_a_rota_a_versao_nao_aparece_e_nao_da_erro(tmp_path):
    from test_r1_ui import rodar_js
    r = rodar_js(tmp_path, """
      $("#rodape-versao").classList.add("hidden"); $("#ajuda-versao").classList.add("hidden");
      cfg.fetch = async () => ({ ok: false, status: 404, headers: { get: () => "application/json" }, json: async () => ({ erro: "não achei" }) });
      await ui.mostrarVersao();
      cfg.fetch = async () => { throw new Error("rede caiu"); };
      await ui.mostrarVersao();
      cfg.fetch = async () => ({ ok: true, headers: { get: () => "application/json" }, json: async () => ({ versao: "<b>x</b>" }) });
      await ui.mostrarVersao();
      return { rodape: $("#rodape-versao").textContent, ajuda: $("#ajuda-versao").textContent,
               escondidoRodape: $("#rodape-versao").classList.contains("hidden"), escondidoAjuda: $("#ajuda-versao").classList.contains("hidden") };
    """)
    assert r == {"rodape": "", "ajuda": "", "escondidoRodape": True, "escondidoAjuda": True}
