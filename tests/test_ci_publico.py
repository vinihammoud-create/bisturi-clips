"""P6 (publicação): confere os dois workflows do repositório PÚBLICO (`instalador/ci/tests.yml` e `instalador/ci/release.yml`) sem rodar nada.

O exportador (P8) os coloca em `.github/workflows/` da cópia pública; a oficina não os executa (o `.github/workflows/tests.yml` dela continua manual,
`tests/test_ci_so_manual_f8.py`). Aqui valem as regras de segurança do plano (seção 5): ações fixadas por SHA de commit, permissões mínimas, nenhum
texto de fora dentro de `run:` (injeção), nenhum segredo escrito, Inno Setup com versão e hash fixos, smoke e desinstalação antes da Release, e a
Release só como rascunho. A 1ª execução de verdade só acontece no GitHub público (P11); o `actionlint` é rodado à mão (relatório da P6).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[1]
PASTA_CI = RAIZ / "instalador" / "ci"

# O Inno fixado (jrsoftware/issrc, release is-7_1_1; hash lido na release oficial e conferido ao baixar). Escrito de novo aqui de propósito:
# trocar a versão ou o hash no workflow sem passar por este teste não vale.
INNO_URL = "https://github.com/jrsoftware/issrc/releases/download/is-7_1_1/innosetup-7.1.1-x64.exe"
INNO_SHA256 = "8a5a6711c3766a6e5fb74f280ba0e3551e719ce94538d901fdbd1f8f9920b3f0"

SHA_COMPLETO = re.compile(r"[0-9a-f]{40}")
USO = re.compile(r"^\s*-?\s*uses:\s*(\S+)(?:\s*#\s*(.*))?$")
EXPRESSAO = re.compile(r"\$\{\{.*?\}\}", re.S)
SEGREDOS_NO_TEXTO = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|passwd|senha|token|api[_-]?key|secret)\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{12,}"),
    re.compile(r"(?i)https?://[^/\s:@]+:[^/\s@]+@"),      # usuário:senha@host
]
PADROES_PERIGOSOS_NO_RUN = [
    (re.compile(r"(?i)\b(iex|invoke-expression)\b"), "Invoke-Expression"),
    (re.compile(r"(?i)(curl|wget|iwr|invoke-webrequest)[^\n|]*\|\s*(sh|bash|iex|pwsh|powershell)\b"), "baixar e executar direto (curl | sh)"),
    (re.compile(r"(?i)\|\s*(sh|bash)\b"), "pipe para o shell"),
]


# ── leitura ───────────────────────────────────────────────────────────────────────────────────────
def ler(nome: str) -> tuple[str, dict[str, Any]]:
    texto = (PASTA_CI / nome).read_text(encoding="utf-8")
    dados = yaml.safe_load(texto)
    assert isinstance(dados, dict), f"{nome} não é um mapa YAML"
    return texto, dados


def gatilhos(wf: dict[str, Any]) -> dict[str, Any]:
    """`on:` vira a chave booleana True no YAML 1.1 do PyYAML; aceita as duas formas e devolve sempre um dict evento -> filtros."""
    chaves: dict[Any, Any] = wf
    bruto = chaves["on"] if "on" in chaves else chaves.get(True)
    if isinstance(bruto, str):
        return {bruto: None}
    if isinstance(bruto, list):
        return {e: None for e in bruto}
    assert isinstance(bruto, dict), "workflow sem gatilho"
    return bruto


def passos(wf: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(nome, p) for nome, job in wf["jobs"].items() for p in job["steps"]]


def scripts(wf: dict[str, Any]) -> list[tuple[str, str]]:
    return [(nome, p["run"]) for nome, p in passos(wf) if "run" in p]


def tudo_de_permissions(wf: dict[str, Any]) -> list[tuple[str, Any]]:
    achadas: list[tuple[str, Any]] = []
    if "permissions" in wf:
        achadas.append(("workflow", wf["permissions"]))
    for nome, job in wf["jobs"].items():
        if "permissions" in job:
            achadas.append((f"job {nome}", job["permissions"]))
    return achadas


# ── verificações reaproveitadas (e testadas com exemplos defeituosos mais abaixo) ──────────────────
def problemas_de_acoes(texto: str) -> list[str]:
    """Toda linha `uses:` tem de ser `dono/repo[/caminho]@<SHA de 40 hex>  # <tag de origem>`. Ação local (./) e docker:// também são recusadas."""
    achados: list[str] = []
    for n, linha in enumerate(texto.splitlines(), 1):
        m = USO.match(linha)
        if not m:
            continue
        alvo, comentario = m.group(1), (m.group(2) or "").strip()
        if "@" not in alvo or alvo.startswith(("./", "docker://")):
            achados.append(f"linha {n}: ação local, docker ou sem versão: {alvo}")
            continue
        _, _, ref = alvo.rpartition("@")
        if not SHA_COMPLETO.fullmatch(ref):
            achados.append(f"linha {n}: {alvo} não está fixada por SHA de commit completo (40 hex)")
        if not re.match(r"v\d+(\.\d+)*", comentario):
            achados.append(f"linha {n}: falta no comentário a tag de origem (# vX.Y.Z): {alvo}")
    return achados


def problemas_de_permissoes(wf: dict[str, Any], escrita_permitida_em: set[str]) -> list[str]:
    achados: list[str] = []
    if "permissions" not in wf:
        achados.append("o workflow não declara `permissions` no topo (o padrão do GitHub pode ser amplo)")
    for onde, perm in tudo_de_permissions(wf):
        if not isinstance(perm, dict):
            achados.append(f"{onde}: permissions precisa ser um mapa explícito, veio {perm!r} (read-all/write-all não valem)")
            continue
        for escopo, nivel in perm.items():
            if escopo != "contents":
                achados.append(f"{onde}: escopo {escopo!r} não é permitido (só contents)")
            elif nivel == "write" and onde not in escrita_permitida_em:
                achados.append(f"{onde}: contents: write fora do job que cria a Release")
            elif nivel not in ("read", "write"):
                achados.append(f"{onde}: nível {nivel!r} inválido")
    return achados


def problemas_de_run(wf: dict[str, Any]) -> list[str]:
    achados: list[str] = []
    for nome, run in scripts(wf):
        if "${{" in run:
            achados.append(f"{nome}: `${{{{ }}}}` dentro de run (injeção): passe por env: e leia a variável de ambiente")
        for padrao, descricao in PADROES_PERIGOSOS_NO_RUN:
            if padrao.search(run):
                achados.append(f"{nome}: {descricao}")
    return achados


def problemas_de_shell(wf: dict[str, Any]) -> list[str]:
    """Todo passo com `run` precisa de `shell` explícito (no passo, no job ou nos defaults do workflow); no Windows só `pwsh`/`powershell`."""
    achados: list[str] = []
    padrao_wf = (wf.get("defaults") or {}).get("run", {}).get("shell")
    for nome_job, job in wf["jobs"].items():
        padrao_job = (job.get("defaults") or {}).get("run", {}).get("shell", padrao_wf)
        for p in job["steps"]:
            if "run" not in p:
                continue
            shell = p.get("shell", padrao_job)
            rotulo = f"{nome_job}/{p.get('name', '?')}"
            if not shell:
                achados.append(f"{rotulo}: sem `shell` explícito")
            elif str(job.get("runs-on", "")).startswith("windows") and shell not in ("pwsh", "powershell"):
                achados.append(f"{rotulo}: no Windows o shell precisa ser pwsh (veio {shell!r})")
    return achados


def problemas_de_segredos(texto: str) -> list[str]:
    achados = [f"parece um segredo escrito no arquivo: {p.pattern[:40]}" for p in SEGREDOS_NO_TEXTO if p.search(texto)]
    for expr in EXPRESSAO.findall(texto):
        if "secrets." in expr and expr.replace(" ", "") != "${{secrets.GITHUB_TOKEN}}":
            achados.append(f"segredo além do GITHUB_TOKEN: {expr}")
    return achados


def posicao(wf: dict[str, Any], job: str, trecho: str) -> int:
    """Posição de `trecho` no texto de todos os `run` do job, na ordem dos passos (-1 se não houver); serve para ordem dentro do mesmo passo."""
    return "\n".join(p.get("run", "") for p in wf["jobs"][job]["steps"]).find(trecho)


def indice(wf: dict[str, Any], job: str, trecho: str) -> int:
    """Posição do 1º passo do job cujo `run` contém `trecho` (-1 se não houver)."""
    for n, p in enumerate(wf["jobs"][job]["steps"]):
        if trecho in p.get("run", ""):
            return n
    return -1


# ══ tests.yml ═════════════════════════════════════════════════════════════════════════════════════
def test_tests_gatilhos_sao_push_e_pull_request():
    _, wf = ler("tests.yml")
    assert set(gatilhos(wf)) == {"push", "pull_request"}


def test_tests_nao_usa_pull_request_target_nem_workflow_run():
    texto, _ = ler("tests.yml")
    for proibido in ("pull_request_target", "workflow_run", "self-hosted"):
        assert proibido not in texto


def test_tests_permissoes_so_leitura():
    _, wf = ler("tests.yml")
    assert wf["permissions"] == {"contents": "read"}
    assert problemas_de_permissoes(wf, escrita_permitida_em=set()) == []


def test_tests_roda_no_windows_com_o_lock_e_as_ferramentas_depois_do_sync():
    _, wf = ler("tests.yml")
    jobs = wf["jobs"]
    assert jobs and all(j["runs-on"] == "windows-latest" for j in jobs.values())
    (nome,) = jobs
    i_sync = posicao(wf, nome, "uv pip sync")
    i_ferramentas = posicao(wf, nome, "requirements-teste.txt")
    i_ruff = posicao(wf, nome, "ruff check --select F,E9")
    i_pytest = posicao(wf, nome, "-m pytest")
    assert -1 not in (i_sync, i_ferramentas, i_ruff, i_pytest)
    assert i_sync < i_ferramentas < i_ruff < i_pytest, "as ferramentas de teste vão DEPOIS do uv pip sync (o sync desinstala o que não está no lock)"
    sync = jobs[nome]["steps"][indice(wf, nome, "uv pip sync")]["run"]
    assert "--require-hashes" in sync and "requirements-runtime.lock" in sync
    assert 'not lento' in jobs[nome]["steps"][indice(wf, nome, "-m pytest")]["run"]


def test_tests_instala_o_uv_com_hash_e_so_versoes_fixas():
    texto, wf = ler("tests.yml")
    assert "--require-hashes" in "\n".join(r for _, r in scripts(wf) if "requirements-uv.txt" in r)
    uv = (PASTA_CI / "requirements-uv.txt").read_text(encoding="utf-8")
    assert re.search(r"^uv==\d+\.\d+\.\d+ \\\n\s+--hash=sha256:[0-9a-f]{64}$", uv, re.M)
    for linha in (PASTA_CI / "requirements-teste.txt").read_text(encoding="utf-8").splitlines():
        if linha.strip() and not linha.startswith("#"):
            assert re.fullmatch(r"[A-Za-z0-9_.-]+==\d+(\.\d+)*", linha.strip()), f"ferramenta de teste sem versão fixa: {linha}"


def test_tests_tem_prazo_e_nao_passa_pela_oficina():
    texto, wf = ler("tests.yml")
    assert all("timeout-minutes" in j for j in wf["jobs"].values())


# ══ release.yml ═══════════════════════════════════════════════════════════════════════════════════
def test_release_gatilho_e_so_tag_vx_y_z():
    _, wf = ler("release.yml")
    g = gatilhos(wf)
    assert set(g) == {"push"}
    assert g["push"] == {"tags": ["v*.*.*"]}, "sem branches e sem outros eventos: só tag v*.*.*"


def test_release_a_escrita_so_existe_no_job_que_cria_a_release():
    _, wf = ler("release.yml")
    assert wf["permissions"] == {"contents": "read"}
    assert problemas_de_permissoes(wf, escrita_permitida_em={"job publicar-rascunho"}) == []
    # o job que roda o código do projeto e o instalador não tem escrita; quem tem escrita não roda nada do projeto
    montar = wf["jobs"]["montar-e-provar"]
    assert "permissions" not in montar
    publicar = wf["jobs"]["publicar-rascunho"]
    assert publicar["permissions"] == {"contents": "write"}
    assert publicar.get("needs") == "montar-e-provar"
    usos = [p.get("uses", "") for p in publicar["steps"]]
    assert not any(u.startswith("actions/checkout@") for u in usos), "o job com escrita não baixa o código do projeto"
    for passo in publicar["steps"]:
        assert "python" not in passo.get("run", "") and "instalador/" not in passo.get("run", "")


def test_release_roda_no_windows_e_so_o_job_de_publicar_no_linux():
    _, wf = ler("release.yml")
    assert wf["jobs"]["montar-e-provar"]["runs-on"] == "windows-latest"
    assert wf["jobs"]["publicar-rascunho"]["runs-on"] == "ubuntu-latest"


def test_release_o_inno_vem_de_versao_fixa_e_o_hash_e_conferido_antes_de_executar():
    _, wf = ler("release.yml")
    run = wf["jobs"]["montar-e-provar"]["steps"][indice(wf, "montar-e-provar", "innosetup")]["run"]
    assert INNO_URL in run and INNO_SHA256 in run
    assert "Get-FileHash" in run and "SHA256" in run
    i_hash, i_comparacao, i_execucao = run.index("Get-FileHash"), run.index("-ne $esperado"), run.index("Start-Process")
    assert i_hash < i_comparacao < i_execucao, "o hash tem de ser conferido (e recusar) ANTES de executar o instalador do Inno"
    assert re.search(r"-ne \$esperado\)\s*\{\s*throw", run), "hash diferente tem de parar o workflow"
    for ruim in ("choco install innosetup", "winget", "7_2_0", "preview"):
        assert ruim not in run


def test_release_monta_com_o_montar_py_e_usa_os_dados_com_hash():
    _, wf = ler("release.yml")
    j = "montar-e-provar"
    validar, montar = indice(wf, j, "montar.py --validar"), indice(wf, j, "montar.py --baixar")
    assert -1 not in (validar, montar) and validar < montar
    comando = wf["jobs"][j]["steps"][montar]["run"]
    for parte in ("--baixar", "--pacote", "--compilar", "--iscc"):
        assert parte in comando
    assert indice(wf, j, "notas_da_versao.py") < indice(wf, j, "innosetup"), "sem seção no CHANGELOG o workflow para antes de montar"
    numpy = wf["jobs"][j]["steps"][indice(wf, j, "requirements-montagem.txt")]["run"]
    assert "--require-hashes" in numpy


def test_release_confere_que_a_tag_e_a_versao_do_codigo():
    _, wf = ler("release.yml")
    j = "montar-e-provar"
    run = wf["jobs"][j]["steps"][indice(wf, j, "__version__")]["run"]
    assert "GITHUB_REF_NAME" in run and r"^v\d+\.\d+\.\d+$" in run and "throw" in run
    assert indice(wf, j, "__version__") < indice(wf, j, "montar.py --baixar")


def test_release_instala_em_silencio_com_os_parametros_de_teste_e_caminho_com_espaco():
    _, wf = ler("release.yml")
    j = "montar-e-provar"
    run = wf["jobs"][j]["steps"][indice(wf, j, "Programa Instalado")]["run"]
    for parte in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SEMATALHO=1", "/SEMMODELO=1", "/SEMNVIDIA=1", "/DIR=", "/DADOS=", "/LOG="):
        assert parte in run, parte
    assert re.search(r"'Bisturi Teste'|'Programa Instalado'", run), "o caminho de teste tem espaço de propósito"
    assert "ExitCode -ne 0" in run and ".bisturi-dados" in run
    assert "RUNNER_TEMP" in run, "instala só dentro da pasta temporária do runner"


def test_release_a_ordem_instalar_smoke_desinstalar_sha_e_so_depois_a_release():
    _, wf = ler("release.yml")
    j = "montar-e-provar"
    i_instala = indice(wf, j, "Programa Instalado")
    i_smoke = indice(wf, j, "instalador/smoke/smoke.py")
    i_desinstala = indice(wf, j, "Start-Process -FilePath $unins")
    i_sha = indice(wf, j, "SHA256SUMS.txt")
    i_compila = indice(wf, j, "montar.py --baixar")
    assert -1 not in (i_instala, i_smoke, i_desinstala, i_sha, i_compila)
    assert i_compila < i_sha < i_instala < i_smoke < i_desinstala
    smoke = wf["jobs"][j]["steps"][i_smoke]["run"]
    assert r"venv\Scripts\python.exe" in smoke and "--raiz" in smoke and "--dados" in smoke and "LASTEXITCODE" in smoke
    des = wf["jobs"][j]["steps"][i_desinstala]["run"]
    assert "/VERYSILENT" in des and "ExitCode -ne 0" in des
    for pasta in ("'app'", "'venv'", "'runtime'"):
        assert pasta in des, f"a desinstalação tem de conferir que {pasta} sumiu"
    assert ".bisturi-dados" in des and "mantê-la" in des, "e que a pasta de dados ficou"
    # o upload do que vai à Release vem DEPOIS de toda a prova (o passo falhou = nada é entregue)
    nomes = [p.get("uses", "") or p.get("run", "") for p in wf["jobs"][j]["steps"]]
    i_upload = max(n for n, u in enumerate(nomes) if "upload-artifact" in u)
    assert i_upload > i_desinstala
    assert wf["jobs"][j]["steps"][i_upload]["with"]["name"] == "release"
    assert wf["jobs"]["publicar-rascunho"]["needs"] == "montar-e-provar"


def test_release_e_sempre_rascunho_e_leva_exe_sha_e_notas():
    _, wf = ler("release.yml")
    publicar = wf["jobs"]["publicar-rascunho"]
    criar = [p["run"] for p in publicar["steps"] if "gh release create" in p.get("run", "")]
    assert len(criar) == 1
    run = re.sub(r"\\\r?\n\s*", " ", criar[0])         # junta as linhas continuadas com barra
    assert "--draft" in run, "a Release nasce como rascunho: o dono confere e publica à mão"
    for proibido in ("--draft=false", "--latest", "--prerelease=false"):
        assert proibido not in run
    assert "--verify-tag" in run and "--notes-file dist/notas.md" in run
    assert "SHA256SUMS.txt" in run and ".exe" in run
    conferir = [p["run"] for p in publicar["steps"] if "sha256sum -c" in p.get("run", "")]
    assert conferir, "confere o hash do que chegou do outro job antes de criar a Release"
    ordem = [p.get("name") for p in publicar["steps"]]
    assert ordem.index("Conferir o SHA-256 do que chegou") < ordem.index("Criar a Release como rascunho (draft)")
    # token da Release só no passo que cria (não no job inteiro)
    assert "env" not in publicar
    (passo,) = [p for p in publicar["steps"] if "gh release create" in p.get("run", "")]
    assert passo["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"


def test_release_calcula_o_sha256sums_no_formato_do_sha256sum():
    _, wf = ler("release.yml")
    j = "montar-e-provar"
    run = wf["jobs"][j]["steps"][indice(wf, j, "SHA256SUMS.txt")]["run"]
    assert 'Get-FileHash -Algorithm SHA256' in run and '"$hash  $nome`n"' in run
    assert "UTF8Encoding]::new($false)" in run, "UTF-8 sem BOM e fim de linha LF, para o `sha256sum -c` aceitar"


# ══ regras para os dois ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("nome", ["tests.yml", "release.yml"])
def test_toda_acao_esta_fixada_por_sha_de_commit_com_a_tag_no_comentario(nome):
    texto, wf = ler(nome)
    assert problemas_de_acoes(texto) == []
    assert any(USO.match(linha) for linha in texto.splitlines()), "o arquivo devia usar ao menos uma ação"
    # o que o YAML enxerga bate com o que o varredor de texto enxergou (nenhum `uses:` escapou)
    do_yaml = [p["uses"] for _, p in passos(wf) if "uses" in p]
    do_texto = [USO.match(linha).group(1) for linha in texto.splitlines() if USO.match(linha)]       # type: ignore[union-attr]
    assert do_yaml == do_texto
    for job in wf["jobs"].values():
        assert "uses" not in job, "workflow reutilizável de job não é usado aqui"


@pytest.mark.parametrize("nome", ["tests.yml", "release.yml"])
def test_nada_do_evento_vai_direto_para_o_run(nome):
    _, wf = ler(nome)
    assert problemas_de_run(wf) == []


@pytest.mark.parametrize("nome", ["tests.yml", "release.yml"])
def test_shell_explicito(nome):
    _, wf = ler(nome)
    assert problemas_de_shell(wf) == []


@pytest.mark.parametrize("nome", ["tests.yml", "release.yml"])
def test_nenhum_segredo_escrito(nome):
    texto, _ = ler(nome)
    assert problemas_de_segredos(texto) == []


@pytest.mark.parametrize("nome", ["tests.yml", "release.yml"])
def test_checkout_nao_guarda_a_credencial(nome):
    _, wf = ler(nome)
    for _, p in passos(wf):
        if p.get("uses", "").startswith("actions/checkout@"):
            assert p["with"]["persist-credentials"] is False


@pytest.mark.parametrize("nome", ["tests.yml", "release.yml"])
def test_arquivos_sao_utf8_sem_bom(nome):
    """Sem BOM (o GitHub não o aceita no começo do YAML); CRLF não importa: o git do Windows pode trocar o fim de linha ao clonar."""
    bruto = (PASTA_CI / nome).read_bytes()
    assert not bruto.startswith(b"\xef\xbb\xbf")
    bruto.decode("utf-8")


@pytest.mark.parametrize("nome", ["tests.yml", "release.yml"])
def test_so_o_que_o_exportador_leva_o_nome_fixo(nome):
    """O contrato com o exportador (P8): os dois ficam em instalador/ci/ e têm estes nomes."""
    assert (PASTA_CI / nome).is_file()


@pytest.mark.skipif(not (RAIZ / "tools" / "exportar_publico.py").is_file(), reason="o exportador é só da oficina: esta cópia é a pública")
def test_a_oficina_continua_manual_e_sem_o_release():
    """Os arquivos públicos NÃO estão em `.github/workflows/` da oficina (lá o teste F8 reprova push/pull_request)."""
    pasta = RAIZ / ".github" / "workflows"
    if pasta.is_dir():
        assert not (pasta / "release.yml").exists()


def test_o_requirements_da_montagem_usa_os_mesmos_hashes_do_lock():
    def bloco(texto: str, pacote: str) -> tuple[str, set[str]]:
        m = re.search(rf"^({re.escape(pacote)}==\S+) \\\n((?:\s+--hash=sha256:[0-9a-f]{{64}}(?: \\)?\n?)+)", texto, re.M)
        assert m, f"{pacote} não achado"
        return m.group(1), set(re.findall(r"sha256:([0-9a-f]{64})", m.group(2)))

    montagem = (PASTA_CI / "requirements-montagem.txt").read_text(encoding="utf-8")
    lock = (RAIZ / "requirements-runtime.lock").read_text(encoding="utf-8")
    assert bloco(montagem, "numpy") == bloco(lock, "numpy")


# ══ os verificadores pegam o defeito (mutações em texto) ═════════════════════════════════════════════
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


@pytest.mark.parametrize("linha", [
    "      - uses: actions/checkout@v7",
    "      - uses: actions/checkout@main",
    f"      - uses: actions/checkout@{SHA[:39]}",
    f"      - uses: actions/checkout@{SHA.upper()}",
    f"      - uses: actions/checkout@{SHA}",                    # sem a tag no comentário
    "      - uses: ./.github/actions/x",
    "      - uses: docker://alpine:3",
])
def test_o_varredor_de_acoes_pega_o_defeito(linha):
    assert problemas_de_acoes(linha + "\n"), linha


def test_o_varredor_de_acoes_aceita_o_certo():
    assert problemas_de_acoes(f"      - uses: actions/checkout@{SHA} # v7.0.1\n") == []
    assert problemas_de_acoes(f"        uses: actions/cache/restore@{SHA} # v4.2.0\n") == []


@pytest.mark.parametrize("perm", ["write-all", "read-all", {"contents": "write"}, {"contents": "read", "id-token": "write"},
                                  {"contents": "read", "pull-requests": "write"}, {"contents": "admin"}])
def test_o_conferidor_de_permissoes_pega_o_defeito(perm):
    wf = {"permissions": perm, "jobs": {"a": {"steps": []}}}
    assert problemas_de_permissoes(wf, escrita_permitida_em=set())


def test_o_conferidor_de_permissoes_pega_workflow_sem_permissions_e_escrita_no_job_errado():
    assert problemas_de_permissoes({"jobs": {"a": {"steps": []}}}, set())
    wf = {"permissions": {"contents": "read"}, "jobs": {"a": {"permissions": {"contents": "write"}, "steps": []}}}
    assert problemas_de_permissoes(wf, set())
    assert problemas_de_permissoes(wf, {"job a"}) == []


@pytest.mark.parametrize("script", [
    'echo "${{ github.event.pull_request.title }}"',
    'git checkout ${{ github.head_ref }}',
    "iex (iwr https://exemplo.org/x.ps1)",
    "curl -s https://exemplo.org/i.sh | bash",
])
def test_o_conferidor_de_run_pega_o_defeito(script):
    wf = {"jobs": {"a": {"runs-on": "windows-latest", "steps": [{"run": script}]}}}
    assert problemas_de_run(wf)


def test_o_conferidor_de_shell_pega_run_sem_shell_e_cmd_no_windows():
    sem = {"jobs": {"a": {"runs-on": "windows-latest", "steps": [{"name": "x", "run": "echo"}]}}}
    assert problemas_de_shell(sem)
    cmd = {"jobs": {"a": {"runs-on": "windows-latest", "steps": [{"name": "x", "shell": "cmd", "run": "echo"}]}}}
    assert problemas_de_shell(cmd)
    ok = {"defaults": {"run": {"shell": "pwsh"}}, "jobs": {"a": {"runs-on": "windows-latest", "steps": [{"name": "x", "run": "echo"}]}}}
    assert problemas_de_shell(ok) == []


@pytest.mark.parametrize("texto", [
    "env:\n  TOKEN: ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2",
    "env:\n  X: ${{ secrets.MINHA_CHAVE }}",
    "password: " + "hunter2hunter2hunter2",
    "-----BEGIN " + "RSA PRIVATE KEY-----",
    "url: https://usuario:" + "senha123" + "@" + "host.exemplo/x",
])
def test_o_varredor_de_segredos_pega_o_defeito(texto):
    assert problemas_de_segredos(texto)


def test_o_varredor_de_segredos_aceita_so_o_github_token():
    assert problemas_de_segredos("GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n") == []
