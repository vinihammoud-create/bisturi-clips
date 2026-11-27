"""Configuração: padrões aqui; config.toml (na raiz do projeto) sobrescreve o que quiser."""
from __future__ import annotations

import math
import tomllib
import unicodedata
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path

from .caminhos import PASTA_DADOS, RAIZ_PROGRAMA


@dataclass
class Deteccao:
    modo: str = "rapido"             # "rapido" (keyframes + janelas) ou "completo" (vídeo todo a 10 fps)
    fps_analise: float = 10.0
    processos: int = 6


@dataclass
class Cortes:
    antes: float = 4.0               # s antes do primeiro abate
    depois: float = 2.5              # s depois do último abate
    juntar_intervalo: float = 8.0    # abates mais próximos que isso viram uma jogada só
    juntar_no_round: float = 120.0   # todos os abates do MESMO round (a menos disso um do outro) vão num vídeo só; 0 = desliga
    pular_parado: float = 6.0        # ANTIGA (só o short compacto a usa): a regra da jogada agora são as 3 chaves abaixo; continua aceita no config.toml
    depois_de_cada_abate: float = 6.0   # N2c (dono, 15/11/2026; pediu 6, trocou por 4 e voltou para 6): numa jogada com mais de um abate, s que o vídeo fica depois de cada abate antes de pular
    antes_do_proximo: float = 6.0       # N2c: s antes do abate seguinte em que o vídeo volta depois de um pulo
    pulo_minimo: float = 2.0            # N2: só pula se sobrar pelo menos isto (s) para cortar; senão o vídeo fica contínuo
    sequencia_max: float = 90.0      # duração máxima do vídeo de uma sequência, já sem o tempo parado (s)
    duracao_minima: float = 7.0
    estender_reacao: bool = True     # inclui a reação do streamer após a jogada
    reacoes_sem_abate: int = 8       # momentos só de grito/chat/falas por vídeo
    so_partidas: bool = True         # cortes, montagem e shorts só de partidas (sem Deathmatch/aquecimento)
    limiar_reacao: float = 0.6       # voz 0–1 (0 = fala normal, 1 = grito) p/ contar como reação
    nota_montagem: float = 20.0      # entra na montagem a partir desta nota (0-99; double kill ~18-26)
    nota_short: float = 30.0         # vira short a partir desta nota (os de maior nota primeiro)
    falha_com_reacao: bool = False   # 23/11/2026: a jogada em que você morre logo depois do último abate e REAGE (raiva ou risada) leva o corte até o fim da reação, ganha nota, passa a regra dos 3 abates (basta 1) e vira SHORT (não entra na montagem com música); FAILS com raiva ou risada também podem virar short. Desligado até o dono aprovar a prévia
    min_abates_round: int = 3        # A2/A3 (dono, 08/11/2026): montagem e short só com round de N+ abates seus (ou resenha/pedido). 0 = desliga
    # efeitos do corte 16:9 (todos desligados: mudam o ritmo e o visual dos vídeos, ligue só depois de ver o resultado)
    speed_ramp: bool = False         # M7: acelera 1,75x o deslocamento vazio entre dois abates distantes do mesmo trecho
    freeze_headshot: bool = False    # V8: congela 0,25 s no último headshot, com punch-in de 8% e um impacto sonoro
    cartao_tatico: bool = False      # V4: cartão "ROUND 12 · CLUTCH 1v4" nos primeiros 1,8 s do corte


@dataclass
class Shorts:
    gerar: bool = True
    quantidade: int = 8              # por vídeo (as de maior nota)
    largura: int = 1080
    altura: int = 1920
    camera_altura: int = 608         # painel da webcam no topo; resto = gameplay
    topo: int = 160                  # faixa no alto (fica sob as abas do TikTok/Reels): desce a câmera para o rosto aparecer
    marca: str = ""                  # seu @ na faixa do topo, ex.: "@seucanal" (vazio = sem texto)
    zoom_camera: float = 5.0         # largura do recorte da câmera em "larguras de rosto"
    zoom_gameplay: float = 1.25      # >1 aproxima o gameplay (corta HUD das bordas)
    legendas: bool = True
    mostrar_killfeed: bool = True    # V1: sobrepõe a tira do kill feed (canto superior direito 16:9) no Short 9:16
    selo_jogada: bool = True         # "TRIPLE KILL" etc. no momento do abate
    gancho_titulo: bool = True       # o título da jogada já nos 1,5 s ANTES do 1º abate (False = só o selo de cada abate; D-G2)
    zoom_no_abate: bool = True       # "soco" de zoom no instante de cada abate
    pan_suave: bool = False          # V2: o recorte do jogo balança de leve (até 18 px) em cada abate, em vez de ficar parado
    duracao_maxima: float = 60.0     # short nunca passa disso (YouTube Shorts/TikTok): jogada longa vira versão compacta
    antes: float = 2.0               # na versão compacta: segundos antes de cada abate
    gancho: float = 2.0              # o short começa só isto antes do 1º abate (a ação logo de cara)
    versao_efeitos: bool = False     # gera também a versão com efeitos (tremida + replay lento do último abate); desligada: nunca foi aprovada (igual ao config.exemplo.toml)
    depois: float = 1.5              # na versão compacta: segundos depois de cada abate
    camera_manual: list[float] = field(default_factory=list)  # [x, y, w, h] relativos (0–1)
    layout: str = "classico"          # 23/11/2026: "classico" (o de sempre: recorte no rosto, faixa escura no topo) ou "webcam_inteira" (webcam inteira com o letreiro, margens, fundo desfocado do jogo; MARCA_E_VOZ §7)
    webcam_retangulo: str = ""       # 23/11: "x,y,largura,altura" da webcam em pixels de 1920x1080 (ex.: "32,428,456,276"); vazio = a câmera achada pela análise. Vale no layout webcam_inteira


@dataclass
class Montagem:
    gerar: bool = True
    ordem: str = "cronologica"       # "cronologica" ou "melhores"
    transicao: float = 0.12          # fade curto no início/fim de cada corte avulso (a montagem tira essa borda)
    transicao_tipo: str = "corte"    # montagem: "corte" (seco, estilo esports) ou "fade" (mistura de 0,25 s)
    musica: str = "auto"             # "auto" = escolhe em musicas/; "" = sem música; ou o nome do arquivo
    volume_musica: float = -4.0      # dB da música em relação ao som do jogo
    tiro_no_drop: bool = True        # A6 (dono, 08/11/2026): o 1º tiro da montagem cai no drop da música (a música pula o começo até o drop chegar nele)
    corte_na_batida: bool = True     # A5: sincroniza as trocas de cena com as batidas da música
    kill_sync: bool = True           # M6: avança até 0,45 s do início do corte para o 1º tiro cair na batida
    incluir_resenha: bool = False    # A4: a resenha também entra na MONTAGEM musical (desligado: a música cobre as vozes, que são a graça; ela vira short)
    sfx: bool = False                # V6: adiciona efeito sonoro sutil CC0 nas emendas da montagem
    ducking_tiros_db: float = 0.0    # C7: a música abaixa este tanto (dB) por ~0,2 s a cada abate; 0 = desligado (padrão)
    versao_com_voz: bool = False     # C8: gera também MONTAGEM_VOZ_*.mp4 (mesmas jogadas, com a sua voz e sem música), para o YouTube
    grade_de_corte: str = "batida"   # C1-C3: "batida" (qualquer batida, o de sempre) ou "compasso" (prefere o "1" do compasso; opt-in)
    perfil: str = ""                 # Q1: preset opcional (esports_blast, fragmovie_musical, criador_live...)


@dataclass
class Audio:
    faixa_jogo: int = 0              # faixa do OBS (1-6) só com o som do jogo; 0 = não usar (vídeo de 1 faixa)
    faixa_voz: int = 0               # faixa do OBS (1-6) só com o microfone; 0 = não usar
    faixa_comms: int = 0             # faixa do OBS (2-6) só com o Discord/comunicação; 0 = não usar. Nunca entra no áudio
                                      # que o programa monta (com tratar_voz o áudio sai só do microfone + jogo)
    tratar_voz: bool = False         # A2/A3: limpa a voz e baixa o jogo enquanto você fala (precisa de faixa_voz e faixa_jogo)
    separar_jogo_faixa_unica: bool = False   # A4: gravação sem faixa só do jogo (1 faixa, ou o OBS gravando a mesma
                                              # mistura em todas): cria a faixa "jogo" tirando a voz (IA Demucs se
                                              # instalada via instalar_separacao_ia.bat; senão filtro do FFmpeg) e a
                                              # montagem ganha música
    fonte_limpa: bool = False        # D7: o vídeo vem do gravador pela demo; a faixa do jogo é o som puro do jogo (não precisa diferir da faixa 1). Uso interno


@dataclass
class Transcricao:
    modelo: str = "large-v3-turbo"
    idioma: str = "pt"


@dataclass
class Chat:
    usar: bool = False               # desligado de fábrica (igual ao config.exemplo.toml): só fala com a Twitch se a pessoa ligar
    atraso: float = 6.0              # chat reage alguns segundos depois do lance


@dataclass
class Fails:
    gerar: bool = True               # trilha de FAILS/engraçados (morte + frustração ou risada), separada das jogadas
    quantidade: int = 8              # no máximo, por vídeo
    exigir_aprovacao: bool = False   # C9: a compilação FAILS_*.mp4 leva só os fails que você marcou "Gostei" (desligado: leva todos)


@dataclass
class Resenha:
    gerar: bool = True               # A4 (dono, 08/11/2026): acha momentos engraçados (Discord alto, várias vozes, você gritando, risadas) e faz short deles
    quantidade: int = 6              # no máximo por vídeo
    min_sinais: int = 2              # sinais juntos para chamar de resenha (2 a 4): um só é reação comum
    discord_basta: bool = False      # um sinal do Discord sozinho (Discord alto ou várias vozes) já vale resenha; o seu grito sozinho nunca vale
    conversa_na_faixa_do_jogo: bool = False   # 17/11/2026: com a faixa do Discord muda, usa a fala da faixa do jogo (é para lá que o OBS do dono manda o Discord, e o chat de voz do CS2
                                     # também vai para ela) como "amigos falando". Desligado até o dono aprovar (CONSTITUICAO §2: opção nova entra desligada)
    risadas_amigos: bool = False     # 17/11/2026: transcreve os trechos com a fala dos amigos (Whisper, GPU) para achar as risadas deles. Desligado: medido na
                                     # gravação de 04/11 (65 min, 630 s de fala dos amigos), custou 177 s de GPU e o Whisper não escreveu NENHUMA risada em 2162 palavras


@dataclass
class GravacaoDemo:
    """Parte D (09/11/2026): gravar a partida pelo CS2 a partir da demo. Nada roda sozinho; tudo começa desligado ou vazio."""
    conta_steamid: str = ""          # SteamID64 da conta do Bisturi (a 2ª conta); vazio = o programa pergunta antes de gravar
    conta_principal_id: str = ""     # SteamID64 da conta de jogo: só para RECUSAR a gravação se ela estiver logada
    conta_apelido: str = ""          # como o dono chama a conta do Bisturi (só rótulo nas mensagens; a trava nunca olha); até 40 caracteres
    conta_principal_apelido: str = ""  # idem para a conta principal
    hud: bool = True                 # HUD do jogo no vídeo limpo (False = só o mundo, sem kill feed nem mira)
    largura: int = 1920
    altura: int = 1080
    fps: int = 60
    cq: int = 18                     # qualidade do NVENC (menor = melhor)
    so_rounds_bons: bool = True      # só os rounds com o mínimo de abates (cortes.min_abates_round); False = a partida inteira
    margem_antes_s: float = 2.0
    margem_depois_s: float = 3.0
    netcon_porta: int = 2121
    tempo_maximo_min: int = 90       # desiste se a gravação passar disto
    permitir_lista_da_principal: bool = False   # "últimas partidas" usa a conta PRINCIPAL logada no Steam: desligado por decisão do dono (08/11); use o código de partilha
    webcam_no_short: bool = True     # S5 (D3 = sim): com a live junto da demo, o short de imagem limpa leva a SUA webcam da live no painel de cima (sem live, ou sem webcam achada: sem painel)


@dataclass
class Capa:
    estilo: str = "grande"           # "grande" (letras grandes; escolha do dono em 08/11/2026) ou "discreta" (cartão pequeno com o round); a outra sai ao lado
    alternativas_da_mesma_jogada: bool = False   # 23/11/2026: kit com UMA jogada só (ex.: imagem limpa da demo) também ganha capas candidatas: quadros de cada abate da mesma jogada. O kit de imagem limpa liga sozinho


@dataclass
class Demo:
    jogador: str = ""                # nome (ou parte do nome) ou steamid de quem você é na demo; vazio = pergunta
    automatica: bool = False         # B1: procura sozinho a demo desta gravação na pasta do CS2 (só lê e copia; sem o nome, descobre quem é você pelos abates)


@dataclass
class Qualidade:
    encoder: str = "auto"            # auto | h264_nvenc | hevc_nvenc | libx264
    cq: int = 21                     # menor = mais qualidade (19–23 é ótimo; fonte de live ~6 Mbps)
    lufs: float = -14.0              # volume padrão de YouTube/TikTok
    renders_paralelos: int = 2        # NVENC + 6 GB de VRAM: 2 cortes ao mesmo tempo é o seguro
    checar_conteudo: bool = True      # G-008: reprova arquivo com vídeo inteiramente preto (custa <1 s por arquivo)
    checar_silencio: bool = False     # G-008: reprova também áudio inteiramente mudo (desligado: pode ser falso alarme)
    garantir_pico: bool = True        # S1.4b: mede o pico real do arquivo PRONTO e baixa só o áudio até ficar <= -1 dBTP
    gate_loudness: bool = False       # A5: volume fora de ±2 LU do alvo ou pico real acima de -1 dBTP reprova a montagem (desligado: só avisa no qualidade.json)


@dataclass
class Config:
    deteccao: Deteccao = field(default_factory=Deteccao)
    cortes: Cortes = field(default_factory=Cortes)
    shorts: Shorts = field(default_factory=Shorts)
    montagem: Montagem = field(default_factory=Montagem)
    transcricao: Transcricao = field(default_factory=Transcricao)
    chat: Chat = field(default_factory=Chat)
    qualidade: Qualidade = field(default_factory=Qualidade)
    demo: Demo = field(default_factory=Demo)
    capa: Capa = field(default_factory=Capa)
    resenha: Resenha = field(default_factory=Resenha)
    gravacao_demo: GravacaoDemo = field(default_factory=GravacaoDemo)
    fails: Fails = field(default_factory=Fails)
    audio: Audio = field(default_factory=Audio)
    modo_previa: bool = False        # não é opção do usuário: o programa liga sozinho com CS2/OBS abertos


def _merge(obj, data: dict, prefixo: str = "") -> None:
    names = {f.name: f for f in fields(obj)}
    for k, v in data.items():
        if k not in names:
            print(f"  [aviso] config: opção desconhecida '{k}' ignorada")
            continue
        cur = getattr(obj, k)
        if is_dataclass(cur) and isinstance(v, dict):
            _merge(cur, v, f"{prefixo}{k}.")
        elif isinstance(cur, bool):
            setattr(obj, k, bool(v))
        elif isinstance(cur, (int, float, str)):
            try:
                setattr(obj, k, type(cur)(v))
            except (ValueError, OverflowError) as e:      # R-018: `qualidade.cq = inf` ou `nan` num campo inteiro não vira erro solto do Python
                raise ValueError(f"{prefixo}{k} = {v}: valor inválido ({e})") from e
        else:
            setattr(obj, k, v)


APELIDO_MAX = 40


def problema_do_apelido(valor: object) -> str | None:
    """Por que o apelido de uma conta não vale (texto curto) ou None se está bom: só texto, até 40 caracteres, sem quebra de linha nem caractere de controle."""
    if not isinstance(valor, str):
        return "precisa ser um texto"
    if len(valor) > APELIDO_MAX:
        return f"use até {APELIDO_MAX} caracteres"
    if any(c in "\n\r\x85\u2028\u2029" for c in valor):
        return "não pode ter quebra de linha"
    if any(unicodedata.category(c) == "Cc" for c in valor):
        return "não pode ter caractere de controle"
    return None


LAYOUTS_SHORT = ("classico", "webcam_inteira")


def retangulo_da_webcam(texto: str) -> tuple[int, int, int, int] | None:
    """`"x,y,largura,altura"` em pixels de 1920x1080 -> (x, y, w, h); vazio -> None. ValueError com a explicação se não serve (23/11/2026)."""
    texto = str(texto or "").strip()
    if not texto:
        return None
    partes = [p.strip() for p in texto.split(",")]
    if len(partes) != 4 or not all(p.isdigit() for p in partes):
        raise ValueError("shorts.webcam_retangulo precisa ser \"x,y,largura,altura\" em pixels de 1920x1080 (ex.: \"32,428,456,276\")")
    x, y, w, h = (int(p) for p in partes)
    if w < 32 or h < 32 or x + w > 1920 or y + h > 1080:
        raise ValueError(f"shorts.webcam_retangulo = {texto}: a webcam precisa ter pelo menos 32x32 e caber em 1920x1080")
    return x, y, w, h


# R-018 (26/11/2026): faixas LARGAS e físicas (segundos não são negativos, volume em dB tem teto). Não são os limites estreitos do `EditPlan`:
# aqui só cai o que não faz sentido de jeito nenhum. (campo, mínimo, máximo, mínimo_incluso)
FAIXAS = (
    ("cortes", "antes", 0.0, 600.0, True), ("cortes", "depois", 0.0, 600.0, True),
    ("cortes", "juntar_intervalo", 0.0, 3600.0, True), ("cortes", "juntar_no_round", 0.0, 3600.0, True),
    ("cortes", "pular_parado", 0.0, 3600.0, True), ("cortes", "depois_de_cada_abate", 0.0, 3600.0, True),
    ("cortes", "antes_do_proximo", 0.0, 3600.0, True), ("cortes", "pulo_minimo", 0.0, 3600.0, True),
    ("cortes", "sequencia_max", 0.0, 36000.0, False), ("cortes", "duracao_minima", 0.0, 3600.0, True),
    ("cortes", "limiar_reacao", 0.0, 1.0, True), ("cortes", "nota_montagem", 0.0, 1000.0, True), ("cortes", "nota_short", 0.0, 1000.0, True),
    ("cortes", "reacoes_sem_abate", 0, 1000, True),
    ("shorts", "duracao_maxima", 0.0, 3600.0, False), ("shorts", "antes", 0.0, 600.0, True), ("shorts", "depois", 0.0, 600.0, True),
    ("shorts", "gancho", 0.0, 600.0, True), ("shorts", "zoom_camera", 0.0, 100.0, False), ("shorts", "zoom_gameplay", 1.0, 20.0, True),
    ("montagem", "transicao", 0.0, 30.0, True), ("montagem", "volume_musica", -120.0, 24.0, True), ("montagem", "ducking_tiros_db", 0.0, 120.0, True),
    ("chat", "atraso", 0.0, 3600.0, True),
    ("deteccao", "fps_analise", 0.0, 1000.0, False),
    ("qualidade", "lufs", -70.0, 0.0, True), ("qualidade", "cq", 0, 63, True),
)


def _numeros_finitos(obj, prefixo: str = "") -> None:
    """Nenhum número do config pode ser NaN ou infinito (o TOML aceita `nan` e `inf`; as contas de tempo do render se perdem)."""
    for f in fields(obj):
        valor = getattr(obj, f.name)
        if is_dataclass(valor):
            _numeros_finitos(valor, f"{prefixo}{f.name}.")
        elif isinstance(valor, float) and not math.isfinite(valor):
            raise ValueError(f"{prefixo}{f.name} = {valor}: precisa ser um número comum (nem NaN nem infinito)")
        elif isinstance(valor, list) and any(isinstance(x, float) and not math.isfinite(x) for x in valor):
            raise ValueError(f"{prefixo}{f.name}: tem um número que não é comum (NaN ou infinito)")


def _faixas_largas(cfg: Config) -> None:
    for secao, campo, minimo, maximo, incluso in FAIXAS:
        valor = getattr(getattr(cfg, secao), campo)
        if (valor < minimo if incluso else valor <= minimo) or valor > maximo:
            raise ValueError(f"{secao}.{campo} = {valor}: use um valor {'de' if incluso else 'maior que'} {minimo:g} até {maximo:g}")


def _validar(cfg: Config) -> None:
    """Valores que quebrariam o programa no meio (ex.: renders_paralelos = 0 derruba o render): erro claro
    já na leitura, que o main mostra como problema no config.toml."""
    _numeros_finitos(cfg)
    _faixas_largas(cfg)
    minimos = [("qualidade.renders_paralelos", cfg.qualidade.renders_paralelos, 1),
               ("deteccao.processos", cfg.deteccao.processos, 1),
               ("shorts.quantidade", cfg.shorts.quantidade, 0),
               ("cortes.min_abates_round", cfg.cortes.min_abates_round, 0),
               ("resenha.quantidade", cfg.resenha.quantidade, 0),
               ("resenha.min_sinais", cfg.resenha.min_sinais, 2),
               ("fails.quantidade", cfg.fails.quantidade, 0)]
    for nome, valor, minimo in minimos:
        if valor < minimo:
            raise ValueError(f"{nome} = {valor}: o mínimo é {minimo}")
    for nome in ("depois_de_cada_abate", "antes_do_proximo", "pulo_minimo"):
        if getattr(cfg.cortes, nome) < 0:
            raise ValueError(f"cortes.{nome} = {getattr(cfg.cortes, nome)}: não pode ser negativo")
    if cfg.deteccao.fps_analise <= 0:
        raise ValueError(f"deteccao.fps_analise = {cfg.deteccao.fps_analise}: precisa ser maior que 0")
    # opções de texto com lista fechada: valor fora dela só estouraria lá no meio do processamento
    # (o encoder fica de fora de propósito: o render avisa e cai em libx264)
    for nome, valor, aceitos in (("deteccao.modo", cfg.deteccao.modo, ("rapido", "completo")),
                                 ("montagem.ordem", cfg.montagem.ordem, ("cronologica", "melhores", "arco")),
                                 ("montagem.transicao_tipo", cfg.montagem.transicao_tipo, ("corte", "fade")),
                                 ("montagem.grade_de_corte", cfg.montagem.grade_de_corte, ("batida", "compasso")),
                                 ("capa.estilo", cfg.capa.estilo, ("grande", "discreta", "chamativa"))):
        if valor not in aceitos:
            raise ValueError(f"{nome} = \"{valor}\": use {' ou '.join(aceitos)}")
    for nome_faixa, faixa in (("faixa_jogo", cfg.audio.faixa_jogo), ("faixa_voz", cfg.audio.faixa_voz),
                              ("faixa_comms", cfg.audio.faixa_comms)):
        if not 0 <= faixa <= 6:
            raise ValueError(f"audio.{nome_faixa} = {faixa}: use 0 (desligado) ou a faixa do OBS, de 1 a 6")
    if cfg.audio.faixa_voz == 1:
        raise ValueError("audio.faixa_voz = 1: a faixa 1 é a mistura da live (jogo + Discord), não o microfone; use a faixa só do microfone (2 a 6) ou 0")
    if cfg.audio.faixa_voz and cfg.audio.faixa_voz == cfg.audio.faixa_jogo:
        raise ValueError(f"audio.faixa_voz = {cfg.audio.faixa_voz} é a mesma de audio.faixa_jogo: cada fonte precisa da sua própria faixa")
    comms = cfg.audio.faixa_comms
    if comms == 1:
        raise ValueError("audio.faixa_comms = 1: a faixa 1 é a mistura da live, não a do Discord; use a faixa só do Discord (2 a 6)")
    for outro, valor in (("faixa_voz", cfg.audio.faixa_voz), ("faixa_jogo", cfg.audio.faixa_jogo)):
        if comms and comms == valor:
            raise ValueError(f"audio.faixa_comms = {comms} é a mesma de audio.{outro}: cada fonte precisa da sua própria faixa")
    g = cfg.gravacao_demo
    for nome, valor in (("conta_steamid", g.conta_steamid), ("conta_principal_id", g.conta_principal_id)):
        if valor and not (str(valor).isdigit() and len(str(valor)) == 17):
            raise ValueError(f"gravacao_demo.{nome} = \"{valor}\": o SteamID64 tem 17 números (ou deixe vazio)")
    for nome, valor in (("conta_apelido", g.conta_apelido), ("conta_principal_apelido", g.conta_principal_apelido)):
        problema = problema_do_apelido(valor)
        if problema:
            raise ValueError(f"gravacao_demo.{nome}: {problema}")
    if g.conta_steamid and g.conta_steamid == g.conta_principal_id:
        raise ValueError("As duas contas não podem ser iguais: a gravação usa a conta do Bisturi, nunca a que você joga (Configurar → Contas do Steam).")
    if g.largura <= 0 or g.altura <= 0 or g.largura % 2 or g.altura % 2:
        raise ValueError("gravacao_demo.largura e altura precisam ser números pares maiores que 0")
    if not 1 <= g.fps <= 240:
        raise ValueError(f"gravacao_demo.fps = {g.fps}: use de 1 a 240")
    if not 0 <= g.cq <= 51:
        raise ValueError(f"gravacao_demo.cq = {g.cq}: use de 0 a 51 (menor = melhor)")
    if not (0 <= g.margem_antes_s <= 3600 and 0 <= g.margem_depois_s <= 3600):
        raise ValueError("gravacao_demo.margem_antes_s e margem_depois_s não podem ser negativas nem passar de 3600 s")
    if not 1024 <= g.netcon_porta <= 65535:
        raise ValueError(f"gravacao_demo.netcon_porta = {g.netcon_porta}: use de 1024 a 65535")
    if g.tempo_maximo_min < 1:
        raise ValueError("gravacao_demo.tempo_maximo_min precisa ser pelo menos 1")
    s = cfg.shorts
    for nome, valor in (("largura", s.largura), ("altura", s.altura), ("camera_altura", s.camera_altura),
                        ("zoom_camera", s.zoom_camera)):
        if valor <= 0:
            raise ValueError(f"shorts.{nome} = {valor}: precisa ser maior que 0")
    if s.zoom_gameplay < 1:
        raise ValueError(f"shorts.zoom_gameplay = {s.zoom_gameplay}: o mínimo é 1 (1 = sem aproximar)")
    if s.largura % 2 or s.altura % 2:
        raise ValueError("shorts.largura e shorts.altura precisam ser números pares (limite do H.264)")
    if s.camera_altura + max(0, s.topo) >= s.altura:
        raise ValueError(f"shorts: camera_altura ({s.camera_altura}) + topo ({s.topo}) não deixa espaço para o "
                         f"gameplay na altura {s.altura}")
    if s.layout not in LAYOUTS_SHORT:
        raise ValueError(f"shorts.layout = {s.layout!r}: use um de {', '.join(LAYOUTS_SHORT)}")
    retangulo_da_webcam(s.webcam_retangulo)
    cm = s.camera_manual
    if cm:
        ok = (len(cm) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in cm)
              and cm[0] >= 0 and cm[1] >= 0 and cm[2] > 0 and cm[3] > 0 and cm[0] + cm[2] <= 1 and cm[1] + cm[3] <= 1)
        if not ok:
            raise ValueError("shorts.camera_manual precisa ser [x, y, largura, altura] com valores de 0 a 1 "
                             "(x + largura e y + altura até 1)")


NOME_DO_CONFIG, NOME_DO_EXEMPLO = "config.toml", "config.exemplo.toml"


def _exemplo_do_config(path: Path) -> Path | None:
    """O `config.exemplo.toml` de onde este `config.toml` nasce: o que está ao lado dele e, só se o config é o da pasta de dados (P2.2: no modo
    instalado ela não é a do programa), o do programa. Um `config.toml` de outra pasta (de teste, por exemplo) segue procurando só ao lado."""
    ao_lado = path.with_name(NOME_DO_EXEMPLO)
    if ao_lado.is_file():
        return ao_lado
    try:
        na_pasta_de_dados = path.parent.resolve() == PASTA_DADOS.resolve()
    except OSError:
        return None
    do_programa = RAIZ_PROGRAMA / NOME_DO_EXEMPLO
    return do_programa if na_pasta_de_dados and do_programa.is_file() else None


def nascer_do_exemplo(path: Path) -> Path:
    """O `config.toml` que ainda não existe nasce do `config.exemplo.toml` que está ao lado (ou, na pasta de dados, o do programa; o mesmo que o instalar.bat faz), para um clone novo
    sair com as escolhas de produto do programa (perfil esports_blast, sem a versão "com efeitos"...) e não só com os padrões do código.
    Nunca toca num config que já existe. Se não der para gravar na pasta, devolve o caminho do exemplo (lido direto)."""
    exemplo = _exemplo_do_config(path)
    if path.name != NOME_DO_CONFIG or path.exists() or exemplo is None:
        return path
    import os
    import shutil
    tmp = path.with_name(f"{NOME_DO_CONFIG}.nascendo-{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)      # a pasta de dados nova ainda pode não existir (quem grava cria)
        shutil.copyfile(exemplo, tmp)
        try:
            os.link(tmp, path)          # só cria se ainda não existir: duas janelas abrindo juntas não se atropelam
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
    except OSError:
        tmp.unlink(missing_ok=True)
        return path if path.exists() else exemplo
    return path


def load_config(path: Path | None) -> Config:
    cfg = Config()
    if path is not None:
        path = nascer_do_exemplo(Path(path))
    if path and path.exists():
        with open(path, "rb") as f:
            dados = tomllib.load(f)
        perfil = dados.get("montagem", {}).get("perfil", "") if isinstance(dados.get("montagem"), dict) else ""
        if perfil:
            from .perfis import aplicar_perfil
            aplicar_perfil(cfg, str(perfil))
        _merge(cfg, dados)
    _validar(cfg)
    return cfg
