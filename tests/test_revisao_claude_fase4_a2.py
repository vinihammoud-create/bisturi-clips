"""Revisão do Claude na branch fix/auditoria-g001-g010 (31/10/2026), ajustes menores (A2): testes que faltavam ou
eram fracos. G-004 (caso positivo), G-005 (todo pacote importado está nos requirements), G-007 (erro curto),
mensagens com acento e o VMAF só onde o ffmpeg tem libvmaf."""
import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


# ── G-004: o arquivo escolhido pelo seletor passa; o resto de fora do projeto não ──────────────────

def test_g004_arquivo_escolhido_no_seletor_passa_e_o_nao_autorizado_nao(tmp_path):
    from fastapi.testclient import TestClient
    from ui.servidor import create_app

    raiz = tmp_path / "proj"
    (raiz / "entrada").mkdir(parents=True)
    (raiz / "saida").mkdir()
    escolhido = tmp_path / "fora_escolhido.mp4"
    outro = tmp_path / "fora_outro.mp4"
    escolhido.write_bytes(b"x")
    outro.write_bytes(b"x")
    seletor: list[dict] = []
    app = create_app(root_dir=raiz, port=8765, chooser=lambda: seletor)
    enfileirados: list[list[str]] = []
    app.state.fila.adicionar_varios = lambda caminhos, opcoes: enfileirados.append(list(caminhos)) or [{"id": "j"}]

    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        h = {"X-Token": app.state.token}

        def postar(caminho) -> int:
            return c.post("/api/fila", headers=h, json={"arquivos": [str(caminho)]}).status_code

        assert postar(escolhido) == 403                              # fora do projeto e ainda não escolhido
        seletor.append({"caminho": str(escolhido.resolve()), "nome": escolhido.name, "tamanho": 1, "tipo": "video"})
        assert c.post("/api/escolher-arquivo", headers=h).status_code == 200
        assert postar(escolhido) == 200                              # depois de escolher no seletor: passa
        assert postar(str(escolhido).replace("\\", "/")) == 200      # mesmo arquivo, com barras normais
        assert postar(outro) == 403                                  # outro arquivo de fora continua barrado
        r = c.post("/api/fila", headers=h, json={"arquivos": [r"\\10.0.0.1\share\a.mp4"]})
        assert r.status_code == 403 and "não são permitidos" in r.json()["erro"]   # mensagem com acento
    assert [Path(p[0]).name for p in enfileirados] == ["fora_escolhido.mp4", "fora_escolhido.mp4"]


def test_mensagens_do_bloqueio_unc_tem_acento():
    from ui.seguranca import validar_caminho_sem_unc
    with pytest.raises(PermissionError, match="não são permitidos"):
        validar_caminho_sem_unc(r"\\servidor\pasta\a.mp4")
    with pytest.raises(PermissionError, match="inválido"):
        validar_caminho_sem_unc("")


# ── G-005: todo pacote que o código importa está nos requirements ─────────────────────────────────

NOME_NO_PIP = {"cv2": "opencv-python-headless", "faster_whisper": "faster-whisper",
               "rapidocr_onnxruntime": "rapidocr-onnxruntime", "starlette": "fastapi",    # starlette vem com o fastapi
               "huggingface_hub": "faster-whisper"}   # P4.2: vem com o faster-whisper (o cs2clip/modelo_voz.py usa a sessão HTTP dele)


def _pacotes_importados() -> dict[str, str]:
    achados: dict[str, str] = {}
    for pasta in ("cs2clip", "ui"):
        for arq in (RAIZ / pasta).rglob("*.py"):
            for no in ast.walk(ast.parse(arq.read_text(encoding="utf-8"))):
                nomes = []
                if isinstance(no, ast.Import):
                    nomes = [a.name.split(".")[0] for a in no.names]
                elif isinstance(no, ast.ImportFrom) and no.level == 0 and no.module:
                    nomes = [no.module.split(".")[0]]
                for nome in nomes:
                    if nome not in sys.stdlib_module_names and nome not in ("cs2clip", "ui", "tools", "__future__"):
                        achados.setdefault(nome, f"{pasta}/{arq.name}")
    return achados


def _nomes_nos_requirements(*arquivos: str) -> set[str]:
    nomes = set()
    for arq in arquivos:
        for linha in (RAIZ / arq).read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", linha.split("#")[0])
            if m:
                nomes.add(m.group(1).lower().replace("_", "-"))
    return nomes


def test_g005_todo_pacote_importado_esta_nos_requirements():
    pedidos = _nomes_nos_requirements("requirements.txt", "requirements-ui.txt")
    ausentes = {nome: onde for nome, onde in _pacotes_importados().items()
                if NOME_NO_PIP.get(nome, nome).lower().replace("_", "-") not in pedidos}
    assert not ausentes, f"importados mas fora dos requirements: {ausentes}"


def test_g005_o_ci_tem_o_que_os_testes_importam():
    ci = _nomes_nos_requirements("requirements-ci.txt")
    for pacote in ("pydantic", "av", "fastapi", "uvicorn", "numpy", "opencv-python-headless", "rapidocr-onnxruntime"):
        assert pacote in ci, f"{pacote} falta no requirements-ci.txt"


# ── G-007: URL fora da lista dá mensagem curta, não traceback ─────────────────────────────────────

@pytest.mark.skipif(not (RAIZ / "tools" / "estudar_referencia.py").is_file(), reason="a ferramenta de estudo é só da oficina")
def test_g007_estudar_referencia_com_url_proibida_devolve_1_com_mensagem(tmp_path, capsys):
    from tools.estudar_referencia import main
    for url in ("http://www.youtube.com/watch?v=abc", "https://evil.example.com/watch?v=1"):
        assert main([url, "--saida", str(tmp_path)]) == 1
        assert "✖" in capsys.readouterr().out


# ── G-008: VMAF só onde o ffmpeg tem libvmaf ──────────────────────────────────────────────────────

def _ffmpeg_tem_libvmaf() -> bool:
    from cs2clip.tools import FFMPEG
    r = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True, errors="replace")
    return "libvmaf" in r.stdout


@pytest.mark.skipif(not _ffmpeg_tem_libvmaf(), reason="o ffmpeg desta máquina não tem o filtro libvmaf")
@pytest.mark.lento
def test_g008_vmaf_de_um_clipe_com_ele_mesmo_e_alto(tmp_path):
    from cs2clip import qc
    from cs2clip.tools import FFMPEG
    clipe = tmp_path / "colorido.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clipe)],
                   check=True, capture_output=True)
    vmaf = qc.medir_vmaf(clipe, clipe, duracao=1.0)
    assert vmaf is not None and vmaf >= 90.0
