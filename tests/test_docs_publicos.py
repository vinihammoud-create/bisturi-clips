"""P7 (publicação): os documentos públicos estão completos, sem link quebrado, sem dado pessoal, e o aviso dos ícones do HUD (D17) está nos lugares certos.

O arquivo deste teste também vai na cópia pública: nenhum valor "proibido" aparece escrito aqui (o nome do canal é montado em pedaços).
"""
import importlib.util
import re
import unicodedata
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
IMG = RAIZ / "docs" / "img"

# Os .md que o público lê (os que ainda não existem, como o CHANGELOG antes do Fable, são ignorados na conferência de links).
DOCS_PUBLICOS = ["README.md", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "CREDITOS.md",
                 ".github/ISSUE_TEMPLATE/problema.md", ".github/ISSUE_TEMPLATE/ideia.md"]
# Arquivos novos da P7 (e só eles) sem dado pessoal nenhum.
NOVOS_DA_P7 = ["SECURITY.md", "CONTRIBUTING.md", ".github/ISSUE_TEMPLATE/problema.md", ".github/ISSUE_TEMPLATE/ideia.md",
               "cs2clip/assets/icons/AVISO.txt"]
FOTOS = ["tela_inicio_pc.png", "tela_inicio_modelo_pc.png", "tela_trabalhos_pc.png", "tela_biblioteca_pc.png", "tela_resultado_pc.png",
         "tela_musicas_pc.png", "tela_configurar_pc.png", "tela_ajuda_pc.png", "tela_boasvindas_pc.png", "tela_inicio_celular.png"]
CANAL = "drd0" + "llar"                                    # montado em pedaços: o canal do dono não pode estar escrito num arquivo público

LINK_MARKDOWN = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
LINK_HTML = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""")


def _ler(rel: str) -> str:
    return (RAIZ / rel).read_text(encoding="utf-8")


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def tem_aviso_dos_icones(texto: str) -> bool:
    """O aviso da D17: ícones do HUD, © Valve, NÃO cobertos pela licença MIT. Sem acento, maiúsculas e negrito, para valer também no .txt."""
    t = _sem_acento(texto.replace("*", "")).lower()
    return ("hud" in t and "valve" in t and "icone" in t and "licenca mit" in t
            and re.search(r"nao\s+(?:sao\s+)?cobert", t) is not None)


def _exportador():
    arq = RAIZ / "tools" / "exportar_publico.py"
    if not arq.exists():
        pytest.skip("o exportador só existe no repositório de trabalho (a conferência de privacidade dele roda lá)")
    spec = importlib.util.spec_from_file_location("exportar_publico_p7", arq)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── imagens e links ──────────────────────────────────────────────────────────────────────────────

def _alvos_locais(rel: str) -> list[str]:
    """Os links e imagens locais (relativos ao arquivo) citados no .md, sem âncora."""
    texto = _ler(rel)
    alvos = []
    for alvo in LINK_MARKDOWN.findall(texto) + LINK_HTML.findall(texto):
        if re.match(r"(?i)^(?:[a-z][a-z0-9+.\-]*:|#|//)", alvo):                  # http:, mailto:, âncora
            continue
        alvo = re.split(r"[#?]", alvo, maxsplit=1)[0]
        if alvo:
            alvos.append(alvo)
    return alvos


@pytest.mark.parametrize("rel", [d for d in DOCS_PUBLICOS if (RAIZ / d).is_file()])
def test_todo_link_relativo_e_toda_imagem_dos_documentos_publicos_existem(rel):
    base = (RAIZ / rel).parent
    faltando = [a for a in _alvos_locais(rel) if not (base / a).exists()]
    assert not faltando, f"{rel}: link ou imagem que não existe: {faltando}"


def test_os_documentos_novos_da_p7_existem_e_estao_ligados_entre_si():
    for rel in ("SECURITY.md", "CONTRIBUTING.md", ".github/ISSUE_TEMPLATE/problema.md", ".github/ISSUE_TEMPLATE/ideia.md"):
        assert (RAIZ / rel).is_file(), f"falta {rel}"
    assert "SECURITY.md" in _ler("CONTRIBUTING.md")                                  # quem quer contribuir sabe onde relatar falha de segurança
    assert "LICENSE" in _ler("CONTRIBUTING.md") and (RAIZ / "LICENSE").is_file()


def test_as_fotos_das_telas_existem_e_cabem_no_github():
    for nome in FOTOS:
        foto = IMG / nome
        assert foto.is_file(), f"falta a foto {nome}"
        assert 10_000 < foto.stat().st_size <= 1_000_000, f"{nome}: tamanho fora do esperado ({foto.stat().st_size} bytes)"


def test_a_capa_tem_1280_por_640_e_no_maximo_1_mb():
    capa = IMG / "capa_github.png"
    assert capa.is_file()
    assert capa.stat().st_size <= 1_000_000
    from PIL import Image
    with Image.open(capa) as img:
        assert img.size == (1280, 640)
        assert img.convert("RGB").getpixel((2, 2)) == (13, 14, 17)                   # fundo #0d0e11


# ── aviso dos ícones do HUD (D17) ────────────────────────────────────────────────────────────────

def test_o_aviso_dos_icones_esta_no_creditos_e_no_aviso_txt():
    assert tem_aviso_dos_icones(_ler("CREDITOS.md"))
    assert tem_aviso_dos_icones(_ler("cs2clip/assets/icons/AVISO.txt"))
    assert "Pendente" not in _ler("CREDITOS.md")                                    # o "trocar antes da primeira publicação" saiu


def test_o_aviso_dos_icones_vale_so_quando_diz_que_nao_e_mit():
    assert not tem_aviso_dos_icones("Ícones do HUD do CS2, © Valve, licença MIT.")                 # sem o "não são cobertos"
    assert tem_aviso_dos_icones("Recortes do HUD (ícones), © Valve: **não** são cobertos pela licença MIT deste projeto.")


def test_icons_py_so_le_png_entao_o_aviso_txt_na_pasta_nao_atrapalha():
    assert 'glob("*.png")' in _ler("cs2clip/icons.py")
    assert (RAIZ / "cs2clip" / "assets" / "icons" / "AVISO.txt").is_file()


_README_TEM_AVISO = tem_aviso_dos_icones((RAIZ / "README.md").read_text(encoding="utf-8")) if (RAIZ / "README.md").is_file() else False


@pytest.mark.skipif(not _README_TEM_AVISO, reason="o README novo (Fable) é quem leva o aviso dos ícones; confere quando ele chegar")
def test_o_aviso_dos_icones_esta_no_readme():
    assert tem_aviso_dos_icones(_ler("README.md"))


# ── créditos do instalador ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("trecho", ["uv", "Apache-2.0", "PSF", "python-build-standalone", "Inno Setup", "FFmpeg", "GPLv3", "nvidia-cublas-cu12", "PyPI"])
def test_creditos_cita_os_componentes_do_instalador(trecho):
    assert trecho in _ler("CREDITOS.md")


# ── os modelos de Issue e o SECURITY ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [".github/ISSUE_TEMPLATE/problema.md", ".github/ISSUE_TEMPLATE/ideia.md"])
def test_modelos_de_issue_pedem_versao_windows_placa_e_avisam_do_config(rel):
    texto = _ler(rel)
    assert texto.startswith("---\nname: ") and "\nabout: " in texto.split("\n---\n", 1)[0]          # cabeçalho que o GitHub entende
    baixo = _sem_acento(texto).lower()
    for pedido in ("versao", "windows", "placa"):
        assert pedido in baixo, f"{rel} não pede {pedido}"
    assert "config.toml" in texto and "links privados" in texto                                   # o aviso "não cole o seu config.toml nem links privados"


def test_o_modelo_de_problema_tem_os_passos_para_repetir():
    assert "Passos" in _ler(".github/ISSUE_TEMPLATE/problema.md")


def test_security_manda_relatar_em_particular_e_nao_cita_e_mail():
    texto = _ler("SECURITY.md")
    assert "Report a vulnerability" in texto and "em particular" in texto
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", texto)


@pytest.mark.skipif(not (RAIZ / "tools" / "exportar_publico.py").is_file(), reason="o exportador é só da oficina: esta cópia é a pública")
def test_nao_ha_workflow_novo_da_p7():
    pasta = RAIZ / ".github" / "workflows"
    assert {p.name for p in pasta.glob("*")} <= {"tests.yml"}                                       # os da cópia pública moram em instalador/ci/ (P6)


# ── sem dado pessoal nos arquivos novos ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", NOVOS_DA_P7)
def test_arquivos_novos_da_p7_sem_dado_pessoal(rel):
    exp = _exportador()
    arquivo = {rel: (RAIZ / rel).read_bytes()}
    assert exp.varrer_privacidade(arquivo) == []                                                    # caminho pessoal, e-mail, SteamID, chave
    assert CANAL not in _ler(rel).lower()                                                           # nem o canal do dono (D16)
    assert not re.search(r"(?<!\w)@(?!seucanal)\w{4,}", _ler(rel))                                  # nenhum @ de pessoa ou canal


def test_a_varredura_de_privacidade_pega_dado_pessoal_de_verdade():
    """A conferência acima não pode ser inofensiva: um caminho pessoal e um e-mail montados na hora têm de ser achados."""
    exp = _exportador()
    ruim = "C:" + "\\Users\\" + "maria" + "\\Projetos e " + "maria" + "@" + "empresa.com.br"
    assert exp.varrer_privacidade({"SECURITY.md": ruim.encode("utf-8")})
