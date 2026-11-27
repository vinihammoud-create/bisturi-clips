/* E7 (Plano E): a tela Músicas. Lista o catálogo com licença, liga/desliga faixas, toca o trecho do drop e adiciona música nova SÓ com licença. */
(() => {
  "use strict";

  const token = document.querySelector('meta[name="ui-token"]')?.content || "";
  const $ = (seletor, raiz = document) => raiz.querySelector(seletor);
  const el = (tag, classe, texto) => {
    const node = document.createElement(tag);
    if (classe) node.className = classe;
    if (texto !== undefined && texto !== null) node.textContent = String(texto);
    return node;
  };
  const estado = { faixas: [], licencas: [], resumo: null, escolhida: null, tocando: null };

  async function api(caminho, opcoes = {}) {
    const metodo = (opcoes.method || "GET").toUpperCase();
    const cabecalhos = new Headers();
    cabecalhos.set("X-Token", token);                  // R-015: o servidor exige o token em todo pedido a /api/, GET incluído
    let corpo;
    if (opcoes.body !== undefined) { cabecalhos.set("Content-Type", "application/json"); corpo = JSON.stringify(opcoes.body); }
    const resposta = await fetch(caminho, { method: metodo, headers: cabecalhos, body: corpo, cache: "no-store" });
    const tipo = resposta.headers.get("content-type") || "";
    const conteudo = tipo.includes("application/json") ? await resposta.json() : { erro: "Não consegui concluir essa ação." };
    if (!resposta.ok) throw new Error(conteudo.erro || "Não consegui concluir essa ação.");
    return conteudo;
  }

  function aviso(mensagem, tipo = "") {
    const regiao = $("#toast-region");
    if (!regiao) return;
    const t = el("div", `toast ${tipo}`.trim(), mensagem);
    regiao.append(t);
    window.setTimeout(() => t.remove(), 5500);
  }

  function segundos(s) {
    if (s === null || s === undefined) return "–";
    const total = Math.round(s);                       // arredonda ANTES de separar minutos e segundos (antes dava "2:60")
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

  // ── a lista ──────────────────────────────────────────────────────────────────────────────────
  function linhaDaFaixa(f) {
    const linha = el("article", `musica ${f.ativa ? "" : "musica-off"}`.trim());
    const chave = el("label", "musica-liga");
    const caixa = document.createElement("input");
    caixa.type = "checkbox"; caixa.className = "switch"; caixa.checked = f.ativa; caixa.disabled = !f.existe;
    caixa.setAttribute("aria-label", `Usar ${f.titulo} nas montagens`);
    caixa.addEventListener("change", () => alternar(f.arquivo, caixa.checked));
    chave.append(caixa);
    const corpo = el("div", "musica-corpo");
    corpo.append(el("strong", "musica-titulo", f.titulo), el("span", "musica-autor", f.autor || "autor não informado"));
    const dados = el("div", "musica-dados");
    dados.append(el("span", "chip", `${f.bpm ? Math.round(f.bpm) : "?"} BPM`), el("span", f.tem_drop ? "chip chip-ok" : "chip chip-off", f.tem_drop ? `drop em ${segundos(f.drop_s)}` : "sem drop"),
      el("span", "chip", `${segundos(f.duracao_s)} de duração`), el("span", "chip", `emoção ${f.emocao === null || f.emocao === undefined ? "?" : Number(f.emocao).toFixed(2)}`));
    if (f.exige_credito) dados.append(el("span", "chip chip-credito", "pede crédito"));
    if (!f.existe) dados.append(el("span", "chip chip-erro", "arquivo sumiu"));
    if (f.ativa && f.entra_no_sorteio) dados.append(el("span", "chip chip-sorteio", `entra no sorteio · preferência nº ${f.posicao}`));
    else if (f.ativa) dados.append(el("span", "chip chip-off", `fora do sorteio: ${f.fora_do_sorteio || "sem drop"}`));
    corpo.append(dados, el("small", "musica-licenca", f.licenca || "SEM LICENÇA (não será usada)"));
    if (f.procedencia) corpo.append(el("small", "musica-procedencia", f.procedencia));
    if (f.aviso && (f.exige_credito || /Content ID/.test(f.aviso))) corpo.append(el("small", "musica-aviso", f.aviso));
    linha.append(chave, corpo);
    const ouvir = el("button", "button button-secondary", f.tem_drop ? "▶ Ouvir o drop" : "▶ Ouvir");
    ouvir.type = "button";
    if (f.existe) ouvir.addEventListener("click", () => tocar(f));
    else { ouvir.disabled = true; ouvir.title = "O arquivo desta faixa não está nesta pasta. Veja o aviso no alto da tela para saber como baixar ou adicionar."; }   // M-007
    linha.append(ouvir);
    return linha;
  }

  // Ligadas primeiro; entre elas, as que de fato entram no sorteio da montagem (na ordem de preferência: a de maior emoção primeiro); depois as demais por emoção.
  function ordenar(faixas) {
    const n = v => (v === null || v === undefined ? -1 : Number(v));
    return faixas.slice().sort((a, b) => (Number(!!b.ativa) - Number(!!a.ativa))
      || (Number(!!b.entra_no_sorteio) - Number(!!a.entra_no_sorteio))
      || ((a.posicao || 1e9) - (b.posicao || 1e9))
      || (n(b.emocao) - n(a.emocao))
      || String(a.titulo).localeCompare(String(b.titulo), "pt-BR"));
  }

  // M-007: num clone novo o catálogo vem listado mas os arquivos de áudio não. Sem aviso, parecia que o programa estava quebrado.
  function avisoSemMusicas(resumo, faixas) {
    const ausentes = (faixas || []).filter(f => !f.existe).length;
    if (!resumo || resumo.no_sorteio !== 0 || !ausentes) return "";
    return `Nenhuma música entra no sorteio agora: ${ausentes} das ${faixas.length} faixas do catálogo estão sem o arquivo de áudio neste computador. `
      + "Para ter músicas, baixe as faixas livres do catálogo: no programa instalado, abra o instalador de novo e marque a tarefa das músicas; na versão portátil, rode tools\\baixar_musicas_cc0.py. Ou adicione as suas na caixa abaixo, sempre com a licença.";
  }

  function desenhar() {
    const lista = $("#musicas-lista");
    if (!lista) return;
    const aviso = $("#musicas-aviso");
    if (aviso) { const texto = avisoSemMusicas(estado.resumo, estado.faixas); aviso.textContent = texto; aviso.classList.toggle("hidden", !texto); }
    lista.replaceChildren();
    if (!estado.faixas.length) lista.append(el("p", "drop-hint", "Nenhuma música no catálogo. Adicione uma abaixo (com a licença)."));
    ordenar(estado.faixas).forEach(f => lista.append(linhaDaFaixa(f)));
    const r = estado.resumo;
    $("#musicas-resumo").textContent = r ? `${r.total} músicas · ${r.ativas} ligadas · ${r.no_sorteio} entram no sorteio (têm drop; o primeiro tiro da montagem cai nele). `
      + "A lista segue a preferência da montagem: a de cima é a mais usada, as de baixo quase nunca tocam." : "";
  }

  async function carregar() {
    try {
      const r = await api("/api/musicas");
      estado.faixas = r.faixas || [];
      estado.licencas = r.licencas || [];
      estado.resumo = r.resumo || null;
      desenhar();
      montarFormulario();
    } catch (erro) { aviso(erro.message, "error"); }
  }

  async function alternar(arquivo, ativa) {
    try { await api("/api/musicas/ativar", { method: "POST", body: { arquivo, ativa } }); await carregar(); }
    catch (erro) { aviso(erro.message, "error"); await carregar(); }
  }

  function tocar(f) {
    const player = $("#musicas-player");
    if (!player) return;
    player.pause();
    player.src = `/api/musicas/audio/${encodeURIComponent(f.arquivo)}#t=${f.tem_drop ? Math.max(0, f.drop_s - 4) : 0}`;
    estado.tocando = f.arquivo;
    $("#musicas-tocando").textContent = `Tocando: ${f.titulo}${f.tem_drop ? ` (a partir de 4 s antes do drop)` : ""}`;
    $("#musicas-player-caixa")?.classList.remove("hidden");
    player.classList.remove("hidden");
    player.play().catch(() => { /* o navegador pode pedir um clique: o botão do player resolve */ });
  }

  // ── adicionar música (só com licença) ────────────────────────────────────────────────────────
  function licencaSelecionada() {
    const chave = $("#musica-licenca").value;
    return estado.licencas.find(l => l.chave === chave) || null;
  }

  function atualizarFormulario() {
    const lic = licencaSelecionada();
    $("#musica-link-rotulo").textContent = lic && lic.link_obrigatorio ? "Link da página da faixa (obrigatório)" : "Link da página da faixa (opcional)";
    $("#musica-credito-linha").classList.toggle("hidden", !(lic && lic.credito === null));
    const pede = !!lic && (lic.credito === true || (lic.credito === null && $("#musica-pede-credito").checked));
    $("#musica-credito-texto-linha").classList.toggle("hidden", !pede);
    const dica = $("#musica-licenca-dica");
    dica.textContent = lic ? `${lic.aviso || "Sem exigências especiais."}${lic.url ? ` Termos: ${lic.url}` : ""}` : "";
    $("#musica-adicionar").disabled = !estado.escolhida || !lic;
  }

  function montarFormulario() {
    const sel = $("#musica-licenca");
    if (!sel || sel.options.length > 1) return;
    sel.replaceChildren(new Option("Escolha a licença…", ""));
    estado.licencas.forEach(l => sel.append(new Option(l.nome, l.chave)));
    atualizarFormulario();
  }

  async function escolherArquivo() {
    try {
      const r = await api("/api/musicas/escolher", { method: "POST", body: {} });
      const item = (r.itens || [])[0];
      if (!item) return;
      estado.escolhida = item;
      $("#musica-arquivo").textContent = `${item.nome} (${(item.tamanho / 1e6).toFixed(1)} MB)`;
      if (!$("#musica-titulo").value) $("#musica-titulo").value = item.nome.replace(/\.[^.]+$/, "");
      atualizarFormulario();
    } catch (erro) { aviso(erro.message, "error"); }
  }

  async function adicionar() {
    if (!estado.escolhida) return;
    const lic = licencaSelecionada();
    const corpo = { caminho: estado.escolhida.caminho, licenca: lic ? lic.chave : "", autor: $("#musica-autor").value.trim(),
      titulo: $("#musica-titulo").value.trim(), link: $("#musica-link").value.trim() };
    if (lic && lic.credito === null) corpo.exige_credito = $("#musica-pede-credito").checked;
    const texto = $("#musica-credito-texto").value.trim();
    if (texto) corpo.credito_texto = texto;
    $("#musica-adicionar").disabled = true;
    $("#musica-resultado").textContent = "Analisando a música (BPM, drop, energia)…";
    try {
      const r = await api("/api/musicas/adicionar", { method: "POST", body: corpo });
      $("#musica-resultado").textContent = `Pronto: ${r.faixa.titulo} entrou no catálogo${r.faixa.drop_s ? ` (drop em ${segundos(r.faixa.drop_s)})` : " (sem drop: só toca desde o começo)"}.`;
      estado.escolhida = null;
      ["#musica-autor", "#musica-titulo", "#musica-link", "#musica-credito-texto"].forEach(s => { $(s).value = ""; });
      $("#musica-arquivo").textContent = "nenhum arquivo escolhido";
      await carregar();
    } catch (erro) { $("#musica-resultado").textContent = erro.message; }
    atualizarFormulario();
  }

  function mostrar() { carregar(); }

  function iniciar() {
    if (!$("#view-musicas")) return;
    $("#musica-escolher")?.addEventListener("click", escolherArquivo);
    $("#musica-adicionar")?.addEventListener("click", adicionar);
    $("#musica-licenca")?.addEventListener("change", atualizarFormulario);
    $("#musica-pede-credito")?.addEventListener("change", atualizarFormulario);
  }

  window.BisturiMusicas = { mostrar, carregar };
  document.addEventListener("DOMContentLoaded", iniciar);

  if (typeof module === "object" && module && module.exports) {
    module.exports = { segundos, linhaDaFaixa, ordenar, estado, avisoSemMusicas };
  }
})();
