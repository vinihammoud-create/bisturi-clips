"""P5 itens 3 a 7 (27/11/2026): testes ESTÁTICOS do `instalador/BisturiClips.iss` (o instalador .exe do Inno Setup).

Só leem o texto do `.iss`: nada é instalado nem executado. O compilador do Inno não avisa quando um download fica sem `Hash:` (ACHADOS_POC_DEF,
surpresa 2), nem quando um caminho entra num comando sem aspas; por isso as regras de segurança são conferidas aqui, no texto.
O último bloco COMPILA o `.iss` com um pacote falso mínimo, se houver um `ISCC.exe` (variável `BISTURI_ISCC` ou as pastas padrão do Inno);
sem compilador ele pula. O `Setup.exe` gerado nunca é executado (a instalação de verdade é a P9, do dono).
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import cs2clip
from cs2clip.tools import sem_janela

RAIZ = Path(__file__).resolve().parents[1]
ISS = RAIZ / "instalador" / "BisturiClips.iss"
MODELO_TESTE = RAIZ / "docs" / "instalador" / "modelo_teste.iss"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
GUID = re.compile(r"^\{\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}$")

# Valores do FFmpeg que o montar.py passa por /D (os mesmos da prova D, ACHADOS_POC_DEF.md D.6); aqui só para COMPILAR.
FFMPEG_TESTE = {
    "FFmpegUrl": "https://github.com/GyanD/codexffmpeg/releases/download/9.1/ffmpeg-9.1-essentials_build.7z",
    "FFmpegSha256": "835b9a733445ba7a56a37a26cf7bde84e2f3ce7da2a8ec984276bb2abb1b922c",
    "FFmpegTamanhoExtraido": "345175907",
}


# ── leitura do .iss ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _texto() -> str:
    return ISS.read_text(encoding="utf-8-sig")


def _secoes(texto: str) -> dict[str, list[str]]:
    """Linhas de cada seção (`[Setup]`, `[Files]`...). Fora do `[Code]` tira comentário (`;`) e linha do pré-processador (`#`)."""
    secoes: dict[str, list[str]] = {"": []}
    atual = ""
    for linha in texto.splitlines():
        m = re.match(r"^\[(\w+)\]\s*$", linha)
        if m and atual != "Code":
            atual = m.group(1)
            secoes.setdefault(atual, [])
            continue
        if atual != "Code" and (not linha.strip() or linha.lstrip().startswith((";", "#"))):
            continue
        secoes[atual].append(linha)
    return secoes


def _diretivas_setup(texto: str) -> dict[str, str]:
    diretivas = {}
    for linha in _secoes(texto)["Setup"]:
        nome, _, valor = linha.partition("=")
        diretivas[nome.strip()] = valor.strip()
    return diretivas


def _entrada(linha: str) -> dict[str, str]:
    """`Source: "x"; DestDir: "{app}"; Flags: a b` -> {"Source": "x", ...} (separa por `;` fora das aspas)."""
    campos, atual, dentro = [], "", False
    for c in linha:
        if c == '"':
            dentro = not dentro
        if c == ";" and not dentro:
            campos.append(atual)
            atual = ""
        else:
            atual += c
    campos.append(atual)
    resultado = {}
    for campo in campos:
        nome, _, valor = campo.partition(":")
        if nome.strip():
            resultado[nome.strip()] = valor.strip().strip('"')
    return resultado


def _entradas(texto: str, secao: str) -> list[dict[str, str]]:
    return [_entrada(linha) for linha in _secoes(texto).get(secao, [])]


def _codigo(texto: str) -> str:
    """O `[Code]` (Pascal) sem os comentários `//` (fora das strings)."""
    linhas = []
    for linha in "\n".join(_secoes(texto)["Code"]).splitlines():
        sem, dentro, i = "", False, 0
        while i < len(linha):
            c = linha[i]
            if c == "'":
                dentro = not dentro
            if not dentro and linha.startswith("//", i):
                break
            sem += c
            i += 1
        linhas.append(sem.rstrip())
    return "\n".join(linhas)


def _sem_strings(trecho: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", trecho)


def _funcao(codigo: str, nome: str) -> str:
    """O corpo de `function Nome`/`procedure Nome`, até a próxima rotina que começa na coluna 0."""
    m = re.search(rf"^(?:function|procedure)\s+{nome}\b.*?(?=^(?:function|procedure)\s|\Z)", codigo, re.S | re.M)
    assert m, f"não achei a rotina {nome} no [Code]"
    return m.group(0)


def _downloads_sem_hash(texto: str) -> list[str]:
    """Entradas do `[Files]` que baixam algo sem `Hash:` de 64 hex (ou de um #define conferido com `#if Len(...) != 64` + `#error`),
    sem `ExternalSize:` ou sem o flag `external`. É a regra que o compilador NÃO confere."""
    problemas = []
    for linha in _secoes(texto).get("Files", []):
        e = _entrada(linha)
        flags = e.get("Flags", "").split()
        fonte = e.get("Source", "")
        baixa = "download" in flags or re.match(r"^\s*(https?|ftp)://", fonte, re.I) or re.search(r"\{#\w*Url\}", fonte)
        if not baixa:
            continue
        hash_ = e.get("Hash", "")
        m = re.fullmatch(r"\{#(\w+)\}", hash_)
        conferido = bool(m) and re.search(rf"#if\s+Len\({m.group(1)}\)\s*!=\s*64\s*\n\s*#error", texto) is not None
        if not (HEX64.match(hash_) or conferido):
            problemas.append(f"sem Hash conferido: {linha.strip()}")
        if not e.get("ExternalSize"):
            problemas.append(f"sem ExternalSize: {linha.strip()}")
        if "external" not in flags or "download" not in flags:
            problemas.append(f"download sem os flags external+download: {linha.strip()}")
    return problemas


@pytest.fixture(scope="module")
def texto() -> str:
    assert ISS.is_file(), "instalador/BisturiClips.iss não existe"
    return _texto()


@pytest.fixture(scope="module")
def codigo(texto: str) -> str:
    return _codigo(texto)


# ── arquivo, versão e [Setup] ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_o_iss_existe_e_e_utf8_com_bom():
    """Um .iss com acento só sai certo se for UTF-8 COM BOM (ACHADOS_POC_DEF, surpresa 5d)."""
    assert ISS.is_file()
    bruto = ISS.read_bytes()
    assert bruto.startswith(b"\xef\xbb\xbf")
    bruto.decode("utf-8")


def test_versao_padrao_e_a_do_programa_e_vem_por_parametro(texto):
    m = re.search(r'^#define\s+VersaoApp\s+"(\d+\.\d+\.\d+)"', texto, re.M)
    assert m and m.group(1) == cs2clip.__version__
    assert re.search(r"^#ifndef AppVersion\s*\n\s*#define AppVersion VersaoApp\s*\n\s*#endif", texto, re.M)
    s = _diretivas_setup(texto)
    assert s["AppVersion"] == "{#AppVersion}"
    assert s["OutputBaseFilename"] == "BisturiClips-Setup-{#AppVersion}"
    assert s["VersionInfoVersion"] == "{#AppVersion}.0"


def test_setup_por_usuario_x64_windows_1809_moderno(texto):
    s = _diretivas_setup(texto)
    assert s["PrivilegesRequired"] == "lowest"
    assert "PrivilegesRequiredOverridesAllowed" not in s       # nunca oferece "instalar para todos" (pediria administrador)
    assert s["DefaultDirName"] == r"{autopf}\Bisturi Clips"
    assert s["ArchitecturesAllowed"] == "x64compatible"
    assert s["ArchitecturesInstallIn64BitMode"] == "x64compatible"
    assert s["MinVersion"] == "10.0.17763"
    assert s["WizardStyle"] == "modern"
    assert s["ArchiveExtraction"] == "enhanced/nopassword"   # extractarchive não compila no "basic" do 6.x (surpresa 1)
    assert s["UninstallDisplayIcon"] == r"{app}\app\ui\static\icone.ico"
    assert s["DisableWelcomePage"] == "no"
    # O Restart Manager mataria à força um servidor sem janela (surpresa 3): quem confere o programa aberto é o [Code]
    assert s["CloseApplications"] == "no"


@pytest.mark.skipif(not MODELO_TESTE.is_file(), reason="o instalador de teste (docs/instalador/modelo_teste.iss) é só da oficina")
def test_appid_fixo_parametrizavel_e_diferente_do_modelo_de_teste(texto):
    s = _diretivas_setup(texto)
    assert s["AppId"] == "{#AppIdGuid}"
    m = re.search(r'^#ifndef AppIdGuid\s*\n\s*#define AppIdGuid "([^"]+)"', texto, re.M)
    assert m and GUID.match(m.group(1)), "o AppId padrão é um GUID fixo, com a primeira chave dobrada"
    modelo = MODELO_TESTE.read_text(encoding="utf-8-sig")
    assert m.group(1) not in modelo, "o instalador real não pode usar o GUID do instalador de teste"


def test_espaco_em_disco_realista(texto):
    """~4 GB além dos arquivos: venv com cuBLAS 1,6 GB, modelo 1,6 GB, músicas 0,2, HLAE e boiler 0,13, Python 0,07, FFmpeg 0,22."""
    extra = int(_diretivas_setup(texto)["ExtraDiskSpaceRequired"])
    assert 3_500_000_000 <= extra <= 7_000_000_000


def test_idioma_portugues_do_brasil(texto):
    idiomas = _entradas(texto, "Languages")
    assert [i["MessagesFile"] for i in idiomas] == [r"compiler:Languages\BrazilianPortuguese.isl"]


def test_sem_caminho_pessoal(texto):
    assert not re.search(r"[A-Za-z]:\\Users\\", texto, re.I)
    assert "\\Users\\" not in texto


# ── downloads: sempre com Hash ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_todo_download_tem_hash_tamanho_e_flags(texto):
    assert _downloads_sem_hash(texto) == []
    downloads = [e for e in _entradas(texto, "Files") if "download" in e.get("Flags", "").split()]
    assert len(downloads) == 1, "só o FFmpeg é baixado pelo [Files] (o modelo e o Python vão pelo uv/Python, que retomam)"


@pytest.mark.parametrize("linha", [
    'Source: "https://exemplo.com/a.7z"; DestName: "a.7z"; DestDir: "{tmp}"; ExternalSize: 10; Flags: external download extractarchive',
    'Source: "{#OutroUrl}"; DestName: "a.7z"; DestDir: "{tmp}"; ExternalSize: 10; Hash: "{#OutroHash}"; Flags: external download',
    'Source: "https://exemplo.com/a.7z"; DestName: "a.7z"; DestDir: "{tmp}"; ExternalSize: 10; Hash: "abc"; Flags: external download',
    'Source: "https://exemplo.com/a.7z"; DestName: "a.7z"; DestDir: "{tmp}"; Hash: "' + "a" * 64 + '"; Flags: external download',
])
def test_o_conferente_recusa_download_sem_hash_conferido(linha):
    """O conferente acima pega o que o compilador deixa passar: sem Hash, Hash de #define sem conferência, Hash curto, sem ExternalSize."""
    assert _downloads_sem_hash(f"[Files]\n{linha}\n")


def test_o_conferente_aceita_hash_fixo_ou_define_conferido():
    fixo = 'Source: "https://x/a.7z"; DestName: "a.7z"; ExternalSize: 1; Hash: "' + "b" * 64 + '"; Flags: external download'
    assert _downloads_sem_hash(f"[Files]\n{fixo}\n") == []
    por_define = ('#if Len(H) != 64\n  #error hash\n#endif\n[Files]\n'
                  'Source: "{#U}"; DestName: "a.7z"; ExternalSize: 1; Hash: "{#H}"; Flags: external download\n')
    assert _downloads_sem_hash(por_define) == []


def test_sem_download_escondido_no_code(codigo):
    """Download pelo [Code] (DownloadTemporaryFile, página de download, plugins) aceita hash vazio: não usamos."""
    for proibido in ("DownloadTemporaryFile", "CreateDownloadPage", "idpAdd", "ITD_", "URLDownloadToFile"):
        assert proibido not in codigo


def test_valores_de_teste_do_ffmpeg_sao_os_do_componentes_json():
    """Os /D que este arquivo usa para compilar são os mesmos que o montar.py tira do instalador/componentes.json."""
    import json
    ff = json.loads((RAIZ / "instalador" / "componentes.json").read_text(encoding="utf-8"))["ffmpeg"]
    assert FFMPEG_TESTE == {"FFmpegUrl": ff["url"], "FFmpegSha256": ff["sha256"], "FFmpegTamanhoExtraido": str(ff["tamanho_extraido"])}


def test_ffmpeg_so_por_parametros_conferidos_no_preprocessador(texto):
    for nome in ("Pacote", "FFmpegUrl", "FFmpegSha256", "FFmpegTamanhoExtraido"):
        assert re.search(rf"^#ifndef {nome}\s*\n\s*#error ", texto, re.M), f"/D{nome}= é obrigatório (sem valor padrão escondido)"
    assert re.search(r'^#if Pos\("https://github\.com/", FFmpegUrl\) != 1\s*\n\s*#error ', texto, re.M)
    assert re.search(r"^#if Len\(FFmpegSha256\) != 64\s*\n\s*#error ", texto, re.M)
    ffmpeg = [e for e in _entradas(texto, "Files") if "download" in e.get("Flags", "")][0]
    assert ffmpeg["Source"] == "{#FFmpegUrl}" and ffmpeg["Hash"] == "{#FFmpegSha256}"
    assert ffmpeg["ExternalSize"] == "{#FFmpegTamanhoExtraido}"
    assert ffmpeg["DestDir"] == r"{tmp}\ffx" and ffmpeg["DestName"].endswith(".7z")
    assert "extractarchive" in ffmpeg["Flags"].split()


# ── [Files], [Icons], [InstallDelete], [UninstallDelete] ─────────────────────────────────────────────────────────────────────────────

def test_arquivos_vem_do_pacote_parametrizavel(texto):
    fontes = [e["Source"] for e in _entradas(texto, "Files") if "download" not in e.get("Flags", "")]
    assert fontes == [r"{#Pacote}\app\*", r"{#Pacote}\uv.exe"]


def test_passos_finais_no_after_install_da_ultima_entrada(texto):
    entradas = _entradas(texto, "Files")
    assert entradas[-1].get("AfterInstall") == "PassosFinais"
    assert all("AfterInstall" not in e for e in entradas[:-1])


def test_atalhos_so_quando_os_passos_deram_certo(texto, codigo):
    icones = _entradas(texto, "Icons")
    assert {i["Name"] for i in icones} == {r"{autoprograms}\Bisturi Clips", r"{autodesktop}\Bisturi Clips"}
    pode = _funcao(codigo, "IconesPodem")
    assert "not PassosFalharam" in pode
    for i in icones:
        assert i["Filename"] == r"{app}\venv\Scripts\pythonw.exe" and i["Parameters"] == "-m ui"
        assert i["WorkingDir"] == r"{app}\app" and i["IconFilename"] == r"{app}\app\ui\static\icone.ico"
        checagem = i.get("Check", "")
        assert checagem, f"atalho sem Check: {i['Name']}"
        assert "IconesPodem" in _funcao(codigo, checagem.split("(")[0])


def test_install_delete_so_do_codigo_antigo(texto):
    """Atualização: some o código velho; `app\\bin` (FFmpeg, TwitchDownloader, HLAE) fica."""
    apagar = [(e["Type"], e["Name"]) for e in _entradas(texto, "InstallDelete")]
    assert apagar == [("filesandordirs", r"{app}\app\cs2clip"), ("filesandordirs", r"{app}\app\ui")]


def test_desinstalar_apaga_o_programa_mas_nunca_a_pasta_escolhida_inteira(texto):
    """Se a pessoa escolheu uma pasta cheia para o programa, `filesandordirs` em `{app}` apagaria tudo dela: só as pastas que são nossas."""
    apagar = {(e["Type"], e["Name"]) for e in _entradas(texto, "UninstallDelete")}
    assert ("filesandordirs", "{app}") not in apagar
    for pasta in ("app", "runtime", "venv", "cache-uv"):
        assert ("filesandordirs", rf"{{app}}\{pasta}") in apagar
    assert ("files", r"{app}\instalacao.log") in apagar
    assert ("dirifempty", "{app}") in apagar


def test_tarefas_e_tamanhos(texto):
    tarefas = {t["Name"]: t for t in _entradas(texto, "Tasks")}
    assert set(tarefas) == {"atalho", "musicas", "modelo", "demo"}
    assert "1,6 GB" in tarefas["modelo"]["Description"] and "0,5 GB" in tarefas["modelo"]["Description"]
    # revisão final da 1.1.0, item 11: o medido (HLAE e boiler: ~10 MB de download, ~132 MB no disco), não os 245 MB antigos;
    # o README cita o rótulo da tarefa com as mesmas palavras
    demo = "Gravar pela demo: instalar o HLAE e o boiler (cerca de 130 MB no disco, cerca de 10 MB de download)"
    assert tarefas["demo"]["Description"] == demo
    assert "245" not in texto
    assert f'"{demo}"' in (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "unchecked" in tarefas["demo"].get("Flags", "").split()
    for nome in ("atalho", "musicas", "modelo"):
        assert "unchecked" not in tarefas[nome].get("Flags", "")


# ── [Code]: falhas, comandos e caminhos ─────────────────────────────────────────────────────────────────────────────────────────────

def test_falha_de_passo_vira_mensagem_codigo_4_e_sem_atalho(codigo):
    finais = _funcao(codigo, "PassosFinais")
    # até a página de andamento fica dentro do try: exceção que escapa do AfterInstall não derruba o Setup (sairia com 0 e atalhos)
    assert re.search(r"begin\s+PassosFalharam := False;\s+Avisos := '';\s+try\s+try\s+PaginaAndamento\.Show;\s+PassosFinaisCorpo;\s+except", finais)
    assert finais.count("PaginaAndamento.Show") == 1
    assert "PassosFalharam := True" in finais
    assert "RemoverAtalhos" in finais                     # atualização que falhou não deixa o atalho velho apontando para o venv pela metade
    assert "abra o instalador de novo para continuar" in finais.lower()
    assert "instalacao.log" in codigo
    assert "SuppressibleMsgBox" in finais
    remover = _funcao(codigo, "RemoverAtalhos")
    assert "DeleteFile(LinkAreaDeTrabalho);" in remover and "DeleteFile(LinkMenuIniciar);" in remover
    assert r"ExpandConstant('{autodesktop}\Bisturi Clips.lnk')" in _funcao(codigo, "LinkAreaDeTrabalho")
    assert r"ExpandConstant('{autoprograms}\Bisturi Clips.lnk')" in _funcao(codigo, "LinkMenuIniciar")
    assert "ArquivoAtalhosCriados" in remover              # e só os que o marcador lista (P9, F1 e revisão final, item 4: testes abaixo)
    fim = _funcao(codigo, "DeinitializeSetup")
    assert "if PassosFalharam then" in fim and "ExitProcess(4)" in fim
    assert re.search(r"procedure ExitProcess\(\w+: Cardinal\); external 'ExitProcess@\{#Kernel32\} stdcall';", codigo)
    assert re.search(r'^#define Kernel32 "kernel32\.dll"\s*$', _texto(), re.M)


MARCADOR_ATALHOS = r"{app}\atalhos_criados.txt"


def test_falha_so_apaga_atalho_que_o_marcador_lista(codigo):
    """P9, F1 e revisão final da 1.1.0, item 4: uma instalação que falhou apagou o `Bisturi Clips.lnk` de outro programa (mesmo nome).
    "Já existe o unins000.exe" não prova nada: ele fica depois de uma 1ª instalação que FALHOU, e numa 2ª falha o atalho alheio sumiria.
    Só sai o atalho que o marcador `{app}\\atalhos_criados.txt` lista (gravado só no fim de uma instalação que deu certo, teste abaixo), e
    nunca com /SEMATALHO=1. Depois de apagar, o marcador também sai: um atalho de mesmo nome criado depois não é nosso."""
    remover = _funcao(codigo, "RemoverAtalhos")
    trava = re.search(r"begin\s+if \(Param\('SEMATALHO', '0'\) = '1'\) or \(not FileExists\(ArquivoAtalhosCriados\)\) then\s+begin(.*?)\bExit;\s+end;",
                      remover, re.S)
    assert trava, "RemoverAtalhos começa pela trava (antes de qualquer DeleteFile)"
    assert "DeleteFile" not in trava.group(1)
    assert trava.end() < remover.index("DeleteFile(")
    area = re.search(r"if AtalhoNoMarcador\(ATALHO_AREA\) then\s+DeleteFile\(LinkAreaDeTrabalho\);", remover)
    menu = re.search(r"if AtalhoNoMarcador\(ATALHO_MENU\) then\s+DeleteFile\(LinkMenuIniciar\);", remover)
    assert area and menu, "cada atalho só sai se o marcador o lista"
    fim = re.search(r"DeleteFile\(ArquivoAtalhosCriados\);\s+end;\s*$", remover)
    assert fim and fim.start() > max(area.end(), menu.end()), "o marcador sai por último, depois dos atalhos"
    assert len(re.findall(r"DeleteFile\(", remover)) == 3
    # os caminhos dos atalhos só existem nas duas funções; nenhum outro lugar do [Code] apaga um atalho
    assert len(re.findall(r"\.lnk", codigo)) == 2
    for m in re.finditer(r"DeleteFile\((Link\w+)\)", codigo):
        assert codigo.index(remover) < m.start() < codigo.index(remover) + len(remover), m.group(0)
    # o unins000.exe não serve de prova (revisão final, item 4)
    assert "HaviaDesinstalador" not in codigo and "unins000" not in codigo
    assert "AtalhosSaoDesteInstalador" not in codigo


def test_marcador_de_atalhos_so_depois_de_instalacao_que_deu_certo(texto, codigo):
    """O marcador é gravado em `ssPostInstall` (depois do `[Icons]`) e só se os passos deram certo; lista só o atalho que existe E que é
    nosso: o que o `[Icons]` acabou de criar (o mesmo Check) ou o que o marcador já listava. O desinstalador o apaga com o programa."""
    m = re.search(r"Result := ExpandConstant\('([^']+)'\);", _funcao(codigo, "ArquivoAtalhosCriados"))
    assert m and m.group(1) == MARCADOR_ATALHOS
    assert ("files", MARCADOR_ATALHOS) in {(e["Type"], e["Name"]) for e in _entradas(texto, "UninstallDelete")}
    assert re.search(r"^\s*ATALHO_MENU = 'menu_iniciar';", codigo, re.M) and re.search(r"^\s*ATALHO_AREA = 'area_de_trabalho';", codigo, re.M)
    passo = _funcao(codigo, "CurStepChanged")
    assert re.search(r"if \(CurStep = ssPostInstall\) and \(not PassosFalharam\) then\s+GravarAtalhosCriados;", passo)
    assert len(re.findall(r"\bGravarAtalhosCriados\b", codigo)) == 2          # a rotina e a única chamada
    gravar = _funcao(codigo, "GravarAtalhosCriados")
    assert "if FileExists(LinkMenuIniciar) and (AtalhoMenuIniciar or AtalhoNoMarcador(ATALHO_MENU)) then" in gravar
    assert "if FileExists(LinkAreaDeTrabalho) and (AtalhoAreaDeTrabalho or AtalhoNoMarcador(ATALHO_AREA)) then" in gravar
    assert "SaveStringsToFile(ArquivoAtalhosCriados, Linhas, False)" in gravar
    assert re.search(r"if N = 0 then\s+DeleteFile\(ArquivoAtalhosCriados\)", gravar)
    # só esta rotina grava o marcador; quem lê é o AtalhoNoMarcador (linha a linha, sem espaços)
    assert len(re.findall(r"SaveStringsToFile\(ArquivoAtalhosCriados", codigo)) == 1
    ler = _funcao(codigo, "AtalhoNoMarcador")
    assert "LoadStringsFromFile(ArquivoAtalhosCriados, Linhas)" in ler and "if Trim(Linhas[I]) = Atalho then" in ler


def _simular_instalacoes(eventos: list[str]) -> tuple[dict[str, str], list[str]]:
    """A regra do Pascal acima, em Python. Eventos: "ok" e "falha" (execuções do instalador, com o atalho da Área de Trabalho marcado) e
    "alheio" (outro programa cria um atalho com o mesmo nome). Começa com atalhos de OUTRO programa. Devolve (dono de cada atalho que
    existe, os atalhos alheios que o RemoverAtalhos apagou)."""
    atalhos, marcador, apagados_alheios = {"menu": "outro", "area": "outro"}, set(), []
    for e in eventos:
        if e == "alheio":
            atalhos.update(menu="outro", area="outro")
        elif e == "falha":
            for a in sorted(marcador):                                      # RemoverAtalhos: só o que o marcador lista
                if a in atalhos and atalhos.pop(a) != "nosso":
                    apagados_alheios.append(a)
            marcador = set()                                                # ... e o marcador sai junto
        else:
            atalhos.update(menu="nosso", area="nosso")                      # o [Icons] cria (ou troca) os dois
            marcador = {a for a in ("menu", "area") if a in atalhos}        # GravarAtalhosCriados (ssPostInstall)
    return atalhos, apagados_alheios


@pytest.mark.parametrize("eventos,sobram", [
    (["falha"], {"menu", "area"}),                    # 1ª instalação falhou: o atalho alheio fica
    (["falha", "falha"], {"menu", "area"}),           # a 2ª falha também não o apaga (o caso da revisão final, item 4)
    (["ok", "falha"], set()),                         # atualização que falhou: sai o nosso (apontaria para o venv pela metade)
    (["ok", "falha", "alheio", "falha"], {"menu", "area"}),   # o marcador saiu com os nossos: o alheio criado depois fica
    (["ok", "falha", "ok"], {"menu", "area"}),
])
def test_regra_do_marcador_nunca_apaga_atalho_alheio(eventos, sobram):
    atalhos, apagados_alheios = _simular_instalacoes(eventos)
    assert set(atalhos) == sobram
    assert apagados_alheios == []


def test_icones_mantem_o_check_que_os_segura_ate_o_fim_dos_passos(texto, codigo):
    """O F1 depende disto: sem `Check:` o Inno criaria o atalho mesmo numa instalação que falhou (e aí a 1ª instalação teria atalho nosso)."""
    checks = {i["Name"]: i.get("Check", "") for i in _entradas(texto, "Icons")}
    assert checks == {r"{autoprograms}\Bisturi Clips": "AtalhoMenuIniciar", r"{autodesktop}\Bisturi Clips": "AtalhoAreaDeTrabalho"}
    assert "IconesPodem" in _funcao(codigo, "AtalhoMenuIniciar")
    assert "IconesPodem and WizardIsTaskSelected('atalho')" in _funcao(codigo, "AtalhoAreaDeTrabalho")


def test_saida_com_codigo_4_limpa_o_ffmpeg_baixado_no_tmp(texto, codigo):
    """P9, F4: o ExitProcess(4) pula a limpeza do Setup e o `.7z` baixado (36 MB) ficava em %TEMP%\\is-*.tmp. Antes de sair, some o que
    ESTE .iss pôs no {tmp} (o arquivo baixado e a pasta de extração), dentro de try, e mais nada."""
    ffmpeg = [e for e in _entradas(texto, "Files") if "download" in e.get("Flags", "")][0]
    nome, pasta = ffmpeg["DestName"], ffmpeg["DestDir"]
    limpar = _funcao(codigo, "LimparTemporariosDoFFmpeg")
    bloco = re.search(r"begin\s+try(.*?)except(.*?)end;\s+end;", limpar, re.S)
    assert bloco, "a limpeza fica dentro de try/except (falhar aqui não pode mudar o código de saída)"
    assert re.findall(r"(DeleteFile|DelTree)\(([^;]*)\);", bloco.group(1)) == [
        ("DeleteFile", f"ExpandConstant('{{tmp}}\\{nome}')"),
        ("DelTree", f"ExpandConstant('{pasta}'), True, True, True"),
    ]
    assert "DeleteFile" not in bloco.group(2) and "DelTree" not in bloco.group(2)
    fim = _funcao(codigo, "DeinitializeSetup")
    assert re.search(r"if PassosFalharam then\s+begin\s+.*?LimparTemporariosDoFFmpeg;\s+ExitProcess\(4\);", fim, re.S)


def test_nvidia_smi_procurado_primeiro_no_system32_de_verdade(codigo):
    """P9, F2: o Setup é 32 bits e o FileExists('{sys}\\...') é redirecionado ao SysWOW64 (que não tem o nvidia-smi): instalava sempre em
    CPU. {sysnative} é o System32 de verdade; {sys} fica como segundo palpite (instalador 64 bits) e depois a pasta antiga do NVSMI."""
    smi = _funcao(codigo, "ProgramaNvidiaSmi")
    palpites = re.findall(r"Result := ExpandConstant\('([^']+)'\);", smi)
    assert palpites == [r"{sysnative}\nvidia-smi.exe", r"{sys}\nvidia-smi.exe", r"{commonpf64}\NVIDIA Corporation\NVSMI\nvidia-smi.exe"]
    assert re.search(r"^begin\s+Result := ExpandConstant\('\{sysnative\}", smi, re.M)
    assert len(re.findall(r"if not FileExists\(Result\) then", smi)) == 3
    assert re.search(r"if not FileExists\(Result\) then\s+Result := '';\s+end;\s*$", smi)


def test_log_da_instalacao_na_pasta_do_programa(codigo):
    registrar = _funcao(codigo, "Registrar")
    assert "Log(" in registrar and "SaveStringsToUTF8File" in registrar
    assert r"'\instalacao.log'" in _funcao(codigo, "ArquivoDeLog")


def test_parametros_do_modo_silencioso(codigo):
    for nome in ("DADOS", "SEMATALHO", "SEMMODELO", "SEMNVIDIA"):
        assert re.search(rf"Param\('{nome}'", codigo), nome
    assert "{param:APAGARDADOS|0}" in codigo or re.search(r"Param\('APAGARDADOS'", codigo)
    assert "ExpandConstant('{param:' + Nome + '|' + Padrao + '}')" in _funcao(codigo, "Param")


def test_so_um_lugar_roda_programas_e_nada_de_shell(codigo):
    """Toda execução passa por `Executar` (o caminho vai em `Filename`, os argumentos montados com `Aspas`); a única exceção é o
    `nvidia-smi`, com argumentos fixos. Sem cmd, PowerShell nem ShellExec."""
    padrao = r"\b(ExecAndLogOutput|ExecAndCaptureOutput|ShellExec|ShellExecAsOriginalUser|ExecAsOriginalUser|Exec)\s*\("
    assert set(re.findall(padrao, codigo)) == {"ExecAndLogOutput"}
    executar, nvidia = _funcao(codigo, "Executar"), _funcao(codigo, "DetectarNvidia")
    assert len(re.findall(padrao, executar)) == 1 and len(re.findall(padrao, nvidia)) == 2
    assert len(re.findall(padrao, codigo)) == 3, "ExecAndLogOutput fora do Executar e do DetectarNvidia"
    for args in re.findall(r"ExecAndLogOutput\(Smi, ('(?:[^']|'')*'|[^,]+),", nvidia):
        assert re.fullmatch(r"'[^']*'", args), f"nvidia-smi com argumento montado: {args}"
    baixo = codigo.lower()
    for proibido in ("cmd.exe", "cmd /c", "powershell", "taskkill", "terminateprocess", "stop-process", "wscript", "cscript"):
        assert proibido not in baixo, proibido


def test_argumentos_de_comando_so_com_texto_fixo_ou_aspas(codigo):
    """Caminho com espaço, acento ou apóstrofo nunca entra solto num comando: toda linha que monta `Args` só soma texto fixo,
    `Aspas(...)` ou o próprio `Args`."""
    linhas = [linha for linha in codigo.splitlines() if re.search(r"\bArgs\s*:=", linha)]
    assert len(linhas) >= 6
    for linha in linhas:
        direita = _sem_strings(linha.split(":=", 1)[1].strip().rstrip(";"))
        partes = [p.strip() for p in re.split(r"\+(?![^()]*\))", direita)]
        for parte in partes:
            ok = parte == "''" or parte == "Args" or re.fullmatch(r"Aspas\(.+\)", parte) or re.fullmatch(r"OpcoesUv\(\)|OpcoesUv", parte)
            assert ok, f"pedaço solto num comando: {parte!r} em {linha.strip()!r}"
    for chamada in re.findall(r"Executar\(([^;]*)\);", codigo):
        if "const" in chamada:
            continue
        assert re.search(r",\s*(Args|'[^']*')\s*,", chamada), f"Executar sem Args montado: {chamada}"


def test_aspas_dobra_a_barra_do_fim_e_recusa_aspas_no_caminho(codigo):
    aspas = _funcao(codigo, "Aspas")
    assert "#34" in aspas and "RaiseException" in aspas
    # TODAS as barras do fim são dobradas ("C:\x\\" viraria \\\" sem isso); o I > 0 para o laço mesmo num texto só de barras
    assert "while (I > 0) and (Copy(S, I, 1) = '\\') do" in aspas and "Result := Result + '\\';" in aspas and "I := I - 1;" in aspas
    assert re.search(r"Result := #34 \+ Result \+ #34;\s+end;", aspas)
    resto = codigo.replace(aspas, "")
    assert "#34" not in resto and '"' not in resto, "aspas duplas só saem da função Aspas"


def _pascal_aspas(s: str) -> str:
    """A regra da função Aspas do Pascal, linha a linha."""
    if s == "" or '"' in s:
        raise ValueError(s)
    resultado, i = s, len(s)
    while i > 0 and s[i - 1:i] == "\\":
        resultado += "\\"
        i -= 1
    return '"' + resultado + '"'


@pytest.mark.skipif(sys.platform != "win32", reason="CommandLineToArgvW é do Windows")
@pytest.mark.parametrize("caminho", ["C:\\", "C:\\x\\\\", "C:\\x\\\\\\", "C:\\Arquivos de Programas\\Bisturi Clips\\app\\bin",
                                     "C:\\Usuário D'Ávila\\Vídeos\\", "\\\\", "C:\\a b\\c"])
def test_regra_das_aspas_chega_inteira_ao_programa(caminho):
    """O Windows (CommandLineToArgvW, a mesma regra do C e do Rust que o uv e o Python usam) devolve o caminho exato, com qualquer número
    de barras no fim."""
    import ctypes
    from ctypes import wintypes
    shell32, kernel32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("kernel32")
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    n = ctypes.c_int()
    linha = "prog.exe --dados " + _pascal_aspas(caminho) + " --bin " + _pascal_aspas("C:\\b\\")
    argv = shell32.CommandLineToArgvW(linha, ctypes.byref(n))
    try:
        assert [argv[i] for i in range(n.value)] == ["prog.exe", "--dados", caminho, "--bin", "C:\\b\\"]
    finally:
        kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


def test_expand_constant_so_com_texto_fixo(codigo):
    """Um caminho da pessoa com `{` dentro nunca passa pelo ExpandConstant (viraria constante do Inno)."""
    for m in re.finditer(r"ExpandConstant\(([^;]*?)\)(?=[\s;+,)])", codigo):
        argumento = m.group(1)
        if argumento == "'{param:' + Nome + '|' + Padrao + '}'":
            continue
        assert re.fullmatch(r"'[^']*'", argumento), f"ExpandConstant com texto montado: {argumento}"


def test_uv_com_python_gerenciado_sem_sujar_o_windows(codigo):
    corpo = _funcao(codigo, "PassosFinaisCorpo")
    ambiente = _funcao(codigo, "PrepararAmbiente")
    assert "DefinirVariavel('UV_PYTHON_INSTALL_DIR', App + '\\runtime')" in ambiente
    assert "DefinirVariavel('UV_CACHE_DIR', App + '\\cache-uv')" in ambiente
    assert "DefinirVariavel('UV_NO_CONFIG', '1')" in ambiente
    for nome in ("PYTHONHOME", "PYTHONPATH"):
        assert f"DefinirVariavel('{nome}', '')" in ambiente
    opcoes = _funcao(codigo, "OpcoesUv")
    assert "--no-config" in opcoes and "--cache-dir " in opcoes
    instalar = re.search(r"Args := 'python install 3\.12 --install-dir ' \+ Aspas\(App \+ '\\runtime'\) \+ ' --no-bin --no-registry'", corpo)
    venv = re.search(r"Args := 'venv ' \+ Aspas\(App \+ '\\venv'\) \+ ' --python 3\.12 --managed-python --no-python-downloads", corpo)
    sync = re.search(r"Args := 'pip sync --python ' \+ Aspas\(Python\) \+ ' --require-hashes --compile-bytecode --no-python-downloads'", corpo)
    assert instalar and venv and sync
    assert "requirements-runtime.lock" in corpo and "requirements-gpu.lock" in corpo
    ordem = [corpo.index(t) for t in ("PrepararAmbiente(", "PassoFFmpeg", "'python install", "'venv ", "'pip sync", "PassoPos(",
                                        "DelTree(App + '\\cache-uv'")]
    assert ordem == sorted(ordem), "ordem: ambiente, FFmpeg, Python, venv, pacotes, pos e, no fim, o cache do uv apagado"


def test_chamada_do_pos_segue_o_contrato(codigo):
    pos = _funcao(codigo, "PassoPos")
    assert "Args := '-X utf8 -m cs2clip.instalacao pos --dados ' + Aspas(Dados) + ' --bin ' + Aspas(App + '\\app\\bin');" in pos
    for opcao in ("--nvidia", "--sem-nvidia", "--com-modelo", "--com-gravacao-demo", "--sem-musicas"):
        assert f"' {opcao}'" in pos, opcao
    assert re.search(r"Codigo = 1\b", pos) and re.search(r"Codigo = 2\b", pos) and re.search(r"Codigo <> 0\b", pos)
    assert "App + '\\app'" in pos                         # pasta de trabalho = raiz do programa (o `-m cs2clip` acha o pacote)
    assert "DefinirVariavel('PYTHONIOENCODING', 'utf-8')" in _funcao(codigo, "PrepararAmbiente")
    saida = _funcao(codigo, "SaidaDoComando")
    assert "'AVISO:'" in saida and "'['" in saida and "Registrar(" in saida


def test_flags_do_pos_existem_no_comando_de_verdade():
    """Se o `cs2clip.instalacao` já existe (outro bloco da P5), cada opção que o .iss passa tem de estar na ajuda dele."""
    if importlib.util.find_spec("cs2clip.instalacao") is None:
        pytest.skip("cs2clip/instalacao.py ainda não existe (outro ajudante da P5)")
    r = subprocess.run([sys.executable, "-m", "cs2clip.instalacao", "pos", "--help"], cwd=RAIZ,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, **sem_janela())
    assert r.returncode == 0, r.stderr
    for opcao in ("--dados", "--bin", "--nvidia", "--sem-nvidia", "--com-modelo", "--com-gravacao-demo", "--sem-musicas"):
        assert opcao in r.stdout, opcao


def test_marcador_do_ffmpeg_comparado_em_minusculas(codigo):
    """O /DFFmpegSha256 pode vir em maiúsculas (o componentes.json aceita): o marcador gravado e o comparado são os dois em minúsculas."""
    instalado = _funcao(codigo, "FFmpegInstalado")
    assert "Result := Lowercase(Trim(Linhas[0])) = Lowercase('{#FFmpegSha256}');" in instalado
    assert "Linhas[0] := Lowercase('{#FFmpegSha256}');" in _funcao(codigo, "PassoFFmpeg")
    assert re.findall(r"'\{#FFmpegSha256\}'", codigo) == ["'{#FFmpegSha256}'"] * 2
    assert len(re.findall(r"Lowercase\('\{#FFmpegSha256\}'\)", codigo)) == 2


def test_ffmpeg_fica_so_com_ffmpeg_e_ffprobe(codigo):
    ff = _funcao(codigo, "PassoFFmpeg")
    copias = re.findall(r"CopyFile\(([^)]*)\)", ff)
    assert len(copias) == 2 and "ffmpeg.exe" in copias[0] and "ffprobe.exe" in copias[1]
    assert "CopyFile" not in codigo.replace(ff, "")
    assert "ffplay" not in codigo.lower()
    assert "DelTree(ExpandConstant('{tmp}\\ffx'), True, True, True)" in codigo


def test_placa_nvidia_pelo_nvidia_smi(codigo):
    nv = _funcao(codigo, "DetectarNvidia")
    assert "nvidia-smi.exe" in nv and "'-L'" in nv
    assert "Param('SEMNVIDIA', '0') = '1'" in nv
    pagina = _funcao(codigo, "CriarPaginaNvidia")
    assert "CreateInputOptionPage(wpSelectTasks" in pagina and "small" in pagina


def test_pasta_de_dados_onedrive_validacao_e_atualizacao(codigo):
    proximo = _funcao(codigo, "NextButtonClick")
    assert "onedrive" in proximo and "MB_DEFBUTTON2" in proximo and "DadosValidos" in proximo
    pular = _funcao(codigo, "ShouldSkipPage")
    assert "PaginaDados.ID" in pular and "GetPreviousData('PastaDados', '')" in pular
    validos = _funcao(codigo, "DadosValidos")
    assert "':\\'" in _funcao(codigo, "ProblemaNoCaminho") and "ExpandConstant('{app}')" in validos
    assert "PastaDados" in _funcao(codigo, "RegisterPreviousData")
    assert "DadosValidos" in _funcao(codigo, "PrepareToInstall")


# A regra do ProblemaNoCaminho e do DadosValidos, em Python, com a semântica das funções do Inno (AddBackslash, Copy, Pos, Lowercase).
# O teste estático abaixo prende cada linha do Pascal a esta regra; o teste de cruzamento confere que ela nunca é mais fraca que o `pos`.
def _pascal_problema_no_caminho(dados: str) -> str:
    caminho = dados if dados.endswith("\\") else dados + "\\"
    letra = dados[:1].lower()
    if len(dados) < 4 or dados[1:3] != ":\\" or not letra or letra not in "abcdefghijklmnopqrstuvwxyz":
        return "formato"
    if ".\\" in caminho or " \\" in caminho or "\\\\" in caminho:
        return "pontos"
    if ":" in dados[2:]:
        return "dois-pontos"
    if any(ord(c) < 32 or ord(c) == 34 or c in "<>|?*/" for c in dados):
        return "caractere"
    return ""


def _pascal_dados_validos(dados: str, app: str) -> bool:
    if _pascal_problema_no_caminho(dados) or not Path(dados[:3]).is_dir() or Path(dados).is_file():
        return False
    a, c = (app.rstrip("\\") + "\\").lower(), (dados.rstrip("\\") + "\\").lower()
    return not (c.startswith(a) or a.startswith(c))


def test_validacao_da_pasta_de_dados_no_pascal_tem_as_regras_do_python(codigo):
    problema = _funcao(codigo, "ProblemaNoCaminho")
    for trecho in ("(Length(Dados) < 4)", "(Copy(Dados, 2, 2) <> ':\\')", "'abcdefghijklmnopqrstuvwxyz') = 0",
                   "Caminho := AddBackslash(Dados);", "(Pos('.\\', Caminho) > 0)", "(Pos(' \\', Caminho) > 0)", "(Pos('\\\\', Caminho) > 0)",
                   "Pos(':', Copy(Dados, 3, Length(Dados))) > 0", "(Ord(Dados[I]) < 32)", "(Ord(Dados[I]) = 34)", "Pos(Copy(Dados, I, 1), '<>|?*/') > 0"):
        assert trecho in problema, trecho
    validos = _funcao(codigo, "DadosValidos")
    for trecho in ("Motivo := ProblemaNoCaminho(Dados);", "DirExists(Copy(Dados, 1, 3))", "FileExists(Dados)",
                   "(Pos(App, Caminho) = 1) or (Pos(Caminho, App) = 1)", "Lowercase(AddBackslash(ExpandConstant('{app}')))"):
        assert trecho in validos, trecho


def test_regra_do_pascal_nunca_e_mais_fraca_que_a_do_pos(tmp_path):
    """Todo caminho que o `pos` recusa (código 2), o Pascal também recusa antes de copiar qualquer arquivo; os bons passam nos dois."""
    from cs2clip import instalacao
    disco = tmp_path.anchor                                        # ex.: "C:\\"
    app = str(tmp_path / "Bisturi Clips")
    raiz = Path(app) / "app"
    ruins = [disco, disco[:2], disco + ".", disco + "x\\..", disco + "x\\..\\.", disco + "...", disco + "x\\.\\y", str(tmp_path),
             app, app + "\\dados", str(raiz / "dados"), disco + "a\\\\b", disco + "x:y", disco + "a?b", disco + "a\tb", "relativo\\x",
             "\\\\servidor\\pasta", disco + "x. \\y", disco + "x\\.. "]
    bons = [str(tmp_path / "Bisturi Clips Dados"), str(tmp_path / "Vídeos de João d'Arc" / "Bisturi Clips"), disco + "Bisturi Clips"]
    for d in bons:
        assert _pascal_dados_validos(d, app), d
        instalacao._validar_dados(d, raiz)
    for d in ruins:
        try:
            instalacao._validar_dados(d, raiz)
            recusa_python = False
        except ValueError:
            recusa_python = True
        assert recusa_python <= (not _pascal_dados_validos(d, app)), f"o pos recusa {d!r} e o Pascal aceitaria"
        assert not _pascal_dados_validos(d, app), d               # e o Pascal recusa todos estes (alguns o Python resolve e aceita)


def test_pasta_dados_txt_de_outra_pasta_nunca_e_sobrescrito(codigo):
    """A trava "já usa outra pasta de dados" do `pos` vale também no Pascal: o `pasta_dados.txt` só é gravado se ainda não existe; se aponta
    para outra pasta, o Setup para ANTES de copiar (PrepareToInstall, código 7) e, por garantia, de novo antes de gravar."""
    conflito = _funcao(codigo, "ConflitoComDadosRegistrados")
    assert "Registrada := DadosRegistrados;" in conflito
    assert "(Registrada <> '') and (Lowercase(Registrada) <> Lowercase(Dados))" in conflito
    assert "já usa outra pasta de dados" in conflito                           # a mesma frase do cs2clip/instalacao.py
    from cs2clip import instalacao
    assert "já usa outra pasta de dados" in Path(instalacao.__file__).read_text(encoding="utf-8")
    gravar = _funcao(codigo, "GravarPastaDados")
    ordem = [gravar.index(t) for t in ("ConflitoComDadosRegistrados(Dados)", "RaiseException(Motivo)", "if FileExists(Arquivo) then",
                                       "SaveStringsToUTF8File(Arquivo")]
    assert ordem == sorted(ordem)
    assert re.search(r"if FileExists\(Arquivo\) then\s+Exit;", gravar)
    preparar = _funcao(codigo, "PrepareToInstall")
    ordem = [preparar.index(t) for t in ("DadosValidos(PastaDados", "ConflitoComDadosRegistrados(PastaDados)", "EsperarProgramaFechado(")]
    assert ordem == sorted(ordem)
    rotinas = list(_rotinas(codigo))
    assert rotinas.index("DadosRegistrados") < rotinas.index("ConflitoComDadosRegistrados") < rotinas.index("GravarPastaDados")


def test_codigo_2_do_pos_mostra_a_ultima_linha_de_erro(codigo):
    pos = _funcao(codigo, "PassoPos")
    ramo = re.search(r"Codigo = 2 then\s+RaiseException\(([^;]*)\);", pos)
    assert ramo and "UltimaLinha" in ramo.group(1)
    assert "avise quem mantém" not in pos


def test_programa_aberto_pergunta_e_nunca_derruba(codigo):
    """Com o Bisturi aberto (pode haver trabalho rodando: R-001) o instalador pede para encerrar pela tela e espera; em silêncio, falha."""
    health = _funcao(codigo, "RespondeComoBisturi")
    assert "WinHttp.WinHttpRequest.5.1" in health and "'/health'" in health and "127.0.0.1" in health
    assert re.search(r"PORTA_PRIMEIRA = 8765;", codigo) and re.search(r"PORTA_ULTIMA = 8775;", codigo)   # as de ui/__main__.py
    assert "for Porta := PORTA_PRIMEIRA to PORTA_ULTIMA do" in _funcao(codigo, "PortaDoProgramaAberto")
    # "conectou e não respondeu a tempo" (o servidor ocupado) conta como ABERTO; só erro de conexão (nada escutando) conta como fechado
    assert "Pedido.SetProxy(PROXY_DIRETO);" in health and re.search(r"PROXY_DIRETO = 1;", codigo)   # 127.0.0.1 nunca vai a um proxy
    assert re.search(r"Pedido\.Open\('GET', [^;]*, True\);", health), "pedido assíncrono: o prazo da resposta é do WaitForResponse"
    assert "Respondeu := Pedido.WaitForResponse(" in health
    sem_resposta = re.search(r"if not Respondeu then\s+begin(.*?)end\s+else", health, re.S)
    assert sem_resposta and "Result := True;" in sem_resposta.group(1)
    assert health.index("SetProxy") < health.index("SetTimeouts") < health.index("Open(") < health.index("Send(") < health.index("WaitForResponse")
    esperar = _funcao(codigo, "EsperarProgramaFechado")
    assert "MB_RETRYCANCEL" in esperar and "IDRETRY" in esperar and "SuppressibleMsgBox" in esperar
    assert "EsperarProgramaFechado" in _funcao(codigo, "PrepareToInstall")
    assert "EsperarProgramaFechado" in _funcao(codigo, "InitializeUninstall")


def test_desinstalar_pergunta_dados_padrao_nao_e_silencioso_so_com_parametro(codigo):
    passo = _funcao(codigo, "CurUninstallStepChanged")
    assert "UninstallSilent" in passo and "APAGARDADOS" in passo
    assert passo.count("MB_DEFBUTTON2") >= 2                    # dados e modelo: o padrão é NÃO
    apagar = _funcao(codigo, "ApagarDados")
    assert not re.search(r"DelTree\(Dados\s*,", apagar), "nunca apaga a pasta de dados inteira: só o que o programa cria nela"
    for item in ("'config.toml'", "'entrada'", "'saida'", "'modelos'", "'trabalhos'", "'demos'"):
        assert item in codigo
    assert "RemoveDir(Dados)" in apagar


def test_desinstalar_so_apaga_pasta_com_o_marcador_do_pos(codigo):
    """Sem o `.bisturi-dados` (que o `pos` grava só numa pasta sem coisas da pessoa com os nomes do programa) nada da pasta de dados é
    apagado, nem com /APAGARDADOS=1; o modelo também passa pela mesma trava. Os nomes que o Pascal apaga são os do Python."""
    from cs2clip import instalacao
    m = re.search(r"^\s*MARCADOR_DADOS = '([^']+)';", codigo, re.M)
    assert m and m.group(1) == instalacao.MARCADOR_DADOS
    segura = _funcao(codigo, "PastaDadosSegura")
    assert "FileExists(Dados + '\\' + MARCADOR_DADOS)" in segura and "ProblemaNoCaminho(Dados) = ''" in segura and "DirExists(Dados)" in segura
    assert "config.toml" not in segura
    apagar = _funcao(codigo, "ApagarDados")
    nomes = re.findall(r"ApagarItem\(Dados \+ '\\' \+ '([^']+)'\)", apagar)
    assert sorted(nomes) == sorted(instalacao.ITENS_DO_PROGRAMA) and len(nomes) == len(set(nomes))
    marcador = apagar.index("ApagarItem(Dados + '\\' + MARCADOR_DADOS)")
    assert max(apagar.index(f"'{n}'") for n in nomes) < marcador < apagar.index("RemoveDir")      # o marcador sai por último
    passo = _funcao(codigo, "CurUninstallStepChanged")
    trava = re.search(r"if not PastaDadosSegura\(DadosDesinstalar\) then\s+begin(.*?)\bExit;\s+end;", passo, re.S)
    assert trava and "MsgBox(" in trava.group(1) and "not UninstallSilent" in trava.group(1)
    assert trava.start() < passo.index("Param('APAGARDADOS'"), "a trava vem antes de ler o /APAGARDADOS=1"
    chamadas = re.findall(r"if ([^\n]*?) then\s+begin\s+Log\([^;]*\);\s+ApagarDados\(", passo)
    assert len(chamadas) == 2 and all("PastaDadosSegura(DadosDesinstalar)" in c for c in chamadas), chamadas


def _rotinas(codigo: str) -> dict[str, str]:
    """Nome -> texto de cada rotina (até a próxima). As declarações `external` (uma linha) levam junto o `const`/`var` global que vem depois."""
    return {m.group(1): m.group(0) for m in re.finditer(r"^(?:function|procedure)\s+(\w+)\b.*?(?=^(?:function|procedure)\s|\Z)",
                                                        codigo, re.S | re.M)}


def test_pascal_com_blocos_e_parenteses_fechados(codigo):
    """Sem o compilador por perto, o erro mais comum (um `end` ou um parêntese a menos) é pego aqui: em cada rotina, os `begin`/`try`/`case`
    fecham com `end` e os parênteses fecham."""
    rotinas = _rotinas(codigo)
    assert len(rotinas) >= 40
    for nome, corpo in rotinas.items():
        limpo = _sem_strings(corpo)
        if "external '" in corpo:
            continue
        palavras = re.findall(r"\b(begin|try|case|end)\b", limpo, re.I)
        abertos = sum(1 for p in palavras if p.lower() != "end")
        assert abertos == len(palavras) - abertos, f"{nome}: {abertos} begin/try/case para {len(palavras) - abertos} end"
        assert limpo.count("(") == limpo.count(")"), f"{nome}: parênteses desequilibrados"
        assert re.search(r"\bend;\s*$", limpo), f"{nome}: não termina em end;"


def test_toda_rotina_chamada_existe(codigo, texto):
    """Chamada a rotina nossa com nome errado só apareceria ao compilar: os nomes que o [Code] e as seções usam existem."""
    rotinas = set(_rotinas(codigo))
    usadas = set(re.findall(r"\b(?:Check|AfterInstall):\s*(\w+)", texto))
    assert usadas <= rotinas, usadas - rotinas
    nossas = {"Param", "ArquivoDeLog", "Registrar", "Aspas", "DefinirVariavel", "QuerModelo", "UsarNvidia", "IconesPodem", "RemoverAtalhos",
              "PastaVideos", "DadosPadrao", "PastaDados", "DadosValidos", "GravarPastaDados", "ProgramaNvidiaSmi", "DetectarNvidia",
              "CriarPaginaNvidia", "RespondeComoBisturi", "PortaDoProgramaAberto", "EsperarProgramaFechado", "MostrarPasso", "Executar",
              "Rodar", "Etapa", "PrepararAmbiente", "OpcoesUv", "FFmpegInstalado", "PastaExtraidaFFmpeg", "PassoFFmpeg", "PassoPos",
              "PassosFinaisCorpo", "DadosRegistrados", "PastaDadosSegura", "ApagarItem", "ApagarDados", "ProblemaNoCaminho",
              "ConflitoComDadosRegistrados", "LimparTemporariosDoFFmpeg", "ArquivoAtalhosCriados", "LinkMenuIniciar",
              "LinkAreaDeTrabalho", "AtalhoNoMarcador", "GravarAtalhosCriados", "CurStepChanged"}
    assert nossas <= rotinas, nossas - rotinas


# ── compilação de verdade (só se houver ISCC; nunca executa o Setup gerado) ───────────────────────────────────────────────────────

def _iscc() -> Path | None:
    candidatos = [os.environ.get("BISTURI_ISCC", ""), shutil.which("ISCC") or ""]
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        if base:
            for sub in (r"Programs\Inno Setup 7", r"Programs\Inno Setup 6", "Inno Setup 7", "Inno Setup 6"):
                candidatos.append(str(Path(base) / sub / "ISCC.exe"))
    return next((Path(c) for c in candidatos if c and Path(c).is_file()), None)


def _pacote_falso(pasta: Path) -> Path:
    app = pasta / "app"
    (app / "cs2clip").mkdir(parents=True)
    (app / "cs2clip" / "__init__.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")
    (app / "ui" / "static").mkdir(parents=True)
    shutil.copy2(RAIZ / "ui" / "static" / "icone.ico", app / "ui" / "static" / "icone.ico")
    (app / "requirements-runtime.lock").write_text("# falso\n", encoding="utf-8")
    (pasta / "uv.exe").write_bytes(b"MZ falso")
    return pasta


def _compilar(tmp_path: Path, **defines: str) -> subprocess.CompletedProcess[str]:
    iscc = _iscc()
    if iscc is None:
        pytest.skip("Inno Setup (ISCC.exe) não encontrado: defina BISTURI_ISCC para compilar")
    args = [str(iscc), *(f"/D{k}={v}" for k, v in defines.items()), f"/O{tmp_path / 'saida'}", str(ISS)]
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, **sem_janela())


def _defines_validos(tmp_path: Path) -> dict[str, str]:
    return {"Pacote": str(_pacote_falso(tmp_path / "pacote")), "AppIdGuid": "{{00000000-0000-4000-8000-00000000B15C}", **FFMPEG_TESTE}


def test_compila_sem_avisos(tmp_path):
    r = _compilar(tmp_path, **_defines_validos(tmp_path))
    saida = r.stdout + r.stderr
    assert r.returncode == 0, saida
    assert not re.search(r"^\s*(Warning|Aviso)\b", saida, re.M | re.I), saida
    assert (tmp_path / "saida" / f"BisturiClips-Setup-{cs2clip.__version__}.exe").is_file()


@pytest.mark.parametrize("troca", [{"FFmpegSha256": "835b9a73"}, {"FFmpegUrl": "https://exemplo.com/ffmpeg.7z"}])
def test_compilacao_recusa_ffmpeg_sem_hash_ou_fora_do_github(tmp_path, troca):
    r = _compilar(tmp_path, **{**_defines_validos(tmp_path), **troca})
    assert r.returncode != 0
    assert not (tmp_path / "saida").exists() or not any((tmp_path / "saida").glob("*.exe"))


def test_compilacao_exige_o_pacote(tmp_path):
    defines = _defines_validos(tmp_path)
    defines.pop("Pacote")
    assert _compilar(tmp_path, **defines).returncode != 0
