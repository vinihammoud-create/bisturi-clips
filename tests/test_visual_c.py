"""Visual "Broadcast Limpo" (direção C, escolhida pelo dono em 14/11/2026): etapas V1 (cores, contraste, fontes) e V2 (cabeçalho, botões, cartões).

O que estes testes guardam: o contraste mínimo do WCAG 2.2 calculado a partir das cores do próprio `style.css` (texto 4,5:1; borda de campo 3:1),
as fontes embutidas com a licença ao lado, e os defeitos de antes que não podem voltar (título sem estilo, ícone repetido, degradê no botão).
"""
import re
from pathlib import Path

import pytest

ESTATICO = Path(__file__).resolve().parents[1] / "ui" / "static"
CSS = (ESTATICO / "style.css").read_text(encoding="utf-8")


def _cor(nome: str) -> tuple[int, int, int]:
    achado = re.search(rf"^\s*--{re.escape(nome)}:\s*#([0-9a-fA-F]{{6}})\b", CSS, re.M)
    assert achado, f"a cor --{nome} tem de ser um hexadecimal de 6 dígitos no :root"
    h = achado.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _regra(padrao: str, flags: int = 0) -> str:
    """O texto da regra do CSS que casa com o padrão (falha com mensagem clara se a regra sumiu)."""
    achado = re.search(padrao, CSS, flags)
    assert achado, f"não achei no style.css a regra: {padrao}"
    return achado.group(0)


def _luminancia(cor: tuple[int, int, int]) -> float:
    canais = [c / 255 for c in cor]
    canais = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canais]
    return 0.2126 * canais[0] + 0.7152 * canais[1] + 0.0722 * canais[2]


def contraste(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_a_conta_de_contraste_bate_com_os_valores_conhecidos():
    assert contraste((255, 255, 255), (0, 0, 0)) == pytest.approx(21.0, abs=0.01)                 # CONTROLE da própria conta
    assert contraste((0x6D, 0x7A, 0x88), (0x18, 0x21, 0x2B)) == pytest.approx(3.71, abs=0.03)     # o texto de apoio ANTIGO: reprovado
    assert contraste((119, 119, 119), (255, 255, 255)) == pytest.approx(4.48, abs=0.02)           # o cinza #777 clássico: quase 4,5


@pytest.mark.parametrize("frente, fundo, minimo", [
    ("txt", "surface", 7.0), ("txt", "bg", 7.0),
    ("txt-2", "surface", 4.5), ("txt-2", "surface-2", 4.5),
    ("txt-3", "surface", 4.5), ("txt-3", "surface-2", 4.5), ("txt-3", "bg", 4.5),              # o texto de ajuda (antes 4,00 e 3,71)
    ("on-orange", "orange", 4.5), ("on-orange", "orange-hover", 4.5),                          # o texto do botão principal
    ("orange", "bg", 4.5), ("cyan", "surface", 4.5), ("ok", "surface", 4.5), ("warn", "surface", 4.5), ("bad", "surface", 4.5),
    ("field-line", "surface", 3.0), ("field-line", "bg", 3.0), ("field-line", "surface-2", 3.0),  # borda de campo e de botão (antes 1,47)
])
def test_contraste_minimo_do_wcag(frente, fundo, minimo):
    assert contraste(_cor(frente), _cor(fundo)) >= minimo, f"--{frente} sobre --{fundo}"


def test_campos_chave_e_botao_secundario_usam_a_borda_que_se_ve():
    campo = _regra(r'input\[type="text"\], input\[type="number"\], select, \.inline-input \{[^}]*\}')
    assert "border: 1px solid var(--field-line)" in campo
    assert "border: 1px solid var(--field-line)" in _regra(r"\.caixa-linha input \{[^}]*\}")
    assert "var(--field-line)" in _regra(r"^\.switch \{[^}]*\}", re.M)
    assert "border-color: var(--field-line)" in _regra(r"^\.button-secondary \{[^}]*\}", re.M)


def test_fontes_embutidas_com_a_licenca_ao_lado_e_sem_rede():
    for arquivo in ("Barlow-Regular.ttf", "Barlow-Medium.ttf", "Barlow-SemiBold.ttf", "Barlow-Bold.ttf",
                    "BarlowCondensed-SemiBold.ttf", "BarlowCondensed-Bold.ttf"):
        dados = (ESTATICO / "fontes" / arquivo).read_bytes()
        assert dados[:4] == b"\x00\x01\x00\x00" and len(dados) > 50_000, arquivo                 # TrueType de verdade, não uma página de erro
        assert f'url("/static/fontes/{arquivo}")' in CSS, arquivo
    for licenca in ("OFL-Barlow.txt", "OFL-BarlowCondensed.txt"):
        texto = (ESTATICO / "fontes" / licenca).read_text(encoding="utf-8")
        assert "SIL Open Font License, Version 1.1" in texto and "Barlow Project Authors" in texto
    assert (ESTATICO / "fontes" / "Anton-Regular.ttf").is_file()                                 # CONTROLE: a fonte dos vídeos continua lá
    assert re.search(r'--font:\s*"Barlow"', CSS) and re.search(r'--font-title:\s*"Barlow Condensed"', CSS)
    assert "https://" not in CSS and "@import" not in CSS                                        # nada vem da internet


def test_os_defeitos_de_antes_nao_voltam():
    assert re.search(r"\.view-head h1\s*\{[^}]*font-family: var\(--font-title\)", CSS), "o título de Trabalhos e Músicas ficava sem estilo"
    assert len(re.findall(r"^\.i-play \{", CSS, re.M)) == 1, "o ícone play estava definido duas vezes"
    assert re.search(r"--grad:\s*var\(--orange\)", CSS), "a ação é laranja chapado, sem degradê"
    primario = _regra(r"^\.button-primary \{[^}]*\}", re.M)
    assert "gradient" not in primario and "box-shadow" not in primario
    assert "radial-gradient" not in _regra(r"^body \{[^}]*\}", re.M), "fundo sem manchas de brilho"
    # títulos na fonte condensada pedem o peso que existe no arquivo (600 ou 700), nunca o 400 da fonte antiga
    for regra in re.findall(r"[^{}]*\{[^}]*font-family: var\(--font-title\)[^}]*\}", CSS):
        assert re.search(r"font-weight: (600|700)", regra), regra.strip()[:90]
