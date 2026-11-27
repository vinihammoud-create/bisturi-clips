"""M-008 (revisão de 26/11/2026): UMA resposta para "o que está rodando?" e UM jeito de desligar tudo.

Antes cada ponto olhava só uma parte: o vigia da janela fechada e o botão "Encerrar o programa" só a fila de análise; o "Apagar kit" a fila e as
30 receitas mais recentes; o "Gostei/Descartar" só a fila. Aqui ficam juntos os gerenciadores: a fila de análise, as receitas (Trabalhos),
a gravação pela demo, o download do VOD da Twitch e (P4.2b) o download do modelo de voz.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from cs2clip import modelo_voz
from cs2clip.tools import encerrar_arvore

PRAZO_ENCERRAR_S = 20.0    # fim do servidor: quanto o gravador tem para fechar o jogo com calma antes de a árvore de processos dele cair


def derrubar_arvore(proc: Any) -> None:
    """O processo e TODOS os descendentes caem (`taskkill /T`, só pelo PID do Popen que o app criou); sem taskkill, o `terminate()`."""
    if proc is None:
        return
    if encerrar_arvore(proc):
        return
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001  (o processo já saiu)
        pass


def _sem_derrubar(acao: Callable[[], Any]) -> None:
    """Um gerenciador que falha ao parar não pode impedir os outros de pararem."""
    try:
        acao()
    except Exception as exc:  # noqa: BLE001
        print(f"[aviso] não consegui parar um trabalho ao encerrar: {type(exc).__name__}: {exc}")


class Coordenador:
    def __init__(self, fila: Any, receitas: Any, gravacao: Any, vods: Any, modelo: Any = None):
        """`modelo` (P4.2b) = o download do modelo de voz pedido pela tela (`baixando`, `parar`, `esperar_fim`, como o de VOD)."""
        self.fila, self.receitas, self.gravacao, self.vods, self.modelo = fila, receitas, gravacao, vods, modelo

    def baixando_modelo(self) -> bool:
        """P4.2b: o download do modelo de voz da tela, ou o trabalho de fundo do `modelo_voz` (depois de cancelar ele ainda pode levar alguns
        segundos para soltar a conexão e o `.incomplete`)."""
        return bool((self.modelo is not None and self.modelo.baixando()) or modelo_voz.em_andamento())

    def rodando(self) -> bool:
        """Algo de FATO rodando: análise da fila (inclusive a iniciada fora da interface), receita rodando, gravação pela demo, download de VOD
        ou do modelo de voz. Receita só "esperando você" (trocar a conta do Steam, baixar no YouTube Studio) não conta."""
        return bool(self.fila.esta_processando() or self.receitas.rodando_de_fato()
                    or self.gravacao.ocupado() or self.vods.baixando() or self.baixando_modelo())

    def pesado_rodando(self) -> bool:
        """P4.2b: análise da fila, receita rodando ou gravação pela demo (o jogo aberto). Baixar o modelo de voz espera eles: a análise
        carregaria o mesmo modelo no meio do download e a gravação disputa a internet e o disco com o jogo."""
        return bool(self.fila.esta_processando() or self.receitas.rodando_de_fato() or self.gravacao.ocupado())

    def segura_o_programa(self) -> bool:
        """R-001 (decisão do dono, 26/11/2026): com algo rodando, ou na fila para rodar, fechar a janela NÃO encerra o programa; ele sai quando
        acabar. Receita só "esperando você" não segura: ela já está salva e volta como "interrompida" (Retomar)."""
        return self.rodando() or bool(self.fila.tem_pendentes())

    def mexe_nos_kits(self) -> bool:
        """G-007/R-011 (ajuste da revisão final de 26/11): algo está GRAVANDO dentro de `saida/` agora? Só isso impede "Apagar kit" e
        "Gostei/Descartar": a análise da fila, a etapa de edição de uma receita (qualquer uma, não só as 30 da tela) e a gravação pela demo
        pedida com edição. Download de VOD, gravação sem edição e receita que só espera você não mexem em kit nenhum e não bloqueiam nada."""
        return bool(self.fila.esta_processando() or self.receitas.editando() or self.gravacao.editando())

    def cancelar_tudo(self) -> None:
        """Botão "Encerrar o programa": pede a todos para parar. As receitas vivas ficam "interrompida" (dá para Retomar)."""
        _sem_derrubar(self.receitas.interromper_todas)       # primeiro: assim nenhuma receita põe trabalho novo na fila ou no baixador
        _sem_derrubar(self.fila.cancelar_tudo)
        _sem_derrubar(self.gravacao.parar)
        _sem_derrubar(self.vods.parar)
        if self.modelo is not None:
            _sem_derrubar(self.modelo.parar)                 # P4.2b: o que já veio fica guardado (o próximo download continua dali)

    def encerrar(self, prazo_s: float = PRAZO_ENCERRAR_S) -> None:
        """Fim do servidor (janela fechada sem nada rodando, botão Encerrar, Ctrl+C): pede a todos para parar e espera. O que não sair no
        prazo tem a ÁRVORE de processos derrubada (o ffmpeg neto não sobra)."""
        self.cancelar_tudo()
        fim = time.monotonic() + prazo_s

        def resto() -> float:
            return max(0.0, fim - time.monotonic())
        _sem_derrubar(lambda: self.receitas.esperar_fim(resto()))
        _sem_derrubar(lambda: self.gravacao.esperar_fim(resto()))
        _sem_derrubar(lambda: self.vods.esperar_fim(resto()))
        if self.modelo is not None:
            _sem_derrubar(lambda: self.modelo.esperar_fim(resto()))
        _sem_derrubar(self.fila.encerrar)
