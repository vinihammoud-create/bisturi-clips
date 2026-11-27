/* E1/E2 (Plano E): a caixa única da tela Início, o cartão do plano e a tela Trabalhos. Fala só com /api/entrada/* e /api/trabalhos/*. */
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

  const TIPOS = {
    demo: "Demo do CS2", codigo_partilha: "Código de partida do CS2", twitch_vod: "VOD da Twitch", youtube: "Vídeo do YouTube",
    video: "Vídeo", pasta: "Pasta de vídeos", recusado: "Não posso usar isso", desconhecido: "Não entendi"
  };
  const ESTADOS = {
    na_fila: "Na fila", rodando: "Rodando", esperando_voce: "Esperando você", pronta: "Pronta", erro: "Parou",
    cancelada: "Cancelada", interrompida: "Interrompida"
  };
  const SIMBOLOS = { pendente: "○", rodando: "●", esperando_voce: "◐", ok: "✓", erro: "✕", cancelada: "–" };

  const estado = { texto: "", plano: null, trabalhos: [], timer: null, debounce: null, pedido: 0 };

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
    window.setTimeout(() => t.remove(), 5000);
  }

  function minutos(segundos) {
    if (!segundos) return "";
    const m = Math.max(1, Math.round(segundos / 60));
    return `≈ ${m} min`;
  }

  // ── a caixa única ────────────────────────────────────────────────────────────────────────────
  function montarPlano(r) {
    const caixa = $("#caixa-plano");
    caixa.replaceChildren();
    caixa.classList.remove("hidden");
    const e = r.entrada;
    const cartao = el("div", `plano-cartao ${e.aceita ? "plano-ok" : "plano-recusa"}`);
    cartao.append(el("span", "plano-tipo", TIPOS[e.tipo] || e.tipo));
    if (e.detalhes && e.detalhes.nome) cartao.append(el("strong", "plano-nome", e.detalhes.nome));
    if (e.mensagem) cartao.append(el("p", e.aceita ? "plano-aviso" : "plano-motivo", e.mensagem));
    if (e.aceita) {
      if (r.demo && r.demo.rounds && r.demo.rounds.length) {
        cartao.append(el("p", "plano-demo", `${r.demo.partida} · rounds bons: ${r.demo.rounds.join(", ")} (≈ ${r.demo.minutos_de_jogo} min de jogo)`));
      } else if (e.tipo === "demo") {
        cartao.append(el("p", "plano-aviso", "Não achei rounds com 3 ou mais abates seus nessa demo (ou ela não abre). Dá para tentar mesmo assim."));
      }
      const lista = el("ol", "plano-etapas");
      (r.etapas || []).forEach(et => {
        const item = el("li", "", et.titulo);
        if (et.estimativa_s) item.append(el("small", "", ` ${minutos(et.estimativa_s)}`));
        lista.append(item);
      });
      cartao.append(lista);
      if (r.estimativa_s) cartao.append(el("p", "plano-total", `Tempo total: ${minutos(r.estimativa_s)}. Você só precisa estar por perto na troca de conta do Steam.`));
      if (["video", "twitch_vod", "youtube"].includes(e.tipo)) {
        const rotulo = el("label", "plano-opcao");
        const marca = document.createElement("input");
        marca.type = "checkbox"; marca.checked = true; marca.id = "plano-limpo";
        rotulo.append(marca, el("span", "", " Se o Bisturi achar a demo da partida, regravar os rounds bons em imagem limpa e juntar a minha voz (precisa trocar o Steam para a conta do Bisturi)"));
        cartao.append(rotulo);
      }
      const botao = el("button", "button button-primary", "Começar");
      botao.type = "button";
      botao.addEventListener("click", () => comecar(e.tipo));
      cartao.append(botao);
    }
    caixa.append(cartao);
  }

  async function reconhecer() {
    const texto = $("#caixa-texto").value.trim();
    estado.texto = texto;
    const caixa = $("#caixa-plano");
    if (!texto) { caixa.classList.add("hidden"); caixa.replaceChildren(); estado.plano = null; return; }
    const meu = ++estado.pedido;
    caixa.classList.remove("hidden");
    caixa.replaceChildren(el("p", "drop-hint", "Reconhecendo…"));
    try {
      const r = await api("/api/entrada/reconhecer", { method: "POST", body: { texto } });
      if (meu !== estado.pedido) return;                          // chegou uma resposta mais nova
      estado.plano = r;
      montarPlano(r);
    } catch (erro) { if (meu === estado.pedido) caixa.replaceChildren(el("p", "plano-motivo", erro.message)); }
  }

  async function comecar() {
    if (!estado.plano || !estado.plano.entrada.aceita) return;
    if (window.BisturiModelo && await window.BisturiModelo.bloqueia()) return;   // P4.2b: sem o modelo de voz, explica e leva ao cartão (app.js)
    try {
      const limpo = $("#plano-limpo");
      const t = await api("/api/trabalhos", { method: "POST", body: { texto: estado.texto, limpo: limpo ? !!limpo.checked : true } });
      aviso("Trabalho começou. Acompanhe em Trabalhos.", "success");
      $("#caixa-texto").value = "";
      $("#caixa-plano").classList.add("hidden");
      estado.plano = null;
      await atualizar();
      if (window.location.hash !== "#trabalhos") $("#nav-trabalhos")?.click();
      return t;
    } catch (erro) { aviso(erro.message, "error"); }
  }

  async function escolherArquivo() {
    try {
      const r = await api("/api/escolher-arquivo", { method: "POST", body: {} });
      const item = (r.itens || [])[0];
      if (item && item.caminho) { $("#caixa-texto").value = item.caminho; await reconhecer(); }
    } catch (erro) { aviso(erro.message, "error"); }
  }

  async function ultimaPartida() {
    try {
      const r = await api("/api/entrada/ultima-demo");
      if (!r.demo) { aviso("Não achei nenhuma demo na pasta do CS2. No jogo: Assistir → Suas partidas → Baixar.", "error"); return; }
      $("#caixa-texto").value = r.demo.caminho;
      await reconhecer();
    } catch (erro) { aviso(erro.message, "error"); }
  }

  // ── os trabalhos ─────────────────────────────────────────────────────────────────────────────
  function cartaoTrabalho(t) {
    const cartao = el("article", `trabalho trabalho-${t.estado}`);
    const topo = el("header", "trabalho-topo");
    topo.append(el("strong", "trabalho-titulo", t.titulo), el("span", `selo-estado selo-${t.estado}`, ESTADOS[t.estado] || t.estado));
    cartao.append(topo);
    const etapas = el("ol", "trabalho-etapas");
    t.etapas.forEach(et => {
      const item = el("li", `etapa etapa-${et.estado}`);
      item.append(el("span", "etapa-simbolo", SIMBOLOS[et.estado] || "○"), el("span", "etapa-titulo", et.titulo));
      if (et.detalhe) {
        const small = el("small", "etapa-detalhe");
        const m = et.nome === "aguardar_download" ? /(https:\/\/studio\.youtube\.com\/video\/[A-Za-z0-9_-]{11}\/edit)/.exec(et.detalhe) : null;
        if (m) {                                                          // só este endereço do YouTube Studio vira link (e só nesta etapa)
          const a = el("a", "etapa-link", "Abrir o vídeo no YouTube Studio");
          a.href = m[1]; a.target = "_blank"; a.rel = "noopener noreferrer";
          small.append(el("span", "", "Em Opções (⋮) → Baixar. Estou vigiando a pasta Downloads. "), a);
        } else {
          small.textContent = et.detalhe;
        }
        item.append(small);
      }
      etapas.append(item);
    });
    cartao.append(etapas);
    if (t.erro) cartao.append(el("p", "trabalho-erro", t.erro));
    const acoes = el("div", "trabalho-acoes");
    if (["na_fila", "rodando", "esperando_voce"].includes(t.estado)) {
      const b = el("button", "button button-quiet", "Cancelar");
      b.type = "button";
      b.addEventListener("click", () => acaoTrabalho(t.id, "cancelar"));
      acoes.append(b);
    }
    const cand = t.dados && t.dados.candidato;
    if (cand && t.estado === "esperando_voce") {
      const sim = el("button", "button button-primary", "É este arquivo");
      sim.type = "button";
      sim.addEventListener("click", () => acaoTrabalho(t.id, "confirmar", { aceitar: true }));
      const nao = el("button", "button button-quiet", "Não é este");
      nao.type = "button";
      nao.addEventListener("click", () => acaoTrabalho(t.id, "confirmar", { aceitar: false }));
      acoes.append(sim, nao);
    }
    if (["erro", "cancelada", "interrompida"].includes(t.estado)) {
      const b = el("button", "button button-secondary", "Retomar");
      b.type = "button";
      b.addEventListener("click", () => acaoTrabalho(t.id, "retomar"));
      acoes.append(b);
    }
    if (t.estado === "pronta" && t.resultado) {
      const id = String(t.resultado).split(/[\\/]/).pop();
      const b = el("button", "button button-primary", "Abrir o kit");
      b.type = "button";
      b.addEventListener("click", async () => { try { await api("/api/abrir-pasta", { method: "POST", body: { id } }); } catch (erro) { aviso(erro.message, "error"); } });
      acoes.append(b);
    }
    if (acoes.childElementCount) cartao.append(acoes);
    return cartao;
  }

  function desenhar() {
    const caixa = $("#trabalhos-lista");
    if (!caixa) return;
    caixa.replaceChildren();
    if (!estado.trabalhos.length) {
      caixa.append(el("p", "drop-hint", "Nenhum trabalho ainda. Na tela Início, cole um link, um código de partida ou escolha uma demo."));
      return;
    }
    estado.trabalhos.forEach(t => caixa.append(cartaoTrabalho(t)));
  }

  function avisoDeConta() {
    const faixa = $("#aviso-conta");
    if (!faixa) return;
    const esperando = estado.trabalhos.find(t => t.estado === "esperando_voce" && t.etapas.some(e => e.nome === "esperar_conta" && e.estado === "esperando_voce"));
    if (!esperando) { faixa.classList.add("hidden"); return; }
    const etapa = esperando.etapas.find(e => e.nome === "esperar_conta");
    $("#aviso-conta-texto").textContent = etapa.detalhe || "Troque o Steam para a conta do Bisturi.";
    faixa.classList.remove("hidden");
  }

  function selo() {
    const n = estado.trabalhos.filter(t => ["na_fila", "rodando", "esperando_voce"].includes(t.estado)).length;
    const marca = $("#nav-trabalhos-n");
    if (marca) { marca.textContent = n ? String(n) : ""; marca.classList.toggle("hidden", !n); }
  }

  // ── primeiros passos: o que falta preparar para gravar pelo CS2 (só leitura; usa o checklist da gravação) ──
  const SITUACIONAIS = ["CS2 e OBS fechados", "Steam aberto", "demo"];

  function classificarChecklist(itens) {
    const pendentes = [];
    (itens || []).forEach(it => {
      if (it.ok || SITUACIONAIS.includes(it.item)) return;
      if (it.item.startsWith("Steam na 2ª conta")) {
        if (/^Falta dizer qual/.test(it.detalhe)) pendentes.push({ ...it, acao: "configurar" });   // o resto é "troque o Steam", que a receita pede na hora
        return;
      }
      pendentes.push(it.item.startsWith("Firewall") && /New-NetFirewallRule/.test(it.detalhe) ? { ...it, acao: "firewall" } : it);
    });
    return pendentes;
  }

  function comandoDoFirewall(detalhe) {
    const m = /(New-NetFirewallRule[^\n]*)$/.exec(detalhe || "");
    return m ? m[1].trim() : "";
  }

  function montarPrimeirosPassos(itens) {
    const caixa = $("#primeiros-passos");
    if (!caixa) return [];
    const pendentes = classificarChecklist(itens);
    caixa.replaceChildren();
    caixa.classList.remove("hidden");
    if (!pendentes.length) {
      caixa.append(el("p", "primeiros-ok", "✓ Gravação pelo CS2: tudo pronto (HLAE, Firewall e contas do Steam conferidos)."));
      return pendentes;
    }
    caixa.append(el("strong", "", "Falta preparar para gravar pelo CS2"));
    const lista = el("ul", "primeiros-lista");
    pendentes.forEach(p => {
      const li = el("li", "");
      li.append(el("span", "primeiros-item", p.item), el("small", "primeiros-detalhe", p.acao === "firewall" ? "O console do jogo fica aberto para a rede; bloqueie a porta no Firewall (uma vez, como administrador)." : p.detalhe));
      if (p.acao === "firewall") {
        const b = el("button", "button button-secondary", "Copiar o comando");
        b.type = "button";
        b.addEventListener("click", async () => {
          try { await navigator.clipboard.writeText(comandoDoFirewall(p.detalhe)); aviso("Comando copiado. Abra o PowerShell como administrador e cole.", "success"); }
          catch { aviso(comandoDoFirewall(p.detalhe), ""); }
        });
        li.append(b);
      }
      if (p.acao === "configurar") {
        const b = el("button", "button button-secondary", "Abrir Configurar");
        b.type = "button";
        b.addEventListener("click", () => $("#nav-configurar")?.click());
        li.append(b);
      }
      lista.append(li);
    });
    caixa.append(lista);
    const de_novo = el("button", "button button-quiet", "Verificar de novo");
    de_novo.type = "button";
    de_novo.addEventListener("click", primeirosPassos);
    caixa.append(de_novo);
    return pendentes;
  }

  async function primeirosPassos() {
    try { montarPrimeirosPassos((await api("/api/gravacao/checklist")).itens || []); }
    catch { $("#primeiros-passos")?.classList.add("hidden"); }
  }

  // ── partidas novas na pasta do CS2: só avisa, o dono decide ──
  async function partidasNovas() {
    const caixa = $("#partidas-novas");
    if (!caixa) return;
    let itens = [];
    try { itens = (await api("/api/partidas-novas")).itens || []; } catch { return; }
    caixa.replaceChildren();
    caixa.classList.toggle("hidden", !itens.length);
    itens.forEach(p => {
      const linha = el("div", "partida-nova");
      linha.append(el("span", "partida-nova-txt", `Partida nova: ${p.data} (${p.mb} MB). Fazer os vídeos dela?`));
      const sim = el("button", "button button-primary", "Fazer vídeos");
      sim.type = "button";
      sim.addEventListener("click", () => { $("#caixa-texto").value = p.caminho; reconhecer(); $("#caixa-unica")?.scrollIntoView?.({ behavior: "smooth" }); });
      const nao = el("button", "button button-quiet", "Agora não");
      nao.type = "button";
      nao.addEventListener("click", async () => {
        try { await api("/api/partidas-novas/dispensar", { method: "POST", body: { nome: p.nome } }); await partidasNovas(); } catch (erro) { aviso(erro.message, "error"); }
      });
      linha.append(sim, nao);
      caixa.append(linha);
    });
  }

  // M-006: rápido (2,5 s) com trabalho vivo, lento (6 s) ocioso, 30 s com a aba oculta. Oculta não para de vez: o vigia do servidor (OCIOSO_S, 180 s)
  // conta estes pedidos como sinal de vida. Toda ação do dono (começar, cancelar, retomar) chama atualizar() na hora, então um trabalho novo não espera.
  const TRABALHOS_MS = { rapido: 2500, lento: 6000, oculto: 30000 };
  const VIVOS = ["na_fila", "rodando", "esperando_voce"];
  function intervalo() {
    if (document.hidden) return TRABALHOS_MS.oculto;
    return estado.trabalhos.some(t => VIVOS.includes(t.estado)) ? TRABALHOS_MS.rapido : TRABALHOS_MS.lento;
  }
  function agendar(atraso) {
    if (!estado.ligado) return;                                    // só depois de ligar(): sem relógio solto nos testes
    window.clearTimeout(estado.timer);
    estado.timer = window.setTimeout(atualizar, atraso === undefined ? intervalo() : atraso);
  }
  function ligar() {
    estado.ligado = true;
    document.addEventListener("visibilitychange", () => agendar(document.hidden ? undefined : 0));    // a aba voltou: atualiza já
    agendar();
  }

  async function atualizar() {
    try {
      estado.trabalhos = (await api("/api/trabalhos")).itens || [];
    } catch { agendar(); return; }
    try {
      if (!estado.partidasEm || Date.now() - estado.partidasEm >= 30000) { estado.partidasEm = Date.now(); partidasNovas(); }   // a cada ~30 s: só leitura da pasta de replays
      selo();
      avisoDeConta();
      if (document.body.dataset.tela === "trabalhos") desenhar();
    } finally { agendar(); }
  }

  async function acaoTrabalho(id, acao, corpo = {}) {
    try { await api(`/api/trabalhos/${encodeURIComponent(id)}/${acao}`, { method: "POST", body: corpo }); await atualizar(); }
    catch (erro) { aviso(erro.message, "error"); }
  }

  function mostrar() { atualizar().then(desenhar); }

  function iniciar() {
    const texto = $("#caixa-texto");
    if (!texto) return;
    texto.addEventListener("input", () => { window.clearTimeout(estado.debounce); estado.debounce = window.setTimeout(reconhecer, 600); });
    texto.addEventListener("paste", () => window.setTimeout(reconhecer, 50));
    texto.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); reconhecer(); } });
    $("#caixa-arquivo")?.addEventListener("click", escolherArquivo);
    $("#caixa-ultima")?.addEventListener("click", ultimaPartida);
    $("#aviso-conta-ir")?.addEventListener("click", () => $("#nav-trabalhos")?.click());
    $("#abrir-gravacao-manual")?.addEventListener("click", () => $("#nav-gravar")?.click());
    primeirosPassos();
    ligar();
    atualizar();
  }

  window.BisturiTrabalhos = { mostrar, atualizar, reconhecer };
  document.addEventListener("DOMContentLoaded", iniciar);

  if (typeof module === "object" && module && module.exports) {
    module.exports = { atualizar, ligar, TIPOS, ESTADOS, SIMBOLOS, minutos, estado, cartaoTrabalho, montarPlano, partidasNovas, classificarChecklist, comandoDoFirewall, montarPrimeirosPassos };
  }
})();
