"""An independent reader for the reprints' Word files, for the word-stream check: it shares no code with the build.

It reads the main-document characters through the piece table (OLE stream WordDocument + the table stream), drops
field instructions (keeping each field's shown result), and keeps only letters and digits, case-folded. No styles,
no paragraphs, no tables, no structure: if the build left out, invented or moved any letter, the check sees it.
RTF reprints (a few old Stamp Duty Act reprints) are read by rtf_text, a scanner of its own that shares no code with
rtfdoc.py.
"""
from __future__ import annotations

import io
import re
import struct

import olefile

_KEEP = re.compile(r'[^\W_]+')


def letters(s: str) -> str:
    return ''.join(_KEEP.findall(s.casefold()))


def main_text(raw: bytes) -> str:
    ole = olefile.OleFileIO(io.BytesIO(raw))
    wd = ole.openstream('WordDocument').read()
    tbl = ole.openstream('1Table' if struct.unpack_from('<H', wd, 10)[0] & 0x200 else '0Table').read()
    p = 32
    p += 2 + 2 * struct.unpack_from('<H', wd, p)[0]           # fibRgW
    nlw = struct.unpack_from('<H', wd, p)[0]
    ccp = struct.unpack_from('<i', wd, p + 2 + 12)[0]           # ccpText, the fourth long
    p += 2 + 4 * nlw
    fc_clx, lcb_clx = struct.unpack_from('<II', wd, p + 2 + 33 * 8)
    clx = tbl[fc_clx:fc_clx + lcb_clx]
    i = 0
    while clx[i] == 1:
        i += 3 + struct.unpack_from('<H', clx, i + 1)[0]
    size = struct.unpack_from('<I', clx, i + 1)[0]
    plc = clx[i + 5:i + 5 + size]
    n = (size - 4) // 12
    out = []
    for k in range(n):
        a, b = struct.unpack_from('<II', plc, 4 * k)
        if a >= ccp:
            break
        b = min(b, ccp)
        fc = struct.unpack_from('<I', plc, 4 * (n + 1) + 8 * k + 2)[0]
        if fc & 0x40000000:
            off = (fc & 0x3FFFFFFF) // 2
            out.append(wd[off:off + b - a].decode('cp1252', 'replace'))
        else:
            out.append(wd[fc:fc + 2 * (b - a)].decode('utf-16-le', 'replace'))
    text = ''.join(out)
    shown = []
    stack = []
    for ch in text:
        if ch == '\x13':
            stack.append(1)
        elif ch == '\x14':
            if stack:
                stack[-1] = 0
        elif ch == '\x15':
            if stack:
                stack.pop()
        elif not any(stack):
            shown.append(ch)
    return ''.join(shown)


_RTF_SKIP = ('fonttbl', 'colortbl', 'stylesheet', 'info', 'pict', 'object', 'header', 'footer', 'footnote',
             'listtable', 'listoverridetable', 'fldinst', 'themedata', 'datastore', 'latentstyles')


def rtf_text(raw: bytes) -> str:
    """The shown characters of an RTF file, read here with its own scanner (no code shared with rtfdoc.py): groups
    that open with \\* or a font / colour / style table, picture, header, footer or field instruction are skipped;
    control words are dropped, except \\'hh (code page 1252) and \\uN (a character, then \\ucN fallback bytes
    skipped); \\{ \\} \\\\ are literal. Paragraph and tab marks need not be kept: only letters and digits are compared."""
    s = raw.decode('latin-1')
    out = []
    depth = 0
    skip_from = None    # the depth at which a skipped group opened
    uc = 1
    pending_skip = 0
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == '{':
            depth += 1
            i += 1
            j = i
            if s.startswith('\\*', j):
                skip_from = skip_from or depth
            else:
                m = re.match(r'\\([a-z]+)', s[j:j + 40])
                if m and m.group(1) in _RTF_SKIP:
                    skip_from = skip_from or depth
            continue
        if c == '}':
            if skip_from == depth:
                skip_from = None
            depth -= 1
            i += 1
            continue
        if c == '\\':
            nxt = s[i + 1:i + 2]
            if nxt == "'":
                ch = bytes([int(s[i + 2:i + 4], 16)]).decode('cp1252', 'replace')
                i += 4
                if pending_skip:
                    pending_skip -= 1
                elif skip_from is None:
                    out.append(ch)
                continue
            if nxt.isalpha():
                m = re.match(r'\\([a-z]+)(-?\d+)? ?', s[i:i + 64])
                word, num = m.group(1), m.group(2)
                i += m.end()
                if word == 'uc':
                    uc = int(num or 0)
                elif word == 'u' and skip_from is None:
                    out.append(chr(int(num) % 65536))
                    pending_skip = uc
                elif word in ('par', 'tab', 'cell', 'line', 'row'):
                    out.append(' ')
                continue
            if nxt in '{}\\' and skip_from is None:
                out.append(nxt)
            i += 2
            continue
        if c in '\r\n':
            i += 1
            continue
        if pending_skip:
            pending_skip -= 1
        elif skip_from is None:
            out.append(c.encode('latin-1').decode('cp1252', 'replace'))
        i += 1
    return ''.join(out)


def tile(raw: bytes, files: list[str]) -> tuple[bool, str]:
    """Do the files' letters, end to end in some order, equal the reprint's letters from the start of the first file
    found (the title over the long title) to the ENDNOTES heading (or the oldest reprints' Notes / Table of
    Amendments)? Returns (ok, detail)."""
    src = letters(rtf_text(raw) if raw.lstrip()[:5] == b'{\\rtf' else main_text(raw))
    streams = [letters(f) for f in files]
    streams = [s for s in streams if s]
    if not streams:
        return False, 'no text'
    total = sum(len(s) for s in streams)
    # the ENDNOTES heading; in the oldest reprints 'Notes' (followed by '1. The ... Act comprises') or 'Table of
    # Amendments'
    ends = [m.start() for m in re.finditer('endnotes|tableofamendments|notes(?=1the)', src)][::-1] + [len(src)]
    detail = ''
    for end in ends:
        ok, detail = _tile_at(src, streams, end, total)
        if ok:
            return True, detail
    return False, detail


def _tile_at(src: str, streams: list[str], end: int, total: int) -> tuple[bool, str]:
    start = end - total
    if start < 0:
        return False, f'files have {total} letters, more than the reprint before its endnotes ({end})'
    left = sorted(range(len(streams)), key=lambda j: -len(streams[j]))
    pos = start
    while left:
        hit = next((j for j in left if src.startswith(streams[j], pos)), None)
        if hit is None:
            return False, f'no file continues the reprint at letter {pos - start}: {src[pos:pos + 60]!r}'
        pos += len(streams[hit])
        left.remove(hit)
    return pos == end, f'{total} letters'
