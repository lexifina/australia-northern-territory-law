"""Builds a git repository of the Northern Territory's Acts in force from NT Legislation (legislation.nt.gov.au), in
which each amending Act is one commit per day it took effect, dated by the reprints' "as in force at" days.

    python -X utf8 build.py --today 2026-10-28 [--repo ../../../australia-northern-territory-law] [--offline]
                            [--only SLUG,SLUG | --upto LETTER] [--verify-rebuild]

NT Legislation, managed by the Office of Parliamentary Counsel, keeps every reprint of each Act: its text "as in force
at" a day, with a start and end date in the Act's history listing, as a Word document. Each reprint's endnotes have a
LIST OF LEGISLATION (each Act, its assent and commencement) and a LIST OF AMENDMENTS (a row per provision naming the
Acts that inserted, amended, substituted or repealed it: "amd No. 17, 1996, s 6").

We read every reprint, compare consecutive reprints provision by provision, and credit each change to the Act that
is new in that provision's row, on the reprint's start day. A change two Acts could explain, or none, is never
credited to an Act: it gets its own commit by the publisher, labelled.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fetch  # noqa: E402
import rtfdoc  # noqa: E402
import worddoc  # noqa: E402

CACHE = HERE / '.cache'
SITE = fetch.SITE
COMMITTER = ('Lexifina', 'australia-northern-territory-law@lexifina.com')
PUBLISHER = ('NT Legislation (Office of Parliamentary Counsel, Northern Territory)', 'opc@legislation.nt.invalid')
DOMAIN = 'legislation.nt.invalid'
WORKERS = 4

MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December']
MON = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}
MON.update({m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)})
MON['sept'] = 9
DATE = re.compile(r'\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|'
                  r'December)\s+(\d{4})\b', re.I)


def iso(m) -> str:
    return f'{m.group(3)}-{MON[m.group(2).lower()]:02d}-{int(m.group(1)):02d}'


def dates_in(s: str) -> list[str]:
    return [iso(m) for m in DATE.finditer(s or '')]


SHORT_DATE = re.compile(r'\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b', re.I)


def short_dates_in(s: str) -> list[str]:
    """Dates in the oldest reprints' tables: '17 Dec 1982', '19 Sept 2006', '30 June 1993'."""
    out = []
    for m in SHORT_DATE.finditer(s or ''):
        mon = MON['sept' if m.group(2).lower() == 'sep' else m.group(2).lower()]
        out.append(f'{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}')
    return out


def long_day(day: str) -> str:
    d = dt.date.fromisoformat(day)
    return f'{d.day} {MONTHS[d.month - 1]} {d.year}'


_WS = re.compile(r'[\s      ]+')
_DROP = re.compile('[­​‌‍﻿]')


def clean(s: str) -> str:
    """Runs of white space (tabs, line breaks, no-break spaces) become one space; soft hyphens and zero-width
    characters go; a non-breaking hyphen is a hyphen."""
    return _WS.sub(' ', _DROP.sub('', s).replace('‑', '-')).strip()


# ---------------------------------------------------------------- units

def num_key(n: str, width: int = 3) -> str:
    m = re.match(r'(\d+)(.*)$', n)
    return f'{int(m.group(1)):0{width}d}{m.group(2)}' if m else n


def _n(s: str) -> tuple:
    m = re.match(r'(\d+)(.*)$', s)
    if not m:
        return (0, s)
    suffix = m.group(2)   # Australian numbering: s 6AA comes between s 6 and s 6A
    return (int(m.group(1)), '!' + suffix if suffix.startswith('AA') else suffix)


ROMAN = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100}


def _sched_n(s: str) -> tuple:
    if s and re.fullmatch(r'[IVXLC]+', s):
        v = 0
        for a, b in zip(s, s[1:] + ' '):
            v += -ROMAN[a] if b != ' ' and ROMAN[b] > ROMAN[a] else ROMAN[a]
        return (v, '')
    return _n(s or '0')


def unit_sort(key: str) -> tuple:
    base = re.sub(r'-dup\d+$', '', key)
    dup = int(key.rsplit('-dup', 1)[1]) if '-dup' in key else 0
    if base == 'intro':
        return (0, (0, ''), (0, ''), dup)
    if base.startswith('s-'):
        return (1, _n(base[2:]), (0, ''), dup)
    m = re.match(r'sch(?:-([^/]+))?(?:/(.+))?$', base)
    if m:
        return (2, _sched_n(m.group(1) or ''), _n(m.group(2)) if m.group(2) else (-1, ''), dup)
    return (9, (0, base), (0, ''), dup)


def unit_label(key: str) -> str:
    base = re.sub(r'-dup(\d+)$', r' (\1)', key)
    if base == 'intro':
        return 'long title'
    if base.startswith('s-'):
        return f's {base[2:].lstrip("0")}'
    m = re.match(r'sch(?:-([^/ ]+))?(?:/([^ ]+))?( \(\d+\))?$', base)
    if m:
        sid = m.group(1) or ''
        sch = ('Dictionary' if sid == 'dict' else f'App {sid[3:]}'.strip() if sid.startswith('app')
               else f'Sch {sid}' if sid else 'Sch')
        return (f'{sch} cl {m.group(2).lstrip("0")}' if m.group(2) else sch) + (m.group(3) or '')
    return key


def file_name(key: str) -> str:
    return f'{key}.md'


SECTION_STYLES = {'New Section Heading', 'Section Heading', 'Heading 1,Section Heading'}
HEAD_STYLES = {'Part Heading': 'part', 'Division Heading': 'division', 'Subdivision Heading': 'subdivision',
               'Chapter Heading': 'chapter', 'New Part Heading': 'part', 'New Division Heading': 'division',
               'New Subdivision Heading': 'subdivision'}


def para_line(p: dict) -> str:
    if p['table'] is not None:
        cells = [clean(c) for c in p['table']]
        return ' | '.join(c for c in cells if c)
    return clean(p['text'])


RULE = re.compile(r'_{3,}')
OLD_END = {'endnotes', 'notes', 'table of amendments'}


def _blank(p: dict) -> bool:
    return p['table'] is None and not RULE.sub('', clean(p['text']))


def old_body_start(paras: list[dict]) -> int:
    """Older reprints (before about 2008) have no LongTitle style. Their body follows a second cover: 'NORTHERN
    TERRITORY OF AUSTRALIA', a rule, 'As in force at <day>' (or 'This reprint shows the Act as in force at ...'), a
    rule, then the title and the long title. The body starts at the first line after the last such 'as in force at'
    line before the first section or Part heading (outside the table of contents)."""
    first = next((i for i, p in enumerate(paras) if p['table'] is None and
                  p['style'] in SECTION_STYLES and clean(p['text'])), None)
    if first is None:
        raise ValueError('no long title')
    cover = [i for i in range(first) if paras[i]['table'] is None and re.search(r'(?i)\bas in force at\b', clean(paras[i]['text']))]
    if not cover:
        raise ValueError('no long title')
    start = cover[-1] + 1
    while start < first and _blank(paras[start]):
        start += 1
    return start


def body_range(paras: list[dict]) -> tuple[int, int]:
    """The body: from the title above the long title (or the long title) to the ENDNOTES heading (in the oldest
    reprints, the 'Notes' or 'Table of Amendments' heading after the last provision). Rules at the end are left out."""
    lt = next((i for i, p in enumerate(paras) if p['style'] == 'LongTitle'), None)
    if lt is None:
        start = old_body_start(paras)
        end = next((i for i, p in enumerate(paras) if i > start and p['table'] is None and not p['style'].startswith('TOC')
                    and (p['style'] == 'EndNotes' or clean(p['text']).casefold() in OLD_END)), len(paras))
        while end > start and _blank(paras[end - 1]):
            end -= 1
        return start, end
    start = lt
    j = lt - 1
    while j >= 0 and paras[j]['table'] is None and not clean(paras[j]['text']):
        j -= 1
    if j >= 0 and paras[j]['style'] == 'ShortTitle':
        start = j
    # the heading styled EndNotes that reads ENDNOTES (the Motor Accidents (Compensation) Act's reprints of 2014-2018
    # style its last subsection EndNotes too)
    ends = [i for i, p in enumerate(paras) if i > lt and p['style'] == 'EndNotes']
    is_head = lambda i: paras[i]['table'] is None and clean(paras[i]['text']).casefold() == 'endnotes'
    end = next((i for i in ends if is_head(i)), None)
    if end is None:   # no EndNotes-styled ENDNOTES: the first ENDNOTES line after the first EndNotes-styled one
        end = ends[0] if ends else len(paras)
        end = next((i for i in range(end, len(paras)) if is_head(i) and not paras[i]['style'].startswith('TOC')), end)
    return start, end


ORDINAL = {'first': '1', 'second': '2', 'third': '3', 'fourth': '4', 'fifth': '5', 'sixth': '6', 'seventh': '7',
           'eighth': '8', 'ninth': '9', 'tenth': '10'}


def schedule_id(line: str) -> str | None:
    """'Schedule 2' -> '2', 'Schedule II' -> 'II', 'The First Schedule' -> '1', 'Schedule' -> '', 'Appendix' -> 'app',
    'Dictionary' -> 'dict'; None if the line is no schedule heading."""
    m = re.match(r'(?i)(?:the\s+)?schedules?\b\s*([0-9]+[A-Z]*|[IVXLC]+\b)?', line)
    if m:
        return m.group(1) or ''
    m = re.match(r'(?i)(?:the\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+schedule\b', line)
    if m:
        return ORDINAL[m.group(1).lower()]
    m = re.match(r'(?i)appendix\b\s*([0-9]+[A-Z]*)?', line)
    if m:
        return 'app' + (m.group(1) or '')
    if re.match(r'(?i)dictionary\b', line):
        return 'dict'
    return None


def is_rtf(raw: bytes) -> bool:
    return raw.lstrip()[:5] == b'{\\rtf'


def read_version(raw: bytes) -> dict:
    # a few old reprints are served as RTF without styles: rtfdoc gives their paragraphs the styles the Word files
    # have, from the reprint's own table of provisions and headings (or ValueError)
    paras = rtfdoc.styled(raw) if is_rtf(raw) else worddoc.read(raw)
    as_at = None
    for p in paras[:40]:
        if p['table'] is None:
            m = re.match(r'(?i)as in force at\s+(.*)$', clean(p['text']))
            if m and dates_in(m.group(1)):
                as_at = dates_in(m.group(1))[0]
                break
    start, end = body_range(paras)
    old_format = not any(p['style'] == 'LongTitle' for p in paras)
    units: dict[str, dict] = {}
    order: list[str] = []
    pending: list[str] = []
    ctx = {'cur': None, 'sched': None}

    def unit(key: str) -> str:
        base, n = key, 2
        while key in units:
            key = f'{base}-dup{n}'
            n += 1
        units[key] = {'key': key, 'lines': []}
        order.append(key)
        return key

    # Some old reprints number their sections with Word list numbering, which is not in the text: the heading reads
    # 'Short title'. The number is then taken from the table of contents entry with the same heading, in order.
    toc = []
    if old_format:
        for p in paras[:start]:
            if p['table'] is None and p['style'].startswith('TOC'):
                mt = re.match(r'(\d+[A-Z]*)\.?\s+(.*)$', clean(p['text']))
                if mt:
                    toc.append((mt.group(1), style_key(mt.group(2))))
    toc_at = 0
    ctx['cur'] = unit('intro')
    body = paras[start:end]
    if end < len(paras) and paras[end]['table'] is None:
        # the Motor Accidents (Compensation) Act's reprints of 2014-2018 have no paragraph mark between the last
        # subsection and the ENDNOTES heading: one paragraph '...before commencement.ENDNOTES'
        mlast = re.fullmatch(r'(?s)(.*\S)\s*ENDNOTES\s*', paras[end]['text'] or '')
        if mlast and clean(mlast.group(1)):
            body = body + [{**paras[end], 'style': 'Subsection', 'text': mlast.group(1)}]
    for p in body:
        line = para_line(p)
        if not line or p['style'] == 'BlankHeader' and not line.strip():
            continue
        st = p['style']
        if p['table'] is None and st in SECTION_STYLES:
            m = re.match(r'[-–]?\s*(\d+[A-Z]*)\b\.?\s*(.*)$', line)   # '-1 Definitions' (Criminal Code 2002)
            n = m.group(1) if m else None
            if n is None and toc:
                hit = next((j for j in range(toc_at, len(toc)) if toc[j][1] == style_key(line)), None)
                if hit is not None:
                    n, toc_at = toc[hit][0], hit + 1
            if n is not None:
                key = (f'sch-{ctx["sched"]}/' + num_key(n, 2)) if ctx['sched'] is not None and ctx['sched'] != '' \
                    else ('sch/' + num_key(n, 2) if ctx['sched'] == '' else f's-{num_key(n)}')
                k = unit(key)
                units[k]['lines'] = pending + ['# ' + line]
                pending = []
                ctx['cur'] = k
                continue
        if p['table'] is None and st == 'Schedule Heading':
            sid = schedule_id(line)
            if sid is None:   # a paragraph styled as a schedule heading ('(2)', '(b)')
                if ctx['cur'] is None:
                    pending.append(line)
                else:
                    units[ctx['cur']]['lines'].append(line)
                continue
            ctx['sched'] = sid
            k = unit(f'sch-{ctx["sched"]}' if ctx['sched'] else 'sch')
            units[k]['lines'] = pending + ['## ' + line]
            pending = []
            ctx['cur'] = k
            continue
        if p['table'] is None and st in HEAD_STYLES:
            if old_format and ctx['cur'] == 'intro' and not re.match(r'(?i)(chapter|part|division|subdivision)\b', line):
                units['intro']['lines'].append(line)   # the long title, styled as a Part heading in some old reprints
                continue
            pending.append('## ' + line)
            ctx['cur'] = None
            continue
        if ctx['cur'] is None:
            pending.append(line)
        else:
            units[ctx['cur']]['lines'].append(line)
    if pending:
        units[order[-1]]['lines'].extend(pending)
    for k in order:
        units[k] = {'text': '\n'.join(units[k]['lines']) + '\n'}
    notes = read_endnotes(paras[end:])
    if notes['principal'] is None and is_rtf(raw):
        raise ValueError('RTF: no principal Act in the endnotes')   # it could not be told from a predecessor's reprint
    return {'as_at': as_at, 'units': units, 'order': order, **notes}


# ---------------------------------------------------------------- endnotes

ACT_REF = re.compile(r'\bNo\.?\s+(\d+)\s*,\s*(\d{4})\b')
PRINCIPAL = re.compile(r'\((?:Act\s+)?No\.?\s*(\d+)\s*,?\s*(?:of\s+)?(\d{4})\)\s*$')
HEAD_WORD = {'pt': 'part', 'div': 'division', 'sdiv': 'subdivision', 'subdiv': 'subdivision', 'ch': 'chapter'}


def acts_in(s: str) -> set:
    return {(int(n), int(y)) for n, y in ACT_REF.findall(s)}


def read_endnotes(paras: list[dict]) -> dict:
    history: dict[str, set] = {}
    ranges: list[tuple[str, str, set]] = []
    heads: dict[str, set] = {}
    table: dict[tuple, dict] = {}
    principal = None
    mode = None
    cur_act = None
    label = None
    sched = None
    part = ''
    carry = ''
    for p in paras:
        if p['table'] is not None:
            cells = [clean(c) for c in p['table']]
            cells = [c for c in cells if c] or ['']
            if mode == 'old-notes' and len(cells) >= 3 and ACT_REF.fullmatch(cells[1]):
                # the oldest reprints: 'Act | Number and year | Date of assent | Date of commencement'
                x = acts_in(cells[1]).pop()
                if principal is None:
                    principal = x
                ds = short_dates_in(cells[2])
                table.setdefault(x, {'name': cells[0], 'assent': ds[0] if ds else None,
                                     'commenced': ' '.join(cells[3:])})
                continue
            if mode != 'legislation':
                continue
            if len(cells) == 1:
                m = PRINCIPAL.search(cells[0])
                if m:
                    cur_act = (int(m.group(1)), int(m.group(2)))
                    name = clean(cells[0][:m.start()])
                    if principal is None:
                        principal = cur_act
                    table.setdefault(cur_act, {'name': name, 'assent': None, 'commenced': ''})
                continue
            if cur_act is not None and len(cells) >= 2:
                head = cells[0].lower()
                if head.startswith('assent'):
                    ds = dates_in(cells[1])
                    table[cur_act]['assent'] = ds[0] if ds else None
                elif head.startswith('commenced'):
                    table[cur_act]['commenced'] = ' '.join(cells[1:])
            continue
        raw = p['text'] or ''
        text = clean(raw)
        if not text:
            continue
        if re.match(r'^\d+\.?\s+LIST OF LEGISLATION', text):
            mode = 'legislation'
            continue
        if re.match(r'^\d+\.?\s+(?:LIST|TABLE) OF AMENDMENTS', text):
            mode = 'amendments'
            continue
        if re.match(r'^\d+\.?\s+[A-Z][A-Z ]+$', text) and mode:
            mode = 'other'
            continue
        if mode in (None, 'old-notes', 'old-amendments') and text.casefold() == 'notes':
            mode = 'old-notes'
            continue
        if mode in (None, 'old-notes', 'old-amendments') and text.casefold() == 'table of amendments':
            mode = 'old-amendments'
            continue
        if mode == 'old-amendments':
            # '5.<tab>Amended by No. 75, 1991, s. 3; ...', 'Long title<tab>Amended by ...', 'Schedule 2 Inserted by ...'
            m = re.match(r'(?i)(.*?)[\s.]*\b(amended|inserted|repealed|substituted|added|omitted|expired|renumbered)\b',
                         text)
            if not m:
                continue
            lab = m.group(1).strip()
            found = acts_in(text[m.start(2):])
            if re.fullmatch(r'(?i)(long title|title|preamble)', lab):
                history.setdefault('intro', set()).update(found)
            elif re.fullmatch(r'\d+[A-Z]*', lab):
                history.setdefault(f's-{num_key(lab)}', set()).update(found)
            else:
                ms = re.fullmatch(r'(?i)schedules?\s*([0-9]+[A-Z]*|[IVXLC]+)?', lab)
                if ms:
                    history.setdefault(f'sch-{ms.group(1)}' if ms.group(1) else 'sch', set()).update(found)
            continue
        if mode != 'amendments':
            continue
        raw = raw.replace('\n', ' ')
        if carry:
            raw = carry + ' ' + raw.lstrip()
            carry = ''
        if '\t' not in raw.strip(' ') and (re.search(r'(?:[–—-]|,|\band)\s*$', raw)
                                           or re.fullmatch(r'(?i)\s*(div|sdiv|subdiv)\s+[0-9A-Z]+\s*', raw)):
            carry = raw.strip()   # a label the reprint breaks over two lines: 'ss 54A –' / '54AD<tab>ins ...'
            continue
        lab, _, entries = raw.partition('\t')
        lab = clean(lab)
        entries = clean(entries)
        if lab:
            ms = re.fullmatch(r'(?i)schedules?\s*([0-9]+[A-Z]*|[IVXLC]+)?', lab)
            if ms and not entries:
                sched = ms.group(1) or ''
                part = ''
                label = None
                continue
            mp = re.fullmatch(r'(?i)(pt|ch)\s+([0-9A-Z]+)', lab)
            if mp and not entries:
                part = f'{HEAD_WORD[mp.group(1).lower()]} {mp.group(2)}'
                label = None
                continue
            label = lab
        if label is None:
            continue
        found = acts_in(entries)
        if not found and not entries:
            continue
        # a heading row: 'pt 3 hdg', 'div 5 hdg', 'pt IIAA div 2 subdiv 1 hdg'
        if re.search(r'(?i)\bhdg\b', label):
            words = re.findall(r'(?i)\b(pt|ch|div|sdiv|subdiv)\s+([0-9A-Z]+)', label)
            if words:
                path = []
                if words[0][0].lower() not in ('pt', 'ch') and part:
                    path.append(part)
                path += [f'{HEAD_WORD[w.lower()]} {n}' for w, n in words]
                hk = (f'sch-{sched}:' if sched is not None else '') + '/'.join(path)
                heads.setdefault(hk, set()).update(found)
            continue
        if re.fullmatch(r'(?i)(lt|long title|preamble|title)', label):
            history.setdefault('intro', set()).update(found)
            continue
        msch = re.fullmatch(r'(?i)sch(?:s|edules?)?\s*([0-9]+[A-Z]*|[IVXLC]+)?(?:\s*[–—-]\s*([0-9]+[A-Z]*|[IVXLC]+))?', label)
        if msch:
            a, b = msch.group(1) or '', msch.group(2)
            if b:
                ranges.append((f'sch-{a}', f'sch-{b}', found))
            else:
                history.setdefault(f'sch-{a}' if a else 'sch', set()).update(found)
            continue
        mu = re.match(r'(?i)(ss?|cll?|rr?|items?)\s+(.*)$', label)
        if not mu:
            continue
        kind = mu.group(1).lower()

        def mk(n: str) -> str:
            if sched is not None and (kind.startswith('c') or kind.startswith('s') or kind.startswith('i')):
                return (f'sch-{sched}/' if sched else 'sch/') + num_key(n, 2)
            return f's-{num_key(n)}'
        for a, b in re.findall(r'(\d+[A-Z]*)(?:\s*[–—-]\s*(\d+[A-Z]*))?', mu.group(2)):
            if b:
                ranges.append((mk(a), mk(b), found))
            else:
                history.setdefault(mk(a), set()).update(found)
    return {'history': history, 'ranges': ranges, 'heads': heads, 'table': table, 'principal': principal}


# ---------------------------------------------------------------- one Act

def unit_history(p: dict, key: str) -> set:
    base = re.sub(r'-dup\d+$', '', key)
    out = set(p['history'].get(base, set()))
    if base.startswith('sch') and '/' in base:   # a whole schedule's row covers its clauses
        out |= p['history'].get(base.split('/')[0], set())
    group = lambda k: k.split('/')[0] if '/' in k else k.split('-')[0]
    for a, b, found in p['ranges']:
        if a.startswith('sch-') and '/' not in a and base.startswith('sch'):
            if unit_sort(a)[:2] <= unit_sort(base.split('/')[0])[:2] <= unit_sort(b)[:2]:
                out |= found
        elif group(a) == group(base) and unit_sort(a) <= unit_sort(base) <= unit_sort(b):
            out |= found
    return out


def head_keys(text: str, sched) -> list[str]:
    out = []
    part = None
    for line in text.split('\n'):
        m = re.match(r'## (Chapter|Part|Division|Subdivision)\s+([0-9A-Z]+)\b', line)
        if not m:
            continue
        w = m.group(1).lower()
        pre = f'sch-{sched}:' if sched is not None else ''
        if w in ('chapter', 'part'):
            part = f'{w} {m.group(2)}'
            out.append(pre + part)
        else:
            out.append(pre + (f'{part}/' if part else '') + f'{w} {m.group(2)}')
    return out


def style_key(t: str) -> str:
    t = t.casefold()
    t = re.sub(r'[‘’“”"\'`]', '', t)
    t = re.sub(r'[–—‑\-]', '-', t)
    return re.sub(r'[\s.,:;]+', '', t)


def title_base(t: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', re.sub(r'\s+\d{4}\s*$', '', t.upper()))


def act_slug(title: str) -> str:
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', title.lower())).strip('-')


def version_list(a: dict) -> list[dict]:
    """The Act's reprints, oldest first: the history rows of this Act (rows of a predecessor Act with another name
    are left out here; rows of an older Act with the same name are left out once read, by Act number) and the
    current reprint. One reprint per start day (the latest upload)."""
    vs = []
    base = title_base(a['title'])
    for v in a['versions']:
        if v.get('word') and v.get('start') and title_base(v['title']) == base:
            vs.append({'start': v['start'], 'end': v['end'], 'url': fetch.word_url(v, False), 'id': int(v['word']),
                       'status': v['status'], 'current': False})
    if a['current'].get('word'):
        vs.append({'start': None, 'end': None, 'url': fetch.word_url(a['current'], True), 'id': int(a['current']['word']),
                   'status': 'In Force', 'current': True, 'reprint': a['current'].get('reprint')})
    return vs


def build_act(a: dict, offline: bool) -> dict:
    vs = version_list(a)
    for v in vs:
        raw = fetch.fetch(v['url'], offline)
        v['sha256'] = hashlib.sha256(raw).hexdigest()
        if not raw.startswith(b'\xd0\xcf\x11\xe0') and not is_rtf(raw):
            v['error'] = 'not a Word or RTF document'
            continue
        try:
            v.update(read_version(raw))
        except Exception as exc:  # noqa: BLE001
            v['error'] = f'{type(exc).__name__}: {exc}'
    problems = [f'{v["url"]}: {v["error"]}' for v in vs if v.get('error')]
    vs = [v for v in vs if not v.get('error')]
    if not vs:
        return {'act': a, 'versions': [], 'changes': [], 'problems': problems or ['no readable reprint']}
    cur = vs[-1]
    if cur['current']:
        cur['start'] = cur['as_at'] or (vs[-2]['end'] if len(vs) > 1 else None)
        if not cur['start']:
            problems.append('current reprint without an "as in force at" day')
            vs = vs[:-1]
    principal = vs[-1]['principal'] if vs else None
    keep = []
    for v in vs:
        if principal and v['principal'] and v['principal'] != principal:
            problems.append(f'{v["start"]}: reprint of another Act (No. {v["principal"][0]}, {v["principal"][1]}) left out')
            continue
        if v['as_at'] and v['as_at'] != v['start']:
            v['as_at_differs'] = True
        keep.append(v)
    byday: dict[str, dict] = {}
    for v in keep:
        if v['start'] not in byday or (v['current'], v['id']) > (byday[v['start']]['current'], byday[v['start']]['id']):
            byday[v['start']] = v
    vs = [byday[d] for d in sorted(byday)]
    if not vs:
        return {'act': a, 'versions': [], 'changes': [], 'problems': problems or ['no dated reprint']}
    table: dict[tuple, dict] = {}
    for v in reversed(vs):
        for k, t in v['table'].items():
            table.setdefault(k, t)
    changes = []
    for prev, cur in zip(vs, vs[1:]):
        hi = cur['start']
        keys = sorted(set(prev['units']) | set(cur['units']), key=unit_sort)
        changed = [k for k in keys if (prev['units'].get(k) or {}).get('text') != (cur['units'].get(k) or {}).get('text')]
        listed_new = sorted(set(cur['table']) - set(prev['table']))
        cause: dict[str, tuple] = {}
        why: dict[str, str] = {}
        for k in changed:
            p, c = prev['units'].get(k), cur['units'].get(k)
            new = unit_history(cur, k) - unit_history(prev, k)
            src = 'provision row'
            if not new:
                sched = None
                m = re.match(r'sch-([^/]+)', k)
                if m:
                    sched = m.group(1)
                hks = sorted({hk for u in (p, c) if u for hk in head_keys(u['text'], sched)})
                heads = set().union(*[cur['heads'].get(hk, set()) for hk in hks]) if hks else set()
                old_heads = set().union(*[prev['heads'].get(hk, set()) for hk in hks]) if hks else set()
                new = heads - old_heads
                src = 'heading row'
            if not new and len(listed_new) == 1:
                new = set(listed_new)
                src = 'list of legislation (the only Act added to it in this reprint)'
            if len(new) > 1:
                hit = {x for x in new if hi in dates_in((table.get(x) or {}).get('commenced', ''))}
                if len(hit) == 1:
                    new = hit
                    src += '; the only one of them whose commencement is this day'
            if len(new) == 1:
                x = next(iter(new))
                cause[k] = ('act', x, hi)
                why[k] = src
            elif len(new) > 1:
                cause[k] = ('candidates', tuple(sorted(new)), hi)
                why[k] = f'{len(new)} Acts new in the amendment history ({src})'
            elif p and c and style_key(p['text']) == style_key(c['text']):
                cause[k] = ('style', (), hi)
                why[k] = 'letter case, punctuation or spacing only'
            else:
                cause[k] = ('unexplained', (), hi)
                why[k] = 'no new entry in the list of amendments'
        groups: dict[tuple, list[str]] = {}
        for k in changed:
            groups.setdefault(cause[k], []).append(k)
        changes.append({'from': hi, 'prev_from': prev['start'], 'groups': groups, 'why': why,
                        'units': {k: cur['units'].get(k) for k in changed}, 'url': cur['url'],
                        'listed_new': listed_new})
    first = vs[0]
    out_versions = [{'start': v['start'], 'url': v['url'], 'sha256': v['sha256'], 'current': v['current'],
                     'as_at': v['as_at'], 'order': v['order'], 'n_units': len(v['units'])} for v in vs]
    return {'act': a, 'slug': act_slug(a['title']), 'title': a['title'], 'principal': principal, 'table': table,
            'versions': out_versions, 'first_units': first['units'], 'first_order': first['order'],
            'first_amended': bool(first['history'] or first['ranges'] or first['heads']),
            'changes': changes, 'problems': problems,
            'last_units': {k: True for k in vs[-1]['units']}}


# ---------------------------------------------------------------- git

def git(repo: Path, *args: str, env: dict | None = None, input: str | None = None) -> str:
    out = subprocess.run(['git', *args], cwd=repo, capture_output=True, env=env,
                         input=input.encode('utf-8') if input is not None else None)
    if out.returncode:
        raise SystemExit(f'git {" ".join(args)} failed: {out.stderr.decode("utf-8", "replace")}')
    return out.stdout.decode('utf-8').replace('\r\n', '\n')


def commit_date(day: str, seq: list[int]) -> str:
    """Noon in Darwin on the day. The Northern Territory keeps Australian Central Standard Time, UTC+9:30, all year:
    it has had no daylight saving since 1944. Git cannot record a time before 1970, so changes in force before then
    are dated 1 January 1970 (UTC), one second apart in order; the real day is in the In-force trailer."""
    d = dt.date.fromisoformat(day)
    if d.year < 1970:
        seq[0] += 1
        return f'{seq[0]} +0000'
    stamp = int(dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.timezone(dt.timedelta(hours=9, minutes=30))).timestamp())
    return f'{stamp} +0930'


def act_name(x: tuple, table: dict) -> str:
    n, y = x
    name = (table.get(x) or {}).get('name') or ''
    return f'{name} (No. {n}, {y})' if name else f'Act No. {n}, {y}'


def act_email(x: tuple) -> str:
    return f'act-{x[1]}-{x[0]}@{DOMAIN}'


def wrap(text: str) -> list[str]:
    return textwrap.wrap(text, 100, break_on_hyphens=False, break_long_words=False)


PRE_1970 = ['Git cannot record a date before 1970, so this commit is dated 1 January 1970; the day it took effect is',
            'in the In-force trailer.', '']
KIND_RANK = {'start': 0, 'act': 1, 'candidates': 2, 'style': 3, 'unexplained': 4, 'unchanged': 5}


def plan(built: list[dict]) -> list[dict]:
    """Every commit: the first reprint of each Act; one commit per amending Act per day, across all the Acts it
    changed that day; then, per Act, changes credited to two or more candidates, style changes and unexplained
    changes; and an empty marker for a reprint whose text did not change."""
    commits: dict[tuple, dict] = {}
    for ai, b in enumerate(built):
        if not b['versions']:
            continue
        d0 = b['versions'][0]['start']
        commits[(d0, 0, (0, 0), ai)] = {'kind': 'start', 'day': d0, 'parts': [(b, None, None)]}
        for ch in b['changes']:
            if not ch['groups']:
                commits[(ch['from'], KIND_RANK['unchanged'], (0, 0), ai)] = {
                    'kind': 'unchanged', 'day': ch['from'], 'parts': [(b, ch, None)]}
            for g, keys in ch['groups'].items():
                kind, x, day = g
                if kind == 'act':
                    key = (day, 1, (x[1], x[0]), -1)
                    c = commits.setdefault(key, {'kind': 'act', 'day': day, 'act': x, 'parts': []})
                    c['parts'].append((b, ch, keys))
                else:   # one commit per group: two sets of candidate Acts on one day are two commits
                    sub = tuple((y, n) for n, y in x) if kind == 'candidates' else ()
                    key = (day, KIND_RANK[kind], sub, ai)
                    assert key not in commits, key
                    commits[key] = {'kind': kind, 'day': day, 'g': g, 'parts': [(b, ch, keys)]}
    return [commits[k] for k in sorted(commits)]


def labels_of(b: dict, ch: dict, keys: list[str]) -> list[str]:
    by: dict[str, list[str]] = {}
    for k in sorted(keys, key=unit_sort):
        kind = 'Added' if k not in ch['prev_keys'] else ('Removed' if ch['units'].get(k) is None else 'Changed')
        by.setdefault(kind, []).append(unit_label(k))
    out = []
    for n in ('Added', 'Changed', 'Removed'):
        if n in by:
            out += textwrap.wrap(f'{n}: {", ".join(by[n])}', 100, subsequent_indent='  ', break_on_hyphens=False,
                                 break_long_words=False)
    return out


def title_case(t: str) -> str:
    small = {'of', 'and', 'the', 'for', 'in', 'on', 'to', 'a', 'an', 'or', 'by', 'at', 'with', 'into'}
    words = t.lower().split(' ')
    out = []
    for i, w in enumerate(words):
        if w.startswith('('):
            out.append('(' + (w[1:2].upper() + w[2:] if w[1:] not in small else w[1:]))
        elif i and w in small:
            out.append(w)
        else:
            out.append(re.sub(r"(^|-)([a-z])", lambda m: m.group(1) + m.group(2).upper(), w))
    return ' '.join(out)


def subject(*options: str) -> str:
    """The first of the subjects that fits in 72 characters (GitHub cuts longer ones), else the last one."""
    return next((o for o in options if len(o) <= 72), options[-1])


def short_name(b: dict) -> str:
    p = b['principal']
    if p:
        return f'Act No. {p[0]}, {p[1]}'
    name = title_case(b['title'])
    return name if len(name) <= 40 else name[:37].rstrip() + '...'


def message(c: dict, built_table: dict) -> tuple[tuple[str, str], list[str], list[str], list[tuple]]:
    """(author, message lines, trailers, files) for one planned commit; files are (path, text or None)."""
    day = c['day']
    files: list[tuple[str, str | None]] = []
    trailers: list[str] = []
    kind = c['kind']
    if kind == 'start':
        b = c['parts'][0][0]
        v = b['versions'][0]
        name = title_case(b['title'])
        files = [(f'{b["slug"]}/{file_name(k)}', b['first_units'][k]['text']) for k in b['first_order']]
        p = b['principal']
        if not b['first_amended'] and p:
            author = (act_name(p, b['table']), act_email(p))
            how = 'as made: its list of amendments is empty'
            k = 'Act'
        else:
            author = PUBLISHER
            how = ('as amended up to that day: the first reprint the history listing keeps. Amendments made before '
                   'it are in this text, not separated out; the reprint\'s list of amendments names them')
            k = 'Baseline'
        msg = [subject(f'{name}: reprint as in force at {day}', f'{short_name(b)}: reprint as in force at {day}'), '']
        msg += wrap(f'The {name}' + (f' (Act No. {p[0]}, {p[1]})' if p else '') + f' as in force at {long_day(day)}, '
                    f'{how}.') + ['']
        trailers = [f'Kind: {k}', f'In-force: {day}']
        t = b['table'].get(p) if p else None
        if t and t.get('assent'):
            trailers.append(f'Assented: {t["assent"]}')
        trailers += [f'Reprint: {v["url"]}', f'Act-page: {b["act"]["url"]}']
        if day < '1970-01-01':
            msg += PRE_1970
        return author, msg, trailers, files
    if kind == 'act':
        x = c['act']
        tables = [b['table'] for b, _ch, _k in c['parts']]
        name = next((act_name(x, t) for t in tables if t.get(x)), f'Act No. {x[0]}, {x[1]}')
        author = (name, act_email(x))
        titles = [title_case(b['title']) for b, _ch, _k in c['parts']]
        n_prov = sum(len(k) for _b, _ch, k in c['parts'])
        what = titles[0] if len(titles) == 1 else f'{len(titles)} Acts'
        subj = subject(f'{name}: {what}', f'Act No. {x[0]}, {x[1]}: {what}',
                       f'No. {x[0]}, {x[1]}: {len(titles)} Act{"s" if len(titles) > 1 else ""}, {n_prov} provisions')
        msg = [subj, '', name + '.', '']
        t = next((t[x] for t in tables if t.get(x)), {})
        ev = []
        for b, ch, keys in c['parts']:
            msg.append(f'{title_case(b["title"])}:')
            msg += ['  ' + l for l in labels_of(b, ch, keys)]
            srcs = sorted({ch['why'][k] for k in keys})
            ev += [f'Evidence: {title_case(b["title"])}: {s}' for s in srcs]
            for k in keys:
                files.append((f'{b["slug"]}/{file_name(k)}', (ch['units'].get(k) or {}).get('text')))
        msg.append('')
        msg += wrap(f'The list of amendments in the endnotes of each reprint as in force at {day} cites this Act for '
                    'these provisions, which the reprint before did not; the change is dated by that reprint\'s start '
                    'day in the Act\'s history listing.') + ['']
        trailers = ['Kind: Act', f'Act: No. {x[0]}, {x[1]}']
        if t.get('assent'):
            trailers.append(f'Assented: {t["assent"]}')
        trailers.append(f'In-force: {day}')
        comm = t.get('commenced') or ''
        if comm:
            trailers.append('Commenced: ' + clean(comm)[:300])
            if day not in dates_in(comm):
                trailers.append('Date-note: the commencement in the list of legislation does not name this day; '
                                'the day is the reprint\'s start')
        if t.get('assent') and day < t['assent']:
            trailers.append('Date-note: the reprint\'s start is before the Act\'s assent (retrospective)')
        trailers += ev
        for b, ch, keys in c['parts']:
            trailers += [f'Affecting: {title_case(b["title"])} {unit_label(k)}' for k in sorted(keys, key=unit_sort)]
        for b, ch, keys in c['parts']:
            trailers.append(f'Reprint: {ch["url"]}')
        if day < '1970-01-01':
            msg += PRE_1970
        return author, msg, trailers, files
    b, ch, keys = c['parts'][0]
    name = title_case(b['title'])
    author = PUBLISHER
    if kind == 'unchanged':
        msg = [subject(f'{name}: reprint as in force at {day}, no change in the text',
                       f'{short_name(b)}: reprint as in force at {day}, no change in the text',
                       f'{short_name(b)}: reprint of {day}, text unchanged'), '']
        msg += wrap(f'The reprint as in force at {long_day(day)} has the same text, provision for provision, as the '
                    f'reprint before (as in force at {ch["prev_from"]}). Whatever changed is outside the words this '
                    'record keeps (such as the endnotes). This empty commit marks the reprint.')
        trailers = ['Kind: reprint marker (no change in the text)', f'In-force: {day}']
        for x in ch['listed_new']:
            trailers.append(f'Named-in-legislation-list: {act_name(x, b["table"])}')
        trailers.append(f'Reprint: {ch["url"]}')
        return author, msg, trailers, []
    for k in keys:
        files.append((f'{b["slug"]}/{file_name(k)}', (ch['units'].get(k) or {}).get('text')))
    lst = labels_of(b, ch, keys)
    many = len(keys) > 1
    if kind == 'candidates':
        x = c['g'][1]
        msg = [subject(f'{name}: {len(keys)} provision{"s" if many else ""} (two or more Acts)',
                       f'{short_name(b)}: {len(keys)} provision{"s" if many else ""} (two or more Acts)'), '']
        msg += wrap(f'The list of amendments in the reprint as in force at {day} newly cites more than one Act for '
                    f'{"these provisions" if many else "this provision"}, and no reprint separates them. The change is '
                    'credited to none of them.') + [''] + lst + ['']
        trailers = ['Kind: two or more Acts (not credited)', f'In-force: {day}']
        trailers += [f'Candidate: {act_name(a, b["table"])}' for a in x]
    elif kind == 'style':
        msg = [subject(f'{name}: {len(keys)} provision{"s" if many else ""} (reprint style)',
                       f'{short_name(b)}: {len(keys)} provision{"s" if many else ""} (reprint style)'), '']
        msg += wrap(f'The text of the reprint as in force at {day} differs from the reprint before only in letter case, '
                    'punctuation or spacing: the publisher\'s style, not an amendment. No Act is credited.') + [''] + lst + ['']
        trailers = ['Kind: reprint style (not credited)', f'In-force: {day}']
    else:
        msg = [subject(f'{name}: {len(keys)} provision{"s" if many else ""} (no Act named)',
                       f'{short_name(b)}: {len(keys)} provision{"s" if many else ""} (no Act named)'), '']
        msg += wrap(f'The text of the reprint as in force at {day} differs from the reprint before, but its list of '
                    f'amendments cites no new Act for {"these provisions" if many else "this provision"} (an editorial '
                    'change, a renumbering, or text moved between provisions). The change is credited to no Act.') \
            + [''] + lst + ['']
        trailers = ['Kind: no Act named (not credited)', f'In-force: {day}']
        for x in ch['listed_new']:
            trailers.append(f'Named-in-legislation-list: {act_name(x, b["table"])}')
    trailers += [f'Affecting: {name} {unit_label(k)}' for k in sorted(keys, key=unit_sort)]
    trailers.append(f'Reprint: {ch["url"]}')
    if day < '1970-01-01':
        msg += PRE_1970
    return author, msg, trailers, files


def write_repo(repo: Path, built: list[dict], today: str, docs: bool = True) -> dict:
    remote = None
    if (repo / '.git').exists():
        out = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=repo, capture_output=True, text=True)
        remote = out.stdout.strip() or None

    def force(fn, path, _exc):
        os.chmod(path, 0o700)
        fn(path)
    if repo.exists():
        for p in repo.iterdir():
            shutil.rmtree(p, onexc=force) if p.is_dir() else p.unlink()
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'core.autocrlf', 'false')
    if remote:
        git(repo, 'remote', 'add', 'origin', remote)
    for b in built:
        prev = set(b['first_order'])
        for ch in b['changes']:
            ch['prev_keys'] = set(prev)
            for k, u in ch['units'].items():
                if u is None:
                    prev.discard(k)
                else:
                    prev.add(k)
    seq = [0]
    out = io.BytesIO()

    def data(x: bytes):
        out.write(b'data %d\n' % len(x))
        out.write(x)
        out.write(b'\n')
    mark = 0
    last_main = None
    last_future = None
    tag_marks: dict[str, int] = {}
    act_days: dict[tuple, list[tuple[str, int]]] = {}
    log = []
    for c in plan(built):
        author, msg, trailers, files = message(c, {})
        day = c['day']
        future = day > today
        when = commit_date(day, seq)
        mark += 1
        branch = 'future' if future else 'main'
        out.write(f'commit refs/heads/{branch}\nmark :{mark}\n'.encode())
        out.write(f'author {author[0]} <{author[1]}> {when}\n'.encode('utf-8'))
        out.write(f'committer {COMMITTER[0]} <{COMMITTER[1]}> {when}\n'.encode('utf-8'))
        data(('\n'.join(msg).rstrip('\n') + '\n\n' + '\n'.join(trailers) + '\n').encode('utf-8'))
        if future:
            parent = last_future or last_main
            if parent and last_future is None:
                out.write(f'from :{parent}\n'.encode())
            last_future = mark
        else:
            last_main = mark
        for path, text in files:
            p = path.encode('utf-8')
            if text is None:
                out.write(b'D ' + p + b'\n')
            else:
                out.write(b'M 100644 inline ' + p + b'\n')
                data(text.encode('utf-8'))
        out.write(b'\n')
        if not future:
            for b, _ch, _k in c['parts']:
                tag_marks[f'{b["slug"]}/{day}'] = mark
            if c['kind'] == 'act':
                act_days.setdefault(c['act'], []).append((day, mark))
        log.append({'day': day, 'kind': c['kind'], 'author': author[0], 'future': future,
                    'files': len(files), 'acts': [b['slug'] for b, _c, _k in c['parts']]})
    for x, ds in act_days.items():
        if len(ds) == 1:
            tag_marks[f'act/{x[1]}-{x[0]}'] = ds[0][1]
        else:
            for d, m in ds:
                tag_marks[f'act/{x[1]}-{x[0]}/{d}'] = m
    for name, m in sorted(tag_marks.items()):
        out.write(f'reset refs/tags/{name}\nfrom :{m}\n\n'.encode('utf-8'))
    out.write(b'done\n')
    marks_file = repo / '.git' / 'lexifina-marks'
    r = subprocess.run(['git', 'fast-import', '--quiet', '--done', f'--export-marks={marks_file}'], cwd=repo,
                       input=out.getvalue(), capture_output=True)
    if r.returncode:
        raise SystemExit(f'git fast-import failed: {r.stderr.decode(errors="replace")}')
    shas = dict(l.split(' ') for l in marks_file.read_text().split('\n') if l)
    marks_file.unlink()
    tags = {name: shas[f':{m}'] for name, m in tag_marks.items()}
    git(repo, 'reset', '-q', '--hard', 'main')
    if docs:
        write_docs(repo, built, log, tags, today, seq)
    return {'log': log, 'tags': tags}


def write_docs(repo: Path, built: list[dict], log: list, tags: dict, today: str, seq: list[int]):
    docs = HERE / 'repo'
    for p in sorted(docs.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:
            dest = repo / p.relative_to(docs)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(p.read_bytes())
    (repo / 'tools').mkdir(exist_ok=True)
    for name in ('fetch.py', 'worddoc.py', 'rtfdoc.py', 'build.py', 'reproduce.py', 'check_repo.py', 'check_words.py',
                 'test_repo.py'):
        if (HERE / name).exists():
            (repo / 'tools' / name).write_bytes((HERE / name).read_bytes())
    write_tables(repo, built, today)
    try:
        from reproduce import write_manifest, write_reproduce
        write_manifest(repo, built, log, today)
        write_reproduce(repo, built, log, tags, today)
    except ImportError:
        pass
    git(repo, 'add', '-A')
    when = commit_date(today, seq)
    env = {**os.environ, 'GIT_AUTHOR_NAME': COMMITTER[0], 'GIT_AUTHOR_EMAIL': COMMITTER[1], 'GIT_AUTHOR_DATE': when,
           'GIT_COMMITTER_NAME': COMMITTER[0], 'GIT_COMMITTER_EMAIL': COMMITTER[1], 'GIT_COMMITTER_DATE': when}
    git(repo, 'commit', '-q', '--cleanup=verbatim', '-F', '-', env=env,
        input=f'Documentation, tables, tools, corpus.json and REPRODUCE.md\n\nNo law text changes.\n\nIn-force: {today}\nKind: documentation\n')


def write_tables(repo: Path, built: list[dict], today: str):
    rows = []
    for b in built:
        for ch in b['changes']:
            for (kind, x, day), keys in ch['groups'].items():
                if day > today:
                    continue
                rows.append({'act_in_force': b['title'], 'in_force': day, 'kind': kind,
                             'amending_act': act_name(x, b['table']) if kind == 'act' else
                             '; '.join(act_name(a, b['table']) for a in x if isinstance(a, tuple)),
                             'provisions': ' '.join(unit_label(k).replace(' ', ' ') for k in sorted(keys, key=unit_sort)),
                             'reprint': ch['url']})
    rows.sort(key=lambda r: (r['in_force'], r['act_in_force'], r['kind'], r['amending_act'], r['provisions']))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=['act_in_force', 'in_force', 'kind', 'amending_act', 'provisions', 'reprint'],
                       lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
    (repo / 'changes.csv').write_text(buf.getvalue(), encoding='utf-8', newline='\n')
    idx = ['# Acts', '', '| Act | Folder | Reprints | From | To |', '| --- | --- | ---: | --- | --- |']
    for b in sorted(built, key=lambda b: b['slug']):
        vs = [v for v in b['versions'] if v['start'] <= today]
        if not vs:
            continue
        idx.append(f'| {title_case(b["title"])} | [{b["slug"]}]({b["slug"]}/) | {len(vs)} | {vs[0]["start"]} | {vs[-1]["start"]} |')
    (repo / 'ACTS.md').write_text('\n'.join(idx) + '\n', encoding='utf-8', newline='\n')


# ---------------------------------------------------------------- main

def load_index(offline: bool) -> list[dict]:
    p = CACHE / 'index.json'
    if not p.exists():
        raise SystemExit('run fetch.py first')
    return json.loads(p.read_text(encoding='utf-8'))


def _build_one(args):
    a, offline = args
    try:
        return build_act(a, offline)
    except SystemExit as exc:
        return {'act': a, 'versions': [], 'changes': [], 'problems': [str(exc)]}


def build_all(acts: list[dict], offline: bool) -> list[dict]:
    with cf.ProcessPoolExecutor(WORKERS) as ex:
        built = list(ex.map(_build_one, [(a, offline) for a in acts], chunksize=1))
    return built


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(HERE.parents[2] / 'australia-northern-territory-law-build'))
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--today', required=True)
    ap.add_argument('--only', default=None)
    ap.add_argument('--upto', default=None, help='only Acts whose title starts with a letter up to this one')
    ap.add_argument('--no-docs', action='store_true')
    ap.add_argument('--verify-rebuild', action='store_true')
    args = ap.parse_args()
    if args.verify_rebuild:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [sys.executable, '-X', 'utf8', str(HERE / 'build.py'), '--offline', '--repo', tmp, '--today', args.today]
            if args.upto:
                cmd += ['--upto', args.upto]
            out = subprocess.run(cmd, capture_output=True, text=True)
            if out.returncode:
                raise SystemExit(out.stderr)

            def hashes(r):
                log = git(Path(r), 'log', '--format=%H', 'main').split()
                tags = dict(l.split(' ', 1)[::-1] for l in git(Path(r), 'show-ref', '--tags').split('\n') if l)
                fut = subprocess.run(['git', 'rev-parse', '-q', '--verify', 'future'], cwd=r, capture_output=True,
                                     text=True).stdout.strip()
                return log, tags, fut
            a, b = hashes(args.repo), hashes(tmp)
            rec = {'identical': a[0][1:] == b[0][1:] and a[1] == b[1] and a[2] == b[2], 'history_tip': a[0][1],
                   'commits': len(a[0]) - 1, 'tags': len(a[1]), 'main_identical': a[0][0] == b[0][0],
                   'main': a[0][0], 'rebuilt_main': b[0][0], 'future': a[2]}
            print(json.dumps(rec, indent=1))
            p = HERE / 'verification.json'
            ver = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
            ver['rebuild'] = {k: rec[k] for k in ('identical', 'history_tip', 'commits', 'tags', 'main_identical')}
            p.write_text(json.dumps(ver, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
            for f in Path(tmp).rglob('*'):
                os.chmod(f, 0o700)
            raise SystemExit(0 if rec['identical'] else 1)
    acts = load_index(args.offline)
    if args.only:
        acts = [a for a in acts if a['slug'] in args.only.split(',')]
    if args.upto:
        acts = [a for a in acts if a['title'][:1].upper() <= args.upto.upper()]
    built = build_all(acts, args.offline)
    probs = {b['act']['slug']: b['problems'] for b in built if b['problems']}
    built = [b for b in built if b['versions']]
    built.sort(key=lambda b: b['slug'])
    (HERE / 'problems.json').write_text(json.dumps(probs, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    kinds: dict[str, int] = {}
    for b in built:
        for ch in b['changes']:
            for g, ks in ch['groups'].items():
                kinds[g[0]] = kinds.get(g[0], 0) + len(ks)
    print(f'{len(built)} acts, {sum(len(b["versions"]) for b in built)} reprints, unit changes {json.dumps(kinds, sort_keys=True)}')
    res = write_repo(Path(args.repo), built, args.today, docs=not args.no_docs)
    n_main = sum(1 for l in res['log'] if not l['future'])
    print(f'repo: {n_main} law commits on main, {len(res["log"]) - n_main} on future, {len(res["tags"])} tags -> {args.repo}')


if __name__ == '__main__':
    main()
