"""E7 (Plano E): o catálogo de músicas com LICENÇA. Nenhuma faixa entra sem dizer de onde veio e sob qual licença; o crédito sai pronto para a descrição.

Regras (decisão do dono, 08/11/2026):
  * CC0 (domínio público): sem crédito.
  * Pixabay: sem crédito, mas algumas faixas recebem reclamação automática (Content ID) no YouTube — guarde o certificado da página da faixa.
  * NCS (NoCopyrightSounds): crédito OBRIGATÓRIO (artista e faixa) na descrição.
  * Biblioteca de Áudio do YouTube, CC BY e "outra": o dono diz se a faixa exige crédito; sempre com o link da página.
  * StreamBeats: o FAQ oficial (lido em 09/11/2026) diz que dá para monetizar no YouTube, sem crédito obrigatório, e que a empresa usa Content ID/Audible Magic
    para evitar reclamação, "mas nenhum sistema é perfeito" — entra, com esse aviso.
  * Música comercial (artistas, Spotify…) nunca.
A montagem só sorteia faixas do catálogo com `licenca` preenchida e que não estejam `desligada`.

P2, item 6 (fábrica × pessoa): o `catalogo.json` vem com o programa e é SOMENTE LEITURA para o app; o que a pessoa muda pela tela vai para
`<dados>/musicas/catalogo_usuario.json`: `{"faixas": [as dela], "ajustes": {"<arquivo de fábrica>": {"desligada": true, ...}}}`. Quem lê o
catálogo usa `catalogo_efetivo` (fábrica com os ajustes por cima, mais as faixas da pessoa); o arquivo de áudio é procurado na pasta da
pessoa e depois na de fábrica (`caminho_da_faixa`). No modo portátil as duas pastas são a mesma `musicas/` de hoje.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any
from collections.abc import Callable
from .caminhos import PASTA_DADOS, RAIZ_PROGRAMA
from .tools import gravar_atomico, substituir_arquivo

EXTENSOES = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus"}
TAMANHO_MIN = 50_000
PASTA_FABRICA = RAIZ_PROGRAMA / "musicas"
PASTA_PESSOA = PASTA_DADOS / "musicas"
ARQUIVO_PESSOA = "catalogo_usuario.json"

LICENCAS: dict[str, dict[str, Any]] = {
    "cc0": {"nome": "CC0 1.0 (domínio público)", "url": "https://creativecommons.org/publicdomain/zero/1.0/", "credito": False, "link_obrigatorio": False,
            "aviso": ""},
    "pixabay": {"nome": "Pixabay Content License", "url": "https://pixabay.com/service/license-summary/", "credito": False, "link_obrigatorio": True,
                "aviso": "Algumas faixas do Pixabay recebem reclamação automática (Content ID) no YouTube. Guarde o certificado de licença da página da faixa para contestar."},
    "ncs": {"nome": "NCS (NoCopyrightSounds) — Usage Policy", "url": "https://ncs.io/usage-policy", "credito": True, "link_obrigatorio": True,
            "aviso": "Criador independente pode monetizar, com crédito: copie a seção de crédito da descrição do vídeo da faixa no YouTube da NCS. "
                     "Em live (Twitch ou YouTube), a NCS pede 'Music provided by NCS' com o link na descrição. Uso comercial de marca precisa de autorização da NCS."},
    "streambeats": {"nome": "StreamBeats by Harris Heller", "url": "https://www.streambeats.com/faq", "credito": False, "link_obrigatorio": False,
                    "aviso": "Pelo FAQ oficial dá para monetizar no YouTube e o crédito não é obrigatório. A empresa usa Content ID e Audible Magic para evitar reclamação, "
                             "mas diz que 'nenhum sistema é perfeito': se acontecer, conteste."},
    "yt_audio": {"nome": "Biblioteca de Áudio do YouTube", "url": "https://support.google.com/youtube/answer/3376882", "credito": None, "link_obrigatorio": False,
                 "aviso": "Seguro contra Content ID e liberado para monetizar nos vídeos do YouTube (Programa de Parcerias). Vale só no YouTube: a página não cobre outras plataformas. "
                          "Faixa 'Atribuição necessária' pede o texto de crédito que o Studio gera (cole abaixo)."},
    "cc_by": {"nome": "Creative Commons Atribuição (CC BY)", "url": "https://creativecommons.org/licenses/by/4.0/", "credito": True, "link_obrigatorio": True,
              "aviso": "Crédito obrigatório: autor, título, licença e link."},
    "outra": {"nome": "Outra licença (com o link da página)", "url": "", "credito": None, "link_obrigatorio": True,
              "aviso": "Só use se a página disser por escrito que dá para usar em vídeo público. Marque se pede crédito."},
}
RECUSADAS = {"comercial": "Música comercial (artistas, Spotify…) nunca entra."}


class ErroCatalogo(ValueError):
    """A faixa não pode entrar (ou não existe); a mensagem diz o que fazer."""


def _arquivo(pasta: Path) -> Path:
    return Path(pasta) / "catalogo.json"


def _mesma_pasta(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def pasta_da_pessoa(pasta_fabrica: Path | None = None, pasta_pessoa: Path | None = None) -> Path:
    """Onde mora o catálogo da pessoa (e os mp3 e o `historico.json` dela) para esta pasta de músicas: a pedida; para a pasta de
    fábrica padrão, a de dados (`PASTA_PESSOA`); para qualquer outra pasta (testes, ferramentas), ela mesma, como antes da divisão."""
    if pasta_pessoa is not None:
        return Path(pasta_pessoa)
    if pasta_fabrica is None or _mesma_pasta(Path(pasta_fabrica), PASTA_FABRICA):
        return PASTA_PESSOA
    return Path(pasta_fabrica)


def pastas_da_musica(musica: Path) -> tuple[Path, Path]:
    """(fábrica, pessoa) de um arquivo de música: o que mora na pasta de dados padrão pertence à fábrica padrão; outra pasta vale pelas duas."""
    pai = Path(musica).parent
    if _mesma_pasta(pai, PASTA_PESSOA):
        return PASTA_FABRICA, PASTA_PESSOA
    return pai, pasta_da_pessoa(pai)


def ler_fabrica(pasta: Path) -> dict[str, Any]:
    """O `catalogo.json` de fábrica, como está no disco (só as ferramentas de manutenção o regravam: `tools/baixar_musicas_cc0.py`)."""
    arq = _arquivo(pasta)
    try:
        cat = json.loads(arq.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"faixas": []}
    except (OSError, ValueError) as e:
        raise ErroCatalogo("O catalogo.json está ilegível. Corrija ou restaure o arquivo antes de mexer nas músicas.") from e
    if not isinstance(cat, dict) or not isinstance(cat.get("faixas"), list):
        raise ErroCatalogo("O catalogo.json não tem a lista de faixas.")
    return cat


def _ler_pessoa(pasta_pessoa: Path) -> dict[str, Any]:
    """O `catalogo_usuario.json`. Ilegível ou fora do formato = `ErroCatalogo`: quem grava nunca escreve por cima de um arquivo assim."""
    arq = Path(pasta_pessoa) / ARQUIVO_PESSOA
    ilegivel = f"O {ARQUIVO_PESSOA} (as suas músicas e ajustes) está ilegível. Corrija ou restaure o arquivo antes de mexer nas músicas."
    try:
        cat = json.loads(arq.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"faixas": [], "ajustes": {}}
    except (OSError, ValueError) as e:
        raise ErroCatalogo(ilegivel) from e
    faixas, ajustes = (cat.get("faixas", []), cat.get("ajustes", {})) if isinstance(cat, dict) else (None, None)
    if (not isinstance(faixas, list) or not all(isinstance(f, dict) and isinstance(f.get("arquivo"), str) for f in faixas)
            or not isinstance(ajustes, dict) or not all(isinstance(v, dict) for v in ajustes.values())):
        raise ErroCatalogo(ilegivel)
    return {**cat, "faixas": faixas, "ajustes": ajustes}


def _salvar_pessoa(pasta_pessoa: Path, cat: dict[str, Any]) -> None:
    pasta = Path(pasta_pessoa)
    pasta.mkdir(parents=True, exist_ok=True)
    texto = json.dumps({**cat, "faixas": cat["faixas"], "ajustes": cat["ajustes"]}, ensure_ascii=False, indent=2) + "\n"
    gravar_atomico(pasta / ARQUIVO_PESSOA, texto, fsync=True)          # escolha da pessoa: não dá para refazer


_AVISADOS: set[tuple[str, int]] = set()


def _avisar_pessoa_ilegivel(pasta_pessoa: Path) -> None:
    """Uma vez por estado do arquivo (a montagem lê o catálogo várias vezes; o aviso não precisa sair em todas)."""
    arq = Path(pasta_pessoa) / ARQUIVO_PESSOA
    try:
        marca = arq.stat().st_mtime_ns
    except OSError:
        marca = 0
    if (str(arq), marca) not in _AVISADOS:
        _AVISADOS.add((str(arq), marca))
        print(f"    [aviso] {arq} ilegível: as suas músicas e ajustes ficam de fora até ele ser corrigido (vale só o catálogo de fábrica)")


def _mesclar(fabrica: dict[str, Any], pessoa: dict[str, Any]) -> dict[str, Any]:
    """Faixas de fábrica na ordem delas, cada uma com o ajuste da pessoa por cima (o `arquivo` nunca muda); a faixa da pessoa com o mesmo
    nome de uma de fábrica toma o lugar dela (é o arquivo da pessoa que toca); depois as outras da pessoa, na ordem dela."""
    da_pessoa: dict[str, dict[str, Any]] = {}
    for f in pessoa["faixas"]:
        da_pessoa.setdefault(f["arquivo"], f)
    usadas: set[str] = set()
    faixas: list[Any] = []
    for f in fabrica["faixas"]:
        nome = f.get("arquivo") if isinstance(f, dict) else None
        if not isinstance(nome, str):
            faixas.append(dict(f) if isinstance(f, dict) else f)       # linha estranha: passa como está (quem lê decide, como sempre)
        elif nome in da_pessoa:
            faixas.append(dict(da_pessoa[nome]))
            usadas.add(nome)
        else:
            ajuste = pessoa["ajustes"].get(nome) or {}
            faixas.append({**f, **{k: v for k, v in ajuste.items() if k != "arquivo"}})
    faixas += [dict(f) for nome, f in da_pessoa.items() if nome not in usadas]
    return {**{k: v for k, v in fabrica.items() if k != "faixas"}, "faixas": faixas}


def catalogo_efetivo(pasta_fabrica: Path | None = None, pasta_pessoa: Path | None = None) -> dict[str, Any]:
    """O catálogo que vale (toda leitura passa por aqui): o de fábrica com os `ajustes` da pessoa por cima, mais as faixas da pessoa.
    Fábrica ilegível = `ErroCatalogo` (como antes da divisão); catálogo da pessoa ilegível = aviso, e vale só a fábrica (não derruba)."""
    fab = Path(pasta_fabrica) if pasta_fabrica is not None else PASTA_FABRICA
    fabrica = ler_fabrica(fab)
    pessoa_pasta = pasta_da_pessoa(fab, pasta_pessoa)
    try:
        pessoa = _ler_pessoa(pessoa_pasta)
    except ErroCatalogo:
        _avisar_pessoa_ilegivel(pessoa_pasta)
        pessoa = {"faixas": [], "ajustes": {}}
    return _mesclar(fabrica, pessoa)


def tem_catalogo(pasta_fabrica: Path | None = None, pasta_pessoa: Path | None = None) -> bool:
    """Há catálogo (de fábrica ou da pessoa)? Sem nenhum, a música automática fica desligada (A-012)."""
    fab = Path(pasta_fabrica) if pasta_fabrica is not None else PASTA_FABRICA
    return _arquivo(fab).is_file() or (pasta_da_pessoa(fab, pasta_pessoa) / ARQUIVO_PESSOA).is_file()


def nome_de_faixa_valido(nome: object) -> bool:
    """Só um nome simples de arquivo (nada de pasta, `..` ou caminho completo): a faixa mora numa das duas pastas de músicas."""
    return (isinstance(nome, str) and nome not in ("", ".", "..") and "/" not in nome and "\\" not in nome and ":" not in nome
            and Path(nome).name == nome)


def caminho_da_faixa(arquivo: str, pasta_fabrica: Path | None = None, pasta_pessoa: Path | None = None) -> Path | None:
    """O arquivo de áudio da faixa: primeiro na pasta da pessoa, depois na de fábrica. None = não existe (ou nome inválido)."""
    if not nome_de_faixa_valido(arquivo):
        return None
    fab = Path(pasta_fabrica) if pasta_fabrica is not None else PASTA_FABRICA
    for pasta in (pasta_da_pessoa(fab, pasta_pessoa), fab):
        if (pasta / arquivo).is_file():
            return pasta / arquivo
    return None


def ler(pasta: Path, pasta_pessoa: Path | None = None) -> dict[str, Any]:
    """O catálogo efetivo desta pasta de músicas (ver `catalogo_efetivo`)."""
    return catalogo_efetivo(pasta, pasta_pessoa)


def salvar(pasta: Path, cat: dict[str, Any]) -> None:
    """Regrava o `catalogo.json` de FÁBRICA: só para as ferramentas de manutenção (a tela grava no da pessoa)."""
    cat["total_faixas"] = len(cat.get("faixas", []))
    arq = _arquivo(pasta)
    tmp = arq.with_name(arq.name + ".tmp")
    tmp.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    substituir_arquivo(tmp, arq)


def nome_seguro(nome: str) -> str:
    base = re.sub(r"[^\w .()\-]+", "", Path(nome).stem, flags=re.UNICODE).strip(" .")
    return (base[:90] or "faixa") + Path(nome).suffix.lower()


def _link_ok(link: str) -> bool:
    return bool(re.match(r"^https?://[^\s]+\.[^\s]+$", (link or "").strip()))


def medir_faixa(arquivo: Path) -> dict[str, Any]:
    """BPM, batidas, duração e os campos de energia/drop/emoção (os mesmos de `tools.medir_musicas`)."""
    from .montagem import batidas as detectar_batidas
    from .musica_energia import medir_arquivo
    medidas = medir_arquivo(Path(arquivo))
    bats, dur = detectar_batidas(Path(arquivo))
    b = [float(x) for x in bats]
    bpm = None
    if len(b) >= 4:
        diffs = sorted(b[i + 1] - b[i] for i in range(len(b) - 1) if 0.22 <= b[i + 1] - b[i] <= 1.5)
        if diffs:
            bpm = round(60.0 / diffs[len(diffs) // 2], 1)
    return {"duracao_s": round(float(dur), 2), "bpm": bpm, "batidas_detectadas": len(b), **medidas}


def linha_de_credito(autor: str, titulo: str, link: str = "", licenca_nome: str = "") -> str:
    partes = [f"Música: {autor} — {titulo}".strip(" —")]
    if licenca_nome:
        partes.append(f"({licenca_nome})")
    if link:
        partes.append(link)
    return " ".join(partes)


def adicionar_faixa(pasta: Path, origem: Path, licenca: str, autor: str, titulo: str = "", link: str = "", exige_credito: bool | None = None,
                    estilo: str = "", medir: Callable[[Path], dict[str, Any]] | None = None, copiar: bool = True,
                    aviso_extra: str = "", credito_texto: str = "", pasta_pessoa: Path | None = None) -> dict[str, Any]:
    """Põe uma faixa no catálogo DA PESSOA, COM a licença. Sem licença conhecida, sem link quando a licença exige, arquivo estranho ou nome
    que já está no catálogo de fábrica ou no da pessoa = `ErroCatalogo`. Copia o arquivo para a pasta de músicas da pessoa (a menos que já
    esteja lá), mede BPM/drop/emoção e grava. Devolve a linha do catálogo. `pasta` é a de fábrica (o padrão da pessoa: `pasta_da_pessoa`)."""
    pasta, origem = Path(pasta), Path(origem)
    pessoa_pasta = pasta_da_pessoa(pasta, pasta_pessoa)
    chave = str(licenca or "").strip().lower()
    if chave in RECUSADAS:
        raise ErroCatalogo(RECUSADAS[chave])
    if chave not in LICENCAS:
        raise ErroCatalogo("Escolha a licença da música: " + ", ".join(f"{k} ({v['nome']})" for k, v in LICENCAS.items()) + ".")
    info = LICENCAS[chave]
    if info["link_obrigatorio"] and not _link_ok(link):
        raise ErroCatalogo(f"Para a licença '{info['nome']}' cole o link da página da faixa (começa com https://).")
    if link and not _link_ok(link):
        raise ErroCatalogo("O link da faixa precisa começar com http:// ou https://.")
    if not str(autor or "").strip():
        raise ErroCatalogo("Diga quem é o autor da música (aparece no crédito).")
    if origem.suffix.lower() not in EXTENSOES:
        raise ErroCatalogo(f"Formato de áudio não aceito ({origem.suffix or 'sem extensão'}). Use mp3, wav, ogg, flac, m4a ou opus.")
    if not origem.is_file() or origem.stat().st_size < TAMANHO_MIN:
        raise ErroCatalogo("Não achei o arquivo de música (ou ele é pequeno demais para ser uma música).")
    fabrica = ler_fabrica(pasta)                          # ilegível (um ou outro) = para aqui, antes de copiar ou gravar qualquer coisa
    cat = _ler_pessoa(pessoa_pasta)
    destino = pessoa_pasta / nome_seguro(origem.name)
    if any(isinstance(f, dict) and str(f.get("arquivo")) == destino.name for f in fabrica["faixas"] + cat["faixas"]):
        raise ErroCatalogo(f"A faixa {destino.name} já está no catálogo.")
    if info["credito"] is None and exige_credito is None:
        raise ErroCatalogo("Diga se essa faixa pede crédito na descrição (a página da faixa informa).")
    pede = bool(info["credito"]) if info["credito"] is not None else bool(exige_credito)
    pessoa_pasta.mkdir(parents=True, exist_ok=True)
    copiou = False
    if copiar and origem.resolve() != destino.resolve():
        if destino.exists():
            raise ErroCatalogo(f"Já existe um arquivo com o nome {destino.name} na pasta de músicas. Renomeie o seu.")
        shutil.copy2(origem, destino)
        copiou = True
    try:
        medidas = (medir or medir_faixa)(destino)
    except Exception as e:  # noqa: BLE001
        if copiou:
            destino.unlink(missing_ok=True)
        raise ErroCatalogo(f"Não consegui analisar a música ({type(e).__name__}). O arquivo está inteiro?") from e
    titulo = (titulo or Path(origem).stem).strip()
    faixa = {"arquivo": destino.name, "autor": autor.strip(), "titulo": titulo, "licenca": f"{info['nome']}" + (f" — {link.strip()}" if link else ""),
             "licenca_chave": chave, "fonte": link.strip(), "exige_credito": pede, "estilo": estilo.strip(), **medidas}
    if pede:
        # o texto oficial de crédito da fonte (NCS, YouTube Studio…) vale mais que o montado aqui
        faixa["credito"] = credito_texto.strip() or linha_de_credito(autor.strip(), titulo, link.strip(), info["nome"])
    avisos = [x for x in (info["aviso"], aviso_extra.strip()) if x]
    if avisos:
        faixa["aviso"] = " ".join(avisos)
    intens = faixa.get("bpm") or 0
    faixa["intensidade"] = "alta" if intens >= 126 else ("media" if intens >= 105 else "tensa")
    cat["faixas"].append(faixa)
    _salvar_pessoa(pessoa_pasta, cat)
    return faixa


def definir_ativa(pasta: Path, arquivo: str, ativa: bool, pasta_pessoa: Path | None = None) -> dict[str, Any]:
    """Liga ou desliga a faixa (tela Músicas). A da pessoa muda na própria linha; a de fábrica, só nos `ajustes` da pessoa (o
    `catalogo.json` nunca é regravado): voltar ao estado de fábrica apaga o ajuste. Devolve a linha efetiva."""
    pessoa_pasta = pasta_da_pessoa(pasta, pasta_pessoa)
    cat = _ler_pessoa(pessoa_pasta)
    for f in cat["faixas"]:
        if f["arquivo"] == arquivo:
            if ativa:
                f.pop("desligada", None)
            else:
                f["desligada"] = True
            _salvar_pessoa(pessoa_pasta, cat)
            return dict(f)
    de_fabrica = next((f for f in ler_fabrica(pasta)["faixas"] if isinstance(f, dict) and str(f.get("arquivo")) == arquivo), None)
    if de_fabrica is None:
        raise ErroCatalogo("Essa música não está no catálogo.")
    antes = cat["ajustes"].get(arquivo)
    ajuste = {k: v for k, v in (antes or {}).items() if k != "desligada"}
    if bool(de_fabrica.get("desligada")) != (not ativa):
        ajuste["desligada"] = not ativa
    if ajuste:
        cat["ajustes"][arquivo] = ajuste
    else:
        cat["ajustes"].pop(arquivo, None)
    if cat["ajustes"].get(arquivo) != antes:
        _salvar_pessoa(pessoa_pasta, cat)
    return {**de_fabrica, **{k: v for k, v in ajuste.items() if k != "arquivo"}}


def procedencia(f: dict[str, Any]) -> str:
    """De onde a faixa veio, em uma frase, para TODAS as faixas (as do pacote inicial não têm `fonte`/`aviso` no catálogo; a origem delas está em `licencas.txt`)."""
    fonte, licenca = str(f.get("fonte") or "").strip(), str(f.get("licenca") or "")
    if "pixabay" in licenca.lower():
        return "Origem: Pixabay (licença própria deles, não é CC0)."
    if "soundsafari" in fonte.lower() or (not fonte and licenca.upper().startswith("CC0")):
        return "Origem: repositório SoundSafari/CC0-1.0-Music, que classifica a faixa como CC0 (o site original pode ter fechado; não foi reverificada nele)."
    if fonte:
        return f"Origem: {fonte}"
    return "Origem não registrada."


def listar(pasta: Path, pasta_pessoa: Path | None = None) -> list[dict[str, Any]]:
    """Para a tela: cada faixa com o que importa (tem drop? pede crédito? existe? está ativa? entra no sorteio da montagem e em que posição da preferência?)."""
    from .montagem import DURACAO_MIN_MUSICA, faixas_do_rodizio
    pessoa_pasta = pasta_da_pessoa(pasta, pasta_pessoa)
    faixas = catalogo_efetivo(pasta, pessoa_pasta)["faixas"]
    saida = []
    for f in faixas:
        existe = caminho_da_faixa(str(f.get("arquivo", "")), pasta, pessoa_pasta) is not None
        saida.append({"arquivo": f.get("arquivo"), "titulo": f.get("titulo") or Path(str(f.get("arquivo", ""))).stem, "autor": f.get("autor", ""),
                      "licenca": f.get("licenca", ""), "fonte": f.get("fonte", ""), "estilo": f.get("estilo", ""), "bpm": f.get("bpm"),
                      "duracao_s": f.get("duracao_s"), "drop_s": f.get("drop_s"), "emocao": f.get("emocao"), "tem_drop": f.get("drop_s") is not None,
                      "exige_credito": bool(f.get("exige_credito")), "credito": f.get("credito", ""), "aviso": f.get("aviso", ""),
                      "procedencia": procedencia(f),
                      "existe": existe, "ativa": not f.get("desligada") and bool(str(f.get("licenca", "")).strip()) and existe})
    ligadas = [f for f, s in zip(faixas, saida) if s["ativa"]]
    ordem = {str(f.get("arquivo")): i + 1 for i, f in enumerate(faixas_do_rodizio(ligadas))}
    for s in saida:                                    # quem de fato pode ser sorteada (mesma regra da montagem) e em que posição da preferência
        s["posicao"] = ordem.get(str(s["arquivo"])) if s["ativa"] else None
        s["entra_no_sorteio"] = s["posicao"] is not None
        if s["ativa"] and not s["entra_no_sorteio"]:
            s["fora_do_sorteio"] = ("muito curta para a montagem (menos de %d s)" % DURACAO_MIN_MUSICA) if float(s["duracao_s"] or DURACAO_MIN_MUSICA) < DURACAO_MIN_MUSICA else "sem drop"
        else:
            s["fora_do_sorteio"] = ""
    return saida


def credito_da_faixa(pasta: Path, arquivo: str, pasta_pessoa: Path | None = None) -> dict[str, Any] | None:
    """O que vai no `publicar.txt` para a música usada: o crédito (se a licença exige) e o aviso (se houver). None = faixa fora do catálogo."""
    try:
        faixas = catalogo_efetivo(pasta, pasta_pessoa)["faixas"]
    except ErroCatalogo:
        return None
    for f in faixas:
        if str(f.get("arquivo")) == arquivo:
            return {"arquivo": arquivo, "titulo": f.get("titulo") or Path(arquivo).stem, "autor": f.get("autor", ""), "licenca": f.get("licenca", ""),
                    "exige_credito": bool(f.get("exige_credito")), "credito": f.get("credito", ""), "aviso": f.get("aviso", "")}
    return None
