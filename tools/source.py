"""Where the build finds the retrieved files: the list of Acts and reprints (index.json) and each reprint, stored
under a name taken from the sha256 of its address on NT Legislation. Reads only; it never goes to the network.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / '.cache'
SITE = 'https://legislation.nt.gov.au'


def cache_path(url: str) -> Path:
    return CACHE / hashlib.sha256(url.encode()).hexdigest()[:32]


def word_url(v: dict, current: bool) -> str:
    return f'{SITE}/api/sitecore/Act/Word{"" if current else "_History"}?id={v["word"]}'


def read(url: str) -> bytes:
    path = cache_path(url)
    if not path.exists():
        raise SystemExit(f'not among the retrieved files: {url}')
    return path.read_bytes()
