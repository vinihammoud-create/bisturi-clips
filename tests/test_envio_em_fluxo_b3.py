"""B3 (F1): o envio de arquivo grande pela tela vai em fluxo, direto para `entrada/`, sem passar pela pasta temporária do sistema.

Antes (POST /api/upload, multipart): o Starlette gravava o arquivo na pasta temporária do Windows (disco C:) e depois a rota copiava para `entrada/`:
um envio de vários GB podia encher o C: antes do erro, e todo envio grande era gravado duas vezes. Agora: PUT /api/upload/arquivo?nome=..., corpo = os bytes.
"""
import asyncio
import collections
import hashlib
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

import pytest

from test_ui_dados_seguranca import cliente, criar_projeto, token_headers
from ui import envio, seguranca

MB = 1024 * 1024
ROTA = "/api/upload/arquivo"
Uso = collections.namedtuple("Uso", "total used free")


def _limites(**mudancas):
    import dataclasses
    return dataclasses.replace(seguranca.LimitesEnvio(), **mudancas)


def _definir_limites(c, **mudancas):
    setattr(c.app_state, "limites", _limites(**mudancas))


def _enviar(c, nome, dados, **extra):
    return c.put(f"{ROTA}?nome={quote(nome)}", headers={**token_headers(c), **extra.pop("headers", {})}, content=dados, **extra)


def _entrada(tmp_path):
    return sorted(p.name for p in (tmp_path / "entrada").iterdir())


def test_envio_pequeno_chega_igual_e_fica_autorizado_para_a_fila(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        dados = bytes(range(256)) * (3 * MB // 256) + b"fim"
        r = _enviar(c, "partida com acento-á.mp4", dados)
        assert r.status_code == 200, r.text
        item = r.json()["itens"][0]
        arquivo = Path(item["caminho"])
        assert hashlib.sha256(arquivo.read_bytes()).hexdigest() == hashlib.sha256(dados).hexdigest()
        assert item["tamanho"] == len(dados) and item["tipo"] == "video" and arquivo.parent == (tmp_path / "entrada").resolve()
        assert str(arquivo) in getattr(c.app_state, "arquivos_escolhidos")
        demo = _enviar(c, "partida.dem", b"D" * 10)
        assert demo.status_code == 200 and demo.json()["itens"][0]["tipo"] == "demo"
        assert not [n for n in _entrada(tmp_path) if n.endswith(".part")]


def test_nada_e_gravado_na_pasta_temporaria_do_sistema(tmp_path, monkeypatch):
    criar_projeto(tmp_path)
    temp = tmp_path / "temp_do_sistema"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))                    # é onde o Starlette gravaria o multipart

    def proibido(*_a, **_k):
        raise AssertionError("usou a pasta temporária do sistema")

    for nome in ("SpooledTemporaryFile", "NamedTemporaryFile", "TemporaryFile", "mkdtemp"):
        monkeypatch.setattr(tempfile, nome, proibido)
    original = tempfile.mkstemp

    def mkstemp_so_em_entrada(*a, dir=None, **k):                          # o único temporário permitido é o .part DENTRO de entrada/
        assert dir is not None and Path(dir).resolve() == (tmp_path / "entrada").resolve(), dir
        return original(*a, dir=dir, **k)

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp_so_em_entrada)
    with cliente(tmp_path) as c:
        r = _enviar(c, "live.mp4", b"V" * (4 * MB))
        assert r.status_code == 200, r.text
        assert list(temp.iterdir()) == []                                  # nem arquivo nem pasta
        assert Path(r.json()["itens"][0]["caminho"]).stat().st_size == 4 * MB


def test_content_length_maior_que_o_espaco_livre_recusa_e_nao_grava_nada(tmp_path, monkeypatch):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        monkeypatch.setattr(shutil, "disk_usage", lambda _p: Uso(10 ** 12, 0, 2 * MB))
        r = _enviar(c, "live.mp4", b"V" * (3 * MB))
        assert r.status_code == 507 and "espaço" in r.json()["erro"], r.text
        assert _entrada(tmp_path) == []


def test_sem_content_length_responde_411(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = c.put(f"{ROTA}?nome=live.mp4", headers=token_headers(c), content=iter([b"abc", b"def"]))
        assert r.status_code == 411, r.text
        assert _entrada(tmp_path) == []


@pytest.mark.parametrize("nome, esperado", [("segredo.exe", 415), ("sem_extensao", 415), ("", 400)])
def test_extensao_proibida_e_nome_vazio_sao_recusados(tmp_path, nome, esperado):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = _enviar(c, nome, b"x")
        assert r.status_code == esperado, r.text
        assert _entrada(tmp_path) == []


def test_sem_o_parametro_nome_e_recusado(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = c.put(ROTA, headers=token_headers(c), content=b"x")
        assert r.status_code == 422 and _entrada(tmp_path) == []


@pytest.mark.parametrize("nome", ["../fora.mp4", "..\\fora.mp4", "a/b/c.mp4", "con .mp4", "C:\\x\\y.mp4"])
def test_nome_perigoso_vira_nome_seguro_dentro_de_entrada(tmp_path, nome):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = _enviar(c, nome, b"x")
        assert r.status_code == 200, r.text
        gravado = Path(r.json()["itens"][0]["caminho"])
        assert gravado.parent == (tmp_path / "entrada").resolve() and ".." not in gravado.name
        assert not (tmp_path / "fora.mp4").exists() and not (tmp_path.parent / "fora.mp4").exists()


def test_acima_do_teto_do_tipo_recusa_antes_de_gravar(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        _definir_limites(c, video_arquivo=10 * MB, demo_arquivo=1 * MB)
        r = _enviar(c, "partida.dem", b"D" * (2 * MB))
        assert r.status_code == 413 and "grande" in r.json()["erro"], r.text
        assert _entrada(tmp_path) == []
        assert _enviar(c, "outra.mp4", b"V" * (2 * MB)).status_code == 200            # controle: vídeo tem teto maior
        _definir_limites(c, video_arquivo=1 * MB)
        r = _enviar(c, "enorme.mp4", b"V" * (3 * MB))
        assert r.status_code == 413 and "grande" in r.json()["erro"]
        assert _entrada(tmp_path) == ["outra.mp4"]


def test_corpo_maior_que_o_declarado_recusa_e_apaga_o_part(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = _enviar(c, "live.mp4", b"V" * (3 * MB), headers={"Content-Length": "100"})
        assert r.status_code in (400, 413), (r.status_code, r.text)
        assert _entrada(tmp_path) == []


def test_sem_token_ou_com_host_errado_e_recusado_como_nas_outras_rotas(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        assert c.put(f"{ROTA}?nome=live.mp4", content=b"x").status_code == 403
        assert c.put(f"{ROTA}?nome=live.mp4", headers={**token_headers(c), "Host": "evil.example"}, content=b"x").status_code == 403
        assert c.put(f"{ROTA}?nome=live.mp4", headers={**token_headers(c), "Origin": "http://evil.example"}, content=b"x").status_code == 403
        assert _entrada(tmp_path) == []


def test_dois_envios_com_o_mesmo_nome_nao_se_sobrescrevem(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        a = _enviar(c, "live.mp4", b"A" * 100).json()["itens"][0]
        b = _enviar(c, "live.mp4", b"B" * 50).json()["itens"][0]
        assert a["nome"] == "live.mp4" and b["nome"] == "live (2).mp4"
        assert Path(a["caminho"]).read_bytes() == b"A" * 100 and Path(b["caminho"]).read_bytes() == b"B" * 50


def test_a_rota_multipart_antiga_nao_existe_mais(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = c.post("/api/upload", headers=token_headers(c), files=[("files", ("a.mp4", b"x", "video/mp4"))])
        assert r.status_code in (404, 405)
        assert _entrada(tmp_path) == []


def test_o_middleware_conta_o_envio_em_fluxo_e_nao_deixa_passar_do_teto(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        _definir_limites(c, video_arquivo=1 * MB)
        r = _enviar(c, "live.mp4", b"V" * (3 * MB))                     # Content-Length 3 MB > teto: recusado antes de ler o corpo
        assert r.status_code == 413 and "grande demais" in r.json()["erro"]
        assert _entrada(tmp_path) == []


# ── a função que recebe o fluxo, sem servidor (queda no meio, espaço acabando) ────────────────

async def _fluxo(*blocos, erro=None):
    for b in blocos:
        yield b
    if erro:
        raise erro


def _rodar(corotina):
    return asyncio.run(corotina)


def test_conexao_cai_no_meio_o_part_e_apagado(tmp_path):
    from starlette.requests import ClientDisconnect
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    with pytest.raises(ClientDisconnect):
        _rodar(envio.receber(_fluxo(b"A" * MB, b"B" * MB, erro=ClientDisconnect()), entrada, "live.mp4", 5 * MB, 10 * MB, 0))
    assert list(entrada.iterdir()) == []


def test_corpo_menor_que_o_declarado_e_recusado_e_apaga_tudo(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    with pytest.raises(envio.ErroEnvio) as e:
        _rodar(envio.receber(_fluxo(b"A" * MB), entrada, "live.mp4", 3 * MB, 10 * MB, 0))
    assert e.value.status == 400
    assert list(entrada.iterdir()) == []


def test_espaco_que_acaba_no_meio_do_envio_apaga_o_part(tmp_path, monkeypatch):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    chamadas = {"n": 0}

    def uso(_p):
        chamadas["n"] += 1
        return Uso(10 ** 12, 0, 10 ** 12 if chamadas["n"] == 1 else 1000)       # a 1ª conferência (antes) passa; as seguintes não

    monkeypatch.setattr(envio.shutil, "disk_usage", uso)
    with pytest.raises(envio.ErroEnvio) as e:
        _rodar(envio.receber(_fluxo(*[b"A" * MB] * 40), entrada, "live.mp4", 40 * MB, 100 * MB, 0))
    assert e.value.status == 507
    assert list(entrada.iterdir()) == []


def test_envio_ok_pela_funcao_devolve_caminho_e_tamanho(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    caminho, total = _rodar(envio.receber(_fluxo(b"A" * 1000, b"B" * 24), entrada, "live.mp4", 1024, 10 * MB, 0))
    assert total == 1024 and caminho.read_bytes() == b"A" * 1000 + b"B" * 24 and [p.name for p in entrada.iterdir()] == ["live.mp4"]


# ── o app.js: um PUT por arquivo, em sequência, e para no primeiro que falhar ─────────────────

def test_js_envia_um_put_por_arquivo_em_sequencia_e_para_no_que_falha(tmp_path):
    from test_r1_ui import rodar_js
    r = rodar_js(tmp_path, """
      XHR.prototype.open = function (m, u) { this.m = m; this.u = u; };
      XHR.prototype.setRequestHeader = function (k, v) { (this.h = this.h || {})[k] = v; };
      XHR.prototype.addEventListener = function (ev, fn) { (this.ev = this.ev || {})[ev] = fn; };
      XHR.prototype.send = function (d) { this.enviado = d; };
      const a = { name: "partida com acento-á.mp4", size: 10 }, b = { name: "outra.dem", size: 5 }, c = { name: "terceira.mp4", size: 7 };
      ui.enviarArquivos([a, b, c, { name: "notas.txt", size: 1 }]);
      await espera();
      const primeiro = XHR.todos[0];
      const so_um_no_inicio = XHR.todos.length;
      primeiro.status = 200;
      primeiro.responseText = JSON.stringify({ itens: [{ caminho: "C:/x/a.mp4", nome: "a.mp4", tamanho: 10, tipo: "video" }] });
      primeiro.ev.load();
      await espera();
      const segundo = XHR.todos[1];
      segundo.status = 413;
      segundo.responseText = JSON.stringify({ erro: "O arquivo é grande demais." });
      segundo.ev.load();
      await espera();
      return { so_um_no_inicio, metodo: primeiro.m, url: primeiro.u, corpo_e_o_arquivo: primeiro.enviado === a, tem_token: "X-Token" in primeiro.h,
               url2: segundo.u, total_de_pedidos: XHR.todos.length, texto: textoDe($("#upload-status-text")) };
    """)
    assert r["so_um_no_inicio"] == 1 and r["metodo"] == "PUT" and r["corpo_e_o_arquivo"] and r["tem_token"]
    assert r["url"] == "/api/upload/arquivo?nome=partida%20com%20acento-%C3%A1.mp4" and r["url2"] == "/api/upload/arquivo?nome=outra.dem"
    assert r["total_de_pedidos"] == 2                                    # o 3º arquivo não foi enviado depois da falha do 2º
    assert "Não consegui" in r["texto"]


def test_o_middleware_recusa_o_content_length_gigante_antes_mesmo_de_a_rota_olhar_o_nome(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        _definir_limites(c, video_arquivo=1 * MB, demo_arquivo=1 * MB)
        r = _enviar(c, "segredo.exe", b"x" * (3 * MB))                 # a extensão é proibida (415 na rota), mas o corpo passa do teto: 413 vem antes
        assert r.status_code == 413, r.text
        assert _entrada(tmp_path) == []


def test_corpo_maior_que_o_declarado_para_de_ler_logo_sem_gastar_o_disco(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    lidos = {"n": 0}

    async def torrente():
        for _ in range(1000):
            lidos["n"] += 1
            yield b"A" * MB

    with pytest.raises(envio.ErroEnvio) as e:
        _rodar(envio.receber(torrente(), entrada, "live.mp4", 2 * MB, 10 * MB, 0))
    assert e.value.status == 400 and lidos["n"] <= 3                  # parou no 3º bloco, não leu os 1000
    assert list(entrada.iterdir()) == []


# ── achados do QA da F1 (tentativa 1): falha ao FECHAR o arquivo, fdopen que falha, nomes ──────────────────────

def test_close_que_falha_por_disco_cheio_ainda_apaga_o_part(tmp_path, monkeypatch):
    import errno
    import io
    entrada = tmp_path / "entrada"
    entrada.mkdir()

    class CruComFalha(io.RawIOBase):
        """Arquivo cru que aceita o 1º write e dá disco cheio no 2º (o resto do buffer só é descarregado no close)."""
        def __init__(self, real):
            self.real = real
            self.n = 0

        def writable(self):
            return True

        def fileno(self):
            return self.real.fileno()

        def write(self, b):
            self.n += 1
            if self.n >= 2:
                raise OSError(errno.ENOSPC, "disco cheio")
            return self.real.write(b)

        def close(self):
            self.real.close()
            super().close()

    abrir = envio.os.fdopen
    monkeypatch.setattr(envio.os, "fdopen", lambda fd, modo: io.BufferedWriter(CruComFalha(abrir(fd, modo, buffering=0))))
    with pytest.raises(OSError):
        _rodar(envio.receber(_fluxo(b"A" * MB, b"B" * 100), entrada, "live.mp4", 2 * MB, 10 * MB, 0))      # termina menor que o declarado: cai no finally
    assert list(entrada.iterdir()) == []


def test_fdopen_que_falha_nao_deixa_o_part_nem_vaza_o_descritor(tmp_path, monkeypatch):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    fechados = []
    real_close = envio.os.close
    monkeypatch.setattr(envio.os, "fdopen", lambda *a, **k: (_ for _ in ()).throw(TypeError("falhou")))
    monkeypatch.setattr(envio.os, "close", lambda fd: (fechados.append(fd), real_close(fd))[1])
    with pytest.raises(TypeError):
        _rodar(envio.receber(_fluxo(b"A"), entrada, "live.mp4", 1, 10 * MB, 0))
    assert list(entrada.iterdir()) == [] and len(fechados) == 1


@pytest.mark.parametrize("nome", ["a‮gnp.mp4", "x‎y.mp4", "linha nova.mp4"])
def test_nome_com_caractere_de_direcao_ou_separador_de_linha_e_neutralizado(nome):
    seguro = seguranca.nome_arquivo_seguro(nome)
    assert all(ord(c) >= 32 and c not in "‮‎ " for c in seguro) and seguro.endswith(".mp4"), seguro


def test_nome_muito_longo_mantem_a_extensao():
    seguro = seguranca.nome_arquivo_seguro("n" * 400 + ".mp4")
    assert len(seguro) == 240 and seguro.endswith(".mp4")


# ── revisão do Opus (14/11): disco sem hard link (exFAT/FAT32/rede) + recusa momentânea do Windows não pode descartar o envio ──

def test_sem_hard_link_uma_recusa_momentanea_do_windows_nao_perde_o_envio(tmp_path, monkeypatch):
    import os as _os
    criar_projeto(tmp_path)
    real_replace = _os.replace
    estado = {"n": 0}

    def recusa_uma_vez(a, b):
        estado["n"] += 1
        if estado["n"] == 1:
            raise PermissionError(13, "Acesso negado (antivírus segurando o arquivo recém-criado)")
        return real_replace(a, b)

    with cliente(tmp_path) as c:
        monkeypatch.setattr(envio.os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError(1, "Função incorreta")))
        monkeypatch.setattr(envio.os, "replace", recusa_uma_vez)
        monkeypatch.setattr("cs2clip.tools.time", type("SemEspera", (), {"sleep": staticmethod(lambda s: None), "__getattr__": lambda self, n: getattr(__import__("time"), n)})())
        r = _enviar(c, "live.mp4", b"V" * 1000)
        assert r.status_code == 200, r.text
        assert _entrada(tmp_path) == ["live.mp4"] and (tmp_path / "entrada" / "live.mp4").read_bytes() == b"V" * 1000
        assert estado["n"] == 2
