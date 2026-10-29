"""Reads a Word 97-2003 binary document (.doc): the main text as paragraphs, each with its style name, and table
rows as lists of cells. Only what the reprints need: the piece table (text), the paragraph properties in the FKPs
(style index, in-table and row-end flags) and the style sheet (names). Header, footer, footnote and text-box text are
in separate ranges and are not read.

    paras = read(raw)   # [{'style': 'hs', 'text': '1\\tShort title', 'table': None}, {'style': ..., 'table': [[...]]}]
"""
from __future__ import annotations

import bisect
import io
import re
import struct

import olefile

SPRM_IN_TABLE = 0x2416
SPRM_TTP = 0x2417
SPRM_ITAP = 0x6649
SPRM_INNER_TTP = 0x244C
SPRM_INNER_CELL = 0x244B


def _sprms(grpprl: bytes):
    i = 0
    while i + 2 <= len(grpprl):
        sprm = struct.unpack_from('<H', grpprl, i)[0]
        i += 2
        spra = sprm >> 13
        if spra in (0, 1):
            n = 1
        elif spra in (2, 4, 5):
            n = 2
        elif spra == 3:
            n = 4
        elif spra == 7:
            n = 3
        else:
            if sprm in (0xD606, 0xD608):   # a two-byte length that counts one byte of itself
                n = struct.unpack_from('<H', grpprl, i)[0] + 1 if i + 2 <= len(grpprl) else 0
            else:
                n = grpprl[i] + 1 if i < len(grpprl) else 0
        yield sprm, grpprl[i:i + n]
        i += n


def _styles(table: bytes, fc: int, lcb: int) -> list[str]:
    if not lcb:
        return []
    st = table[fc:fc + lcb]
    cb = struct.unpack_from('<H', st, 0)[0]
    cstd, cb_base = struct.unpack_from('<HH', st, 2)
    pos = 2 + cb
    names = []
    for _ in range(cstd):
        if pos + 2 > len(st):
            break
        cbstd = struct.unpack_from('<H', st, pos)[0]
        std = st[pos + 2:pos + 2 + cbstd]
        pos += 2 + cbstd
        if not cbstd or len(std) < cb_base + 2:
            names.append('')
            continue
        n = struct.unpack_from('<H', std, cb_base)[0]
        names.append(std[cb_base + 2:cb_base + 2 + 2 * n].decode('utf-16-le', 'replace'))
    return names


def _pieces(table: bytes, fc: int, lcb: int):
    clx = table[fc:fc + lcb]
    i = 0
    while i < len(clx) and clx[i] == 1:
        i += 3 + struct.unpack_from('<H', clx, i + 1)[0]
    if i >= len(clx) or clx[i] != 2:
        raise ValueError('no piece table')
    lcb_pcd = struct.unpack_from('<I', clx, i + 1)[0]
    plc = clx[i + 5:i + 5 + lcb_pcd]
    n = (len(plc) - 4) // 12
    cps = struct.unpack_from(f'<{n + 1}I', plc, 0)
    out = []
    for k in range(n):
        raw_fc = struct.unpack_from('<I', plc, 4 * (n + 1) + 8 * k + 2)[0]
        compressed = bool(raw_fc & 0x40000000)
        f = raw_fc & 0x3FFFFFFF
        out.append((cps[k], cps[k + 1], f // 2 if compressed else f, compressed))
    return out


def _papx_runs(word: bytes, table: bytes, fc: int, lcb: int):
    """(fc_start, fc_end, istd, in_table, ttp) for each paragraph run in the FKPs."""
    n = (lcb - 4) // 8
    pns = struct.unpack_from(f'<{n}I', table, fc + 4 * (n + 1))
    runs = []
    for pn in pns:
        page = word[(pn & 0x3FFFFF) * 512:(pn & 0x3FFFFF) * 512 + 512]
        crun = page[511]
        rgfc = struct.unpack_from(f'<{crun + 1}I', page, 0)
        for j in range(crun):
            bx = page[4 * (crun + 1) + 13 * j]
            if bx == 0:
                runs.append((rgfc[j], rgfc[j + 1], 0, False, False, 0))
                continue
            off = bx * 2
            cb = page[off]
            if cb == 0:
                cb2 = page[off + 1]
                data = page[off + 2:off + 2 + 2 * cb2]
            else:
                data = page[off + 1:off + 1 + 2 * cb - 1]
            istd = struct.unpack_from('<H', data, 0)[0] if len(data) >= 2 else 0
            in_table = ttp = False
            itap = 0
            for sprm, arg in _sprms(data[2:]):
                if sprm == SPRM_IN_TABLE and arg[:1] == b'\x01':
                    in_table = True
                elif sprm == SPRM_TTP and arg[:1] == b'\x01':
                    ttp = True
                elif sprm == SPRM_ITAP and len(arg) >= 4:
                    itap = struct.unpack_from('<i', arg)[0]
                elif sprm == SPRM_INNER_TTP and arg[:1] == b'\x01':
                    ttp = True
            runs.append((rgfc[j], rgfc[j + 1], istd, in_table or itap > 0, ttp, itap))
    runs.sort()
    return runs


def raw_text(raw: bytes) -> tuple[str, list]:
    """The main-document text (characters 0..ccpText) and the piece list."""
    ole = olefile.OleFileIO(io.BytesIO(raw))
    word = ole.openstream('WordDocument').read()
    return _text_of(ole, word)[:2]


def _text_of(ole, word):
    flags = struct.unpack_from('<H', word, 0x0A)[0]
    table = ole.openstream('1Table' if flags & 0x0200 else '0Table').read()
    pos = 32
    csw = struct.unpack_from('<H', word, pos)[0]
    pos += 2 + 2 * csw
    cslw = struct.unpack_from('<H', word, pos)[0]
    lw = struct.unpack_from(f'<{cslw}i', word, pos + 2)
    pos += 2 + 4 * cslw
    cb_fclcb = struct.unpack_from('<H', word, pos)[0]
    fclcb = struct.unpack_from(f'<{2 * cb_fclcb}I', word, pos + 2)
    ccp_text = lw[3]
    pieces = _pieces(table, fclcb[66], fclcb[67])
    chars = []
    for cp0, cp1, fc, comp in pieces:
        if cp0 >= ccp_text:
            break
        end = min(cp1, ccp_text)
        if comp:
            chars.append(word[fc:fc + (end - cp0)].decode('cp1252', 'replace'))
        else:
            chars.append(word[fc:fc + 2 * (end - cp0)].decode('utf-16-le', 'replace'))
    text = ''.join(chars)
    return text, pieces, table, fclcb


def _fc_of(pieces, starts, cp: int) -> int:
    k = bisect.bisect_right(starts, cp) - 1
    cp0, _cp1, fc, comp = pieces[k]
    return fc + (cp - cp0) * (1 if comp else 2)


_SPECIAL = {'\x1e': '-', '\x1f': '', '\x0b': '\n', '\xa0': ' ', '\x0c': '', '\x0e': '', '\x01': '', '\x08': '',
            '\x02': '', '\x05': '', '\x03': '', '\x04': ''}
_SPECIAL_RE = re.compile('[' + ''.join(_SPECIAL) + ']')


def _display(s: str) -> str:
    """Field codes go (keeping each field's result); special characters become what Word shows."""
    out = []
    depth = []   # per open field: True while in its code part
    for ch in s:
        if ch == '\x13':
            depth.append(True)
            continue
        if ch == '\x14':
            if depth:
                depth[-1] = False
            continue
        if ch == '\x15':
            if depth:
                depth.pop()
            continue
        if any(depth):
            continue
        out.append(ch)
    return _SPECIAL_RE.sub(lambda m: _SPECIAL[m.group(0)], ''.join(out))


def read(raw: bytes) -> list[dict]:
    ole = olefile.OleFileIO(io.BytesIO(raw))
    word = ole.openstream('WordDocument').read()
    text, pieces, table, fclcb = _text_of(ole, word)
    names = _styles(table, fclcb[2], fclcb[3])
    runs = _papx_runs(word, table, fclcb[26], fclcb[27])
    run_starts = [r[0] for r in runs]
    starts = [p[0] for p in pieces]

    def props(cp: int):
        fc = _fc_of(pieces, starts, cp)
        k = bisect.bisect_right(run_starts, fc) - 1
        if k >= 0 and runs[k][0] <= fc < runs[k][1]:
            return runs[k]
        return (0, 0, 0, False, False, 0)

    out: list[dict] = []
    row: list[str] = []
    cell: list[str] = []
    row_style = ''
    i0 = 0
    for i, ch in enumerate(text):
        if ch not in '\r\x07':
            continue
        seg = _display(text[i0:i])
        i0 = i + 1
        _f0, _f1, istd, in_table, ttp, _itap = props(i)
        style = names[istd] if istd < len(names) else ''
        if ttp:   # the row-end mark
            out.append({'style': row_style, 'text': None, 'table': row})
            row, cell = [], []
            continue
        if in_table:
            cell.append(seg)
            if not row and len(cell) == 1:
                row_style = style
            if ch == '\x07':
                row.append('\n'.join(cell))
                cell = []
            continue
        if row or cell:   # a table that ended without a row mark the reader saw
            out.append({'style': row_style, 'text': None, 'table': row + (['\n'.join(cell)] if cell else [])})
            row, cell = [], []
        out.append({'style': style, 'text': seg, 'table': None})
    if row or cell:
        out.append({'style': row_style, 'text': None, 'table': row + (['\n'.join(cell)] if cell else [])})
    return out


if __name__ == '__main__':
    import sys
    from collections import Counter
    data = open(sys.argv[1], 'rb').read()
    ps = read(data)
    if len(sys.argv) > 2 and sys.argv[2] == 'styles':
        print(Counter(p['style'] for p in ps).most_common())
    else:
        for p in ps:
            if p['table'] is not None:
                print(f'[{p["style"]}] TABLE ' + ' | '.join(c.replace('\n', ' / ') for c in p['table']))
            else:
                print(f'[{p["style"]}] {p["text"]!r}')
