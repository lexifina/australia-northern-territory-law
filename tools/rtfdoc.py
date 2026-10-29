"""Reads the RTF reprints NT Legislation serves for a few old reprints (the Stamp Duty Act's of 1995-2010) in place of a
Word document: paragraphs with their direct formatting (there are no styles in these files), and table rows as lists
of cells. Only the RTF these files use: groups, \\par / \\pard / \\plain, \\b \\i \\fs \\li \\fi \\qc \\qr \\qj,
\\tab, \\line, \\'hh (code page 1252), \\uN, \\intbl / \\cell / \\row, and the destinations to skip (font and colour
tables, \\* groups, pictures, headers and footers).

    paras = read(raw)   # [{'style': '', 'text': '1\\tShort title', 'table': None, 'fmt': {...}}, {..., 'table': [...]}]

'fmt' holds: 'b' (every letter or digit of the paragraph is bold), 'i' (every one is italic), 'fs' (the largest font
size, in half points), 'li' and 'fi' (left and first-line indents, twips), 'align' ('l', 'c', 'r' or 'j').
"""
from __future__ import annotations

import re

SKIP = {'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict', 'header', 'footer', 'headerl', 'headerr', 'headerf',
        'footerl', 'footerr', 'footerf', 'footnote', 'object', 'listtable', 'listoverridetable', 'rsidtbl',
        'generator', 'xmlnstbl', 'themedata', 'colorschememapping', 'datastore', 'latentstyles', 'pgdsctbl',
        'fldinst'}
_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([\r\n]+)|([^\\{}\r\n]+)", re.S)


def read(raw: bytes) -> list[dict]:
    if not raw.lstrip().startswith(b'{\\rtf'):
        raise ValueError('not an RTF document')
    out: list[dict] = []
    stack: list[dict] = []
    st = {'skip': False, 'b': False, 'i': False, 'fs': 24, 'uc': 1}
    pp = {'li': 0, 'fi': 0, 'align': 'l', 'intbl': False}
    chars: list[tuple[str, bool, bool, int]] = []   # the paragraph's characters with bold, italic, size
    cells: list[str] = []
    cell_paras: list[str] = []
    skip_bytes = 0
    first = [False]   # the next control word opens a destination ({\*\foo} or {\fonttbl ...})

    def text_of(cs) -> str:
        return ''.join(c for c, *_ in cs)

    def end_para(kind: str):
        nonlocal chars
        if pp['intbl'] or kind in ('cell', 'row'):
            if kind == 'par':
                cell_paras.append(text_of(chars))
            elif kind == 'cell':
                cell_paras.append(text_of(chars))
                cells.append('\n'.join(cell_paras))
                cell_paras.clear()
            chars = []
            if kind == 'row':
                if cell_paras or chars:
                    cells.append('\n'.join(cell_paras))
                    cell_paras.clear()
                out.append({'style': '', 'text': None, 'table': list(cells),
                            'fmt': {'b': False, 'i': False, 'fs': 0, 'li': pp['li'], 'fi': pp['fi'], 'align': pp['align']}})
                cells.clear()
            return
        sig = [(b, i, fs) for c, b, i, fs in chars if c.isalnum()]
        out.append({'style': '', 'text': text_of(chars), 'table': None,
                    'fmt': {'b': bool(sig) and all(b for b, _, _ in sig), 'i': bool(sig) and all(i for _, i, _ in sig),
                            'fs': max((fs for _, _, fs in sig), default=0), 'li': pp['li'], 'fi': pp['fi'],
                            'align': pp['align']}})
        chars = []

    def put(s: str):
        if not st['skip']:
            for c in s:
                chars.append((c, st['b'], st['i'], st['fs']))

    for m in _TOKEN.finditer(raw):
        word, arg, hexd, sym, brace, nl, lit = m.groups()
        if skip_bytes and (hexd or lit or sym):
            if lit and len(lit) > skip_bytes:
                lit = lit[skip_bytes:]
                skip_bytes = 0
            else:
                skip_bytes -= 1 if not lit else len(lit)
                skip_bytes = max(skip_bytes, 0)
                continue
        if brace == b'{':
            stack.append(dict(st))
            first[0] = True
            continue
        if brace == b'}':
            st = stack.pop() if stack else st
            first[0] = False
            continue
        if nl:
            continue
        opening, first[0] = first[0], False
        if lit is not None:
            put(lit.decode('cp1252'))
            continue
        if hexd is not None:
            put(bytes([int(hexd, 16)]).decode('cp1252', 'replace'))
            continue
        if sym is not None:
            s = sym.decode('latin-1')
            if s == '*':
                st['skip'] = True
            elif s in '\\{}':
                put(s)
            elif s == '~':
                put('\u00a0')
            elif s == '_':
                put('\u2011')
            elif s == '-':
                pass
            continue
        w = word.decode()
        n = int(arg) if arg is not None else None
        if w in SKIP and opening:
            st['skip'] = True
            continue
        if st['skip']:
            continue
        if w == 'par':
            end_para('par')
        elif w == 'cell':
            end_para('cell')
        elif w == 'row':
            end_para('row')
        elif w == 'pard':
            pp = {'li': 0, 'fi': 0, 'align': 'l', 'intbl': False}
        elif w == 'plain':
            st.update(b=False, i=False, fs=24)
        elif w == 'b':
            st['b'] = n != 0
        elif w == 'i':
            st['i'] = n != 0
        elif w == 'fs':
            st['fs'] = n or 24
        elif w in ('li', 'fi'):
            pp[w] = n or 0
        elif w in ('ql', 'qc', 'qr', 'qj'):
            pp['align'] = w[1]
        elif w == 'intbl':
            pp['intbl'] = True
        elif w == 'tab':
            put('\t')
        elif w == 'line':
            put('\n')
        elif w == 'uc':
            st['uc'] = n or 0
        elif w == 'u':
            put(chr(n % 65536))
            skip_bytes = st['uc']
        elif w in ('emdash', 'endash', 'lquote', 'rquote', 'ldblquote', 'rdblquote', 'bullet'):
            put({'emdash': '\u2014', 'endash': '\u2013', 'lquote': '\u2018', 'rquote': '\u2019', 'ldblquote': '\u201c',
                 'rdblquote': '\u201d', 'bullet': '\u2022'}[w])
    if chars:
        end_para('par')
    return out


# ---------------------------------------------------------------- structure

_WS = re.compile(r'[\s ]+')
_TOC_SEC = re.compile(r'\s*(\d+[A-Z]*)\.?[ ]*\t\s*(\S.*?)(?:\t\s*\d+)?\s*$', re.S)   # '1\tShort title\t1', '\t 1.\tShort title'
_BODY_SEC = re.compile(r'\s*(\d+[A-Z]*)(?:\.?[ ]*\t|\.[ ]{2,})\s*(\S.*?)\s*$', re.S)   # also '8B.  Concession ...'
_HEAD = re.compile(r'(?i)(chapter|part|division|subdivision)\s*([0-9]+[A-Z]*|[IVXL]+)\b')
_SCHED = re.compile(r'(?i)(?:the\s+)?schedules?\b\s*([0-9]+[A-Z]*|[IVXL]+\b)?')
_ACT_LINE = re.compile(r'\((?:Act\s+)?No\.?\s*\d+\s*,?\s*(?:of\s+)?\d{4}\)\s*$')
_ACT_NO = re.compile(r'No\.?\s+\d+\s*,\s*\d{4}')
HEAD_STYLE = {'chapter': 'Chapter Heading', 'part': 'Part Heading', 'division': 'Division Heading',
              'subdivision': 'Subdivision Heading'}


def _clean(s: str) -> str:
    return _WS.sub(' ', s or '').strip()


def _key(s: str) -> str:
    return re.sub(r'[^0-9a-z]', '', s.casefold())


def _heading_like(p: dict) -> bool:
    """Bold, or (the oldest, typewriter reprints) a heading in capitals ('8.  [Repealed]' counts as one)."""
    t = re.sub(r'\[(?:Repealed|Omitted|Expired)\]', 'X', _clean(p['text']))
    return p['fmt']['b'] or (any(c.isalpha() for c in t) and not any(c.islower() for c in t))


def styled(raw: bytes) -> list[dict]:
    """The paragraphs of an RTF reprint with the Word style names the build reads, given from the reprint's own
    structure: the table of provisions lists every section (number and heading), Part / Division heading and schedule
    in order, and the body must show each of them, in the same order, as a heading paragraph (bold, or in capitals in
    the oldest reprints) -- or ValueError. The endnotes' LIST OF LEGISLATION (paragraphs here, a table in the Word
    files) and the oldest reprints' Notes table (tab-separated lines) are turned into table rows."""
    paras = read(raw)
    text = [_clean(p['text']) if p['table'] is None else '' for p in paras]
    low = [t.casefold() for t in text]
    toc_at = next((i for i, t in enumerate(low) if t == 'table of provisions'), None)
    if toc_at is None:
        raise ValueError('RTF: no table of provisions')
    cover2 = next((i for i in range(toc_at + 1, len(paras)) if low[i] == 'northern territory of australia'), None)
    if cover2 is None:
        raise ValueError('RTF: no inner cover after the table of provisions')
    # the table of provisions: (kind, number or id, heading key)
    toc: list[tuple[str, str, str]] = []
    for i in range(toc_at + 1, cover2):
        if paras[i]['table'] is not None or not text[i]:
            continue
        p = paras[i]
        p['style'] = 'TOC'
        t = text[i]
        if low[i] in ('endnotes', 'notes', 'table of amendments', 'section'):
            continue
        ms = _SCHED.match(t)
        if ms:
            toc.append(('sched', (ms.group(1) or '').upper(), ''))
            continue
        mh = _HEAD.match(t)
        if mh:
            toc.append(('head', f'{mh.group(1).lower()} {mh.group(2).upper()}', ''))
            continue
        m = _TOC_SEC.fullmatch(p['text'])
        if m:
            toc.append(('sec', m.group(1), _key(m.group(2))))
            continue
        if toc and toc[-1][0] == 'sec':   # a heading carried onto a second line
            k, n, h = toc[-1]
            toc[-1] = (k, n, h + _key(t))
    if not any(k == 'sec' for k, _, _ in toc):
        raise ValueError('RTF: no sections in the table of provisions')
    cover_titles = {low[i] for i in range(toc_at) if text[i] and low[i] != 'northern territory of australia'
                    and 'as in force' not in low[i] and not re.fullmatch(r'reps\d+', low[i])}
    title = next((i for i in range(cover2 + 1, len(paras)) if low[i] in cover_titles), None)
    if title is None:
        raise ValueError('RTF: no title after the inner cover')
    paras[title]['style'] = 'ShortTitle'
    lt = next((i for i in range(title + 1, len(paras)) if text[i] and not re.fullmatch(r'_+', text[i])
               and 'as in force at' not in low[i]), None)
    if lt is None:
        raise ValueError('RTF: no long title')
    paras[lt]['style'] = 'LongTitle'
    end = next((i for i in range(lt + 1, len(paras)) if low[i] in ('endnotes', 'notes')
                and (paras[i]['fmt']['align'] == 'c' or paras[i]['fmt']['b'] or paras[i]['fmt']['i'])), None)
    if end is None:
        raise ValueError('RTF: no ENDNOTES or Notes heading')
    paras[end]['style'] = 'EndNotes'
    at = 0
    in_sched = False
    for i in range(lt + 1, end):
        p = paras[i]
        if p['table'] is not None or not text[i]:
            continue
        nxt = toc[at] if at < len(toc) else None
        ms = _SCHED.match(text[i])
        if ms and nxt and nxt[0] == 'sched' and (ms.group(1) or '').upper() == nxt[1] and \
                (_heading_like(p) or p['fmt']['align'] == 'c'):
            p['style'] = 'Schedule Heading'
            in_sched = True
            at += 1
            continue
        if in_sched:
            continue
        mh = _HEAD.match(text[i])
        if mh and _heading_like(p):
            if not (nxt and nxt[0] == 'head' and nxt[1] == f'{mh.group(1).lower()} {mh.group(2).upper()}'):
                raise ValueError(f'RTF: heading {text[i][:60]!r} is not the next entry of the table of provisions '
                                 f'({nxt})')
            p['style'] = HEAD_STYLE[mh.group(1).lower()]
            at += 1
            continue
        m = _BODY_SEC.fullmatch(p['text'])
        if m and _heading_like(p):
            h = _key(m.group(2))
            if not (nxt and nxt[0] == 'sec' and nxt[1] == m.group(1) and (h == nxt[2] or
                                                                            (len(h) >= 12 and nxt[2].startswith(h)))):
                raise ValueError(f'RTF: section heading {text[i][:60]!r} is not the next entry of the table of '
                                 f'provisions ({nxt})')
            p['style'] = 'New Section Heading'
            at += 1
    if at != len(toc):
        raise ValueError(f'RTF: {len(toc) - at} entries of the table of provisions not found in the body, from {toc[at]}')
    # The RTF export breaks a few paragraphs in two where the Word file has one paragraph ('(i)<tab>because of
    # non-execution by an essential party,' / 'non-fulfilment of a condition ...'): a paragraph that starts with a small
    # letter, right after a labelled paragraph ('(a)', '(ii)', '(11B)') with the same indents that does not end in . ; or
    # :, continues it.
    for i in range(lt + 2, end):
        p, q = paras[i - 1], paras[i]
        if p['table'] is None and q['table'] is None and p['style'] == '' and q['style'] == '' and \
                re.match(r'\s*\([0-9A-Za-z]+\)\t', p['text'] or '') and re.match(r'[a-z]', q['text'] or '') and \
                not re.search(r'[.;:]\s*$', p['text']) and \
                (p['fmt']['li'], p['fmt']['fi']) == (q['fmt']['li'], q['fmt']['fi']):
            q['text'] = p['text'].rstrip() + ' ' + q['text']
            q['fmt'] = p['fmt']
            p['text'] = ''
            text[i - 1], text[i] = '', _clean(q['text'])
    # endnotes: the LIST OF LEGISLATION and the oldest Notes table as table rows
    mode = None
    for i in range(end, len(paras)):
        p = paras[i]
        if p['table'] is not None:
            continue
        t = text[i]
        if re.match(r'\d+\.?\s+LIST OF LEGISLATION', t):
            mode = 'legislation'
            continue
        if re.match(r'\d+\.?\s+[A-Z][A-Z ]+$', t) or low[i] == 'table of amendments':
            mode = None
            continue
        if mode == 'legislation':
            if _ACT_LINE.search(t):
                p['table'], p['text'] = [t], None
            else:
                mh = re.match(r'(?i)(assent date|commenced)\s*\t\s*(.*)$', p['text'], re.S)
                if mh:
                    p['table'], p['text'] = [mh.group(1), _clean(mh.group(2))], None
        elif low[i] == 'notes' or (mode is None and i == end):
            mode = 'notes'
        elif mode == 'notes' and '\t' in p['text']:
            cells = [_clean(c) for c in p['text'].split('\t')]
            cells = [c for c in cells if c]
            if len(cells) >= 3 and _ACT_NO.fullmatch(cells[1]):
                p['table'], p['text'] = cells, None
    return paras
