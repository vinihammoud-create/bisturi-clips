"""P3.3 (26/11/2026): trava das versões de `av`, `faster-whisper` e `ctranslate2` no lock do instalador.

Por quê: o `faster-whisper` decodifica áudio com o `av` (`faster_whisper/audio.py` importa o `av` no topo) e o par quebra em combinações novas.
O `av` 19.0.1 está RETIRADO ("yanked") no PyPI por corrupção de memória ao DECODIFICAR ÁUDIO com layouts de canal customizados. O programa só usa o
`av` para quadros de vídeo (`cs2clip/scan.py`) e entrega ao `model.transcribe` um `np.ndarray` já decodificado (`cs2clip/transcribe.py`).

O teste existe para um bump acidental dessas 3 chamar atenção: mudou? Só passa se a combinação estiver em COMBINACOES_QUE_FUNCIONAM, e a pessoa
que acrescenta a linha tem de ter medido (suíte `lento` e linha de base). As varreduras com `ast` garantem que o programa continua longe da parte
que quebra (`decode_audio`, ou caminho de arquivo em `.transcribe`)."""
import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# (faster-whisper, ctranslate2, av) -> por que funciona. Acrescente a combinação nova SÓ depois de medir (nunca para "fazer o teste passar").
COMBINACOES_QUE_FUNCIONAM = {
    ("1.2.1", "4.8.2", "19.0.1"):
        "ambiente do dono em 26/11/2026 (prova B reproduziu a suíte rápida: 2293 testes). faster-whisper 1.2.1 x av 19.0.1 funciona porque o "
        "programa não chama `decode_audio` (onde o par quebra) e entrega ao `model.transcribe` um np.ndarray já decodificado. O av 19.0.1 está "
        "retirado no PyPI (áudio com layout de canal customizado), mas aqui só decodifica vídeo.",
}


def _versoes_do_lock() -> tuple[str, str, str]:
    achadas: dict[str, str] = {}
    for linha in (RAIZ / "requirements-runtime.lock").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(av|faster-whisper|ctranslate2)==(\S+)", linha)
        if m:
            achadas[m.group(1)] = m.group(2)
    faltam = {"av", "faster-whisper", "ctranslate2"} - set(achadas)
    assert not faltam, f"requirements-runtime.lock sem `==` para: {sorted(faltam)}"
    return achadas["faster-whisper"], achadas["ctranslate2"], achadas["av"]


def test_o_lock_fixa_av_faster_whisper_e_ctranslate2_numa_combinacao_que_funciona():
    combinacao = _versoes_do_lock()
    assert combinacao in COMBINACOES_QUE_FUNCIONAM, (
        f"faster-whisper/ctranslate2/av mudaram para {combinacao}, que não está em COMBINACOES_QUE_FUNCIONAM. Meça (suíte `lento`, linha de base, "
        f"transcrição de um trecho) e só então acrescente a combinação, com o motivo, em tests/test_trava_versoes.py.")


def test_a_lista_de_combinacoes_tem_motivo_e_inclui_a_atual():
    assert _versoes_do_lock() in COMBINACOES_QUE_FUNCIONAM
    for combinacao, motivo in COMBINACOES_QUE_FUNCIONAM.items():
        assert len(combinacao) == 3 and len(motivo) > 40, combinacao


def test_o_requirements_in_explica_o_av_retirado():
    texto = (RAIZ / "requirements-runtime.in").read_text(encoding="utf-8")
    assert "RETIRADO" in texto and "yanked" in texto and "19.0.1" in texto


# ── varredura do código: nada de decode_audio, nada de caminho em .transcribe ───────────────────────────────────

def _arvores():
    for pasta in ("cs2clip", "ui"):
        for arq in sorted((RAIZ / pasta).rglob("*.py")):
            yield arq, ast.parse(arq.read_text(encoding="utf-8"))


def _e_decode_audio(no: ast.AST) -> bool:
    """Chamada `decode_audio(...)` ou `x.decode_audio(...)`, ou importação do nome (`from faster_whisper.audio import decode_audio`)."""
    if isinstance(no, ast.Call):
        f = no.func
        return (isinstance(f, ast.Name) and f.id == "decode_audio") or (isinstance(f, ast.Attribute) and f.attr == "decode_audio")
    if isinstance(no, ast.ImportFrom):
        return any(a.name == "decode_audio" for a in no.names)
    return False


def _primeiro_argumento_e_caminho(no: ast.Call) -> bool:
    """Claramente um caminho: string literal, f-string, `Path(...)`/`PurePath(...)`, `str(...)`/`os.fspath(...)`, ou `a / b` com literal."""
    if not no.args:
        valor = next((k.value for k in no.keywords if k.arg in ("audio", "path", "file")), None)
        if valor is None:
            return False
    else:
        valor = no.args[0]
    if isinstance(valor, ast.Constant) and isinstance(valor.value, (str, bytes)):
        return True
    if isinstance(valor, ast.JoinedStr):
        return True
    if isinstance(valor, ast.Call):
        f = valor.func
        nome = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        if nome in ("Path", "PurePath", "PosixPath", "WindowsPath", "str", "fspath", "resolve", "expanduser", "absolute"):
            return True
    if isinstance(valor, ast.BinOp) and isinstance(valor.op, ast.Div):
        return True
    return False


def test_o_programa_nunca_chama_decode_audio():
    achados = [f"{arq.relative_to(RAIZ)}:{getattr(no, 'lineno', 0)}" for arq, arvore in _arvores() for no in ast.walk(arvore) if _e_decode_audio(no)]
    assert not achados, f"decode_audio é onde o par faster-whisper x av quebra; o programa decodifica com o FFmpeg: {achados}"


def test_o_programa_so_entrega_ndarray_ao_transcribe_nunca_um_caminho():
    chamadas = 0
    caminhos = []
    for arq, arvore in _arvores():
        for no in ast.walk(arvore):
            if isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute) and no.func.attr == "transcribe":
                chamadas += 1
                if _primeiro_argumento_e_caminho(no):
                    caminhos.append(f"{arq.relative_to(RAIZ)}:{no.lineno}")
    assert chamadas >= 1, "a varredura não achou nenhum `.transcribe(`: o teste está quebrado"
    assert not caminhos, f"`.transcribe(` com caminho de arquivo faria o faster-whisper decodificar com o av: {caminhos}"


def test_as_varreduras_reconhecem_o_que_procuram():
    """Os detectores acusam o caso ruim (sem isso, o teste poderia passar sempre)."""
    def chamada(codigo: str) -> ast.Call:
        return next(n for n in ast.walk(ast.parse(codigo)) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "transcribe")
    for ruim in ('m.transcribe("a.wav")', 'm.transcribe(Path(x))', 'm.transcribe(f"{x}.wav")', 'm.transcribe(str(p))', 'm.transcribe(pasta / "a.wav")',
                 'm.transcribe(audio="a.wav")'):
        assert _primeiro_argumento_e_caminho(chamada(ruim)), ruim
    for bom in ('m.transcribe(audio, language="pt")', 'm.transcribe(self.audio)', 'm.transcribe(x.astype(np.float32))'):
        assert not _primeiro_argumento_e_caminho(chamada(bom)), bom
    for ruim in ("decode_audio(x)", "av.decode_audio(x)", "from faster_whisper.audio import decode_audio"):
        assert any(_e_decode_audio(n) for n in ast.walk(ast.parse(ruim))), ruim
    assert not any(_e_decode_audio(n) for n in ast.walk(ast.parse("x = decodificar_audio(y)")))
