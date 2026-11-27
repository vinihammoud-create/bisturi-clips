"""Leitura e edição pontual e validada do config.toml."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import threading
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Callable
from cs2clip.tools import substituir_arquivo


@dataclass(frozen=True)
class CampoConfig:
    secao: str
    chave: str
    tipo: str
    rotulo: str
    dica: str
    minimo: float | int | None = None
    maximo: float | int | None = None
    opcoes: tuple[str, ...] = ()
    comprimento: int | None = None
    proibidos: str = ""   # caracteres recusados (ex.: { } \ viram comandos na legenda do short)
    so_digitos: bool = False   # texto que só pode ter números (ex.: SteamID64); vazio continua valendo (= não configurado)


CAMPOS: tuple[CampoConfig, ...] = (
    CampoConfig("cortes", "antes", "numero", "Segundos antes da jogada", "Quanto mostrar antes do momento.", 0, 15),
    CampoConfig("cortes", "depois", "numero", "Segundos depois da jogada", "Quanto mostrar depois do momento.", 0, 15),
    CampoConfig("cortes", "falha_com_reacao", "bool", "Valorizar \"começou bem e terminou mal\", com a minha reação",
                "A jogada em que você morre logo depois do último abate e reage (raiva ou risada) vira short mesmo com menos de 3 abates, ganha nota e o corte vai até o fim da reação; FAILS com raiva ou risada também podem virar short. Fica fora da montagem com música (ela cobriria a reação). Começa desligado."),
    CampoConfig("cortes", "juntar_intervalo", "numero", "Unir abates próximos", "Abates mais próximos que isso viram uma jogada só (s).", 1, 30),
    CampoConfig("cortes", "juntar_no_round", "numero", "Juntar os abates do mesmo round", "Todos os abates do round viram um vídeo só (0 desliga).", 0, 300),
    CampoConfig("cortes", "depois_de_cada_abate", "numero", "Segundos depois de cada abate", "Numa jogada com vários abates, o vídeo fica este tempo depois de cada abate antes de pular para o próximo (padrão 6).", 0, 30),
    CampoConfig("cortes", "antes_do_proximo", "numero", "Segundos antes do próximo abate", "Depois de um pulo, o vídeo volta este tempo antes do abate seguinte (padrão 6).", 0, 30),
    CampoConfig("cortes", "pulo_minimo", "numero", "Só pular se sobrar pelo menos (s)", "Se o tempo a cortar entre dois abates for menor que isso, o vídeo não pula e fica contínuo (padrão 2).", 0, 30),
    CampoConfig("cortes", "duracao_minima", "numero", "Duração mínima do corte", "Duração mínima de cada corte (s).", 3, 30),
    CampoConfig("cortes", "estender_reacao", "bool", "Continuar enquanto eu reajo", "Estende o corte se a reação continuar."),
    CampoConfig("cortes", "reacoes_sem_abate", "inteiro", "Momentos só de reação", "Quantos momentos sem abate por vídeo.", 0, 30),
    CampoConfig("cortes", "so_partidas", "bool", "Só partidas competitivas", "Ignora aquecimento e Deathmatch."),
    CampoConfig("cortes", "limiar_reacao", "numero", "Sensibilidade ao grito", "Maior = só gritos mais fortes.", 0.3, 0.95),
    CampoConfig("cortes", "min_abates_round", "inteiro", "Abates mínimos no round (montagem e short)", "Montagem e short só levam rounds com este número de abates seus ou mais (o padrão é 3; 0 desliga e volta a decidir só pela nota).", 0, 10),
    CampoConfig("cortes", "nota_montagem", "numero", "Nota mínima para a montagem", "Só entram momentos com esta nota ou maior.", 0, 100),
    CampoConfig("cortes", "nota_short", "numero", "Nota mínima para virar short", "Só viram shorts momentos com esta nota ou maior.", 0, 100),
    # Efeitos do corte 16:9: começam desligados (mudam o ritmo e o visual; ligue depois de ver o resultado).
    CampoConfig("cortes", "speed_ramp", "bool", "Acelerar o caminho entre abates",
                "Acelera 1,75x o trecho vazio entre dois abates distantes. Começa desligado: veja antes de usar."),
    CampoConfig("cortes", "freeze_headshot", "bool", "Congelar o headshot decisivo",
                "Para a imagem 0,25 s no último headshot, com zoom curto e som de impacto. Começa desligado."),
    CampoConfig("cortes", "cartao_tatico", "bool", "Cartão da jogada no início",
                "Mostra \"ROUND 12 · CLUTCH 1v4\" nos primeiros segundos do corte, estilo transmissão. Começa desligado."),
    CampoConfig("shorts", "gerar", "bool", "Gerar shorts verticais", "Cria vídeos verticais 9:16."),
    CampoConfig("shorts", "layout", "opcao", "Formato do short em pé",
                "classico: o de sempre (recorte no rosto, faixa escura no topo). webcam_inteira: a webcam inteira com o letreiro em cima, o jogo embaixo com a mira no centro e o fundo desfocado do próprio jogo (o formato que funcionou nos Shorts). Começa no clássico.", opcoes=("classico", "webcam_inteira")),
    CampoConfig("shorts", "webcam_retangulo", "texto", "Onde fica a sua webcam (x,y,largura,altura)",
                "Em pixels de 1920x1080, ex.: 32,428,456,276. Vazio = a câmera que a análise achar. Vale no formato webcam_inteira.", comprimento=25, proibidos="{}\\"),
    CampoConfig("shorts", "duracao_maxima", "numero", "Duração máxima do short (s)", "Jogada maior que isso vira short compacto.", 15, 180),
    CampoConfig("shorts", "quantidade", "inteiro", "Quantidade de shorts", "Limite de shorts por vídeo.", 0, 30),
    CampoConfig("shorts", "legendas", "bool", "Legendas nos shorts", "Ativa legendas nos vídeos verticais."),
    CampoConfig("shorts", "selo_jogada", "bool", "Título da jogada no short", "Mostra ACE, DOUBLE KILL e outros títulos."),
    CampoConfig("shorts", "gancho_titulo", "bool", "Título no começo do short",
                "Mostra o nome da jogada (ex.: TRIPLE KILL) já no início, antes do 1º abate. Desligado = só o título de cada abate (o perfil esports_blast desliga)."),
    CampoConfig("shorts", "zoom_no_abate", "bool", "Zoom a cada abate", "Aplica um zoom rápido a cada abate."),
    CampoConfig("shorts", "pan_suave", "bool", "Câmera balança no abate",
                "O recorte do jogo se move de leve (até 18 px) a cada abate, em vez de ficar parado. Começa desligado."),
    CampoConfig("shorts", "marca", "texto", "Seu @ no short", "Texto discreto no topo do short (só aparece quando o programa detecta a sua webcam) e nas hashtags (vazio = sem texto).",
                comprimento=40, proibidos="{}\\"),
    CampoConfig("demo", "automatica", "bool", "Procurar a demo da partida no CS2", "Acha sozinho a demo (.dem) desta gravação na pasta do CS2 (só lê e copia) e descobre quem é você pelos abates. Baixe a partida antes: CS2, Assistir, Suas partidas."),
    CampoConfig("resenha", "gerar", "bool", "Achar momentos de resenha", "Short dos momentos engraçados: Discord alto, várias vozes ao mesmo tempo, você gritando ou rindo (2 sinais juntos)."),
    CampoConfig("resenha", "discord_basta", "bool", "Discord alto sozinho já é resenha", "Liga: um pico de volume do Discord (ou várias vozes ao mesmo tempo) já vira momento de resenha, sem precisar de um segundo sinal. Seu grito sozinho nunca basta. Começa desligado."),
    CampoConfig("capa", "estilo", "opcao", "Estilo da capa", "Grande = letras grandes (o padrão). Discreta = cartão pequeno com o round. "
                "Chamativa = o jogo (e a sua webcam no canto, se houver) com um texto curto e grande que dá vontade de clicar (\"3 KILLS\", \"COMEÇOU BEM...\"). "
                "A outra versão sai ao lado.", opcoes=("grande", "discreta", "chamativa")),
    CampoConfig("capa", "alternativas_da_mesma_jogada", "bool", "Capas alternativas com uma jogada só",
                "Quando o vídeo tem só uma jogada na montagem, gera também capas de outros kills da mesma jogada (pasta capas, com o capas.json). Começa desligado; o kit de imagem limpa da demo já faz isso sozinho."),
    CampoConfig("montagem", "gerar", "bool", "Gerar montagem", "Cria um vídeo com os melhores momentos."),
    CampoConfig("shorts", "mostrar_killfeed", "bool", "Kill feed no short", "Sobrepõe a tira do kill feed (16:9) no topo do short 9:16 durante cada abate."),
    CampoConfig("montagem", "perfil", "opcao", "Perfil de edição", "Estilo pronto de edição (vazio = personalizado).", opcoes=("", "esports_blast", "fragmovie_musical", "criador_live", "jogo_puro", "shorts_viral")),
    CampoConfig("montagem", "ordem", "opcao", "Ordem da montagem", "Escolha como ordenar os momentos.", opcoes=("cronologica", "melhores", "arco")),
    CampoConfig("montagem", "transicao_tipo", "opcao", "Tipo de transição", "Corte seco (esports) ou fade suave (fragmovie).", opcoes=("corte", "fade")),
    CampoConfig("montagem", "musica", "texto", "Música da montagem", "auto = escolhe da pasta musicas/, vazio = sem música, ou nome do arquivo .mp3 (só o nome, sem pasta).", comprimento=80, proibidos="{}\\/:"),
    CampoConfig("montagem", "versao_com_voz", "bool", "Também gerar a montagem com a minha voz",
                "Cria MONTAGEM_VOZ_*.mp4 ao lado da montagem musical: as mesmas jogadas, com o seu microfone e sem música. Começa desligado."),
    CampoConfig("montagem", "ducking_tiros_db", "numero", "Música abaixa a cada abate (dB)",
                "Quanto a música abaixa por ~0,2 s em cada abate. 0 = desligado (padrão). Comece com 2 a 4 e ouça.", 0, 6),
    CampoConfig("montagem", "grade_de_corte", "opcao", "Cortar na batida ou no compasso",
                "Batida = qualquer batida (o de sempre). Compasso = prefere o \"1\" do compasso quando a música tem compasso confiável. Começa em batida.",
                opcoes=("batida", "compasso")),
    CampoConfig("montagem", "corte_na_batida", "bool", "Cortar na batida da música", "Sincroniza as trocas de cena com as batidas da música."),
    CampoConfig("montagem", "tiro_no_drop", "bool", "O 1º tiro cai no drop da música",
                "A música pula o começo até o drop dela chegar no primeiro tiro da montagem (só em músicas com drop medido). Ligado por padrão."),
    CampoConfig("montagem", "incluir_resenha", "bool", "A resenha também entra na montagem musical",
                "Os momentos engraçados (Discord alto, risadas) viram short. Aqui você escolhe se entram também na montagem com música, que cobre as vozes. Começa desligado."),
    CampoConfig("montagem", "kill_sync", "bool", "Kill-Sync (1º tiro na batida)", "Ajusta o início do corte para o primeiro abate cair cravado na batida."),
    CampoConfig("montagem", "sfx", "bool", "Efeitos sonoros (whoosh e impacto)", "Adiciona efeitos sonoros abertos CC0 nas transições e headshots."),
    CampoConfig("audio", "faixa_jogo", "inteiro", "Faixa do OBS só com o jogo",
                "Número da faixa que grava só o som do jogo (1 a 6; 0 = não tenho). Com ela, a montagem troca a sua voz por música.", 0, 6),
    CampoConfig("audio", "faixa_voz", "inteiro", "Faixa do OBS só com o microfone",
                "Número da faixa que grava só a sua voz (1 a 6; 0 = não tenho).", 0, 6),
    CampoConfig("audio", "faixa_comms", "inteiro", "Faixa do OBS só com o Discord",
                "Número da faixa que grava só o Discord/comunicação (2 a 6; 0 = não tenho). Ela nunca entra no áudio que o programa monta.", 0, 6),
    CampoConfig("audio", "tratar_voz", "bool", "Limpar a voz e baixar o jogo quando eu falo",
                "Precisa das duas faixas acima. Começa desligado."),
    CampoConfig("audio", "separar_jogo_faixa_unica", "bool", "Tirar a voz quando tudo está misturado",
                "Gravação sem faixa só do jogo: cria essa faixa tirando a voz (IA, se instalada; senão um filtro mais simples). "
                "Assim a montagem ganha música. Começa desligado."),
    CampoConfig("fails", "exigir_aprovacao", "bool", "FAILS só com a minha aprovação",
                "A compilação de FAILS leva só os que você marcou Gostei na revisão. Começa desligado: leva todos."),
    CampoConfig("transcricao", "modelo", "opcao", "Qualidade da transcrição", "Modelo menor costuma ser mais rápido.", opcoes=("large-v3-turbo", "medium", "small")),
    CampoConfig("transcricao", "idioma", "opcao", "Idioma da fala", "Idioma em que você fala na live. As emoções (HYPE, RAIVA...) só funcionam em português.",
                opcoes=("pt", "en", "es")),
    CampoConfig("chat", "usar", "bool", "Usar chat da Twitch", "Considera o chat da transmissão."),
    CampoConfig("qualidade", "encoder", "opcao", "Codificador de vídeo", "Auto escolhe uma opção disponível.", opcoes=("auto", "h264_nvenc", "hevc_nvenc", "libx264")),
    CampoConfig("qualidade", "cq", "inteiro", "Qualidade do vídeo (CQ)", "Menor = melhor qualidade e arquivo maior.", 15, 30),
    CampoConfig("qualidade", "renders_paralelos", "inteiro", "Cortes ao mesmo tempo", "Mais cortes simultâneos usam mais recursos.", 1, 4),
    CampoConfig("qualidade", "checar_conteudo", "bool", "Conferir se o vídeo saiu preto", "Recusa arquivo com a imagem toda preta (leva menos de 1 s)."),
    CampoConfig("qualidade", "gate_loudness", "bool", "Reprovar volume ou pico fora do padrão",
                "Reprova a montagem se o volume sair longe de -14 LUFS ou o pico passar de -1 dBTP. Começa desligado: só avisa."),
    CampoConfig("qualidade", "garantir_pico", "bool", "Garantir pico de som abaixo de -1 dBTP",
                "Mede o arquivo pronto e, se o pico passar de -1 dBTP (padrão do YouTube/TikTok), baixa só o volume. Começa ligado."),
    CampoConfig("qualidade", "checar_silencio", "bool", "Conferir se o som saiu mudo",
                "Recusa também arquivo sem som nenhum. Começa desligado: pode dar alarme falso."),
    CampoConfig("demo", "jogador", "texto", "Meu nome no jogo (para demos)", "Use o nome ou parte dele como aparece no jogo.", comprimento=60),
    # Gravar pela demo (Parte D): as duas contas do Steam. O Bisturi nunca guarda senha e nunca troca de conta sozinho.
    CampoConfig("gravacao_demo", "conta_steamid", "texto", "SteamID64 da conta do Bisturi (a 2ª conta)",
                "A conta que ASSISTE a demo e grava. Nunca a que você joga. Abra o Steam nessa conta e clique em \"Usar a conta aberta agora no Steam\", ou digite o SteamID64 (17 números).", comprimento=17, so_digitos=True),
    CampoConfig("gravacao_demo", "conta_apelido", "texto", "Como você chama esta conta",
                "Só um nome para você reconhecer a conta do Bisturi nos avisos (ex.: Smurf do Bisturi). Pode deixar vazio.", comprimento=40),
    CampoConfig("gravacao_demo", "conta_principal_id", "texto", "SteamID64 da sua conta principal",
                "A conta com que você joga. Serve só para o Bisturi RECUSAR gravar se ela estiver logada. Abra o Steam nessa conta e clique em \"Usar a conta aberta agora no Steam\", ou digite o SteamID64 (17 números).", comprimento=17, so_digitos=True),
    CampoConfig("gravacao_demo", "conta_principal_apelido", "texto", "Como você chama esta conta",
                "Só um nome para você reconhecer a sua conta principal nos avisos (ex.: Conta de jogo). Pode deixar vazio.", comprimento=40),
    CampoConfig("gravacao_demo", "so_rounds_bons", "bool", "Gravar só os rounds bons",
                "Só grava os rounds em que você fez o mínimo de abates (3). Desligado = a partida inteira (bem mais demorado)."),
)
CAMPOS_POR_CHAVE = {(campo.secao, campo.chave): campo for campo in CAMPOS}

# R-007 (26/11/2026): o "Voltar aos padrões" geral manda TODAS as chaves; estas nunca são apagadas por ele (as contas do Steam e o nome na demo
# são do dono, não um ajuste do programa). Para limpar uma delas: apague o texto na tela e salve.
PROTEGIDOS_NO_RESET = {("gravacao_demo", "conta_steamid"), ("gravacao_demo", "conta_apelido"), ("gravacao_demo", "conta_principal_id"),
                       ("gravacao_demo", "conta_principal_apelido"), ("demo", "jogador")}


class ErroConfig(ValueError):
    pass


class ConfiguracaoProjeto:
    def __init__(self, raiz: Path, load_config_fn: Callable[[Path], Any] | None = None, config_cls: Callable[[], Any] | None = None):
        self.raiz = raiz.resolve()
        self.caminho = self.raiz / "config.toml"
        self.load_config_fn = load_config_fn
        self.config_cls = config_cls
        # as rotas de salvar rodam em threads (R-010): sem esta trava, dois cliques em Salvar se cruzavam e a cópia de segurança
        # guardava o arquivo já mudado pelo primeiro (revisão final de 26/11)
        self._trava = threading.RLock()

    def _resolver_core(self) -> tuple[Callable[[Path], Any], Callable[[], Any]]:
        load = self.load_config_fn
        cls = self.config_cls
        if load is None or cls is None:
            try:
                from cs2clip.config import Config, load_config
            except (ImportError, AttributeError) as exc:
                raise ErroConfig("Não encontrei a configuração do programa. Abra a pasta do projeto original e tente novamente.") from exc
            load = load or load_config
            cls = cls or Config
        return load, cls

    def _config_toml(self) -> tuple[str, dict[str, Any]]:
        try:
            from cs2clip.config import nascer_do_exemplo        # o config.toml que ainda não existe nasce do config.exemplo.toml ao lado
            nascer_do_exemplo(self.caminho)
        except (ImportError, OSError):
            pass
        try:
            if self.caminho.is_symlink() or self.caminho.resolve(strict=False).parent != self.raiz:
                raise ErroConfig("O config.toml não pode ser um link para outro arquivo.")
            texto = self.caminho.read_bytes().decode("utf-8") if self.caminho.exists() else ""
            estrutura = tomllib.loads(texto) if texto.strip() else {}
        except (OSError, tomllib.TOMLDecodeError, UnicodeError) as exc:
            raise ErroConfig("O config.toml está ilegível. Nenhuma alteração foi feita.") from exc
        if not isinstance(estrutura, dict):
            raise ErroConfig("O config.toml não contém opções válidas.")
        return texto, estrutura

    @staticmethod
    def _atributo_padrao(config_obj: Any, secao: str, chave: str) -> Any:
        if isinstance(config_obj, dict):
            grupo = config_obj.get(secao)
        else:
            grupo = getattr(config_obj, secao, None)
        if isinstance(grupo, dict):
            return grupo.get(chave, _AUSENTE)
        if grupo is not None:
            return getattr(grupo, chave, _AUSENTE)
        return _AUSENTE

    def padrao(self, secao: str, chave: str) -> Any:
        _, cls = self._resolver_core()
        try:
            instancia = cls()
        except Exception as exc:
            raise ErroConfig("Não consegui consultar os padrões do programa. Nenhum valor foi inventado.") from exc
        valor = self._atributo_padrao(instancia, secao, chave)
        if valor is _AUSENTE:
            raise ErroConfig(f"O núcleo não documenta o padrão de [{secao}] {chave}. É preciso confirmar como esse campo aparece em Config().")
        return valor

    def ler(self) -> dict[str, Any]:
        _, estrutura = self._config_toml()
        campos: list[dict[str, Any]] = []
        for campo in CAMPOS:
            valores_secao = estrutura.get(campo.secao, {})
            configurado = isinstance(valores_secao, dict) and campo.chave in valores_secao
            if configurado:
                valor = valores_secao[campo.chave]
                origem = "config.toml"
            else:
                valor = self.padrao(campo.secao, campo.chave)
                origem = "padrão do programa"
            campos.append({
                "secao": campo.secao, "chave": campo.chave, "tipo": campo.tipo,
                "rotulo": campo.rotulo, "dica": campo.dica,
                "minimo": campo.minimo, "maximo": campo.maximo,
                "opcoes": list(campo.opcoes), "valor": valor, "origem": origem,
                "comprimento": campo.comprimento,
            })
        return {"campos": campos}

    @staticmethod
    def validar(campo: CampoConfig, valor: Any) -> Any:
        if campo.tipo == "bool":
            if not isinstance(valor, bool):
                raise ErroConfig(f"{campo.rotulo}: escolha ligado ou desligado.")
            return valor
        if campo.tipo == "texto":
            if not isinstance(valor, str):
                raise ErroConfig(f"{campo.rotulo}: digite um texto.")
            if len(valor) > (campo.comprimento or 60):
                raise ErroConfig(f"{campo.rotulo}: use até {campo.comprimento or 60} caracteres.")
            if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in valor):      # controle (quebra de linha, tab, DEL...) e separadores de linha/parágrafo
                raise ErroConfig(f"{campo.rotulo}: remova caracteres de controle.")
            if any(c in campo.proibidos for c in valor):
                raise ErroConfig(f"{campo.rotulo}: não use os caracteres {' '.join(campo.proibidos)}")
            if campo.so_digitos and valor and not valor.isdigit():
                raise ErroConfig(f"{campo.rotulo}: use só números ({campo.comprimento or 17} dígitos).")
            if campo.so_digitos and valor and len(valor) != (campo.comprimento or 17):
                raise ErroConfig(f"{campo.rotulo}: o SteamID64 tem {campo.comprimento or 17} números.")
            return valor
        if campo.tipo == "opcao":
            if not isinstance(valor, str) or valor not in campo.opcoes:
                raise ErroConfig(f"{campo.rotulo}: escolha uma das opções da lista.")
            return valor
        if campo.tipo in {"numero", "inteiro"}:
            if isinstance(valor, bool) or not isinstance(valor, (int, float)):
                raise ErroConfig(f"{campo.rotulo}: digite um número válido.")
            if isinstance(valor, float) and not math.isfinite(valor):                          # R-006: NaN e infinito passavam pelas comparações (todas falsas)
                raise ErroConfig(f"{campo.rotulo}: digite um número válido.")
            if campo.tipo == "inteiro" and not isinstance(valor, int):
                raise ErroConfig(f"{campo.rotulo}: use um número inteiro.")
            if campo.minimo is not None and valor < campo.minimo or campo.maximo is not None and valor > campo.maximo:
                raise ErroConfig(f"{campo.rotulo}: o valor precisa ficar entre {campo.minimo} e {campo.maximo}.")
            return valor
        raise ErroConfig("Tipo de configuração não reconhecido.")

    @staticmethod
    def _valor_toml(valor: Any) -> str:
        if isinstance(valor, bool):
            return "true" if valor else "false"
        if isinstance(valor, str):
            return json.dumps(valor, ensure_ascii=False)
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            return str(valor)
        raise ErroConfig("Tipo de valor não permitido.")

    @staticmethod
    def _separar_valor_comentario(resto: str) -> tuple[str, str]:
        aspas = None
        escapado = False
        for i, caractere in enumerate(resto):
            if escapado:
                escapado = False
                continue
            if caractere == "\\" and aspas == '"':
                escapado = True
                continue
            if caractere in {'"', "'"}:
                if aspas is None:
                    aspas = caractere
                elif aspas == caractere:
                    aspas = None
            elif caractere == "#" and aspas is None:
                inicio = i
                while inicio > 0 and resto[inicio - 1].isspace():
                    inicio -= 1
                return resto[:inicio], resto[inicio:]
        return resto.rstrip(), resto[len(resto.rstrip()):]

    @classmethod
    def _editar_linha(cls, texto: str, secao: str, chave: str, valor_toml: str | None) -> str:
        linhas = texto.splitlines(keepends=True)
        newline = "\r\n" if "\r\n" in texto else "\n"
        cabecalho = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?(?:\r?\n)?$")
        chave_re = re.compile(rf"^(\s*{re.escape(chave)}\s*=\s*)(.*?)(\r?\n)?$")
        em_secao = False
        inicio_secao = None
        fim_secao = len(linhas)
        alvos: list[int] = []
        for indice, linha in enumerate(linhas):
            match_secao = cabecalho.match(linha)
            if match_secao:
                if em_secao:
                    fim_secao = indice
                    break
                em_secao = match_secao.group(1).strip() == secao
                if em_secao:
                    inicio_secao = indice
                continue
            if em_secao and chave_re.match(linha):
                alvos.append(indice)
        if inicio_secao is None:
            if valor_toml is None:
                return texto
            if linhas and not linhas[-1].endswith(("\n", "\r")):
                linhas[-1] += newline
            if linhas and linhas[-1].strip():
                linhas.append(newline)
            linhas.append(f"[{secao}]{newline}")
            linhas.append(f"{chave} = {valor_toml}{newline}")
            return "".join(linhas)
        if valor_toml is not None:
            if alvos:
                indice = alvos[0]
                linha = linhas[indice]
                match = chave_re.match(linha)
                assert match is not None
                prefixo, resto, fim_linha = match.groups()
                valor_antigo, comentario = cls._separar_valor_comentario(resto)
                espacamento_final = len(resto) - len(resto.rstrip())
                # O comentário e os espaços antes dele permanecem intactos.
                if comentario:
                    sufixo = comentario
                else:
                    sufixo = " " * espacamento_final
                linhas[indice] = f"{prefixo}{valor_toml}{sufixo}{fim_linha or ''}"
            else:
                inserir = f"{chave} = {valor_toml}{newline}"
                # Insere antes da próxima seção, preservando o resto do arquivo.
                linhas.insert(fim_secao, inserir)
        elif alvos:
            for indice in reversed(alvos):
                del linhas[indice]
        return "".join(linhas)

    def _guardar_copia_dos_padroes(self) -> None:
        """R-007: o `config.toml.bak` é trocado a cada salvamento; antes de "Voltar aos padrões" fica UMA cópia a mais,
        `config.toml.bak-antes-dos-padroes`, que os salvamentos seguintes não trocam (só o próximo "Voltar aos padrões").
        Uma só de propósito: o dono não quer pilha de cópias do config na raiz (faxina de 23/11)."""
        if not self.caminho.exists():
            return
        try:
            shutil.copy2(self.caminho, self.caminho.with_name("config.toml.bak-antes-dos-padroes"))
        except OSError:
            pass                                          # a cópia extra nunca impede a ação; o `.bak` simples continua sendo feito

    def salvar(self, alteracoes: dict[str, Any]) -> dict[str, Any]:
        with self._trava:
            return self._salvar(alteracoes)

    def _salvar(self, alteracoes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(alteracoes, dict) or not alteracoes:
            raise ErroConfig("Nenhuma opção foi enviada para salvar.")
        pares: list[tuple[CampoConfig, Any]] = []
        for nome, valor in alteracoes.items():
            if not isinstance(nome, str) or "." not in nome:
                raise ErroConfig("Há uma opção que não pode ser editada pela interface.")
            secao, chave = nome.split(".", 1)
            campo = CAMPOS_POR_CHAVE.get((secao, chave))
            if campo is None:
                raise ErroConfig(f"A opção [{secao}] {chave} não pode ser alterada pela interface.")
            pares.append((campo, self.validar(campo, valor)))
        load_config, _ = self._resolver_core()
        texto_original, _ = self._config_toml()
        backup = self.caminho.with_name("config.toml.bak")
        if self.caminho.exists():
            shutil.copy2(self.caminho, backup)
        else:
            backup.write_text("", encoding="utf-8", newline="\n")
        texto_novo = texto_original
        for campo, valor in pares:
            texto_novo = self._editar_linha(texto_novo, campo.secao, campo.chave, self._valor_toml(valor))
        try:
            self._gravar_atomico(texto_novo)
            load_config(self.caminho)
        except Exception as exc:
            try:
                if backup.exists():
                    shutil.copy2(backup, self.caminho)
                elif self.caminho.exists():
                    self.caminho.unlink()
            except OSError:
                pass
            if isinstance(exc, ErroConfig):
                raise
            raise ErroConfig("O programa recusou essa configuração. Restaurei o arquivo anterior.") from exc
        return {"sucesso": True, "mensagem": "Salvo. A mudança vale na próxima execução."}

    def voltar_padrao(self, nomes: list[str]) -> dict[str, Any]:
        with self._trava:
            return self._voltar_padrao(nomes)

    def _voltar_padrao(self, nomes: list[str]) -> dict[str, Any]:
        if not isinstance(nomes, list) or not nomes:
            raise ErroConfig("Selecione pelo menos uma opção.")
        campos: list[CampoConfig] = []
        for nome in nomes:
            if not isinstance(nome, str) or "." not in nome:
                raise ErroConfig("Há uma opção que não pode ser restaurada.")
            grupo, _, opcao = nome.partition(".")      # (o "." existe: checado acima)
            campo = CAMPOS_POR_CHAVE.get((grupo, opcao))
            if campo is None:
                raise ErroConfig(f"A opção {nome} não pode ser alterada pela interface.")
            campos.append(campo)
        mantidos = [c for c in campos if (c.secao, c.chave) in PROTEGIDOS_NO_RESET]
        campos = [c for c in campos if (c.secao, c.chave) not in PROTEGIDOS_NO_RESET]
        if not campos:
            raise ErroConfig("As contas do Steam e o nome na demo não voltam ao padrão por aqui (são seus). Para limpar uma delas, apague o texto e salve.")
        load_config, _ = self._resolver_core()
        texto_original, _ = self._config_toml()
        backup = self.caminho.with_name("config.toml.bak")
        if self.caminho.exists():
            shutil.copy2(self.caminho, backup)
        else:
            backup.write_text("", encoding="utf-8", newline="\n")
        self._guardar_copia_dos_padroes()
        texto_novo = texto_original
        for campo in campos:
            texto_novo = self._editar_linha(texto_novo, campo.secao, campo.chave, None)
        try:
            self._gravar_atomico(texto_novo)
            load_config(self.caminho)
        except Exception as exc:
            try:
                shutil.copy2(backup, self.caminho)
            except OSError:
                pass
            raise ErroConfig("Não consegui restaurar o padrão. O arquivo anterior foi recuperado.") from exc
        mensagem = "Padrão do programa restaurado. Vale na próxima execução. Cópia do arquivo anterior em config.toml.bak e em config.toml.bak-antes-dos-padroes."
        if mantidos:
            mensagem += " As contas do Steam e o nome na demo foram mantidos."
        return {"sucesso": True, "mensagem": mensagem}

    def _gravar_atomico(self, texto: str) -> None:
        newline = "\r\n" if "\r\n" in texto else "\n"
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_nome = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=str(self.caminho.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as arquivo:
                arquivo.write(texto.replace("\r\n", "\n").replace("\n", newline))
                arquivo.flush()
                os.fsync(arquivo.fileno())
            substituir_arquivo(Path(temp_nome), self.caminho)
        except Exception:
            try:
                os.unlink(temp_nome)
            except OSError:
                pass
            raise


_AUSENTE = object()
