"""Baixa as músicas livres do catálogo DE FÁBRICA (`musicas/catalogo.json`) e mantém esse catálogo e o `licencas.txt` coerentes.
O catálogo da pessoa (`catalogo_usuario.json`: as faixas dela e os ajustes da tela) nunca é lido para baixar nem regravado aqui.

Num clone novo o catálogo vem no git e os `.mp3` não (são pesados). Este script baixa o que falta:
  1. as faixas que o catálogo já lista e que têm origem registrada (`fonte` do repositório aberto `SoundSafari/CC0-1.0-Music`)
     e as 15 do pacote inicial (FreePD e uma do Pixabay);
  2. com `--buscar <termo>`, faixas novas entre as +7.000 do repositório (a licença sai da coleção de origem).
Regras (auditoria de 26/11/2026, P-002):
  * o catálogo existente NUNCA é apagado nem encolhido: faixa sem arquivo no disco continua listada, e faixa que já está nele não é refeita
    (licença, crédito e medidas ficam como estão); catálogo ilegível = o script para sem mexer em nada;
  * nenhuma faixa é rotulada CC0 por engano: a licença vem da coleção de origem (a pasta `pixbay.com` do repositório é Pixabay, com a licença
    própria do Pixabay) e arquivo solto na pasta, sem origem conhecida, fica FORA do catálogo (use a tela Músicas, com a licença);
  * `musicas/licencas.txt` é gerado a partir do catálogo (um teste confere que os dois batem).
Faixas suas, de outras fontes (NCS, YouTube...): adicione pela tela Músicas, sempre com a licença (veja `musicas/COMO_ADICIONAR_MUSICAS.md`).

Uso:
  .venv\\Scripts\\python.exe tools\\baixar_musicas_cc0.py
  .venv\\Scripts\\python.exe tools\\baixar_musicas_cc0.py --buscar "battle" --limite 3
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from cs2clip.musicas_catalogo import EXTENSOES, TAMANHO_MIN, ErroCatalogo, catalogo_efetivo, ler_fabrica, salvar  # noqa: E402
from cs2clip.tools import gravar_atomico, substituir_arquivo  # noqa: E402

DESTINO = RAIZ / "musicas"
RAW_BASE = "https://raw.githubusercontent.com/SoundSafari/CC0-1.0-Music/main/"
BLOB_BASE = "https://github.com/SoundSafari/CC0-1.0-Music/blob/main/"
API_BASE = "https://api.github.com/repos/SoundSafari/CC0-1.0-Music/contents/"
LICENCA_PIXABAY = "Pixabay Content License — https://pixabay.com/service/license-summary/"
AVISO_CC0 = ("Licença CC0 conforme a classificação do repositório SoundSafari/CC0-1.0-Music (o site de origem pode ter fechado; "
             "não foi reverificada na fonte). CC0 não exige crédito.")
AVISO_PIXABAY = ("O repositório SoundSafari lista esta faixa como CC0, mas o Pixabay usa a licença própria dele (uso livre, sem crédito). "
                 "Algumas faixas do Pixabay recebem reclamação automática (Content ID) no YouTube: se acontecer, conteste com o certificado "
                 "de licença da página da faixa.")
TEXTO_FONTE = ("https://github.com/SoundSafari/CC0-1.0-Music (e faixas adicionadas pelo dono; "
               "a licença de cada faixa está no campo `licenca`)")
TEXTO_LICENCA = ("Varia por faixa: veja o campo `licenca` e `aviso` de cada uma. "
                 "A maioria é CC0 1.0 (classificação do repositório SoundSafari).")

# (caminho_no_repo, nome_local, autor_fonte, estilo): o pacote inicial
FAIXAS_CC0: list[tuple[str, str, str, str]] = [
    ("freepd.com/Action Strike.mp3", "Action Strike.mp3", "Rafael Krux / FreePD", "Ação / Eletrônica rápida (CS2 Esports)"),
    ("freepd.com/Blippy Trance.mp3", "Blippy Trance.mp3", "Kevin MacLeod / FreePD", "Eletrônica / Synth rápido"),
    ("freepd.com/Circuit.mp3", "Circuit.mp3", "Kevin MacLeod / FreePD", "Eletrônica / Techno limpo"),
    ("freepd.com/Beat One.mp3", "Beat One.mp3", "Kevin MacLeod / FreePD", "Batida marcada para corte no beat"),
    ("freepd.com/Energizing.mp3", "Energizing.mp3", "Kevin MacLeod / FreePD", "Eletrônica energética para montagem"),
    ("freepd.com/Apex.mp3", "Apex.mp3", "Alexander Nakarada / FreePD", "Eletrônica / Clímax de torneio"),
    ("freepd.com/Battle Ready.mp3", "Battle Ready.mp3", "Bryan Teoh / FreePD", "Ação / Entrada de round decisivo"),
    ("freepd.com/Behind Enemy Lines.mp3", "Behind Enemy Lines.mp3", "Rafael Krux / FreePD", "Tensão tática / Clutch"),
    ("freepd.com/City Run.mp3", "City Run.mp3", "Rafael Krux / FreePD", "Synthwave / Fragmovie veloz"),
    ("freepd.com/Drop Point.mp3", "Drop Point.mp3", "Kevin MacLeod / FreePD", "Eletrônica com drop pesado"),
    ("freepd.com/Epic Boss Battle.mp3", "Epic Boss Battle.mp3", "Rafael Krux / FreePD", "Épico / Montagem de Ace e Clutch"),
    ("freepd.com/Hit n Smash.mp3", "Hit n Smash.mp3", "Rafael Krux / FreePD", "Impacto / Percussão agressiva"),
    ("freepd.com/Quick Metal Riff 1.mp3", "Quick Metal Riff 1.mp3", "Alexander Nakarada / FreePD", "Rock / Metal curto para Shorts"),
    ("freepd.com/Spec Ops.mp3", "Spec Ops.mp3", "Rafael Krux / FreePD", "Tático / Operações especiais CS2"),
    ("pixbay.com/black-box-legendary-9509.mp3", "black-box-legendary-9509.mp3", "Pixabay (autor não registrado) / via SoundSafari",
     "Bass / Trap épico para highlights"),
]
ORIGEM_DO_PACOTE = {nome: cam for cam, nome, _, _ in FAIXAS_CC0}


def nome_local_valido(nome: str) -> bool:
    """O nome vem do catálogo (um arquivo que pode ter sido editado à mão): só um nome simples, de áudio, dentro de `musicas/`."""
    return bool(nome) and Path(nome).name == nome and nome not in (".", "..") and Path(nome).suffix.lower() in EXTENSOES


def caminho_do_repo_valido(cam: str) -> bool:
    partes = cam.split("/")
    return bool(cam) and "\\" not in cam and not cam.startswith("/") and ".." not in partes and "" not in partes


def url_blob(caminho_repo: str) -> str:
    return BLOB_BASE + "/".join(urllib.parse.quote(p) for p in caminho_repo.split("/"))


def origem_no_repo(faixa: dict[str, Any]) -> str | None:
    """O caminho da faixa no repositório SoundSafari, ou None se o catálogo não registra a origem (faixa do próprio usuário).
    Só usa o que o catálogo já tem (`fonte` do repositório) ou o pacote inicial: nunca inventa endereço."""
    fonte = str(faixa.get("fonte") or "")
    cam = urllib.parse.unquote(fonte[len(BLOB_BASE):]) if fonte.startswith(BLOB_BASE) else ORIGEM_DO_PACOTE.get(str(faixa.get("arquivo")))
    return cam if cam and caminho_do_repo_valido(cam) else None


def licenca_da_colecao(caminho_repo: str) -> dict[str, Any]:
    """Licença e aviso de uma faixa nova pela coleção de onde veio. A pasta `pixbay.com` é do Pixabay (não é CC0)."""
    if caminho_repo.split("/")[0] == "pixbay.com":
        return {"licenca": LICENCA_PIXABAY, "licenca_chave": "pixabay", "exige_credito": False, "aviso": AVISO_PIXABAY}
    return {"licenca": f"CC0 1.0 (domínio público) — {url_blob(caminho_repo)}", "licenca_chave": "cc0", "exige_credito": False, "aviso": AVISO_CC0}


def _baixar_arquivo(caminho_repo: str, destino: Path) -> Path:
    url = RAW_BASE + "/".join(urllib.parse.quote(p) for p in caminho_repo.split("/"))
    req = urllib.request.Request(url, headers={"User-Agent": "BisturiClips/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        dados = resp.read()
    if len(dados) <= TAMANHO_MIN:
        raise OSError("o arquivo baixado é pequeno demais para ser uma música")
    tmp = destino.with_name(destino.name + ".tmp")
    tmp.write_bytes(dados)
    substituir_arquivo(tmp, destino)
    return destino


def buscar_no_repositorio_cc0(termo: str, colecoes: tuple[str, ...] = ("freepd.com", "pixbay.com", "chosic.com"),
                              limite: int = 5) -> list[tuple[str, str]]:
    """Busca faixas pelo nome nas coleções do repositório `SoundSafari/CC0-1.0-Music`."""
    termo_l = termo.strip().lower()
    achados: list[tuple[str, str]] = []
    for col in colecoes:
        url = API_BASE + urllib.parse.quote(col)
        req = urllib.request.Request(url, headers={"User-Agent": "BisturiClips/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                itens = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
        for it in itens:
            nome = it.get("name", "")
            if nome.lower().endswith((".mp3", ".wav", ".ogg")) and termo_l in nome.lower():
                achados.append((f"{col}/{nome}", nome))
                if len(achados) >= limite:
                    return achados
    return achados


def medir_faixa_nova(arq: Path) -> dict[str, Any]:
    """Duração, BPM, segundo do drop e batidas de uma faixa nova (o resto da medição é do `tools.medir_musicas`)."""
    from cs2clip.montagem import batidas as detectar_batidas
    bats, dur_medida = detectar_batidas(arq)
    batidas_l = [float(x) for x in bats]
    if len(batidas_l) >= 4:
        diffs = [batidas_l[i + 1] - batidas_l[i] for i in range(len(batidas_l) - 1) if 0.22 <= batidas_l[i + 1] - batidas_l[i] <= 1.5]
        diffs_ord = sorted(diffs)
        med = diffs_ord[len(diffs_ord) // 2] if diffs_ord else 0.5
        bpm = round(60.0 / med, 1)
        drop_s = next((round(b, 2) for idx, b in enumerate(batidas_l[:-2])
                       if b >= 2.0 and (batidas_l[idx + 2] - b) <= med * 2.4), round(batidas_l[0], 2))
    else:
        bpm, drop_s = 120.0, 0.0
    return {"duracao_s": round(float(dur_medida), 2), "bpm": bpm, "drop_s": drop_s, "batidas_detectadas": len(batidas_l),
            "intensidade": "alta" if bpm >= 126 else ("media" if bpm >= 105 else "tensa")}


def indexar_catalogo_musicas(pasta: Path = DESTINO, origens: dict[str, tuple[str, str, str]] | None = None,
                             medir: Callable[[Path], dict[str, Any]] | None = None) -> dict:
    """Põe no `catalogo.json` as faixas NOVAS que estão em `pasta` e têm origem conhecida (`origens`: arquivo → (caminho no repositório,
    autor, estilo)), com a licença da coleção. NUNCA remove nem refaz faixa que já está no catálogo (mesmo a que não tem arquivo no disco)
    e nunca põe no catálogo um arquivo sem origem conhecida: sem licença registrada, a música não entra."""
    medir = medir_faixa_nova if medir is None else medir
    origens = origens or {}
    cat = ler_fabrica(pasta)                            # ilegível = ErroCatalogo, sem escrever nada
    existentes = {str(f.get("arquivo")) for f in cat["faixas"]}
    existentes |= {str(f.get("arquivo")) for f in catalogo_efetivo(pasta)["faixas"] if isinstance(f, dict)}   # as da pessoa não são "arquivo solto"
    novas = 0
    for arq in sorted(pasta.iterdir()):
        if arq.suffix.lower() not in EXTENSOES or arq.stat().st_size <= TAMANHO_MIN or arq.name in existentes:
            continue
        origem = origens.get(arq.name)
        if origem is None:
            print(f"  [aviso] {arq.name} está na pasta mas não no catálogo e não tem licença registrada: fica de fora. "
                  "Adicione pela tela Músicas, com a licença.", flush=True)
            continue
        cam, autor, estilo = origem
        extra = licenca_da_colecao(cam)
        cat["faixas"].append({"arquivo": arq.name, "autor": autor, "licenca": extra["licenca"], "licenca_chave": extra["licenca_chave"],
                              "fonte": url_blob(cam), "exige_credito": extra["exige_credito"], "estilo": estilo, **medir(arq),
                              "aviso": extra["aviso"]})
        novas += 1
    if novas:
        cat.setdefault("fonte", TEXTO_FONTE)
        cat.setdefault("licenca", TEXTO_LICENCA)
        salvar(pasta, cat)
    return cat


def texto_de_licencas(catalogo: dict) -> str:
    """O `licencas.txt` inteiro, a partir do catálogo: uma linha por faixa, com a licença que o catálogo registra (nunca outra)."""
    linhas = [
        "# Licenças das músicas na pasta musicas/",
        "# Gerado a partir de musicas/catalogo.json por tools/baixar_musicas_cc0.py (não edite à mão: mude o catálogo e rode o script).",
        "# A licença de CADA faixa é a da linha dela: a maioria é CC0 1.0 (classificação do repositório SoundSafari), mas há exceções.",
        "# Fonte da maioria: https://github.com/SoundSafari/CC0-1.0-Music (coleções freepd.com, pixbay.com, chosic.com)",
        "",
    ]
    for f in catalogo.get("faixas", []):
        linha = f"- {f.get('arquivo', '')} | Autor: {f.get('autor', '')} | Licença: {f.get('licenca', '')} | Estilo: {f.get('estilo', '')}"
        if f.get("fonte"):
            linha += f" | Fonte: {f['fonte']}"
        if f.get("credito"):
            linha += f" | Crédito: {f['credito']}"
        if f.get("aviso"):
            linha += f" | Aviso: {f['aviso']}"
        linhas.append(linha)
    return "\n".join(linhas) + "\n"


def gravar_licencas(pasta: Path = DESTINO) -> None:
    cat = ler_fabrica(pasta)
    if cat["faixas"]:
        gravar_atomico(pasta / "licencas.txt", texto_de_licencas(cat))


def faixas_a_baixar(cat: dict, extras: list[tuple[str, str]] | None = None) -> list[tuple[str, str, str, str]]:
    """(nome local, caminho no repositório, autor, estilo): primeiro as do catálogo que têm origem registrada, depois as do pacote
    inicial que o catálogo não lista e as buscadas. Faixa do catálogo sem origem (do próprio usuário) não é baixada."""
    pedidos: list[tuple[str, str, str, str]] = []
    vistos: set[str] = set()
    for f in cat.get("faixas", []):
        nome = str(f.get("arquivo") or "")
        vistos.add(nome)
        cam = origem_no_repo(f)
        if cam and nome_local_valido(nome):
            pedidos.append((nome, cam, str(f.get("autor") or ""), str(f.get("estilo") or "")))
    for cam, nome, autor, estilo in FAIXAS_CC0:
        if nome not in vistos:
            vistos.add(nome)
            pedidos.append((nome, cam, autor, estilo))
    for cam, nome in (extras or []):
        if nome not in vistos and nome_local_valido(nome) and caminho_do_repo_valido(cam):
            vistos.add(nome)
            pedidos.append((nome, cam, "SoundSafari/CC0-1.0-Music", "Busca personalizada"))
    return pedidos


def baixar_musicas(pasta: Path = DESTINO, extras: list[tuple[str, str]] | None = None,
                   baixar: Callable[[str, Path], Path] | None = None, medir: Callable[[Path], dict[str, Any]] | None = None) -> list[Path]:
    """Baixa o que falta, põe no catálogo só as faixas novas (com a licença da coleção) e regera o `licencas.txt`. Devolve os arquivos que existem."""
    baixar = _baixar_arquivo if baixar is None else baixar
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    cat = ler_fabrica(pasta)                            # catálogo ilegível: para aqui, antes de baixar ou escrever qualquer coisa
    pedidos = faixas_a_baixar(cat, extras)
    existentes: list[Path] = []
    falhas = 0
    for nome, cam, _autor, estilo in pedidos:
        arq = pasta / nome
        if arq.exists() and arq.stat().st_size > TAMANHO_MIN:
            existentes.append(arq)
            continue
        print(f"Baixando {nome} ({estilo})...", flush=True)
        try:
            baixar(cam, arq)
        except (OSError, ValueError) as e:              # sem rede, 404, arquivo pequeno demais: segue com as outras
            falhas += 1
            print(f"  [aviso] não consegui baixar {nome}: {e}", flush=True)
            continue
        existentes.append(arq)
    origens = {nome: (cam, autor, estilo) for nome, cam, autor, estilo in pedidos}
    indexar_catalogo_musicas(pasta, origens, medir)
    gravar_licencas(pasta)
    if falhas:
        print(f"  [aviso] {falhas} faixa(s) não baixaram; rode de novo quando a internet voltar (o catálogo não foi mexido por causa disso).")
    return existentes


def main() -> None:
    ap = argparse.ArgumentParser(description="Baixa as músicas livres do catálogo do Bisturi Clips e mantém catalogo.json e licencas.txt coerentes.")
    ap.add_argument("--buscar", default="", help="Termo opcional para buscar mais músicas no repositório de +7.000 faixas.")
    ap.add_argument("--limite", type=int, default=3, help="Limite de faixas extras ao usar --buscar.")
    args = ap.parse_args()

    extras: list[tuple[str, str]] = []
    if args.buscar:
        print(f"Buscando '{args.buscar}' em SoundSafari/CC0-1.0-Music...", flush=True)
        extras = buscar_no_repositorio_cc0(args.buscar, limite=args.limite)
        for cam, _ in extras:
            print(f"  + encontrada: {cam}")
    try:
        arqs = baixar_musicas(DESTINO, extras=extras)
    except ErroCatalogo as e:
        print(f"Parei sem mexer em nada: {e}")
        raise SystemExit(1) from e
    print(f"Pronto: {len(arqs)} músicas no disco; catálogo em {DESTINO / 'catalogo.json'}")


if __name__ == "__main__":
    main()
