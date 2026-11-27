"""Revisão de 26/11/2026, lote D1 (M-010): o `python-multipart` (sem uso na interface) saiu dos requisitos e do iniciar."""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


# ── M-010: python-multipart saiu ─────────────────────────────────────────────────────────────────

def test_python_multipart_saiu_dos_requisitos_e_do_iniciar():
    assert "multipart" not in (RAIZ / "requirements-ui.txt").read_text(encoding="utf-8").lower()
    assert "multipart" not in (RAIZ / "ui" / "iniciar.bat").read_text(encoding="utf-8").lower()
    assert "multipart" not in (RAIZ / "CREDITOS.md").read_text(encoding="utf-8").lower()
    licencas = RAIZ / "docs" / "engineering" / "LICENSES.md"
    if licencas.exists():
        assert "multipart" not in licencas.read_text(encoding="utf-8").lower()


def test_nenhum_codigo_da_interface_usa_formulario_nem_upload_de_multipart():
    proibidos = {"Form", "File", "UploadFile"}
    for arq in sorted((RAIZ / "ui").glob("*.py")):
        arvore = ast.parse(arq.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if isinstance(no, ast.ImportFrom) and no.module in ("fastapi", "starlette.datastructures"):
                assert not proibidos & {a.name for a in no.names}, f"{arq.name} importa {proibidos & {a.name for a in no.names}}"
            if isinstance(no, (ast.Import, ast.ImportFrom)):
                nomes = [a.name for a in no.names] + [getattr(no, "module", "") or ""]
                assert not any("multipart" in n for n in nomes), f"{arq.name} importa multipart"
            if isinstance(no, ast.Attribute) and no.attr == "form" and isinstance(no.value, ast.Name) and no.value.id == "request":
                pytest.fail(f"{arq.name} lê formulário (request.form)")


CODIGO_SEM_MULTIPART = """
import sys
sys.path.insert(0, sys.argv[1])
sys.modules['multipart'] = None
sys.modules['python_multipart'] = None
from pathlib import Path
from fastapi.testclient import TestClient
from ui.servidor import create_app
raiz = Path(sys.argv[2])
(raiz / 'saida').mkdir(parents=True)
(raiz / 'entrada').mkdir()
(raiz / 'saida' / 'indice.json').write_text('[]', encoding='utf-8')
with TestClient(create_app(root_dir=raiz, port=8766), base_url='http://127.0.0.1:8766') as c:
    assert c.get('/api/indice', headers={'X-Token': c.app.state.token}).status_code == 200
    assert '<title>' in c.get('/').text
print('ok')
"""


def test_interface_sobe_e_responde_sem_o_python_multipart(tmp_path):
    """Prova de que ninguém precisa dele: com o pacote bloqueado, o app monta e serve. Em outro processo, para o bloqueio não vazar para os outros testes."""
    r = subprocess.run([sys.executable, "-c", CODIGO_SEM_MULTIPART, str(RAIZ), str(tmp_path / "projeto")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-800:]
