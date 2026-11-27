"""M-005 (rev2611, lote E): ajuste fino, motivo e avaliação só mudam a tela quando o servidor aceitou o voto; em falha, o controle volta ao que era."""
from test_r1_ui import rodar_js

_PREPARO = """
  const resp = (status, corpo) => ({ ok: status < 400, status, headers: { get: () => "application/json" }, json: async () => corpo });
  const falha = %s;
  const posts = [];
  cfg.fetch = async (url, op) => { posts.push({ url, corpo: op.body }); if (falha === "rede") throw new Error("sem conexão");
    return falha ? resp(409, { erro: "Já tem um processamento rodando." }) : resp(200, { ok: true }); };
  ui.state.videoAtual = "v1";
  const toasts = () => $("#toast-region").children.map(textoDe);
"""


def _cenario(falha: str, corpo: str) -> str:
    return _PREPARO % falha + corpo


def test_ajuste_fino_nao_muda_nada_quando_o_servidor_recusa(tmp_path):
    for falha in ("true", '"rede"'):
        r = rodar_js(tmp_path, _cenario(falha, """
          const m = { foco: 12, ajuste: null };
          const d = ui.blocoAjuste(m);
          const botao = acharTag(d, "BUTTON").find(b => textoDe(b).includes("início +0,5"));
          botao.dispatch("click"); await espera();
          return { ajuste: m.ajuste, pendentes: ui.state.pendentesAplicar["v1"] ? ui.state.pendentesAplicar["v1"].size : 0, erro: toasts().length,
                   texto: textoDe(d), posts: posts.length };
        """))
        assert r["ajuste"] is None and r["pendentes"] == 0 and r["posts"] == 1 and r["erro"] >= 1, falha
        assert "início 0 s · fim 0 s" in r["texto"], r["texto"]                      # o texto do estado não passou para +0,5


def test_ajuste_fino_guarda_quando_o_servidor_aceita(tmp_path):
    r = rodar_js(tmp_path, _cenario("false", """
      const m = { foco: 12, ajuste: null };
      const d = ui.blocoAjuste(m);
      acharTag(d, "BUTTON").find(b => textoDe(b).includes("fim -0,5")).dispatch("click"); await espera();
      return { ajuste: m.ajuste, pendentes: Array.from(ui.state.pendentesAplicar["v1"] || []) };
    """))
    assert r["ajuste"] == {"inicio": 0, "fim": -0.5} and r["pendentes"] == ["12"]


def test_motivo_de_rejeicao_volta_ao_valor_anterior_quando_falha(tmp_path):
    r = rodar_js(tmp_path, _cenario("true", """
      const m = { foco: 12, motivo_rejeicao: "comum" };
      const bloco = ui.blocoMotivoRejeicao(m);
      const sel = acharTag(bloco, "SELECT")[0];
      sel.value = "repetido"; sel.dispatch("change"); await espera();
      return { valor: sel.value, motivo: m.motivo_rejeicao };
    """))
    assert r == {"valor": "comum", "motivo": "comum"}


def test_motivo_de_rejeicao_guarda_quando_aceita_e_limpa_com_vazio(tmp_path):
    r = rodar_js(tmp_path, _cenario("false", """
      const m = { foco: 12, motivo_rejeicao: null };
      const sel = acharTag(ui.blocoMotivoRejeicao(m), "SELECT")[0];
      sel.value = "repetido"; sel.dispatch("change"); await espera();
      const aceito = m.motivo_rejeicao;
      sel.value = ""; sel.dispatch("change"); await espera();
      return { aceito, limpo: m.motivo_rejeicao, valor: sel.value };
    """))
    assert r == {"aceito": "repetido", "limpo": None, "valor": ""}


def test_avaliacao_desfaz_os_seletores_quando_falha_e_guarda_quando_aceita(tmp_path):
    r = rodar_js(tmp_path, _cenario("true", """
      const m = { foco: 12 };
      const d = ui.blocoAvaliacao(m);
      const sels = acharTag(d, "SELECT");
      sels[0].value = "2"; sels[0].dispatch("change"); await espera();
      return { valores: sels.map(s => s.value), avaliacao: m.avaliacao === undefined ? null : m.avaliacao };
    """))
    assert r["valores"] == [""] * 6 and r["avaliacao"] is None
    r = rodar_js(tmp_path, _cenario("false", """
      const m = { foco: 12 };
      const sels = acharTag(ui.blocoAvaliacao(m), "SELECT");
      sels[0].value = "2"; sels[0].dispatch("change"); await espera();
      sels[1].value = "3"; sels[1].dispatch("change"); await espera();
      return { valores: sels.map(s => s.value).slice(0, 2), avaliacao: m.avaliacao };
    """))
    assert r["valores"] == ["2", "3"] and r["avaliacao"] == {"impacto": 2, "clareza": 3}


def test_enviar_voto_devolve_verdadeiro_ou_falso(tmp_path):
    r = rodar_js(tmp_path, _cenario("false", "return await ui.enviarVotoDetalhe({ foco: 1 }, { acao: 'motivo', motivo: 'comum' });"))
    assert r is True
    r = rodar_js(tmp_path, _cenario("true", "return await ui.enviarVotoDetalhe({ foco: 1 }, { acao: 'motivo', motivo: 'comum' });"))
    assert r is False
