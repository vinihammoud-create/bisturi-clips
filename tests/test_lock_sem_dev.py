"""P3 (26/11/2026): os locks do instalador (`requirements-runtime.lock` e `requirements-gpu.lock`) são fixos, com hash, sem ferramenta de
desenvolvimento, sem o que saiu de propósito (cuDNN, nvrtc, multipart) e com tudo o que o programa importa direto. Só lê TEXTO: não
instala nada e não chama o `uv`. O `requirements*.txt` de hoje continuam servindo ao modo portátil (`instalar.bat`); aqui só conferimos a deriva."""
import ast
import re
import sys
from importlib import metadata
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]

# Ferramentas de desenvolvimento: nunca no lock do instalador (214 MB só de nodejs-wheel-binaries + basedpyright + ruff + pytest...).
PACOTES_DE_DESENVOLVIMENTO = {
    "pytest", "pytest-xdist", "pytest-cov", "pytest-asyncio", "execnet", "iniconfig", "pluggy", "coverage",
    "basedpyright", "pyright", "nodejs-wheel-binaries", "nodejs-wheel", "mypy", "pandas-stubs", "types-pyyaml", "types-requests",
    "ruff", "black", "flake8", "pip-audit", "pip-tools", "tabulate",
}
# Saíram de propósito (planos P3 e F7): cuDNN/nvrtc (1,3 GB, provado inútil para o ctranslate2 4.8.2) e o multipart (M-010, a interface não o usa).
PACOTES_PROIBIDOS_PREFIXOS = ("nvidia-cudnn", "nvidia-cuda-nvrtc", "python-multipart", "multipart")

# Nome do módulo que o código importa -> nome do pacote no lock. Tabela pequena e explícita: import novo de fora que não esteja aqui
# reprova o teste, para alguém decidir se o pacote entra no lock.
MODULO_PARA_PACOTE = {
    "av": "av", "cv2": "opencv-python-headless", "demoparser2": "demoparser2", "fastapi": "fastapi",
    "faster_whisper": "faster-whisper", "numpy": "numpy", "pydantic": "pydantic", "rapidocr_onnxruntime": "rapidocr-onnxruntime",
    "starlette": "starlette", "uvicorn": "uvicorn", "PIL": "pillow", "pandas": "pandas",
    # P4.2 (27/11): o `cs2clip/modelo_voz.py` usa a sessão HTTP do huggingface_hub (vem no lock pelo faster-whisper; versão fixada lá)
    "huggingface_hub": "huggingface-hub",
}
NAO_E_PACOTE_DE_FORA = {"cs2clip", "ui", "tools", "__future__"}

# Nomes que o `requirements-runtime.in` tem A MAIS que os `requirements*.txt` (nome -> motivo). O .in pode ter a mais, mas cada excesso se explica.
NO_IN_A_MAIS = {
    "httpx": "saiu do requirements-ui.txt em 26/11 (P3.7: só os testes o usam), mas o lock da prova B (26/11) ainda o traz; tirá-lo exige "
             "recompilar o lock e repetir a prova B. Fica até lá (1,3 MB).",
}
# Nomes dos `requirements*.txt` que o .in NÃO tem (nome -> motivo). Vazio de propósito: o .in não pode ter a menos.
NO_IN_A_MENOS: dict[str, str] = {}


def _norm(nome: str) -> str:
    return re.sub(r"[-_.]+", "-", nome).lower()


def _ler_lock(caminho: Path) -> dict[str, dict]:
    """Lê o lock do uv (texto): {nome_normalizado: {"versao": str|None, "operador": str, "hashes": [..]}}. Ignora comentários."""
    pacotes: dict[str, dict] = {}
    atual: dict | None = None
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(==|>=|<=|~=|!=|>|<|===)?\s*([^\s\\;]*)", linha)
        if m and not linha.startswith((" ", "\t")):
            atual = {"versao": m.group(3) or None, "operador": m.group(2) or "", "hashes": []}
            pacotes[_norm(m.group(1))] = atual
            continue
        for h in re.findall(r"--hash=sha256:([0-9a-f]{64})\b", linha):
            if atual is not None:
                atual["hashes"].append(h)
    return pacotes


def _nomes_de_requirements(*arquivos: str) -> set[str]:
    nomes = set()
    for arq in arquivos:
        for linha in (RAIZ / arq).read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", linha.split("#")[0])
            if m:
                nomes.add(_norm(m.group(1)))
    return nomes


@pytest.fixture(scope="module")
def runtime() -> dict[str, dict]:
    return _ler_lock(RAIZ / "requirements-runtime.lock")


@pytest.fixture(scope="module")
def gpu() -> dict[str, dict]:
    return _ler_lock(RAIZ / "requirements-gpu.lock")


def test_o_leitor_do_lock_entende_o_formato_do_uv(tmp_path):
    arq = tmp_path / "x.lock"
    h = "a" * 64
    arq.write_text(f"# c\n# --hash=sha256:{'b' * 64}\nfoo-bar==1.0 \\\n    --hash=sha256:{h} \\\n    --hash=sha256:{h}\n    # via x\nbaz>=2 \\\n    --hash=sha256:{h}\n",
                   encoding="utf-8")
    p = _ler_lock(arq)
    assert p["foo-bar"] == {"versao": "1.0", "operador": "==", "hashes": [h, h]}
    assert p["baz"]["operador"] == ">=" and len(p["baz"]["hashes"]) == 1


def test_todo_pacote_do_lock_e_fixo_e_tem_hash(runtime, gpu):
    for nome_lock, lock in (("requirements-runtime.lock", runtime), ("requirements-gpu.lock", gpu)):
        assert lock, f"{nome_lock} vazio"
        soltos = [n for n, p in lock.items() if p["operador"] != "==" or not p["versao"]]
        sem_hash = [n for n, p in lock.items() if not p["hashes"]]
        assert not soltos, f"{nome_lock}: fora de `==`: {soltos}"
        assert not sem_hash, f"{nome_lock}: sem --hash=sha256: {sem_hash}"


def test_o_lock_nao_tem_ferramenta_de_desenvolvimento(runtime, gpu):
    for nome_lock, lock in (("requirements-runtime.lock", runtime), ("requirements-gpu.lock", gpu)):
        dev = sorted(set(lock) & {_norm(n) for n in PACOTES_DE_DESENVOLVIMENTO})
        assert not dev, f"{nome_lock} tem pacote de desenvolvimento: {dev}"


def test_o_lock_nao_tem_cudnn_nvrtc_nem_multipart(runtime, gpu):
    for nome_lock, lock in (("requirements-runtime.lock", runtime), ("requirements-gpu.lock", gpu)):
        proibidos = sorted(n for n in lock if n.startswith(tuple(_norm(p) for p in PACOTES_PROIBIDOS_PREFIXOS)))
        assert not proibidos, f"{nome_lock} tem pacote que saiu de propósito: {proibidos}"
    assert not any(n.startswith("nvidia") for n in runtime), "pacote da NVIDIA no lock de runtime (o cuBLAS vai só no lock da GPU)"


def test_o_lock_da_gpu_tem_so_o_cublas(gpu):
    assert list(gpu) == ["nvidia-cublas-cu12"], list(gpu)
    # a mesma versão do requirements-gpu.txt do modo portátil
    portatil = (RAIZ / "requirements-gpu.txt").read_text(encoding="utf-8")
    assert f"nvidia-cublas-cu12=={gpu['nvidia-cublas-cu12']['versao']}" in portatil


def test_o_in_da_gpu_so_pede_o_cublas_e_manda_compilar_sem_dependencias():
    texto = (RAIZ / "requirements-gpu.in").read_text(encoding="utf-8")
    pacotes = [ln.split("#")[0].strip() for ln in texto.splitlines() if ln.split("#")[0].strip()]
    assert [p.split("==")[0] for p in pacotes] == ["nvidia-cublas-cu12"]
    assert "--no-deps" in texto
    assert "--no-deps" in (RAIZ / "requirements-gpu.lock").read_text(encoding="utf-8")


def _modulos_de_fora_importados() -> dict[str, str]:
    achados: dict[str, str] = {}
    for pasta in ("cs2clip", "ui"):
        for arq in sorted((RAIZ / pasta).rglob("*.py")):
            for no in ast.walk(ast.parse(arq.read_text(encoding="utf-8"))):
                nomes: list[str] = []
                if isinstance(no, ast.Import):
                    nomes = [a.name.split(".")[0] for a in no.names]
                elif isinstance(no, ast.ImportFrom) and no.level == 0 and no.module:
                    nomes = [no.module.split(".")[0]]
                for nome in nomes:
                    if nome not in sys.stdlib_module_names and nome not in NAO_E_PACOTE_DE_FORA:
                        achados.setdefault(nome, f"{pasta}/{arq.name}")
    return achados


def test_todo_pacote_que_o_programa_importa_direto_esta_no_lock(runtime):
    achados = _modulos_de_fora_importados()
    assert achados, "a varredura de import não achou nada: o teste está quebrado"
    sem_tabela = {m: onde for m, onde in achados.items() if m not in MODULO_PARA_PACOTE}
    assert not sem_tabela, f"import de fora sem entrada em MODULO_PARA_PACOTE (decida se vai ao lock): {sem_tabela}"
    ausentes = {m: onde for m, onde in achados.items() if _norm(MODULO_PARA_PACOTE[m]) not in runtime}
    assert not ausentes, f"o programa importa, mas o requirements-runtime.lock não tem: {ausentes}"


def test_a_tabela_de_modulos_so_tem_pacotes_que_existem_no_lock(runtime):
    # a tabela guarda também pacotes que ainda não são importados (pandas, PIL): se o lock os perder, o aviso vem aqui
    sem_lock = [p for p in MODULO_PARA_PACOTE.values() if _norm(p) not in runtime]
    assert not sem_lock, sem_lock


def test_deriva_os_pacotes_diretos_dos_requirements_estao_no_in():
    """O `requirements-runtime.in` pode ter A MAIS que os `requirements*.txt`, nunca a menos; cada diferença se explica num dicionário."""
    diretos = _nomes_de_requirements("requirements.txt", "requirements-ui.txt", "requirements-gpu.txt")
    no_in = _nomes_de_requirements("requirements-runtime.in", "requirements-gpu.in")     # o cuBLAS vai no .in da GPU
    a_menos = diretos - no_in - {_norm(n) for n in NO_IN_A_MENOS}
    assert not a_menos, f"estão nos requirements*.txt e faltam nos .in: {sorted(a_menos)}"
    a_mais = no_in - diretos
    assert a_mais == {_norm(n) for n in NO_IN_A_MAIS}, f"o .in tem a mais (explique em NO_IN_A_MAIS) ou a explicação ficou velha: {sorted(a_mais)}"
    for motivo in [*NO_IN_A_MAIS.values(), *NO_IN_A_MENOS.values()]:
        assert len(motivo) > 20, "toda exceção precisa de um motivo"


def test_o_in_e_o_lock_dizem_a_mesma_versao_dos_diretos(runtime):
    for linha in (RAIZ / "requirements-runtime.in").read_text(encoding="utf-8").splitlines():
        texto = linha.split("#")[0].strip()
        if not texto:
            continue
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", texto)
        assert m, f"requirements-runtime.in: `{texto}` não está em `==`"
        assert _norm(m.group(1)) in runtime, f"{m.group(1)} está no .in e falta no lock"
        assert runtime[_norm(m.group(1))]["versao"] == m.group(2), f"{m.group(1)}: .in {m.group(2)} x lock {runtime[_norm(m.group(1))]['versao']}"


def test_o_overrides_e_o_lock_dizem_a_mesma_versao(runtime):
    pinos = {}
    for linha in (RAIZ / "overrides.txt").read_text(encoding="utf-8").splitlines():
        texto = linha.split("#")[0].strip()
        if texto:
            m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", texto)
            assert m, f"overrides.txt: `{texto}` não está em `==`"
            pinos[_norm(m.group(1))] = m.group(2)
    divergentes = {n: (v, runtime[n]["versao"]) for n, v in pinos.items() if n in runtime and runtime[n]["versao"] != v}
    assert not divergentes, f"overrides.txt x lock: {divergentes}"
    sem_pino = sorted(set(runtime) - set(pinos))
    assert not sem_pino, f"no lock sem pino no overrides.txt (a prova B mostrou que sem pino a versão deriva): {sem_pino}"


def test_o_lock_bate_com_o_ambiente_do_dono(runtime, gpu):
    """Conferência com o `pip freeze` do .venv do dono (26/11/2026), lida pelo `importlib.metadata` (o mesmo dado, sem instalar nada).
    Só roda onde está o ambiente completo do programa (no CI enxuto e em outro PC, as versões são outras e o teste se pula)."""
    for obrigatorio in ("faster-whisper", "ctranslate2", "demoparser2", "rapidocr-onnxruntime", "av"):
        try:
            metadata.version(obrigatorio)
        except metadata.PackageNotFoundError:
            pytest.skip(f"{obrigatorio} não está instalado: não é o ambiente completo do programa")
    divergentes = {}
    for nome, p in {**runtime, **gpu}.items():
        try:
            instalada = metadata.version(nome)
        except metadata.PackageNotFoundError:
            continue                                  # (ex.: cuBLAS numa máquina sem ele); o que falta aparece no teste abaixo
        if instalada != p["versao"]:
            divergentes[nome] = (f"lock {p['versao']}", f"instalada {instalada}")
    assert not divergentes, f"o lock não é mais o ambiente do dono (regerar de propósito, com prova): {divergentes}"
    ausentes = [n for n in runtime if _norm(n) not in {_norm(d.metadata['Name']) for d in metadata.distributions()}]
    assert not ausentes, f"no lock e fora do ambiente do dono: {ausentes}"
