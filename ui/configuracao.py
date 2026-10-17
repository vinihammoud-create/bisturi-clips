"""Leitura e edição pontual e validada do config.toml."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


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


CAMPOS: tuple[CampoConfig, ...] = (
    CampoConfig("cortes", "antes", "numero", "Segundos antes da jogada", "Quanto mostrar antes do momento.", 0, 15),
    CampoConfig("cortes", "depois", "numero", "Segundos depois da jogada", "Quanto mostrar depois do momento.", 0, 15),
    CampoConfig("cortes", "juntar_intervalo", "numero", "Unir abates próximos", "Abates mais próximos que isso viram uma jogada só (s).", 1, 30),
    CampoConfig("cortes", "juntar_no_round", "numero", "Juntar os abates do mesmo round", "Todos os abates do round viram um vídeo só (0 desliga).", 0, 300),
    CampoConfig("cortes", "pular_parado", "numero", "Cortar tempo parado maior que (s)", "Dentro da jogada do round, pausas maiores que isso são cortadas.", 0, 60),
    CampoConfig("cortes", "duracao_minima", "numero", "Duração mínima do corte", "Duração mínima de cada corte (s).", 3, 30),
    CampoConfig("cortes", "estender_reacao", "bool", "Continuar enquanto eu reajo", "Estende o corte se a reação continuar."),
    CampoConfig("cortes", "reacoes_sem_abate", "inteiro", "Momentos só de reação", "Quantos momentos sem abate por vídeo.", 0, 30),
    CampoConfig("cortes", "so_partidas", "bool", "Só partidas competitivas", "Ignora aquecimento e Deathmatch."),
    CampoConfig("cortes", "limiar_reacao", "numero", "Sensibilidade ao grito", "Maior = só gritos mais fortes.", 0.3, 0.95),
    CampoConfig("cortes", "nota_montagem", "numero", "Nota mínima para a montagem", "Só entram momentos com esta nota ou maior.", 0, 100),
    CampoConfig("cortes", "nota_short", "numero", "Nota mínima para virar short", "Só viram shorts momentos com esta nota ou maior.", 0, 100),
    CampoConfig("shorts", "gerar", "bool", "Gerar shorts verticais", "Cria vídeos verticais 9:16."),
    CampoConfig("shorts", "duracao_maxima", "numero", "Duração máxima do short (s)", "Jogada maior que isso vira short compacto.", 15, 180),
    CampoConfig("shorts", "quantidade", "inteiro", "Quantidade de shorts", "Limite de shorts por vídeo.", 0, 30),
    CampoConfig("shorts", "legendas", "bool", "Legendas nos shorts", "Ativa legendas nos vídeos verticais."),
    CampoConfig("shorts", "selo_jogada", "bool", "Título da jogada no short", "Mostra ACE, DOUBLE KILL e outros títulos."),
    CampoConfig("shorts", "zoom_no_abate", "bool", "Zoom a cada abate", "Aplica um zoom rápido a cada abate."),
    CampoConfig("shorts", "marca", "texto", "Seu @ no short", "Texto discreto no topo do short e nas hashtags (vazio = sem texto).",
                comprimento=40, proibidos="{}\\"),
    CampoConfig("montagem", "gerar", "bool", "Gerar montagem", "Cria um vídeo com os melhores momentos."),
    CampoConfig("montagem", "ordem", "opcao", "Ordem da montagem", "Escolha como ordenar os momentos.", opcoes=("cronologica", "melhores")),
    CampoConfig("transcricao", "modelo", "opcao", "Qualidade da transcrição", "Modelo menor costuma ser mais rápido.", opcoes=("large-v3-turbo", "medium", "small")),
    CampoConfig("transcricao", "idioma", "opcao", "Idioma da fala", "Idioma em que você fala na live. As emoções (HYPE, RAIVA...) só funcionam em português.",
                opcoes=("pt", "en", "es")),
    CampoConfig("chat", "usar", "bool", "Usar chat da Twitch", "Considera o chat da transmissão."),
    CampoConfig("qualidade", "encoder", "opcao", "Codificador de vídeo", "Auto escolhe uma opção disponível.", opcoes=("auto", "h264_nvenc", "hevc_nvenc", "libx264")),
    CampoConfig("qualidade", "cq", "inteiro", "Qualidade do vídeo (CQ)", "Menor = melhor qualidade e arquivo maior.", 15, 30),
    CampoConfig("qualidade", "renders_paralelos", "inteiro", "Cortes ao mesmo tempo", "Mais cortes simultâneos usam mais recursos.", 1, 4),
    CampoConfig("demo", "jogador", "texto", "Meu nome no jogo (para demos)", "Use o nome ou parte dele como aparece no jogo.", comprimento=60),
)
CAMPOS_POR_CHAVE = {(campo.secao, campo.chave): campo for campo in CAMPOS}


class ErroConfig(ValueError):
    pass


class ConfiguracaoProjeto:
    def __init__(self, raiz: Path, load_config_fn: Callable[[Path], Any] | None = None, config_cls: Callable[[], Any] | None = None):
        self.raiz = raiz.resolve()
        self.caminho = self.raiz / "config.toml"
        self.load_config_fn = load_config_fn
        self.config_cls = config_cls

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
            if any(ord(c) < 32 for c in valor):
                raise ErroConfig(f"{campo.rotulo}: remova caracteres de controle.")
            if any(c in campo.proibidos for c in valor):
                raise ErroConfig(f"{campo.rotulo}: não use os caracteres {' '.join(campo.proibidos)}")
            return valor
        if campo.tipo == "opcao":
            if not isinstance(valor, str) or valor not in campo.opcoes:
                raise ErroConfig(f"{campo.rotulo}: escolha uma das opções da lista.")
            return valor
        if campo.tipo in {"numero", "inteiro"}:
            if isinstance(valor, bool) or not isinstance(valor, (int, float)):
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

    def salvar(self, alteracoes: dict[str, Any]) -> dict[str, Any]:
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
        if not isinstance(nomes, list) or not nomes:
            raise ErroConfig("Selecione pelo menos uma opção.")
        campos: list[CampoConfig] = []
        for nome in nomes:
            if not isinstance(nome, str) or "." not in nome:
                raise ErroConfig("Há uma opção que não pode ser restaurada.")
            campo = CAMPOS_POR_CHAVE.get(tuple(nome.split(".", 1)))
            if campo is None:
                raise ErroConfig(f"A opção {nome} não pode ser alterada pela interface.")
            campos.append(campo)
        load_config, _ = self._resolver_core()
        texto_original, _ = self._config_toml()
        backup = self.caminho.with_name("config.toml.bak")
        if self.caminho.exists():
            shutil.copy2(self.caminho, backup)
        else:
            backup.write_text("", encoding="utf-8", newline="\n")
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
        return {"sucesso": True, "mensagem": "Padrão do programa restaurado. Vale na próxima execução."}

    def _gravar_atomico(self, texto: str) -> None:
        newline = "\r\n" if "\r\n" in texto else "\n"
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_nome = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=str(self.caminho.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as arquivo:
                arquivo.write(texto.replace("\r\n", "\n").replace("\n", newline))
                arquivo.flush()
                os.fsync(arquivo.fileno())
            os.replace(temp_nome, self.caminho)
        except Exception:
            try:
                os.unlink(temp_nome)
            except OSError:
                pass
            raise


_AUSENTE = object()
