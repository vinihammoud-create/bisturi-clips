"""Correções feitas na revisão da interface (E5): estavam erradas na entrega original da outra IA."""
import subprocess
import sys
import time

import pytest

from ui import fila
from ui.fila import construir_argumentos, pid_vivo

from test_ui_dados_seguranca import cliente, criar_projeto, token_headers


# ── pid_vivo: no Windows, os.kill(pid, 0) dava "vivo" para processo já morto ─────────────────────

def test_pid_vivo_distingue_processo_vivo_de_morto_sem_mexer_nele():
    vivo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.5)
        assert pid_vivo(vivo.pid) is True
        assert pid_vivo(vivo.pid) is True and vivo.poll() is None      # perguntar não pode matar nem alterar
    finally:
        vivo.kill()
        vivo.wait()
    time.sleep(0.5)
    assert pid_vivo(vivo.pid) is False                                 # o que o os.kill errava no Windows


def test_pid_vivo_com_lixo_e_pid_inexistente():
    assert pid_vivo(None) is False and pid_vivo("abc") is False and pid_vivo(0) is False and pid_vivo(-5) is False
    assert pid_vivo(4000001) is False


# ── página gerada pelo programa precisa de script e estilo embutidos ─────────────────────────────

def _cliente_com_paginas(tmp_path):
    criar_projeto(tmp_path)
    (tmp_path / "saida" / "v1" / "revisao.html").write_text("<script>1</script>", encoding="utf-8")
    (tmp_path / "saida" / "v1" / "outra.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (tmp_path / "saida" / "relatorio.html").write_text("<script>1</script>", encoding="utf-8")
    return cliente(tmp_path)


def test_revisao_e_relatorio_recebem_politica_propria_sem_rede(tmp_path):
    with _cliente_com_paginas(tmp_path) as c:
        for url in ("/media/v1/revisao.html", "/media-geral/relatorio.html"):
            csp = c.get(url).headers["content-security-policy"]
            assert "script-src 'unsafe-inline'" in csp and "connect-src 'none'" in csp, url
            assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
        # a interface em si continua com a política estrita
        assert "script-src 'self'" in c.get("/").headers["content-security-policy"]
        assert "unsafe-inline'; style" not in c.get("/api/indice").headers["content-security-policy"]


def test_qualquer_outro_html_so_e_servido_como_texto(tmp_path):
    with _cliente_com_paginas(tmp_path) as c:
        r = c.get("/media/v1/outra.html")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
        assert "script-src 'self'" in r.headers["content-security-policy"]   # e sem a política frouxa


# ── opções novas do programa ─────────────────────────────────────────────────────────────────────

def test_forcar_gpu_vira_argumento_so_para_video(tmp_path):
    v, d = tmp_path / "a.mp4", tmp_path / "a.dem"
    v.write_bytes(b"x")
    d.write_bytes(b"x")
    assert "--forcar-gpu" in construir_argumentos(tmp_path, [v], {"forcar_gpu": True})
    assert "--forcar-gpu" not in construir_argumentos(tmp_path, [v], {})
    assert "--forcar-gpu" not in construir_argumentos(tmp_path, [d], {"forcar_gpu": True})   # demo não usa GPU


def test_checar_exige_token_e_devolve_o_texto(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path, checar_fn=lambda raiz: {"codigo": 0, "texto": "Tudo certo para trabalhar."}) as c:
        assert c.post("/api/checar").status_code == 403
        r = c.post("/api/checar", headers=token_headers(c))
        assert r.status_code == 200 and r.json() == {"codigo": 0, "texto": "Tudo certo para trabalhar."}


def test_cancelar_cai_no_terminate_quando_o_taskkill_falha(monkeypatch):
    class Falso:
        pid = 123
        terminado = False

        def terminate(self):
            self.terminado = True

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(fila.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1))
    p = Falso()
    fila.GerenciadorFila._encerrar_processo(p, 123)
    assert p.terminado is True or fila.os.name != "nt"
