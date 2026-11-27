"""QB2 (segurança que sobrou da interface local): nomes reservados do Windows, tetos de tamanho/profundidade e pedidos de outro site.

Cada teste de conserto tem um CONTROLE (o caso legítimo continua aceito). Nada aqui abre arquivo com nome de dispositivo do Windows.
"""
import json
from pathlib import Path
from urllib.parse import quote

import pytest

from test_ui_dados_seguranca import PORT, cliente, criar_projeto, token_headers
from ui.seguranca import nome_arquivo_seguro

MB = 1024 * 1024


def _enviar(c, nome, dados, **kw):
    """Envio de um arquivo pela rota em fluxo (B3): PUT com os bytes no corpo."""
    return c.put("/api/upload/arquivo?nome=" + quote(nome), headers=token_headers(c), content=dados, **kw)


def _limites(**mudancas):
    """Os tetos de envio do app, trocados por valores pequenos para o teste não precisar de gigabytes."""
    import dataclasses
    from ui.seguranca import LimitesEnvio
    return dataclasses.replace(LimitesEnvio(), **mudancas)


# ── 1. nomes reservados do Windows com espaço/ponto antes da extensão ───────────────────────────

RESERVADOS = ["con .mp4", "CON.mp4", "nul..mkv", "aux .dem", "COM1 .mp4", "LPT9. .mp4", "prn  .mp4", "Con .MP4", "com9 .mkv", "conin$.mp4"]
LEGITIMOS = ["console.mp4", "contato.mp4", "comunicado.mp4", "com10.mp4", "auxiliar.dem", "nulo.mkv", "lpt10.mp4", "partida con.mp4", "x.con.mp4"]


@pytest.mark.parametrize("nome", RESERVADOS)
def test_nome_reservado_do_windows_e_neutralizado_mesmo_com_espaco_ou_ponto(nome):
    seguro = nome_arquivo_seguro(nome)
    assert seguro.startswith("_"), f"{nome!r} virou {seguro!r}: o Windows ainda o trataria como dispositivo"
    assert Path(seguro).suffix.lower() == Path(nome).suffix.lower()      # a extensão continua valendo para a checagem de tipo


@pytest.mark.parametrize("nome", LEGITIMOS)
def test_controle_nome_normal_nao_e_mexido(nome):
    assert nome_arquivo_seguro(nome) == nome


def test_upload_de_nome_reservado_vai_para_nome_seguro_e_o_normal_fica_igual(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        for ruim in ("con .mp4", "aux .dem"):
            r = _enviar(c, ruim, b"x")
            assert r.status_code == 200, r.text
            assert r.json()["itens"][0]["nome"].startswith("_"), r.json()
        r = _enviar(c, "console.mp4", b"x")
        assert r.status_code == 200 and r.json()["itens"][0]["nome"] == "console.mp4"


def test_fila_recusa_nome_reservado_da_pasta_entrada_e_aceita_o_normal(tmp_path):
    criar_projeto(tmp_path)
    (tmp_path / "entrada" / "contato.mp4").write_bytes(b"x")
    with cliente(tmp_path) as c:
        r = c.post("/api/fila", headers=token_headers(c), json={"arquivos": ["entrada:con .mp4"]})
        assert r.status_code == 400, r.text
        r = c.post("/api/fila", headers=token_headers(c), json={"arquivos": ["entrada:contato.mp4"], "opcoes": {}})
        assert r.status_code != 400, r.text           # o caso legítimo passa da validação do nome (o resto é com a fila)


def _musica_falsa(tmp_path, nome):
    """Devolve um app cujo seletor de música 'escolheu' um caminho que NÃO existe (nunca cria arquivo com nome de dispositivo)."""
    criar_projeto(tmp_path)
    caminho = str(tmp_path / nome)
    return cliente(tmp_path, music_chooser=lambda: [{"caminho": caminho, "nome": nome, "tamanho": 1}]), caminho


@pytest.mark.parametrize("nome", ["con .mp3", "CON.mp3", "nul..wav", "aux .flac", "COM1 .ogg"])
def test_musica_com_nome_reservado_e_recusada_antes_de_copiar(tmp_path, nome):
    c, caminho = _musica_falsa(tmp_path, nome)
    with c:
        assert c.post("/api/musicas/escolher", headers=token_headers(c)).status_code == 200
        r = c.post("/api/musicas/adicionar", headers=token_headers(c), json={"caminho": caminho, "licenca": "cc0", "autor": "Fulano"})
        assert r.status_code == 400 and "reservado" in r.json()["erro"], r.text


def test_controle_musica_com_nome_normal_passa_da_checagem_de_nome(tmp_path):
    c, caminho = _musica_falsa(tmp_path, "contato.mp3")
    with c:
        c.post("/api/musicas/escolher", headers=token_headers(c))
        r = c.post("/api/musicas/adicionar", headers=token_headers(c), json={"caminho": caminho, "licenca": "cc0", "autor": "Fulano"})
        assert "reservado" not in r.text           # falha depois (arquivo não existe), mas não pela checagem de nome


# ── 2a. upload: teto por arquivo e total, sem sobra no disco ────────────────────────────────────

def _entrada_vazia(tmp_path):
    return [p.name for p in (tmp_path / "entrada").iterdir()]


def test_upload_acima_do_teto_do_arquivo_e_recusado_e_nao_deixa_sobra(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        c.app_state.limites = _limites(video_arquivo=1 * MB)
        grande = b"V" * (3 * MB)
        r = _enviar(c, "live.mp4", grande)
        assert r.status_code == 413, r.text
        assert "grande" in r.json()["erro"]
        assert _entrada_vazia(tmp_path) == []         # nem o final nem o .part parcial
        # controle: o mesmo arquivo dentro do teto entra normalmente
        ok = _enviar(c, "live.mp4", b"V" * (MB // 2))
        assert ok.status_code == 200 and ok.json()["itens"][0]["tamanho"] == MB // 2


def test_upload_de_demo_tem_teto_menor_que_o_de_video(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        c.app_state.limites = _limites(video_arquivo=10 * MB, demo_arquivo=1 * MB)
        r = _enviar(c, "partida.dem", b"D" * (2 * MB))
        assert r.status_code == 413, r.text
        assert _entrada_vazia(tmp_path) == []
        ok = _enviar(c, "outra.mp4", b"V" * (2 * MB))
        assert ok.status_code == 200


# ── 2b. corpo JSON: teto de tamanho e de profundidade ──────────────────────────────────────────

def test_corpo_json_gigante_e_recusado_e_o_normal_passa(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        gigante = json.dumps({"texto": "a" * (3 * MB)})
        r = c.post("/api/entrada/reconhecer", headers={**token_headers(c), "Content-Type": "application/json"}, content=gigante)
        assert r.status_code == 413, r.status_code
        assert "grande" in r.json()["erro"]
        ok = c.post("/api/entrada/reconhecer", headers=token_headers(c), json={"texto": "https://www.twitch.tv/videos/123456789"})
        assert ok.status_code == 200


def test_corpo_json_gigante_sem_content_length_tambem_e_recusado(tmp_path):
    criar_projeto(tmp_path)

    def pedacos():
        for _ in range(3):
            yield b'{"texto": "' if _ == 0 else b""
            yield b"a" * MB
        yield b'"}'

    with cliente(tmp_path) as c:
        r = c.post("/api/entrada/reconhecer", headers={**token_headers(c), "Content-Type": "application/json"}, content=pedacos())
        assert r.status_code == 413, r.status_code


def test_json_muito_aninhado_e_recusado_e_o_normal_passa(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        cab = {**token_headers(c), "Content-Type": "application/json"}
        fundo = '{"texto": ' + "[" * 200 + "]" * 200 + "}"
        r = c.post("/api/entrada/reconhecer", headers=cab, content=fundo)
        assert r.status_code == 400, r.text
        assert "aninhad" in r.json()["erro"]
        # controle 1: aninhamento comum (config e momentos têm 2-3 níveis)
        raso = c.post("/api/entrada/reconhecer", headers=cab, content='{"texto": "x", "extra": {"a": {"b": [1, 2, {"c": 3}]}}}')
        assert raso.status_code == 200, raso.text
        # controle 2: colchetes DENTRO de texto não contam como profundidade
        # (usa /api/janela-fechada, que ignora o corpo; /api/entrada/reconhecer tem um 500 antigo com "[" solto, fora do escopo do QB2)
        texto = c.post("/api/janela-fechada", headers=cab, content=json.dumps({"texto": "[{" * 250 + "\\\"" + "}]" * 250}))
        assert texto.status_code == 200, texto.text


# ── 2c. receitas/trabalhos: teto de texto e de itens ───────────────────────────────────────────

def test_trabalho_com_texto_enorme_e_recusado_e_o_normal_passa(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        enorme = "https://www.twitch.tv/videos/123456789?" + "a" * 20000
        r = c.post("/api/trabalhos", headers=token_headers(c), json={"texto": enorme})
        assert r.status_code == 400 and "grande" in r.json()["erro"], r.text
        assert c.post("/api/entrada/reconhecer", headers=token_headers(c), json={"texto": enorme}).json()["entrada"]["aceita"] is False
        ok = c.post("/api/entrada/reconhecer", headers=token_headers(c), json={"texto": "https://www.twitch.tv/videos/123456789"})
        assert ok.json()["entrada"]["aceita"] is True


def test_receitas_vivas_tem_teto(tmp_path):
    import ui.receitas as receitas_mod
    criar_projeto(tmp_path)
    with cliente(tmp_path, receita_popen_factory=lambda *a, **k: (_ for _ in ()).throw(OSError("sem processo"))) as c:
        gerenciador = c.app_state.receitas
        for i in range(receitas_mod.LIMITE_VIVAS):
            gerenciador._receitas[f"r{i}"] = {"estado": "esperando_voce", "criada_em": float(i), "etapas": [], "opcoes": {}, "entrada": {}, "dados": {}}
        r = c.post("/api/trabalhos", headers=token_headers(c), json={"texto": "https://www.twitch.tv/videos/123456789"})
        assert r.status_code == 400 and "trabalhos" in r.json()["erro"], r.text


def test_fila_com_lista_de_arquivos_gigante_e_recusada(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = c.post("/api/fila", headers=token_headers(c), json={"arquivos": [f"entrada:v{i}.mp4" for i in range(500)]})
        assert r.status_code == 400 and "arquivos" in r.json()["erro"], r.text
        ok = c.post("/api/fila", headers=token_headers(c), json={"arquivos": ["entrada:nao_existe.mp4"]})
        assert ok.status_code == 404                  # controle: lista pequena segue o caminho normal


# ── 3. pedido vindo de outro site ──────────────────────────────────────────────────────────────

def test_sec_fetch_site_cross_site_e_recusado_e_os_outros_valores_passam(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        assert c.get("/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert c.post("/api/fila/cancelar-tudo", headers={**token_headers(c), "Sec-Fetch-Site": "cross-site"}, json={}).status_code == 403
        for valor in ("same-origin", "none", "same-site"):
            assert c.get("/health", headers={"Sec-Fetch-Site": valor}).status_code == 200, valor
        assert c.get("/health").status_code == 200                       # sem o cabeçalho (linha de comando, testes)
        assert c.get("/", headers={"Sec-Fetch-Site": "none"}).status_code == 200      # a janela abrindo a página


@pytest.mark.parametrize("origem", ["null", "https://evil.example", f"http://localhost:{PORT + 1}", f"http://127.0.0.1.evil.example:{PORT}",
                                    f"http://user@127.0.0.1:{PORT}", f"https://127.0.0.1:{PORT}"])
@pytest.mark.parametrize("metodo", ["post", "put", "delete"])
def test_origin_de_outro_site_e_recusado_em_post_put_e_delete(tmp_path, origem, metodo):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = c.request(metodo.upper(), "/api/fila/cancelar-tudo", headers={**token_headers(c), "Origin": origem})
        assert r.status_code == 403, (origem, r.status_code)


def test_controle_origin_do_proprio_servidor_ou_ausente_continua_valendo(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        for origem in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}", None):
            cab = dict(token_headers(c))
            if origem:
                cab["Origin"] = origem
            assert c.post("/api/fila/cancelar-tudo", headers=cab, json={}).status_code == 200, origem


# ── tentativa 2: regex quadrática, multipart em rota JSON, lacunas de teste, "[" solto na caixa única ───────────

@pytest.mark.parametrize("nome", ["COM¹ .mp4", "com².mkv", "COM³.mp4", "LPT¹ .mp4", "lpt².dem", "LPT³.mp4"])
def test_nome_reservado_com_sobrescrito_e_neutralizado(nome):
    assert nome_arquivo_seguro(nome).startswith("_")


def test_controle_nome_parecido_com_sobrescrito_nao_e_mexido():
    assert nome_arquivo_seguro("COM¹⁰.mp4") == "COM¹⁰.mp4" and nome_arquivo_seguro("comunidade.mp4") == "comunidade.mp4"


ADVERSARIOS = {
    "aspa_aberta_e_escapes": b'"' + b'\\"' * (MB // 2),
    "so_aspas": b'"' * MB,
    "so_barras": b"\\" * MB,
    "colchetes_dentro_de_texto": b'{"t": "' + b"[" * MB + b'"}',
    "aspa_aberta_e_colchetes": b'"' + b"[" * MB,
}


def _melhor_tempo(corpo, repeticoes=5):
    """Menor tempo de `profundidade_json(corpo)` em `repeticoes` voltas (o menor tira o ruído de uma máquina ocupada)."""
    import time
    from ui.seguranca import profundidade_json
    melhor = float("inf")
    for _ in range(repeticoes):
        t0 = time.perf_counter()
        profundidade_json(corpo)
        melhor = min(melhor, time.perf_counter() - t0)
    return melhor


@pytest.mark.parametrize("nome", list(ADVERSARIOS))
def test_profundidade_de_corpo_adversario_cresce_em_tempo_linear(nome):
    """R-029: em vez de cravar segundos (que medem a máquina), compara o tempo de 256 KiB com o de 1 MiB (4x mais bytes).
    Linear dá razão perto de 4; uma varredura quadrática daria perto de 16. O teto de 8 separa as duas com folga para o ruído."""
    pequeno, grande = ADVERSARIOS[nome][: MB // 4], ADVERSARIOS[nome]
    assert len(grande) >= 3 * len(pequeno)
    # Até 3 medições: sob carga (suíte em paralelo, o gancho de envio) o trecho pequeno, que leva poucos milissegundos, tem ruído e a razão pode
    # passar do teto uma vez (visto em 26/11). Uma varredura quadrática daria ~16x em TODAS as 3 e continuaria reprovando.
    razoes = []
    for _ in range(3):
        razoes.append(_melhor_tempo(grande, repeticoes=7) / max(_melhor_tempo(pequeno, repeticoes=7), 1e-6))
        if razoes[-1] < 8:
            break
    assert razoes[-1] < 8, f"{nome}: o tempo cresceu {[round(r, 1) for r in razoes]}x para 4x mais bytes (a varredura não é linear)"


def test_profundidade_continua_certa_nos_controles():
    from ui.seguranca import profundidade_json
    assert profundidade_json(b'{"a": "[[[[" , "b": "\\"[[", "c": [1, {"d": 2}]}') == 3
    assert profundidade_json(b"[" * 200 + b"]" * 200) == 200
    assert profundidade_json(b'{"a": {"b": [1, 2, {"c": 3}]}}') == 4
    assert profundidade_json(b'"fecha \\\\" [[') == 2          # a barra dupla fecha o escape; a aspa seguinte encerra o texto
    assert profundidade_json(b"") == 0
    assert profundidade_json(b'{"a": "\\"[[[["}') == 1          # a aspa escapada NÃO fecha o texto: os colchetes depois dela são texto (QA, mutação N2)
    assert profundidade_json(b'{"a": "x\\\\\\"[[[["}') == 1     # barra dupla + aspa escapada: continua dentro do texto
    assert profundidade_json(b"]]]]]" + b"[" * 40) == 40        # fechamento a mais não vira profundidade negativa (QA, mutação N5)


def test_corpo_que_nao_e_utf8_e_recusado_e_nao_contorna_a_profundidade(tmp_path):
    """QA (2ª rodada): em UTF-16 a letra U+0122 tem o byte 0x22 (a aspa); a contagem por bytes se perdia e 100 níveis passavam."""
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        cab = {**token_headers(c), "Content-Type": "application/json"}
        fundo = '{"texto": "xĢ", "x": ' + "[" * 100 + "]" * 100 + "}"
        for codificado in (fundo.encode("utf-16-le"), fundo.encode("utf-16"), fundo.encode("utf-32"), b'{"texto": "\xff\xfe"}'):
            r = c.post("/api/entrada/reconhecer", headers=cab, content=codificado)
            assert r.status_code == 400, (r.status_code, r.text)
            assert "UTF-8" in r.json()["erro"]
        # controles: UTF-8 com acento e emoji passa; o mesmo JSON fundo em UTF-8 é recusado pela profundidade; corpo vazio continua valendo
        ok = c.post("/api/entrada/reconhecer", headers=cab, content='{"texto": "coração Ģ 🔥"}'.encode("utf-8"))
        assert ok.status_code == 200, ok.text
        assert "aninhad" in c.post("/api/entrada/reconhecer", headers=cab, content=fundo.encode("utf-8")).json()["erro"]
        assert c.post("/api/janela-fechada", headers=cab, content=b"").status_code == 200


def test_rota_json_com_corpo_adversario_responde_sem_travar(tmp_path):
    """R-029: o pedido roda numa thread e o teste espera por ela com um prazo largo (60 s). O prazo é só a rede de segurança contra um
    travamento de verdade; a velocidade da varredura é provada pela razão de tempos do teste acima, não por segundos de parede aqui."""
    import threading
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        resposta = []

        def pedir():
            resposta.append(c.post("/api/entrada/reconhecer", headers={**token_headers(c), "Content-Type": "application/json"},
                                   content=ADVERSARIOS["aspa_aberta_e_escapes"][: MB - 1024]))
        t = threading.Thread(target=pedir, daemon=True)
        t.start()
        t.join(timeout=60)
        assert not t.is_alive(), "a rota não respondeu ao corpo adversário no prazo"
        assert resposta[0].status_code in (400, 422)


def _multipart(nome, conteudo, boundary="bnd"):
    cab = f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{nome}"\r\nContent-Type: video/mp4\r\n\r\n'.encode()
    return cab, conteudo, f"\r\n--{boundary}--\r\n".encode()


def test_multipart_em_rota_json_tem_o_teto_de_json_e_o_upload_continua_aceito(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        ini, meio, fim = _multipart("x.mp4", b"V" * (3 * MB))
        corpo = ini + meio + fim
        cab = {**token_headers(c), "Content-Type": "multipart/form-data; boundary=bnd"}
        r = c.post("/api/entrada/reconhecer", headers=cab, content=corpo)
        assert r.status_code == 413, r.status_code
        assert "grande" in r.json()["erro"]
        # sem Content-Length também
        r = c.post("/api/entrada/reconhecer", headers=cab, content=iter([ini, meio, fim]))
        assert r.status_code == 413, r.status_code
        # controle: o upload de verdade, com o mesmo tamanho, entra
        ok = _enviar(c, "live.mp4", b"V" * (3 * MB))
        assert ok.status_code == 200 and ok.json()["itens"][0]["tamanho"] == 3 * MB


def test_texto_com_colchete_solto_vira_nao_reconhecido_em_portugues(tmp_path):
    from cs2clip import entrada as ent
    for texto in ("a[b", "http://[abc", "[x"):
        e = ent.reconhecer(texto, [])
        assert e.tipo == "desconhecido" and not e.aceita and "Não entendi" in e.mensagem, (texto, e)
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = c.post("/api/entrada/reconhecer", headers=token_headers(c), json={"texto": "a[b"})
        assert r.status_code == 200 and r.json()["entrada"]["aceita"] is False
        r = c.post("/api/trabalhos", headers=token_headers(c), json={"texto": "http://[abc"})
        assert r.status_code == 400 and "Invalid" not in r.text and "Não entendi" in r.json()["erro"], r.text


def test_controle_link_da_twitch_e_caminho_normal_continuam_reconhecidos(tmp_path):
    from cs2clip import entrada as ent
    assert ent.reconhecer("https://www.twitch.tv/videos/123456789", []).tipo == "twitch_vod"
    video = tmp_path / "partida.mp4"
    video.write_bytes(b"x" * 10)
    e = ent.reconhecer(str(video), [])
    assert e.tipo == "video" and e.aceita
