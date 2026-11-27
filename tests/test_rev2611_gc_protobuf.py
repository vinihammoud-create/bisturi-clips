"""Revisão de 26/11/2026, lote A1: G-017 (fixed64 e fixed32 cortados levantam ValueError, como o campo com tamanho)."""
from __future__ import annotations

import pytest

from cs2clip import gc_protobuf


@pytest.mark.parametrize("payload", [bytes([(1 << 3) | 1, 0xAA, 0xBB]),                  # fixed64 com 2 bytes
                                     bytes([(1 << 3) | 1]) + bytes(7),                    # fixed64 com 7 bytes
                                     bytes([(2 << 3) | 5, 1, 2, 3]),                      # fixed32 com 3 bytes
                                     bytes([(2 << 3) | 5])])                              # fixed32 sem nenhum byte
def test_g017_fixed_cortado_levanta_valueerror(payload):
    with pytest.raises(ValueError, match="cortado"):
        list(gc_protobuf._campos(payload))


def test_g017_fixed_completo_continua_lendo():
    payload = bytes([(1 << 3) | 1]) + bytes(range(8)) + bytes([(2 << 3) | 5]) + bytes(range(4)) + bytes([(3 << 3) | 0, 7])
    assert list(gc_protobuf._campos(payload)) == [(1, 1, bytes(range(8))), (2, 5, bytes(range(4))), (3, 0, 7)]
