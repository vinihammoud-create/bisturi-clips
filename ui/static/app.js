(() => {
  "use strict";

  const token = document.querySelector('meta[name="ui-token"]')?.content || "";
  const state = {
    arquivos: [],
    fila: [],
    biblioteca: [],
    videoAtual: null,
    dadosAtuais: null,
    momentos: [],
    partes: [],
    filtroFocus: 0,
    configCampos: [],
    configAlteracoes: {},
    pendentesAplicar: {},
    tipoPlayer: "corte",
    ultimoProgresso: {},
    timerFila: null,
    finalizadosVistos: new Set(),
    exclusaoInicial: {},
    assinaturaFila: null,
    nomesDigitados: {},
    logPorJob: {}
  };

  const TELAS = ["inicio", "videos", "configurar", "ajuda"];

  const $ = (seletor, raiz = document) => raiz.querySelector(seletor);
  const $$ = (seletor, raiz = document) => Array.from(raiz.querySelectorAll(seletor));
  const el = (tag, classe, texto) => {
    const node = document.createElement(tag);
    if (classe) node.className = classe;
    if (texto !== undefined && texto !== null) node.textContent = String(texto);
    return node;
  };
  // Ícone desenhado pelo CSS (máscara SVG): nada de HTML montado com texto.
  const icone = nome => {
    const node = el("i", `i i-${nome}`);
    node.setAttribute("aria-hidden", "true");
    return node;
  };
  const button = (texto, classe, acao, title, nomeIcone) => {
    const node = el("button", `button ${classe || "button-secondary"}`, nomeIcone ? null : texto);
    if (nomeIcone) node.append(icone(nomeIcone), el("span", "", texto));
    node.type = "button";
    if (title) node.title = title;
    node.addEventListener("click", acao);
    return node;
  };

  async function api(caminho, opcoes = {}) {
    const metodo = (opcoes.method || "GET").toUpperCase();
    const cabecalhos = new Headers(opcoes.headers || {});
    if (!["GET", "HEAD", "OPTIONS"].includes(metodo)) cabecalhos.set("X-Token", token);
    let corpo = opcoes.body;
    if (corpo !== undefined && corpo !== null && !(corpo instanceof FormData) && typeof corpo !== "string") {
      cabecalhos.set("Content-Type", "application/json");
      corpo = JSON.stringify(corpo);
    }
    const resposta = await fetch(caminho, { method: metodo, headers: cabecalhos, body: corpo, cache: "no-store" });
    let conteudo;
    const tipo = resposta.headers.get("content-type") || "";
    if (tipo.includes("application/json")) conteudo = await resposta.json();
    else conteudo = { erro: resposta.ok ? "A resposta não está no formato esperado." : "Não consegui concluir essa ação." };
    if (!resposta.ok) throw new Error(conteudo.erro || "Não consegui concluir essa ação.");
    return conteudo;
  }

  function toast(mensagem, tipo = "", duracao = 3600, acaoDesfazer = null) {
    const regiao = $("#toast-region");
    const item = el("div", `toast ${tipo}`);
    item.append(el("span", "", mensagem));
    if (typeof acaoDesfazer === "function") {
      const desfazer = el("button", "text-button", "Desfazer");
      desfazer.type = "button";
      desfazer.addEventListener("click", () => { acaoDesfazer(); item.remove(); });
      item.append(desfazer);
    }
    regiao.append(item);
    window.setTimeout(() => item.remove(), duracao);
  }

  function mostrarMensagem(elemento, texto, tipo = "info") {
    elemento.textContent = texto;
    elemento.className = `notice notice-${tipo}`;
    elemento.classList.remove("hidden");
  }

  function formatarTamanho(valor) {
    const tamanho = Number(valor);
    if (!Number.isFinite(tamanho) || tamanho < 0) return "Tamanho não informado";
    if (tamanho < 1024) return `${tamanho} B`;
    if (tamanho < 1024 * 1024) return `${(tamanho / 1024).toFixed(1).replace(".", ",")} KB`;
    if (tamanho < 1024 ** 3) return `${(tamanho / 1024 ** 2).toFixed(1).replace(".", ",")} MB`;
    return `${(tamanho / 1024 ** 3).toFixed(2).replace(".", ",")} GB`;
  }

  function formatarTempo(segundos) {
    const s = Math.max(0, Math.floor(Number(segundos) || 0));
    const horas = Math.floor(s / 3600);
    const minutos = Math.floor((s % 3600) / 60);
    const resto = s % 60;
    if (horas) return `${String(horas).padStart(2, "0")}:${String(minutos).padStart(2, "0")}:${String(resto).padStart(2, "0")}`;
    return `${String(minutos).padStart(2, "0")}:${String(resto).padStart(2, "0")}`;
  }

  // "2026-10-17 19:15" -> "17/10 às 19:15"
  function formatarData(texto) {
    const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(String(texto || ""));
    if (!m) return String(texto || "");
    return m[4] ? `${m[3]}/${m[2]} às ${m[4]}:${m[5]}` : `${m[3]}/${m[2]}/${m[1]}`;
  }

  // Nome para mostrar: sem extensão e sem o código longo (UUID) que alguns programas põem no fim.
  function nomeVideo(item) {
    const bruto = String(item?.arquivo || item?.id || "Vídeo sem nome");
    const limpo = bruto.replace(/\.[a-z0-9]{2,4}$/i, "")
      .replace(/-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i, "")
      .replace(/_+/g, " ").trim();
    // VOD baixado da Twitch: "<id do vídeo>-<id do canal>" vira "Live <id do vídeo>".
    const vod = /^(\d{6,})-\d{5,}$/.exec(limpo);
    if (vod) return `Live ${vod[1]}`;
    return limpo || bruto;
  }

  function escaparParte(caminho) {
    return String(caminho || "").split("/").map(parte => encodeURIComponent(parte)).join("/");
  }

  function urlMidia(videoId, relativo) {
    return `/media/${encodeURIComponent(String(videoId))}/${escaparParte(relativo)}`;
  }

  function secaoAmigavel(secao) {
    const nomes = { cortes: "Cortes", shorts: "Shorts", montagem: "Montagem", transcricao: "Transcrição", chat: "Chat", qualidade: "Qualidade do vídeo", demo: "Demo" };
    return nomes[secao] || secao;
  }

  // Endereço da tela (#videos, #video/ID...): permite voltar direto a uma tela e abrir um vídeo pelo link.
  function mudarEndereco(hash) {
    try { window.history.replaceState(null, "", hash || window.location.pathname); } catch { /* sem histórico */ }
  }

  function trocarTela(nome, { carregar = true, endereco = true } = {}) {
    if (!TELAS.includes(nome)) nome = "inicio";
    $$(".view").forEach(tela => tela.classList.toggle("active", tela.id === `view-${nome}`));
    $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === nome));
    if (document.body) document.body.dataset.tela = nome;
    if (endereco) mudarEndereco(nome === "inicio" ? "" : `#${nome}`);
    if (nome === "videos" && carregar) carregarBiblioteca();
    if (nome === "configurar" && state.configCampos.length === 0) carregarConfiguracao();
    try { window.scrollTo(0, 0); } catch { /* sem janela */ }
  }

  function seguirEndereco() {
    let hash = "";
    try { hash = decodeURIComponent(String(window.location.hash || "").replace(/^#/, "")); } catch { hash = ""; }
    if (hash.startsWith("video/") && hash.length > 6) {
      trocarTela("videos", { carregar: false, endereco: false });
      carregarBiblioteca(false);
      abrirVideo(hash.slice(6));
      return;
    }
    trocarTela(TELAS.includes(hash) ? hash : "inicio", { endereco: false });
  }

  // Os 3 passos da tela inicial: acende o passo em que a pessoa está.
  function atualizarPassos() {
    const temArquivo = state.arquivos.length > 0;
    const ocupado = state.fila.some(job => ["na_fila", "rodando"].includes(job.estado));
    const pronto = state.fila.some(job => job.estado === "pronto");
    let passo = 1;
    if (temArquivo || ocupado) passo = 2;
    else if (pronto) passo = 3;
    [1, 2, 3].forEach(n => {
      const cartao = $(`#step-${n}`);
      if (!cartao) return;
      cartao.classList.toggle("is-active", n === passo);
      cartao.classList.toggle("is-done", n < passo);
    });
    let dica = "Escolha um arquivo no passo 1.";
    if (temArquivo) dica = `${state.arquivos.length} ${state.arquivos.length === 1 ? "arquivo pronto" : "arquivos prontos"}. Eles são processados um por vez.`;
    else if (ocupado) dica = "Processando. Acompanhe no Andamento, logo abaixo.";
    $("#process-hint").textContent = dica;
  }

  function adicionarArquivo(arquivo) {
    if (!arquivo || !arquivo.caminho) return;
    if (state.arquivos.some(item => item.caminho === arquivo.caminho)) return;
    state.arquivos.push({
      caminho: String(arquivo.caminho), nome: String(arquivo.nome || "Arquivo"),
      tamanho: Number(arquivo.tamanho) || 0, tipo: arquivo.tipo === "demo" ? "demo" : "video"
    });
    renderizarArquivosEscolhidos();
  }

  function renderizarArquivosEscolhidos() {
    const wrap = $("#selected-files-wrap");
    const lista = $("#selected-files");
    lista.replaceChildren();
    wrap.classList.toggle("hidden", state.arquivos.length === 0);
    $("#selected-count").textContent = `${state.arquivos.length} ${state.arquivos.length === 1 ? "arquivo escolhido" : "arquivos escolhidos"}`;
    $("#process-button").disabled = state.arquivos.length === 0;
    $("#player-field").classList.toggle("hidden", !state.arquivos.some(item => item.tipo === "demo"));
    state.arquivos.forEach((arquivo, indice) => {
      const linha = el("div", "file-row");
      linha.append(el("span", `file-type-icon ${arquivo.tipo === "demo" ? "demo" : ""}`, arquivo.tipo === "demo" ? "DEM" : "VÍDEO"));
      const nomeBox = el("div", "file-name-block");
      nomeBox.append(el("div", "file-name", arquivo.nome), el("div", "file-meta", `${arquivo.tipo === "demo" ? "Demo de partida" : "Vídeo"} · ${formatarTamanho(arquivo.tamanho)}`));
      linha.append(nomeBox);
      const remover = el("button", "file-remove", "×");
      remover.type = "button";
      remover.setAttribute("aria-label", `Remover ${arquivo.nome}`);
      remover.addEventListener("click", () => {
        state.arquivos.splice(indice, 1);
        renderizarArquivosEscolhidos();
      });
      linha.append(remover);
      lista.append(linha);
    });
    const cortes = $("#opt-cuts").checked;
    $("#opt-shorts").disabled = !cortes;
    $("#opt-montage").disabled = !cortes;
    $("#opt-shorts-wrap").style.opacity = cortes ? "1" : ".48";
    $("#opt-montage-wrap").style.opacity = cortes ? "1" : ".48";
    atualizarPassos();
  }

  async function escolherArquivos() {
    try {
      const resposta = await api("/api/escolher-arquivo", { method: "POST", body: {} });
      (resposta.itens || []).forEach(adicionarArquivo);
      if (resposta.itens?.length) toast(`${resposta.itens.length} arquivo(s) adicionado(s).`, "success");
    } catch (erro) { toast(erro.message, "error", 5000); }
  }

  // Arrastar faz cópia: o servidor grava o arquivo uma vez na pasta temporária do Windows (disco C:) e outra em
  // entrada/. Para live grande, "Escolher no computador" usa o arquivo onde ele está, sem copiar.
  const ENVIO_GRANDE = 2 * 1024 ** 3;
  function avisoEnvioGrande(total) {
    if (!(Number(total) > ENVIO_GRANDE)) return "";
    return `Arquivo grande (${formatarTamanho(total)}): arrastar faz uma cópia e precisa de espaço livre também no disco C:. Para lives grandes, prefira "Escolher no computador", que não copia nada.`;
  }

  function enviarArquivos(arquivos) {
    const validos = Array.from(arquivos || []);
    if (!validos.length) return;
    const dados = new FormData();
    let total = 0;
    validos.forEach(arquivo => { dados.append("files", arquivo, arquivo.name); total += arquivo.size || 0; });
    const aviso = avisoEnvioGrande(total);
    if (aviso) toast(aviso, "", 9000);
    const status = $("#upload-status");
    status.classList.remove("hidden");
    $("#upload-status-text").textContent = `Enviando ${validos.length} arquivo(s)…`;
    $("#upload-percent").textContent = "0%";
    $("#upload-progress").style.width = "0%";
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.setRequestHeader("X-Token", token);
    xhr.upload.addEventListener("progress", event => {
      if (!event.lengthComputable) return;
      const porcentagem = Math.min(100, Math.round(event.loaded / event.total * 100));
      $("#upload-percent").textContent = `${porcentagem}%`;
      $("#upload-progress").style.width = `${porcentagem}%`;
      $("#upload-status-text").textContent = `Enviando ${formatarTamanho(event.loaded)} de ${formatarTamanho(event.total)}…`;
    });
    xhr.addEventListener("load", () => {
      let resposta;
      try { resposta = JSON.parse(xhr.responseText); } catch { resposta = { erro: "O servidor respondeu de um jeito inesperado." }; }
      if (xhr.status >= 200 && xhr.status < 300) {
        (resposta.itens || []).forEach(adicionarArquivo);
        $("#upload-percent").textContent = "100%";
        $("#upload-progress").style.width = "100%";
        $("#upload-status-text").textContent = `Envio concluído · ${formatarTamanho(total)}`;
        toast(`${(resposta.itens || []).length} arquivo(s) recebido(s).`, "success");
      } else {
        $("#upload-status-text").textContent = "Não consegui receber os arquivos.";
        toast(resposta.erro || "Não consegui receber os arquivos.", "error", 5000);
      }
      window.setTimeout(() => status.classList.add("hidden"), 4500);
    });
    xhr.addEventListener("error", () => {
      $("#upload-status-text").textContent = "Falha no envio.";
      toast("Falha no envio. Verifique o espaço livre em disco e tente novamente.", "error", 5000);
    });
    xhr.send(dados);
  }

  async function abrirPasta(id = "") {
    try {
      await api("/api/abrir-pasta", { method: "POST", body: { id } });
    } catch (erro) { toast(erro.message, "error", 5000); }
  }

  async function adicionarFila(arquivos, opcoes = {}, motivo = "") {
    if (!arquivos.length) { toast("Escolha pelo menos um arquivo.", "error"); return; }
    try {
      await api("/api/fila", { method: "POST", body: { arquivos, opcoes } });
      if (motivo) {
        state.pendentesAplicar[state.videoAtual] = new Set();
        if (state.dadosAtuais?.momentos?.momentos) {
          state.exclusaoInicial[state.videoAtual] = new Map(state.dadosAtuais.momentos.momentos.map(m => [String(m.foco), Boolean(m.excluido)]));
        }
        atualizarBannerAplicar();
      }
      trocarTela("inicio");
      state.arquivos = [];
      renderizarArquivosEscolhidos();
      toast("Adicionado à fila. O processamento começa assim que chegar sua vez.", "success", 4200);
      atualizarFila();
    } catch (erro) {
      toast(erro.message, "error", 5000);
      if (/não encontrei|escolha-o novamente/i.test(erro.message) && state.videoAtual) {
        const escolha = await api("/api/escolher-arquivo", { method: "POST", body: {} }).catch(() => ({ itens: [] }));
        if (escolha.itens?.length) {
          const selecionado = escolha.itens.find(item => item.tipo === "video") || escolha.itens[0];
          await adicionarFila([selecionado.caminho], opcoes, motivo);
        }
      }
    }
  }

  async function processarSelecionados() {
    const opcoes = {
      gerar_cortes: $("#opt-cuts").checked,
      gerar_shorts: $("#opt-shorts").checked,
      gerar_montagem: $("#opt-montage").checked,
      forcar_gpu: $("#opt-force").checked,
      jogador: $("#player-name").value.trim()
    };
    await adicionarFila(state.arquivos.map(item => item.caminho), opcoes);
  }

  function estadoAmigavel(estado) {
    const mapa = {
      na_fila: "NA FILA", rodando: "PROCESSANDO", pronto: "PRONTO", com_problemas: "COM PROBLEMAS",
      erro: "ERRO", cancelado: "CANCELADO", precisa_jogador: "FALTA SEU NOME"
    };
    return mapa[estado] || "AGUARDANDO";
  }

  function classeEstado(estado) {
    if (estado === "pronto") return "pronto";
    if (["erro", "com_problemas", "cancelado"].includes(estado)) return estado === "cancelado" ? "erro" : estado;
    if (estado === "rodando") return "running";
    return "";
  }

  // O que aparece na lista da fila. Ela só é redesenhada quando isto muda: redesenhar a cada segundo
  // apagava o nome que a pessoa estava digitando e engolia cliques nos botões.
  function assinaturaFila(itens) {
    return JSON.stringify((itens || []).map(job => [job.id, job.estado, job.mensagem, job.jogadores || ""]));
  }

  function renderizarFila(resposta) {
    state.fila = resposta.itens || [];
    const assinatura = assinaturaFila(state.fila);
    if (assinatura !== state.assinaturaFila) {
      state.assinaturaFila = assinatura;
      desenharListaFila();
      atualizarPassos();
    }
    renderizarProgresso(resposta);
  }

  function desenharListaFila() {
    const lista = $("#queue-list");
    const focado = document.activeElement;
    const jobFocado = focado && focado.dataset ? focado.dataset.jobNome : undefined;
    lista.replaceChildren();
    if (!state.fila.length) {
      const vazio = el("div", "empty-state small-empty");
      vazio.append(el("strong", "", "Nenhum arquivo na fila"), el("p", "", "Adicione um vídeo ou uma demo no passo 1."));
      lista.append(vazio);
    }
    state.fila.forEach(job => {
      const linha = el("div", "queue-item");
      const icone = el("span", `queue-icon ${job.estado === "rodando" ? "running" : job.estado === "pronto" ? "done" : ["erro", "com_problemas"].includes(job.estado) ? "bad" : ""}`, job.tipo === "demo" ? "DEM" : "VÍDEO");
      const main = el("div", "queue-main");
      main.append(el("div", "queue-name", job.nome || "Arquivo"), el("div", "queue-sub", job.mensagem || estadoAmigavel(job.estado)));
      linha.append(icone, main);
      const acoes = el("div", "queue-actions");
      acoes.append(el("span", `status-label ${classeEstado(job.estado)}`, estadoAmigavel(job.estado)));
      if (["na_fila", "rodando"].includes(job.estado)) {
        acoes.append(button("Cancelar", "button-danger-quiet", async () => {
          try { await api(`/api/fila/${encodeURIComponent(job.id)}/cancelar`, { method: "POST", body: {} }); atualizarFila(); }
          catch (erro) { toast(erro.message, "error"); }
        }));
      }
      linha.append(acoes);
      lista.append(linha);
      if (job.estado === "precisa_jogador") {
        const entradaNome = el("input", "inline-input"); entradaNome.type = "text"; entradaNome.maxLength = 60; entradaNome.placeholder = "Seu nome no jogo"; entradaNome.setAttribute("aria-label", "Seu nome no jogo");
        // O texto digitado fica guardado por job: sobrevive se a lista precisar ser redesenhada.
        entradaNome.dataset.jobNome = job.id;
        entradaNome.value = state.nomesDigitados[job.id] || "";
        entradaNome.addEventListener("input", () => { state.nomesDigitados[job.id] = entradaNome.value; });
        const enviarNome = button("Tentar de novo", "button-primary", async () => {
          try {
            await api(`/api/fila/${encodeURIComponent(job.id)}/jogador`, { method: "POST", body: { jogador: entradaNome.value } });
            delete state.nomesDigitados[job.id];
            toast("Nova tentativa adicionada à fila.", "success"); atualizarFila();
          } catch (erro) { toast(erro.message, "error"); }
        });
        const blocoNome = el("div", "notice notice-info");
        blocoNome.append(el("p", "", "A demo precisa do seu nome no jogo para achar as suas jogadas."), entradaNome, enviarNome);
        if (job.jogadores) blocoNome.append(el("p", "muted", `Jogadores nesta demo: ${job.jogadores}`));
        lista.append(blocoNome);
        if (jobFocado === job.id) {
          entradaNome.focus();
          const fim = entradaNome.value.length;
          try { entradaNome.setSelectionRange(fim, fim); } catch { /* campo sem seleção */ }
        }
      }
    });
  }

  // Qual job a área de andamento mostra. O servidor zera `ativo` quando o job termina; aí vale o último
  // que terminou mal (com o nome, a mensagem e os detalhes DELE, não os do progresso.json da última execução).
  function jobReferencia(resposta) {
    const jobs = resposta.itens || [];
    const ativo = jobs.find(job => job.id === resposta.ativo) || jobs.find(job => job.estado === "rodando") || null;
    const externo = !ativo && Boolean(resposta.externo) && (resposta.progresso || {}).estado === "rodando";
    if (ativo || externo) return { ativo, ref: ativo, externo };
    const ruins = jobs.filter(job => ["erro", "com_problemas", "precisa_jogador"].includes(job.estado))
      .sort((a, b) => (Number(b.terminado_em) || 0) - (Number(a.terminado_em) || 0));
    return { ativo: null, ref: ruins[0] || null, externo: false };
  }

  function renderizarProgresso(resposta) {
    const jobs = resposta.itens || [];
    const { ativo, ref, externo } = jobReferencia(resposta);
    const doRef = ref && ref.progresso && Object.keys(ref.progresso).length ? ref.progresso : null;
    let progresso;
    if (ativo || externo) progresso = doRef || resposta.progresso || {};
    else if (ref) progresso = doRef || {};
    else progresso = resposta.progresso || {};
    const estaRodando = Boolean(ativo && ativo.estado === "rodando") || externo;
    const erroSemJob = !ref && !estaRodando && progresso.estado === "erro";
    const mostrar = estaRodando || erroSemJob || Boolean(ref);
    $("#progress-area").classList.toggle("hidden", !mostrar);
    $("#preview-notice").classList.toggle("hidden", (resposta.progresso || {}).modo !== "previa");
    $("#status-chip").textContent = estaRodando ? "PROCESSANDO" : jobs.some(job => job.estado === "na_fila") ? "NA FILA" : "PRONTO";
    if (document.body) document.body.classList.toggle("processando", estaRodando);
    if (!mostrar) return;
    const terminado = Boolean(ref) && !estaRodando;
    $("#progress-video").textContent = terminado ? (ref.nome || "Arquivo") : (progresso.video || ref?.nome || (erroSemJob ? "Processamento interrompido" : "Processamento atual"));
    let etapa;
    if (terminado) etapa = ref.estado === "precisa_jogador" ? "Aguardando o nome do jogador" : (ref.mensagem || "Terminou");
    else etapa = progresso.etapa || progresso.mensagem || ref?.mensagem || "Preparando";
    $("#progress-stage").textContent = etapa;
    const feitas = Number(progresso.etapas_feitas) || 0;
    const estimadas = Number(progresso.etapas_estimadas) || 0;
    const porcentagem = estimadas > 0 ? Math.max(0, Math.min(100, feitas / estimadas * 100)) : 0;
    $("#video-progress").style.width = `${porcentagem}%`;
    $("#progress-count").textContent = estimadas > 0 ? `${feitas} de ${estimadas}` : "—";
    const videoN = Number(progresso.video_n) || 1;
    const videoTotal = Number(progresso.video_total) || Math.max(1, jobs.filter(job => ["na_fila", "rodando"].includes(job.estado)).length);
    const geral = estimadas > 0 ? Math.max(0, Math.min(100, ((videoN - 1) + feitas / estimadas) / videoTotal * 100)) : 0;
    $("#overall-progress").style.width = `${geral}%`;
    $("#overall-count").textContent = `${Math.round(geral)}%`;
    const estadoRef = erroSemJob ? "erro" : (ref?.estado || "erro");
    $("#progress-state").textContent = estaRodando ? "PROCESSANDO" : estadoAmigavel(estadoRef);
    $("#progress-state").className = `status-label ${estaRodando ? "running" : classeEstado(estadoRef)}`;
    const fimTempo = terminado && ref.terminado_em ? Number(ref.terminado_em) : Date.now() / 1000;
    const decorrido = ref?.iniciado_em ? Math.max(0, fimTempo - ref.iniciado_em) : 0;
    $("#elapsed-time").textContent = `Tempo decorrido: ${formatarTempo(decorrido)}`;
    const detalhes = [];
    if (Array.isArray(ref?.detalhes)) detalhes.push(...ref.detalhes);
    if (terminado && ["erro", "com_problemas"].includes(ref.estado)) {
      // O texto do processo fica no topo; o registro (saida/log.txt) vem embaixo. Busca uma vez por job.
      if (!Object.prototype.hasOwnProperty.call(state.logPorJob, ref.id)) {
        state.logPorJob[ref.id] = [];
        api("/api/log").then(log => { state.logPorJob[ref.id] = Array.isArray(log.linhas) ? log.linhas : []; }).catch(() => {});
      }
      const linhasLog = state.logPorJob[ref.id];
      if (linhasLog.length) detalhes.push("", "— Registro de problemas (saida/log.txt) —", linhasLog.join("").trimEnd());
    }
    const texto = detalhes.join("\n") || (estaRodando ? "Aguardando detalhes do programa…" : "Não há detalhes adicionais nesta execução.");
    if ($("#details-text").textContent !== texto) $("#details-text").textContent = texto;
  }

  async function atualizarFila() {
    try {
      const resposta = await api("/api/fila");
      renderizarFila(resposta);
      let temNovidade = false;
      state.fila.forEach(job => {
        if (["pronto", "com_problemas", "erro"].includes(job.estado)) {
          const chave = `${job.id}:${job.estado}`;
          if (!state.finalizadosVistos.has(chave)) { state.finalizadosVistos.add(chave); temNovidade = true; }
        }
      });
      if (temNovidade) carregarBiblioteca(false);
    } catch (erro) { console.warn(erro); }
  }

  async function carregarEntrada() {
    try {
      const resposta = await api("/api/entrada");
      const itens = resposta.itens || [];
      if (!itens.length) { toast("A pasta entrada está vazia."); return; }
      itens.forEach(item => adicionarArquivo({ caminho: `entrada:${item.nome}`, nome: item.nome, tamanho: item.tamanho, tipo: item.tipo }));
      toast(`${itens.length} arquivo(s) da pasta entrada adicionado(s) à lista.`, "success");
    } catch (erro) { toast(erro.message, "error"); }
  }

  async function carregarBiblioteca(mostrar = true) {
    try {
      const resposta = await api("/api/indice");
      state.biblioteca = resposta.itens || [];
      renderizarBiblioteca();
      if (mostrar) {
        $("#library-wrap").classList.remove("hidden");
        $("#result-view").classList.add("hidden");
      }
    } catch (erro) {
      $("#library-grid").replaceChildren();
      $("#library-grid").append(el("div", "notice notice-error", erro.message));
    }
  }

  // Resultado de demo (.dem) no indice.json: abre o resumo de mapa, placar e clutches (montarDemo).
  function eDemoIndice(item) {
    return /\.dem$/i.test(String(item?.arquivo || "")) || /\.dem$/i.test(String(item?.caminho || ""));
  }

  // Miniatura do vídeo (a melhor jogada, escolhida pelo servidor); sem ela, o emblema.
  function miniaturaVideo(item, classe) {
    if (item.capa) {
      const imagem = el("img", classe); imagem.loading = "lazy"; imagem.alt = ""; imagem.src = urlMidia(item.id, item.capa);
      return imagem;
    }
    const marca = el("img", "marca"); marca.alt = ""; marca.src = "/static/emblema.png";
    return marca;
  }

  function renderizarBiblioteca() {
    const grade = $("#library-grid");
    grade.replaceChildren();
    const total = state.biblioteca.length;
    $("#library-count").textContent = total ? `${total} ${total === 1 ? "vídeo analisado" : "vídeos analisados"}. Abra um para ver, revisar e postar.` : "Abra um vídeo para ver, revisar e postar os cortes.";
    renderizarRecentes();
    if (!total) {
      const vazio = el("div", "empty-state");
      vazio.append(el("strong", "", "Ainda não há vídeos analisados"), el("p", "", "Processe um arquivo na tela Início para ver os resultados aqui."));
      grade.append(vazio);
      return;
    }
    state.biblioteca.forEach(item => {
      const card = el("article", "library-card");
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", `Abrir resultado de ${item.arquivo || item.id || "vídeo"}`);
      const thumb = el("div", "library-thumb");
      if (item.capa) thumb.append(miniaturaVideo(item, "capa"));
      else { const marca = el("span", "thumb-mark"); const img = el("img"); img.alt = ""; img.src = "/static/emblema.png"; marca.append(img); thumb.append(marca); }
      const selosThumb = el("div", "thumb-badges");
      if (eDemoIndice(item)) selosThumb.append(el("span", "pill demo", "DEMO"));
      const partidas = Array.isArray(item.partidas) ? item.partidas : [];
      if (partidas.length === 1 && partidas[0]?.placar) selosThumb.append(el("span", "pill placar", `PLACAR ${partidas[0].placar}`));
      else if (partidas.length > 1) selosThumb.append(el("span", "pill placar", `${partidas.length} PARTIDAS`));
      thumb.append(selosThumb);
      if (Number(item.duracao) > 0) thumb.append(el("span", "thumb-duration", formatarTempo(item.duracao)));
      const play = el("span", "thumb-play"); play.append(icone("play")); thumb.append(play);
      const info = el("div", "library-info");
      const nome = el("h3", "library-name", nomeVideo(item)); nome.title = String(item.arquivo || item.id || "");
      info.append(nome);
      const numeros = el("div", "library-stats");
      const momentos = el("span"); momentos.append(el("b", "", Number(item.momentos) || 0), el("span", "", " momentos"));
      const abates = el("span"); abates.append(el("b", "", Number(item.abates) || 0), el("span", "", " abates"));
      numeros.append(momentos, abates);
      info.append(numeros);
      const selos = el("div", "library-badges");
      const emocoes = item.emocoes && typeof item.emocoes === "object" ? item.emocoes : {};
      Object.entries(emocoes).forEach(([nomeEmocao, qtd]) => {
        if (Number(qtd) > 0) selos.append(el("span", `pill ${String(nomeEmocao).toLowerCase()}`, `${nomeEmocao} ${qtd}`));
      });
      if (selos.children.length) info.append(selos);
      if (item.processado_em) info.append(el("div", "library-meta", `Processado em ${formatarData(item.processado_em)}`));
      card.append(thumb, info);
      const abrir = () => abrirVideo(item.id);
      card.addEventListener("click", abrir);
      card.addEventListener("keydown", evento => { if (evento.key === "Enter" || evento.key === " ") { evento.preventDefault(); abrir(); } });
      grade.append(card);
    });
  }

  // Passo 3 da tela inicial: os últimos vídeos prontos.
  function renderizarRecentes() {
    const lista = $("#recent-grid");
    if (!lista) return;
    lista.replaceChildren();
    const recentes = [...state.biblioteca].sort((a, b) => String(b.processado_em || "").localeCompare(String(a.processado_em || ""))).slice(0, 3);
    if (!recentes.length) {
      const vazio = el("div", "empty-state small-empty");
      const simbolo = icone("film"); simbolo.className = "i i-film empty-symbol";
      vazio.append(simbolo, el("strong", "", "Nada pronto ainda"), el("p", "", "Seus vídeos aparecem aqui quando terminarem."));
      lista.append(vazio);
      return;
    }
    recentes.forEach(item => {
      const cartao = el("div", "recent-card");
      cartao.tabIndex = 0;
      cartao.setAttribute("role", "button");
      cartao.setAttribute("aria-label", `Abrir resultado de ${item.arquivo || item.id || "vídeo"}`);
      const thumb = el("div", "recent-thumb"); thumb.append(miniaturaVideo(item, ""));
      const info = el("div", "recent-info");
      info.append(el("div", "recent-name", nomeVideo(item)), el("div", "recent-meta", `${Number(item.momentos) || 0} momentos · ${item.processado_em ? formatarData(item.processado_em) : formatarTempo(item.duracao)}`));
      cartao.append(thumb, info);
      const abrir = () => { trocarTela("videos", { carregar: false }); abrirVideo(item.id); };
      cartao.addEventListener("click", abrir);
      cartao.addEventListener("keydown", evento => { if (evento.key === "Enter" || evento.key === " ") { evento.preventDefault(); abrir(); } });
      lista.append(cartao);
    });
  }

  // Placar no subtítulo do resultado, igual ao cartão da Biblioteca: 1 partida = "Placar 0x7"; várias = "2 partidas: 0x7 · 3x6".
  function resumoPlacar(partidas, placarDemo) {
    const lista = Array.isArray(partidas) ? partidas : [];
    if (lista.length > 1) {
      const placares = lista.map(p => p?.placar).filter(Boolean);
      return `${lista.length} partidas${placares.length ? `: ${placares.join(" · ")}` : ""}`;
    }
    const placar = lista[0]?.placar || placarDemo || "";
    return placar ? `Placar ${placar}` : "";
  }

  async function abrirVideo(id) {
    try {
      const resposta = await api(`/api/videos/${encodeURIComponent(id)}`);
      state.videoAtual = id;
      state.dadosAtuais = resposta;
      const documento = resposta.momentos || {};
      state.momentos = Array.isArray(documento.momentos) ? documento.momentos : [];
      state.partes = Array.isArray(resposta.partes) ? resposta.partes : [];
      if (!Object.prototype.hasOwnProperty.call(state.exclusaoInicial, id)) {
        state.exclusaoInicial[id] = new Map(state.momentos.map(m => [String(m.foco), Boolean(m.excluido)]));
      }
      $("#result-title").textContent = nomeVideo(resposta.indice?.arquivo ? resposta.indice : { id });
      $("#result-title").title = String(resposta.indice?.arquivo || id);
      const placarDemo = Array.isArray(resposta.demo?.placar) ? resposta.demo.placar.join(" × ") : "";
      const placar = resumoPlacar(resposta.indice?.partidas, placarDemo);
      const quando = resposta.indice?.processado_em ? ` · ${formatarData(resposta.indice.processado_em)}` : "";
      $("#result-subtitle").textContent = `${resposta.demo ? "Demo · " : ""}${formatarTempo(resposta.indice?.duracao || documento.duracao)} · ${state.momentos.length} momentos${placar ? ` · ${placar}` : ""}${quando}`;
      $("#library-wrap").classList.add("hidden");
      $("#result-view").classList.remove("hidden");
      mudarEndereco(`#video/${encodeURIComponent(id)}`);
      montarAcoesResultado(resposta);
      montarDemo(resposta.demo);
      montarPublicar(resposta);
      montarLinhaDoTempo();
      renderizarMomentos();
      atualizarBannerAplicar();
      try { window.scrollTo(0, 0); } catch { /* sem janela */ }
    } catch (erro) { toast(erro.message, "error", 5000); }
  }

  function montarAcoesResultado(resposta) {
    const acoes = $("#result-actions");
    acoes.replaceChildren();
    const id = resposta.id;
    if (resposta.links?.montagem) acoes.append(button("Ver montagem", "button-primary", () => abrirPlayerMontagem(resposta.links.montagem_arquivo), null, "play"));
    if (resposta.links?.fails_arquivo) acoes.append(button("Ver FAILS", "button-secondary", () => abrirPlayer(urlMidia(id, resposta.links.fails_arquivo), "FAILS", "Mortes e momentos engraçados", null, null), null, "play"));
    if (resposta.links?.capitulos) acoes.append(button("Copiar capítulos", "button-secondary", copiarCapitulos, "Capítulos prontos para a descrição do YouTube", "list"));
    if (resposta.links?.revisao) {
      acoes.append(button("Revisão (gabarito)", "button-secondary", () => window.open(urlMidia(id, "revisao.html"), "_blank", "noopener"), null, "external"));
    }
    acoes.append(button("Relatório", "button-secondary", () => window.open("/media-geral/relatorio.html", "_blank", "noopener"), null, "doc"));
    acoes.append(button("Abrir pasta", "button-secondary", () => abrirPasta(id), null, "folder"));
    acoes.append(button("Gerar de novo", "button-secondary", () => reprocessarVideo("reprocessar"), "Processa este vídeo de novo", "refresh"));
  }

  // "Pronto para postar": lê o publicar.json do núcleo (títulos, hashtags e descrição de cada short).
  function montarPublicar(resposta) {
    const painel = $("#post-panel");
    const lista = $("#post-list");
    lista.replaceChildren();
    const itens = Array.isArray(resposta.publicar?.itens) ? resposta.publicar.itens.filter(item => item && typeof item === "object" && item.short) : [];
    painel.classList.toggle("hidden", itens.length === 0);
    itens.forEach(item => {
      const titulos = Array.isArray(item.titulos) ? item.titulos.map(String).filter(Boolean) : [];
      const titulo = titulos[0] || "Short";
      const cartao = el("article", "post-card");
      const thumb = el("button", "post-thumb"); thumb.type = "button"; thumb.setAttribute("aria-label", `Ver o short ${titulo}`);
      if (item.capa) { const imagem = el("img"); imagem.loading = "lazy"; imagem.alt = ""; imagem.src = urlMidia(state.videoAtual, item.capa); thumb.append(imagem); }
      if (Number.isFinite(Number(item.nota))) thumb.append(el("span", `score ${classeNota(item.nota)}`, `NOTA ${Math.round(Number(item.nota))}`));
      const play = el("span", "thumb-play"); play.append(icone("play")); thumb.append(play);
      thumb.addEventListener("click", () => abrirShortPublicar(item, titulo));
      const corpo = el("div", "post-body");
      const tituloEl = el("div", "post-title", titulo);
      if (titulos.length > 1) tituloEl.title = `Outras ideias de título:\n${titulos.slice(1).join("\n")}`;
      corpo.append(tituloEl);
      if (Array.isArray(item.hashtags) && item.hashtags.length) corpo.append(el("div", "post-tags", item.hashtags.join(" ")));
      const botoes = el("div", "post-actions");
      botoes.append(button("Título", "button-secondary", () => copiarTexto(titulo, "Título copiado."), "Copiar o título", "copy"));
      if (item.descricao) botoes.append(button("Descrição", "button-secondary", () => copiarTexto(String(item.descricao), "Descrição copiada, com as hashtags."), "Copiar a descrição", "copy"));
      corpo.append(botoes);
      cartao.append(thumb, corpo);
      lista.append(cartao);
    });
  }

  function abrirShortPublicar(item, titulo) {
    const momento = state.momentos.find(m => Number(m.n) === Number(item.n));
    if (momento?.arquivos?.corte) {
      abrirPlayerMomento(momento);
      trocarPlayer(momento.arquivos.short_efeitos ? "efeitos" : "short");
      return;
    }
    abrirPlayer(urlMidia(state.videoAtual, item.short), titulo, "Short vertical", null, item.short_efeitos ? urlMidia(state.videoAtual, item.short_efeitos) : null);
  }

  async function copiarTexto(texto, aviso) {
    try {
      await navigator.clipboard.writeText(texto);
      toast(aviso || "Copiado.", "success");
    } catch { toast("Não consegui copiar. Selecione o texto e copie com Ctrl+C.", "error", 5000); }
  }

  function classeNota(nota) {
    const valor = Number(nota);
    if (valor >= 40) return "alta";
    if (valor >= 25) return "media";
    return "";
  }

  function montarDemo(demo) {
    const aviso = $("#demo-notice"); const resumo = $("#demo-summary");
    aviso.classList.add("hidden"); resumo.classList.add("hidden"); resumo.replaceChildren();
    $("#result-view").querySelectorAll(".demo-list").forEach(lista => lista.remove());
    const eDemo = demo && typeof demo === "object";
    $(".filter-bar").classList.toggle("hidden", Boolean(eDemo));
    $(".moments-heading").classList.toggle("hidden", Boolean(eDemo));
    $("#moments-grid").classList.toggle("hidden", Boolean(eDemo));
    $("#dirty-banner").classList.toggle("hidden", Boolean(eDemo));
    if (!eDemo) { $("#timeline-wrap").classList.remove("hidden"); return; }
    aviso.textContent = "Demo analisada. O corte do vídeo a partir da demo chega numa próxima versão.";
    aviso.className = "notice notice-info";
    resumo.className = "demo-summary";
    const placar = Array.isArray(demo.placar) ? demo.placar.join(" × ") : "Não informado";
    const estatisticas = [["MAPA", demo.mapa || "Não informado"], ["PLACAR", placar], ["JOGADAS", Array.isArray(demo.abates) ? demo.abates.length : 0]];
    estatisticas.forEach(([titulo, valor]) => { const cartao = el("div", "demo-stat"); cartao.append(el("small", "", titulo), el("strong", "", valor)); resumo.append(cartao); });
    if (Array.isArray(demo.abates) && demo.abates.length) {
      const listaAbates = el("div", "demo-list"); listaAbates.append(el("h3", "", "JOGADAS DETECTADAS"));
      demo.abates.forEach(abate => {
        const linha = el("div", "demo-list-row");
        const icones = Array.isArray(abate.icones) ? abate.icones.map(nome => ({headshot:"HS",wallbang:"WALLBANG",noscope:"NOSCOPE",smoke:"FUMAÇA",blind:"CEGO",inair:"NO AR"}[nome] || nome)).join(" · ") : "";
        const detalhe = `${abate.vitima || "Vítima não informada"}${abate.arma ? ` · ${abate.arma}` : ""}${Number.isFinite(abate.round) ? ` · Round ${abate.round}` : ""}${Number.isFinite(abate.distancia) ? ` · ${abate.distancia} m` : ""}${icones ? ` · ${icones}` : ""}`;
        linha.append(el("span", "demo-time", formatarTempo(abate.t)), el("span", "", detalhe));
        listaAbates.append(linha);
      });
      resumo.after(listaAbates);
    }
    if (Array.isArray(demo.clutches) && demo.clutches.length) {
      const lista = el("div", "demo-list"); lista.append(el("h3", "", "CLUTCHES DETECTADOS"));
      demo.clutches.forEach(clutch => {
        const linha = el("div", "demo-list-row"); linha.append(el("span", "demo-time", formatarTempo(clutch.t)), el("span", "", `${Number(clutch.inimigos) || 0} inimigo(s) · ${clutch.ganhou ? "venceu" : "não venceu"}${Number.isFinite(clutch.abates) ? ` · ${clutch.abates} abates` : ""}`)); lista.append(linha);
      }); resumo.after(lista);
    }
    $("#timeline-wrap").classList.add("hidden");
    aviso.classList.remove("hidden"); resumo.classList.remove("hidden");
  }

  function posicaoParte(inicio, fim, duracao) {
    const total = Number(duracao) > 0 ? Number(duracao) : 1;
    const left = Math.max(0, Math.min(100, inicio / total * 100));
    const width = Math.max(0, Math.min(100 - left, (fim - inicio) / total * 100));
    return { left, width };
  }

  function montarLinhaDoTempo() {
    const timeline = $("#timeline"); timeline.replaceChildren();
    const duracao = Number(state.dadosAtuais?.momentos?.duracao || state.dadosAtuais?.indice?.duracao) || Math.max(1, ...state.partes.map(p => Number(p.fim) || 0));
    if (state.partes.length) {
      state.partes.forEach(parte => {
        const inicio = Number(parte.inicio) || 0; const fim = Number(parte.fim) || inicio;
        const faixa = el("div", `timeline-part ${parte.tipo || "fora"}`);
        // Posição absoluta, como os marcadores: as partes não cobrem o vídeo todo (trechos curtos fora de partida somem).
        const pos = posicaoParte(inicio, fim, duracao);
        faixa.style.left = `${pos.left}%`;
        faixa.style.width = `${pos.width}%`;
        faixa.title = parte.titulo || parte.placar || parte.tipo || "Parte do vídeo";
        if (pos.width > 12 && parte.tipo === "partida") faixa.textContent = parte.titulo || parte.placar || "";
        timeline.append(faixa);
      });
    } else {
      const faixa = el("div", "timeline-part partida"); faixa.style.left = "0%"; faixa.style.width = "100%"; timeline.append(faixa);
    }
    state.momentos.forEach((momento, indice) => {
      if (momento.excluido && !$("#filter-excluded").checked) return;
      const foco = Number(momento.foco ?? momento.inicio) || 0;
      const marcador = el("button", `timeline-marker ${momento.tipo === "abate" ? "abate" : "reacao"}`);
      marcador.type = "button"; marcador.style.left = `calc(${Math.max(0, Math.min(100, foco / duracao * 100))}% - 1px)`;
      marcador.title = `${momento.rotulo || "Momento"} · ${formatarTempo(foco)}`;
      marcador.setAttribute("aria-label", `Ir para ${momento.rotulo || "momento"} em ${formatarTempo(foco)}`);
      marcador.addEventListener("click", () => $(`#card-${indice}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
      timeline.append(marcador);
    });
  }

  function seloEmocao(momento) {
    if (!momento.emocao) return null;
    const rotulos = { HYPE: "HYPE", RAIVA: "RAIVA", RISADA: "RISADA" };
    const confianca = momento.confianca === "media" ? "?" : "";
    return el("span", `pill ${String(momento.emocao).toLowerCase()}`, `${rotulos[momento.emocao] || momento.emocao}${confianca}`);
  }

  // Filtra os momentos da tela Vídeos. "Só partidas" só vale quando o vídeo tem alguma partida detectada:
  // sem nenhuma, o núcleo corta todos os momentos, e a tela não pode ficar vazia.
  function filtrarMomentos(lista, filtros) {
    const haPartida = lista.some(m => m.parte_tipo === "partida");
    const itens = lista.map((m, indice) => ({ m, indice })).filter(({ m }) => {
      if (filtros.tipo !== "todos" && m.tipo !== filtros.tipo) return false;
      if (filtros.emocao === "nenhuma" && m.emocao) return false;
      if (filtros.emocao !== "todas" && filtros.emocao !== "nenhuma" && m.emocao !== filtros.emocao) return false;
      if (filtros.soPartidas && haPartida && m.parte_tipo !== "partida") return false;
      if (filtros.soAprovados && m.aprovado !== true) return false;
      if (!filtros.mostrarDescartados && m.excluido === true) return false;
      return true;
    });
    return { itens, semPartida: !haPartida && lista.length > 0 };
  }

  function renderizarMomentos() {
    const grade = $("#moments-grid"); grade.replaceChildren();
    const soPartidas = $("#filter-competitive").checked;
    const ordenacao = $("#filter-order").value;
    const filtrado = filtrarMomentos(state.momentos, {
      tipo: $("#filter-type").value, emocao: $("#filter-emotion").value, soPartidas,
      soAprovados: $("#filter-approved").checked, mostrarDescartados: $("#filter-excluded").checked
    });
    $("#filter-competitive-note").classList.toggle("hidden", !(soPartidas && filtrado.semPartida));
    const momentos = filtrado.itens;
    momentos.sort((a, b) => ordenacao === "tempo" ? (Number(a.m.foco) || 0) - (Number(b.m.foco) || 0) : (Number(b.m.nota) || 0) - (Number(a.m.nota) || 0));
    $("#moment-count").textContent = `${momentos.length} de ${state.momentos.length} momentos`;
    if (!momentos.length) {
      const vazio = el("div", "empty-state"); vazio.append(el("strong", "", "Nenhum momento com esses filtros"), el("p", "", "Mude os filtros para ver outros momentos.")); grade.append(vazio); return;
    }
    momentos.forEach(({ m, indice }) => {
      const card = el("article", `moment-card ${m.excluido ? "excluded" : ""}`);
      card.id = `card-${indice}`;
      const thumb = el("button", "moment-thumb"); thumb.type = "button"; thumb.setAttribute("aria-label", `Reproduzir ${m.rotulo || "momento"}`);
      const arquivo = m.arquivos && typeof m.arquivos === "object" ? m.arquivos : {};
      if (arquivo.miniatura) {
        const imagem = el("img"); imagem.loading = "lazy"; imagem.alt = `Miniatura de ${m.rotulo || "momento"}`; imagem.src = urlMidia(state.videoAtual, arquivo.miniatura); thumb.append(imagem);
      } else thumb.append(el("span", "no-thumb", m.excluido ? "×" : "✦"));
      const play = el("span", "play-button");
      if (arquivo.corte) play.append(icone("play"), el("span", "", "VER CORTE")); else play.textContent = "SEM CORTE";
      thumb.append(play);
      thumb.append(el("span", `moment-rank ${classeNota(m.nota)}`, Number.isFinite(Number(m.nota)) ? `NOTA ${Math.round(Number(m.nota))}` : "SEM NOTA"));
      thumb.addEventListener("click", () => abrirPlayerMomento(m));
      const conteudo = el("div", "moment-content");
      const cabecalho = el("div", "moment-headline");
      cabecalho.append(el("h4", "", m.rotulo || (m.tipo === "reacao" ? "REAÇÃO" : "JOGADA")), el("span", "moment-time", formatarTempo(m.foco ?? m.inicio)));
      conteudo.append(cabecalho);
      const selos = el("div", "moment-meta");
      const emocaoSelo = seloEmocao(m); if (emocaoSelo) selos.append(emocaoSelo);
      if (m.round_resultado === "vitoria") selos.append(el("span", "pill", "ROUND VENCIDO"));
      if (m.round_resultado === "derrota") selos.append(el("span", "pill", "ROUND PERDIDO"));
      if (Number.isFinite(m.round)) selos.append(el("span", "pill", `ROUND ${m.round}`));
      const icones = [];
      if (Array.isArray(m.abates)) m.abates.forEach(abate => { if (Array.isArray(abate.icones)) icones.push(...abate.icones); });
      const iconeLabels = { headshot: "HS", wallbang: "WALLBANG", noscope: "NOSCOPE", smoke: "FUMAÇA", blind: "CEGO", inair: "NO AR" };
      [...new Set(icones)].forEach(nome => selos.append(el("span", "pill", iconeLabels[nome] || nome)));
      if (m.excluido) selos.append(el("span", "pill", "DESCARTADO"));
      conteudo.append(selos);
      const contexto = m.parte || (m.tipo === "reacao" ? "Reação do streamer" : "Jogada detectada");
      conteudo.append(el("div", "moment-context", contexto));
      card.append(thumb, conteudo);
      const botoes = el("div", "moment-actions");
      if (m.excluido) {
        botoes.append(button("Restaurar", "button-secondary", () => marcarMomento(m, "restaurar"), null, "undo"));
      } else {
        const gostei = button("Gostei", `button-like ${m.aprovado === true ? "selected" : ""}`, () => marcarMomento(m, "aprovar"), "Atalho: A", m.aprovado === true ? "check" : "like");
        botoes.append(gostei, button("Descartar", "button-discard", () => marcarMomento(m, "descartar"), "Atalho: D", "trash"));
      }
      card.append(botoes); grade.append(card);
    });
  }

  async function marcarMomento(momento, acao) {
    try {
      await api(`/api/videos/${encodeURIComponent(state.videoAtual)}/momento`, { method: "POST", body: { foco: momento.foco, acao } });
      const resposta = await api(`/api/videos/${encodeURIComponent(state.videoAtual)}`);
      state.dadosAtuais = resposta; state.momentos = Array.isArray(resposta.momentos?.momentos) ? resposta.momentos.momentos : [];
      if (acao === "descartar" || acao === "restaurar") {
        if (!state.pendentesAplicar[state.videoAtual]) state.pendentesAplicar[state.videoAtual] = new Set();
        const foco = String(momento.foco);
        const atual = state.momentos.find(item => Math.abs(Number(item.foco) - Number(momento.foco)) <= 0.05);
        const original = state.exclusaoInicial[state.videoAtual]?.get(foco);
        if (atual && original !== undefined && Boolean(atual.excluido) !== original) state.pendentesAplicar[state.videoAtual].add(foco);
        else state.pendentesAplicar[state.videoAtual].delete(foco);
      }
      renderizarMomentos(); montarLinhaDoTempo(); atualizarBannerAplicar();
      if (acao === "descartar") toast("Momento descartado.", "success", 5000, () => marcarMomento(momento, "restaurar"));
      else if (acao === "restaurar") toast("Momento restaurado.", "success", 5000, () => marcarMomento(momento, "descartar"));
      else toast("Marcado como gostei.", "success");
    } catch (erro) { toast(erro.message, "error", 5000); }
  }

  function atualizarBannerAplicar() {
    const conjunto = state.pendentesAplicar[state.videoAtual];
    const quantidade = conjunto ? conjunto.size : 0;
    $("#dirty-banner").classList.toggle("hidden", quantidade === 0);
    $("#dirty-message").textContent = `Você mudou ${quantidade} ${quantidade === 1 ? "momento" : "momentos"}. Clique em Aplicar para gerar de novo os cortes e a montagem.`;
  }

  async function localizarVideoAtual() {
    const caminho = state.dadosAtuais?.indice?.caminho;
    if (caminho) return caminho;
    const tipo = state.dadosAtuais?.demo ? "demo" : "video";
    const resposta = await api("/api/escolher-arquivo", { method: "POST", body: {} });
    const item = (resposta.itens || []).find(arquivo => arquivo.tipo === tipo);
    if (!item) throw new Error(tipo === "demo" ? "Escolha a demo original (.dem) para continuar." : "Escolha o arquivo de vídeo original para continuar.");
    return item.caminho;
  }

  async function reprocessarVideo(motivo) {
    try {
      const caminho = await localizarVideoAtual();
      const opcoes = { gerar_cortes: true, gerar_shorts: true, gerar_montagem: true };
      // Demo de novo: reaproveita o nome do jogador que a análise anterior achou.
      if (state.dadosAtuais?.demo?.jogador) opcoes.jogador = String(state.dadosAtuais.demo.jogador);
      await adicionarFila([caminho], opcoes, motivo);
    } catch (erro) { toast(erro.message, "error", 5000); }
  }

  async function copiarCapitulos() {
    try {
      const resposta = await fetch(urlMidia(state.videoAtual, "capitulos_youtube.txt"), { cache: "no-store" });
      if (!resposta.ok) throw new Error("Não encontrei os capítulos deste vídeo.");
      const texto = await resposta.text();
      await navigator.clipboard.writeText(texto);
      toast("Capítulos copiados. Agora é só colar no YouTube.", "success");
    } catch (erro) { toast(erro.message || "Não consegui copiar. Abra a página de capítulos e copie o texto.", "error", 5000); }
  }

  function abrirPlayerMomento(momento) {
    const arquivos = momento.arquivos && typeof momento.arquivos === "object" ? momento.arquivos : {};
    if (!arquivos.corte) { toast("Este momento ainda está sem corte. Gere os cortes para assistir aqui."); return; }
    abrirPlayer(urlMidia(state.videoAtual, arquivos.corte), momento.rotulo || "JOGADA", `Momento em ${formatarTempo(momento.foco ?? momento.inicio)}`, arquivos.short ? urlMidia(state.videoAtual, arquivos.short) : null,
      arquivos.short_efeitos ? urlMidia(state.videoAtual, arquivos.short_efeitos) : null);
  }

  function abrirPlayerMontagem(arquivo) {
    if (!arquivo) return;
    abrirPlayer(urlMidia(state.videoAtual, arquivo), "MONTAGEM", "Melhores momentos do vídeo", null);
  }

  function abrirPlayer(url, titulo, legenda, shortUrl, efeitosUrl) {
    const modal = $("#player-modal");
    $("#player-title").textContent = titulo;
    $("#player-caption").textContent = legenda;
    $("#moment-player").src = url;
    $("#moment-player").dataset.mainUrl = url;
    $("#moment-player").load();
    $("#short-tab").classList.toggle("hidden", !shortUrl);
    $("#short-tab").dataset.url = shortUrl || "";
    $("#efeitos-tab").classList.toggle("hidden", !efeitosUrl);
    $("#efeitos-tab").dataset.url = efeitosUrl || "";
    $("#player-tabs").classList.toggle("hidden", !shortUrl && !efeitosUrl);
    $$(".player-tab").forEach(tab => tab.classList.toggle("active", tab.dataset.playerKind === "corte"));
    state.tipoPlayer = "corte";
    modal.classList.remove("hidden");
    // Foco no vídeo, não no Fechar: o Espaço (play/pause) acionava o botão e fechava o player.
    $("#moment-player").focus();
  }

  // Dentro do player, o Espaço sempre toca/pausa (também com o foco no Fechar ou numa aba); só não em campo de texto.
  function espacoTocaPlayer(tag) {
    return !["INPUT", "SELECT", "TEXTAREA"].includes(tag);
  }

  function trocarPlayer(tipo) {
    const tab = $(`.player-tab[data-player-kind="${tipo}"]`);
    if (!tab || tab.classList.contains("hidden")) return;
    const video = $("#moment-player");
    if (tipo === "short" || tipo === "efeitos") video.src = tab.dataset.url;
    else if (state.videoAtual && state.dadosAtuais) {
      // O vídeo atual foi guardado em data-player-main ao abrir; não usar caminhos inventados.
      video.src = video.dataset.mainUrl || video.src;
    }
    video.load(); state.tipoPlayer = tipo;
    $$(".player-tab").forEach(item => item.classList.toggle("active", item.dataset.playerKind === tipo));
  }

  async function carregarConfiguracao() {
    const grupos = $("#config-groups"); grupos.replaceChildren();
    try {
      const resposta = await api("/api/config");
      state.configCampos = resposta.campos || [];
      state.configAlteracoes = {};
      const agrupados = new Map();
      state.configCampos.forEach(campo => {
        if (!agrupados.has(campo.secao)) agrupados.set(campo.secao, []);
        agrupados.get(campo.secao).push(campo);
      });
      agrupados.forEach((campos, secao) => {
        const grupo = el("section", "config-group");
        const cabecalho = el("div", "config-group-header");
        cabecalho.append(el("h3", "", secaoAmigavel(secao).toUpperCase()));
        grupo.append(cabecalho);
        campos.forEach(campo => grupo.append(criarCampoConfig(campo)));
        grupos.append(grupo);
      });
      $("#config-message").classList.add("hidden");
    } catch (erro) {
      const aviso = el("div", "notice notice-error", erro.message);
      grupos.append(aviso);
    }
  }

  function criarCampoConfig(campo) {
    const linha = el("div", "config-field");
    const texto = el("div", "config-field-copy");
    const rotuloId = `cfg-${campo.secao}-${campo.chave}`;
    const label = el("label", "", campo.rotulo); label.htmlFor = rotuloId;
    texto.append(label, el("small", "", campo.dica || ""));
    const controle = el("div", "config-control");
    let entrada;
    if (campo.tipo === "bool") {
      entrada = el("input", "switch"); entrada.type = "checkbox"; entrada.checked = Boolean(campo.valor);
    } else if (campo.tipo === "opcao") {
      entrada = el("select");
      (campo.opcoes || []).forEach(opcao => { const option = el("option", "", opcao); option.value = opcao; entrada.append(option); });
      entrada.value = campo.valor === null ? "" : String(campo.valor);
    } else {
      entrada = el("input");
      entrada.type = campo.tipo === "texto" ? "text" : "number";
      if (campo.tipo === "numero") entrada.step = "any";
      if (campo.tipo === "inteiro") entrada.step = "1";
      if (campo.minimo !== null && campo.minimo !== undefined) entrada.min = String(campo.minimo);
      if (campo.maximo !== null && campo.maximo !== undefined) entrada.max = String(campo.maximo);
      if (campo.comprimento) entrada.maxLength = campo.comprimento;
      entrada.value = campo.valor === null || campo.valor === undefined ? "" : String(campo.valor);
      if (campo.valor === null || campo.valor === undefined) { entrada.placeholder = "Padrão não disponível"; entrada.title = "O núcleo não informou este valor padrão."; }
    }
    entrada.id = rotuloId;
    entrada.dataset.chave = `${campo.secao}.${campo.chave}`;
    entrada.addEventListener("change", () => {
      let valor;
      if (campo.tipo === "bool") valor = entrada.checked;
      else if (campo.tipo === "numero") valor = entrada.value === "" ? null : Number(entrada.value);
      else if (campo.tipo === "inteiro") valor = entrada.value === "" ? null : Number.parseInt(entrada.value, 10);
      else valor = entrada.value;
      state.configAlteracoes[entrada.dataset.chave] = valor;
      entrada.closest(".config-field")?.classList.add("changed");
    });
    controle.append(entrada); linha.append(texto, controle); return linha;
  }

  async function salvarConfiguracao() {
    if (!Object.keys(state.configAlteracoes).length) { toast("Nenhuma alteração para salvar."); return; }
    if (Object.values(state.configAlteracoes).some(valor => valor === null || (typeof valor === "number" && !Number.isFinite(valor)))) {
      toast("Preencha os campos numéricos antes de salvar.", "error"); return;
    }
    try {
      const resultado = await api("/api/config", { method: "POST", body: { alteracoes: state.configAlteracoes } });
      mostrarMensagem($("#config-message"), resultado.mensagem || "Salvo.", "ok");
      state.configAlteracoes = {};
      await carregarConfiguracao();
    } catch (erro) { mostrarMensagem($("#config-message"), erro.message, "error"); }
  }

  async function restaurarPadroes() {
    if (!state.configCampos.length) { toast("Não consegui carregar as opções de configuração.", "error"); return; }
    try {
      const chaves = state.configCampos.map(campo => `${campo.secao}.${campo.chave}`);
      const resultado = await api("/api/config/padroes", { method: "POST", body: { chaves } });
      mostrarMensagem($("#config-message"), resultado.mensagem || "Padrões restaurados.", "ok");
      await carregarConfiguracao();
    } catch (erro) { mostrarMensagem($("#config-message"), erro.message, "error"); }
  }

  // Primeira abertura: boas-vindas curtas que pedem o @ do canal (vai para shorts.marca). Só aparece uma vez.
  const LIMITE_MARCA = 40;
  function marcaDoCanal(texto) {
    const limpo = String(texto || "").trim();
    if (!limpo) return "";
    // Só põe o @ quando cabe no limite de shorts.marca (40); senão manda como está.
    return limpo.startsWith("@") || limpo.length >= LIMITE_MARCA ? limpo : `@${limpo}`;
  }

  // Enquanto as boas-vindas estão abertas, o resto da página fica inerte (o Tab não sai do quadro).
  function travarFundo(travar) {
    $$("header.topbar, main.main-area").forEach(parte => { parte.inert = travar; });
  }

  async function verificarBoasVindas() {
    try {
      const resposta = await api("/api/boas-vindas");
      if (!resposta.mostrar) return;
      $("#welcome-marca").value = typeof resposta.marca === "string" ? resposta.marca : "";
      $("#welcome-modal").classList.remove("hidden");
      travarFundo(true);
      $("#welcome-marca").focus();
    } catch (erro) { console.warn(erro); }
  }

  async function concluirBoasVindas(pular) {
    const erroEl = $("#welcome-error");
    erroEl.classList.add("hidden");
    const corpo = pular ? {} : { marca: marcaDoCanal($("#welcome-marca").value) };
    try {
      await api("/api/boas-vindas", { method: "POST", body: corpo });
      $("#welcome-modal").classList.add("hidden");
      travarFundo(false);
      state.configCampos = [];   // a tela Configurar relê o @ novo
      toast(pular ? "Tudo certo. Dá para pôr o seu @ depois em Configurar." : "Pronto! Agora é só escolher um vídeo.", "success", 4200);
    } catch (erro) {
      erroEl.textContent = erro.message;
      erroEl.classList.remove("hidden");
    }
  }

  function fecharPlayer() {
    const video = $("#moment-player");
    video.pause(); video.removeAttribute("src"); video.load();
    $("#player-modal").classList.add("hidden");
  }

  function configurarEventos() {
    $$(".nav-item").forEach(item => item.addEventListener("click", () => trocarTela(item.dataset.view)));
    window.addEventListener("hashchange", seguirEndereco);
    $("#refresh-button").addEventListener("click", () => { carregarBiblioteca(false); atualizarFila(); toast("Tela atualizada."); });
    $("#choose-files").addEventListener("click", escolherArquivos);
    $("#show-input-folder").addEventListener("click", carregarEntrada);
    $("#go-library").addEventListener("click", () => trocarTela("videos"));
    $("#clear-files").addEventListener("click", () => { state.arquivos = []; renderizarArquivosEscolhidos(); });
    $("#process-button").addEventListener("click", processarSelecionados);
    $("#opt-cuts").addEventListener("change", renderizarArquivosEscolhidos);
    $("#cancel-all").addEventListener("click", async () => {
      try { await api("/api/fila/cancelar-tudo", { method: "POST", body: {} }); toast("Pedido de cancelamento enviado."); atualizarFila(); }
      catch (erro) { toast(erro.message, "error"); }
    });
    $("#toggle-details").addEventListener("click", () => {
      const painel = $("#details-panel"); const aberto = painel.classList.toggle("hidden") === false;
      $("#toggle-details").setAttribute("aria-expanded", String(aberto));
      $("#toggle-details").textContent = aberto ? "Ocultar detalhes −" : "Mostrar detalhes ＋";
    });
    $("#drop-zone").addEventListener("click", evento => { if (evento.target.closest("button")) return; escolherArquivos(); });
    $("#drop-zone").addEventListener("keydown", evento => { if (evento.key === "Enter") { evento.preventDefault(); escolherArquivos(); } });
    ["dragenter", "dragover"].forEach(nome => $("#drop-zone").addEventListener(nome, evento => { evento.preventDefault(); $("#drop-zone").classList.add("dragover"); }));
    ["dragleave", "drop"].forEach(nome => $("#drop-zone").addEventListener(nome, evento => { evento.preventDefault(); $("#drop-zone").classList.remove("dragover"); }));
    $("#drop-zone").addEventListener("drop", evento => enviarArquivos(evento.dataTransfer.files));
    $$("#filter-type, #filter-emotion, #filter-order, #filter-competitive, #filter-approved, #filter-excluded").forEach(filtro => filtro.addEventListener("change", () => { renderizarMomentos(); montarLinhaDoTempo(); }));
    $("#back-library").addEventListener("click", () => { $("#result-view").classList.add("hidden"); $("#library-wrap").classList.remove("hidden"); mudarEndereco("#videos"); carregarBiblioteca(); });
    $("#apply-changes").addEventListener("click", () => reprocessarVideo("aplicar"));
    $("#open-output-folder").addEventListener("click", () => abrirPasta());
    $("#help-open-output").addEventListener("click", () => abrirPasta());
    $("#run-check").addEventListener("click", async () => {
      const botao = $("#run-check"); const original = Array.from(botao.childNodes); botao.disabled = true; botao.textContent = "Verificando…";
      try {
        const r = await api("/api/checar", { method: "POST", body: {} });
        const caixa = $("#check-result"); caixa.textContent = r.texto || "Sem resposta."; caixa.classList.remove("hidden");
      } catch (erro) { toast(erro.message, "error"); }
      finally { botao.disabled = false; botao.replaceChildren(...original); }
    });
    $("#save-config").addEventListener("click", salvarConfiguracao);
    $("#reset-config").addEventListener("click", restaurarPadroes);
    $("#welcome-form").addEventListener("submit", evento => { evento.preventDefault(); concluirBoasVindas(false); });
    $("#welcome-skip").addEventListener("click", () => concluirBoasVindas(true));
    $("#close-player").addEventListener("click", fecharPlayer);
    $("#player-modal").addEventListener("click", evento => { if (evento.target === $("#player-modal")) fecharPlayer(); });
    $$(".player-tab").forEach(tab => tab.addEventListener("click", () => trocarPlayer(tab.dataset.playerKind)));
    document.addEventListener("keydown", evento => {
      if (evento.key === "Escape" && !$("#welcome-modal").classList.contains("hidden")) { concluirBoasVindas(true); return; }   // Esc = Pular
      if (evento.key === "Escape") { fecharPlayer(); return; }
      if ($("#player-modal").classList.contains("hidden") === false) {
        if (evento.code === "Space" && espacoTocaPlayer(document.activeElement?.tagName)) {
          evento.preventDefault();
          const player = $("#moment-player");
          if (player.paused) player.play().catch(() => {}); else player.pause();
        }
        return;
      }
      if (!state.videoAtual || $("#view-videos").classList.contains("active") === false) return;
      if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
      if (evento.key.toLowerCase() === "j" || evento.key.toLowerCase() === "k") {
        const cards = $$(".moment-card", $("#moments-grid"));
        if (!cards.length) return;
        const atual = cards.findIndex(card => card.id === document.activeElement?.closest(".moment-card")?.id);
        const novo = evento.key.toLowerCase() === "j" ? Math.min(cards.length - 1, Math.max(0, atual + 1)) : Math.max(0, atual < 0 ? 0 : atual - 1);
        cards[novo].scrollIntoView({ behavior: "smooth", block: "center" }); cards[novo].tabIndex = -1; cards[novo].focus({ preventScroll: true });
      } else if (evento.key.toLowerCase() === "a") {
        const atual = document.activeElement?.closest(".moment-card"); atual?.querySelector(".button-like")?.click();
      } else if (evento.key.toLowerCase() === "d") {
        const atual = document.activeElement?.closest(".moment-card"); atual?.querySelector(".button-discard")?.click();
      } else if (evento.code === "Space") {
        const video = $("#moment-player");
        if (video.src && !video.paused) video.pause();
        else if (video.src) video.play().catch(() => {});
      }
    });
  }

  async function inicializar() {
    configurarEventos();
    renderizarArquivosEscolhidos();
    seguirEndereco();
    verificarBoasVindas();
    await Promise.all([carregarBiblioteca(false), atualizarFila()]);
    if (state.timerFila) window.clearInterval(state.timerFila);
    state.timerFila = window.setInterval(() => { atualizarFila(); }, 1000);
  }

  // Guardar o endereço principal do player ao abrir para que a aba de short possa alternar com segurança.
  const abrirPlayerOriginal = abrirPlayer;
  abrirPlayer = function (url, titulo, legenda, shortUrl, efeitosUrl) {
    abrirPlayerOriginal(url, titulo, legenda, shortUrl, efeitosUrl);
    $("#moment-player").dataset.mainUrl = url;
  };

  document.addEventListener("DOMContentLoaded", inicializar);

  // Só para os testes (Node, tests/test_r1_ui.py): no navegador `module` não existe e nada é exposto.
  if (typeof module === "object" && module && module.exports) {
    module.exports = { state, assinaturaFila, renderizarFila, renderizarProgresso, jobReferencia, filtrarMomentos,
      posicaoParte, espacoTocaPlayer, eDemoIndice, renderizarBiblioteca, avisoEnvioGrande, marcaDoCanal, resumoPlacar, nomeVideo,
      atualizarPassos, montarPublicar };
  }
})();
