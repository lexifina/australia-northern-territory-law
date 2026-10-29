"""Writes data/reprints.csv, data/left-out.csv, data/labelled.csv, corpus.json and REPRODUCE.md into the Northern
Territory repository from the facts of the build. Called by build.py.

Check results come from verification.json next to this file (written by check_repo.py, `test_repo.py --record` and
`build.py --verify-rebuild`); they describe the build before the one that writes this file, from the same data."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
from pathlib import Path

import source

HERE = Path(__file__).resolve().parent
SITE = 'https://legislation.nt.gov.au'
TERMS = 'https://legislation.nt.gov.au/en/Footer/Terms-of-Use'
MODEL = 'Claude Opus 5.5 (model id `claude-opus-5-5`)'


def record_to(built: list[dict], today: str) -> str:
    return max(v['start'] for b in built for v in b['versions'] if v['start'] <= today)


def _csv(rows: list[list]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows(rows)
    return buf.getvalue()


def write_manifest(repo: Path, built: list[dict], log: list, today: str):
    from build import act_name, title_case, unit_label, unit_sort
    d = repo / 'data'
    d.mkdir(exist_ok=True)
    rows = [['folder', 'title', 'act', 'as_in_force_at', 'current', 'url', 'sha256']]
    for b in built:
        p = b['principal']
        for v in b['versions']:
            rows.append([b['slug'], title_case(b['title']), f'No. {p[0]}, {p[1]}' if p else '', v['start'],
                         'yes' if v['current'] else '', v['url'], v['sha256']])
    (d / 'reprints.csv').write_text(_csv(rows), encoding='utf-8', newline='\n')
    probs = json.loads((HERE / 'problems.json').read_text(encoding='utf-8')) if (HERE / 'problems.json').exists() else {}
    rows = [['act_page_slug', 'note']] + [[k, n] for k in sorted(probs) for n in probs[k]]
    (d / 'left-out.csv').write_text(_csv(rows), encoding='utf-8', newline='\n')
    rows = [['folder', 'in_force', 'kind', 'provisions', 'acts_named']]
    for b in built:
        for ch in b['changes']:
            for (kind, x, day), keys in ch['groups'].items():
                if kind == 'act' or day > today:
                    continue
                rows.append([b['slug'], day, kind, ' '.join(unit_label(k) for k in sorted(keys, key=unit_sort)),
                             '; '.join(act_name(a, b['table']) for a in x if isinstance(a, tuple))])
    (d / 'labelled.csv').write_text(_csv(rows), encoding='utf-8', newline='\n')
    n_rep = sum(1 for b in built for v in b['versions'] if v['start'] <= today)
    corpus = {
        'corpus': 'au/northern-territory/acts',
        'parent': 'au/federal/acts',
        'name': 'Northern Territory (Australia) law: the Acts in force, reprint by reprint',
        'label': 'Northern Territory',
        'language': 'en',
        'acts': [f'{title_case(b["title"])}' + (f' (No. {b["principal"][0]}, {b["principal"][1]})' if b['principal'] else '')
                 for b in built],
        'history_from': min(b['versions'][0]['start'] for b in built),
        'record_to': record_to(built, today),
        'record_note': (f'{len(built)} Northern Territory Acts in force (NT Legislation, Acts by title, '
                        f'{today}), each from the first reprint its history listing keeps that the reader can read '
                        f'through every reprint to the one in force on {today} ({n_rep} reprints); data retrieved '
                        f'{today}. Every reprint day is checked against the reprint\'s own text.'),
        'in_force_trailer': 'In-force',
        'built': today,
        'checked_on': today,
        'history': 'in-force',
        'verification': ('Every reprint on its "as in force at" day: the files equal the parser\'s reading of the '
                         'reprint, and their letters and digits tile the reprint\'s text read by an independent reader.'),
        'dates': {'commit': 'in-force', 'other_trailers': ['Assented', 'Commenced'],
                  'time': '12:00 Darwin (ACST, UTC+9:30, all year; no daylight saving)'},
        'scope': {
            'trailer': 'Applies',
            'when_absent': ('this corpus records no scope notes (no commit carries Applies:); the absence is not a '
                            'statement that the change reaches every case'),
            'default_reach': {
                'rule': ('presumption against retrospective operation; repeal or amendment does not disturb what was '
                         'done or accrued before'),
                'meaning': ('an NT Act is presumed not to operate retrospectively on substantive rights unless it says '
                            'so; the repeal or amendment of an Act does not affect its previous operation or rights '
                            'and liabilities accrued under it, unless the contrary intention appears'),
                'authority': ('Interpretation Act 1978 (NT) s 12 (effect of repeal); Maxwell v Murphy (1957) 96 CLR '
                              '261 (common-law presumption); not checked in this corpus'),
                'decided_here': False,
            },
        },
        'tool': 'tools/in_force_on.py',
        'citation': {'file': r'([a-z0-9-]+)/s-0*([0-9]+[A-Z]*)\.md', 'cites': r'(section|s\.?) ?{n}([^0-9A-Za-z]|$)'},
    }
    (repo / 'corpus.json').write_text(json.dumps(corpus, ensure_ascii=False, indent=2) + '\n', encoding='utf-8',
                                      newline='\n')


def write_reproduce(repo: Path, built: list[dict], log: list, tags: dict, today: str):
    ver = json.loads((HERE / 'verification.json').read_text(encoding='utf-8')) if (HERE / 'verification.json').exists() else {}
    pyv = subprocess.run(['python', '--version'], capture_output=True, text=True).stdout.strip()
    gitv = subprocess.run(['git', '--version'], capture_output=True, text=True).stdout.strip()
    try:
        import olefile
        olev = olefile.__version__
    except Exception:
        olev = '?'
    kinds: dict[str, int] = {}
    for c in log:
        if not c['future']:
            kinds[c['kind']] = kinds.get(c['kind'], 0) + 1
    unit_kinds: dict[str, int] = {}
    for b in built:
        for ch in b['changes']:
            for (kind, _x, day), keys in ch['groups'].items():
                if day <= today:
                    unit_kinds[kind] = unit_kinds.get(kind, 0) + len(keys)
    n_rep = sum(1 for b in built for v in b['versions'] if v['start'] <= today)
    chk = ver.get('check', {})
    det = ver.get('rebuild', {})
    tst = ver.get('smoke') or ver.get('tests', {})   # test_repo.py --record writes 'smoke'
    index = source.CACHE / 'index.json'
    index_sha = hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else '(not present)'
    check_line = (f'{chk["reprints"]} reprints of {chk["acts"]} Acts, {chk["mismatches"]} mismatches; '
                  f'{chk["letters"]} letters and digits tiled' if chk else '(not yet run)')
    rebuild_line = (f'identical: the last law commit `{det["history_tip"]}` and all {det["commits"]} law commits and '
                    f'{det["tags"]} tags had the same hashes' if det.get('identical') else '(not yet run)')
    tests_line = f'{tst.get("passed")} of {tst.get("total")} passed' if tst else '(not yet run)'
    probs = json.loads((HERE / 'problems.json').read_text(encoding='utf-8')) if (HERE / 'problems.json').exists() else {}
    n_left = sum(len(v) for v in probs.values())
    text = f"""# How this history was made, and how to make it again

The build writes this file; it describes the build that made this commit.

## Short version

With the retrieved files in place (the list of Acts and reprints, `index.json`, and every reprint, each matching its
sha256 in `data/reprints.csv`):

```sh
pip install olefile
python -X utf8 tools/build.py --today {today} --repo ../rebuilt
python -X utf8 tools/check_repo.py --today {today} --repo ../rebuilt
python -X utf8 tools/test_repo.py --repo ../rebuilt
```

Outside Lexifina's own folders, set `GITLAW_PUBLIC_REBUILD=1` before running `tools/build.py`. How the files were
retrieved is not part of this record.

## Source

NT Legislation, <{SITE}>, kept by the Office of Parliamentary Counsel, Northern Territory; retrieved 2026-10-28.
- The Acts in force, from the list of Acts by title ({len(built)} Acts built).
- Each Act's history listing: every reprint with its start and end ("as in force at") days and a Word file.
  `index.json` holds the list and the history listings as read on the retrieval day (sha256 `{index_sha}`).
- Each reprint is a Word 97-2003 file. Its endnotes have a LIST OF LEGISLATION (each Act with its number, assent and
  commencement) and a LIST OF AMENDMENTS (one row per provision: "amd No. 17, 1996, s 6", "ins", "sub", "rep").
  `data/reprints.csv` lists every reprint read, with its address and sha256.
- Terms: see SOURCES.md and LICENSE.

## How the text is read

`tools/worddoc.py` reads the Word binary format directly: the piece table for the characters, the paragraph
properties for each paragraph's style name and table marks, the style sheet for names. Field instructions are dropped
(their shown result is kept). The body runs from the title over the long title to the ENDNOTES heading. Files:
- `<act>/intro.md`: the title, long title and anything before the first section;
- `<act>/s-NNN.md`: each section (style `New Section Heading`), with the Part, Division and Subdivision headings that
  open before it as `## ` lines;
- `<act>/sch-X.md`: a schedule's heading and text outside its clauses; `<act>/sch-X/NN.md`: each numbered clause.
One paragraph per line; table rows are one line, cells joined by ` | `; runs of white space are one space.
Older reprints (before about 2008) have no LongTitle style: their body starts after the inner cover's "as in force
at" line and ends at ENDNOTES or, in the oldest, at "Notes" / "Table of Amendments", whose rows ("5. Amended by
No. 1, 2004, s. 62") are read like the LIST OF AMENDMENTS. Where Word list numbering holds a section's number (it is
not in the text), the file is named from the table of contents entry with the same heading.

Seven reprints of the Stamp Duty Act (1995-2010) are served as RTF, with no styles. `tools/rtfdoc.py` reads them and
names their headings from the reprint's own structure: its table of provisions lists every section (number and
heading), Part and Division heading and schedule in order, and the body must show each of them, in that order, as a
bold paragraph (in capitals in the 1995 reprint), or the reprint is left out. The endnotes' LIST OF LEGISLATION and
LIST OF AMENDMENTS are read as in the Word files. The RTF export breaks four paragraphs of the 2010 reprint in two
before a word like "non-fulfilment"; a paragraph that starts with a small letter right after a labelled paragraph
("(a)", "(ii)") with the same indents that does not end in . ; or : is joined to it. Six of the seven (1995-2004) are
reprints of the former Stamp Duty Act, No. 48 of 1978 (their endnotes say so), and are left out with that Act's Word
reprints; the reprint as in force at 1 July 2010 is the current Act's (No. 49 of 1978, the former Taxation
(Administration) Act). Its formulas are as incomplete as in the Word files (the fraction in s 9BB reads "V x"): the
equation objects carry no text in either format.

## How changes are dated and credited

The reprints of an Act are compared in order, file by file. A change is dated by the start day of the reprint that
first shows it (its "as in force at" day). It is credited to an Act when exactly one Act is new in that provision's
row of the reprint's list of amendments (or, for a provision whose row did not change, in the row of the Part or
Division heading over it, or when the reprint added exactly one Act to its list of legislation). When two or more Acts
are new, the one whose commencement in the list of legislation names that day is credited if it is the only one;
otherwise the change is labelled with `Candidate:` trailers and credited to none. Changes with no new Act are labelled
"no Act named"; changes in letter case, punctuation or spacing only are labelled "reprint style". All labelled
commits are authored by the publisher. One commit per amending Act per day, across all the Acts it changed that day.

Counts (on or before {today}): {n_rep} reprints; commits by kind {json.dumps(kinds, sort_keys=True)};
provision changes by kind {json.dumps(unit_kinds, sort_keys=True)}. `data/labelled.csv` lists every labelled change.

Dates: noon in Darwin, UTC+9:30 all year (the Northern Territory has no daylight saving). Every commit carries
`In-force:`; amending-Act commits also carry `Assented:` and `Commenced:` (the list of legislation's words) and a
`Date-note:` when that commencement does not name the reprint's day (a reprint made later than the commencement, or a
staged commencement). Days before 1970 are clamped to 1970-01-01, seconds apart. Reprints dated after the build day go
on the `future` branch, never merged.

Left out ({n_left} notes in `data/left-out.csv`): reprints of a predecessor Act with the same name that the history
listing mixes in (by Act number), among them six RTF reprints of the former Stamp Duty Act; the Supreme Court (Rules
of Procedure) Act 1987, published only as a scanned PDF (6 pages, Canon scanner, 2016) whose OCR text layer is
garbled (letters misread, "Act 7987" for 1987, side notes interleaved with the text), so no reader of it could give
the Act's words; and the
Appropriation (2026-2027) Act 2026, as made, with no "as in force at" day.

## Checks

- check_repo.py: {check_line}.
- Rebuild from the cache into an empty folder: {rebuild_line}.
- test_repo.py: {tests_line}.

## Software and people

{pyv}, {gitv}, olefile {olev}. No model runs at build time; no text is written by a model. The builder, reader and
checks were written by an AI agent, {MODEL}, working for Lexifina, from the source's own data and documentation.
"""
    (repo / 'REPRODUCE.md').write_text(text, encoding='utf-8', newline='\n')
