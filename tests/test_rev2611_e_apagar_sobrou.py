"""R-009 na tela (lote E): quando 'Apagar kit' responde sobrou: true, a tela avisa que parte dos arquivos estava em uso e será limpa na próxima abertura."""
from test_r1_ui import rodar_js

_CENARIO = """
  const resp = corpo => ({ ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => corpo });
  cfg.fetch = async (url, op) => resp(url.endsWith("/apagar") ? %s : { itens: [], progresso: {} });
  win.respostaConfirm = true;
  document.querySelector("#result-title").textContent = "Partida X";
  await ui.apagarKit("v1", "Partida X");
  await espera();
  return $("#toast-region").children.map(textoDe);
"""


def test_aviso_quando_sobrou_arquivo_em_uso(tmp_path):
    r = rodar_js(tmp_path, _CENARIO % '{ apagado: "v1", liberado: 5242880, sobrou: true }')
    texto = " ".join(r)
    assert "Kit apagado" in texto and "5 MB" in texto
    assert "em uso" in texto and "próxima vez que abrir" in texto               # diz o que sobrou e quando some


def test_sem_sobra_a_mensagem_continua_a_de_antes(tmp_path):
    r = rodar_js(tmp_path, _CENARIO % '{ apagado: "v1", liberado: 5242880, sobrou: false }')
    texto = " ".join(r)
    assert "Kit apagado" in texto and "em uso" not in texto
    r = rodar_js(tmp_path, _CENARIO % '{ apagado: "v1", liberado: 0 }')            # resposta sem o campo (servidor antigo)
    assert "em uso" not in " ".join(r)
