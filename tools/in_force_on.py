"""Prints an Act, or one provision of it, as in force on a given day, from this repository's history.

    python tools/in_force_on.py 2015-01-01 criminal-code-act-1983 s5          # section 5 on 1 January 2015
    python tools/in_force_on.py 2015-01-01 criminal-code-act-1983 "sch I 154" # Schedule I, clause/section 154
    python tools/in_force_on.py 2015-01-01 bail-act-1982 sch2                 # Schedule 2 (its heading and text)
    python tools/in_force_on.py 2015-01-01 bail-act-1982 intro                # title and long title
    python tools/in_force_on.py 2020-01-01 bail-act-1982                      # the whole Act on 1 January 2020
    python tools/in_force_on.py 2020-01-01 bail-act-1982 --commit             # the commit holding that day's text

Each commit carries the day its change took effect in an `In-force:` trailer. The text in force on a day is the last
commit on main on or before it that touches the Act (days before 1970 are clamped by git; the trailer has the real
day). An Act's record starts at the first reprint its history listing keeps. Standard library only.
"""
import re
import subprocess
import sys


def git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True, encoding='utf-8', check=True).stdout


def _n(s: str) -> tuple:
    m = re.match(r'(\d+)(.*)$', s)
    if not m:
        return (0, s)
    suffix = m.group(2)   # Australian numbering: s 6AA comes between s 6 and s 6A
    return (int(m.group(1)), '!' + suffix if suffix.startswith('AA') else suffix)


ROMAN = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100}


def _sched(s: str) -> tuple:
    if s and re.fullmatch(r'[IVXLC]+', s):
        v = 0
        for a, b in zip(s, s[1:] + ' '):
            v += -ROMAN[a] if b != ' ' and ROMAN[b] > ROMAN[a] else ROMAN[a]
        return (v, '')
    return _n(s or '0')


def order(path: str) -> tuple:
    key = path.split('/', 1)[1][:-3]
    dup = int(key.rsplit('-dup', 1)[1]) if '-dup' in key else 0
    base = re.sub(r'-dup\d+$', '', key)
    if base == 'intro':
        return (0, (0, ''), (0, ''), dup)
    if base.startswith('s-'):
        return (1, _n(base[2:]), (0, ''), dup)
    m = re.match(r'sch(?:-([^/]+))?(?:/(.+))?$', base)
    if m:
        return (2, _sched(m.group(1) or ''), _n(m.group(2)) if m.group(2) else (-1, ''), dup)
    return (9, (0, base), (0, ''), dup)


def file_for(key: str) -> str:
    """'5' or 's5' -> s-005.md; 'sch2' -> sch-2.md; 'sch I 154' -> sch-I/154.md; 'sch 1 cl 3' -> sch-1/03.md;
    'intro' -> intro.md; anything else is a file name."""
    k = key.strip()
    m = re.fullmatch(r'(?:s|section)?\.?\s*(\d+)([A-Za-z]*)', k, re.I)
    if m:
        return f's-{int(m.group(1)):03d}{m.group(2).upper()}.md'
    m = re.fullmatch(r'sch(?:edule)?\s*-?\s*([0-9]*[A-Za-z]*)(?:\s*,?\s*(?:cl|clause|s|section)?\.?\s*(\d+)([A-Za-z]*))?',
                     k, re.I)
    if m:
        sch = f'sch-{m.group(1).upper()}' if m.group(1) else 'sch'
        return f'{sch}/{int(m.group(2)):02d}{m.group(3).upper()}.md' if m.group(2) else f'{sch}.md'
    return k if k.endswith('.md') else f'{k}.md'


def commit_on(day: str, slug: str) -> str:
    out = git('log', '--format=%H%x09%(trailers:key=In-force,valueonly,separator=)', 'main', '--', slug)
    for line in out.splitlines():
        h, _, d = line.partition('\t')
        if d.strip() and d.strip() <= day:
            return h
    return ''


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    args = [a for a in sys.argv[1:] if a != '--commit']
    if len(args) < 2:
        sys.exit(__doc__)
    day, slug = args[0], args[1].strip('/')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        sys.exit(f'{day} is not a date (YYYY-MM-DD).')
    commit = commit_on(day, slug)
    if not commit:
        sys.exit(f'{slug} is not in the record on {day}.')
    if '--commit' in sys.argv:
        sys.stdout.write(git('show', '-s', '--format=%H%n%an%n%B', commit))
        return
    if len(args) > 2:
        name = file_for(' '.join(args[2:]))
        try:
            sys.stdout.write(git('show', f'{commit}:{slug}/{name}'))
        except subprocess.CalledProcessError:
            sys.exit(f'{slug}/{name} was not in the text on {day}.')
        return
    blobs = {}
    for line in git('ls-tree', '-r', commit, f'{slug}/').split('\n'):
        if line:
            info, f = line.split('\t', 1)
            if f.endswith('.md') and f != f'{slug}/README.md':
                blobs[f] = info.split()[2]
    names = sorted(blobs, key=order)
    data = subprocess.run(['git', 'cat-file', '--batch'], input=''.join(f'{blobs[f]}\n' for f in names).encode(),
                          capture_output=True, check=True).stdout
    pos = 0
    for f in names:
        nl = data.index(b'\n', pos)
        size = int(data[pos:nl].split()[2])
        sys.stdout.write(data[nl + 1:nl + 1 + size].decode('utf-8') + '\n')
        pos = nl + 1 + size + 1


if __name__ == '__main__':
    main()
