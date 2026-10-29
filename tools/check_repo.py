"""Checks the repository against every reprint it was built from (cache only, no network).

    python -X utf8 check_repo.py --today 2026-10-29 [--repo ...] [--no-record] [--only SLUG,...]

For every Act and every reprint as in force on or before --today:
1. Files: the Act's folder at the tag <act>/<day> holds exactly the files the build's parser makes from that reprint
   (same names, same bytes), and the tagged commit's In-force trailer is that day.
2. Letters and digits (independent): check_words.py reads the reprint's characters through the Word piece table with
   its own code (no styles, no paragraphs, no build code); the files' letters and digits, case-folded, must tile the
   reprint's letters and digits exactly from the title over the long title up to the ENDNOTES heading.
Writes verification.json next to this script unless --no-record.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import check_words  # noqa: E402
import fetch  # noqa: E402


def files_at(repo: str, ref: str, slug: str) -> dict[str, str]:
    out = subprocess.run(['git', 'ls-tree', '-r', ref, f'{slug}/'], cwd=repo, capture_output=True).stdout.decode()
    blobs = {}
    for line in out.split('\n'):
        if line:
            info, f = line.split('\t', 1)
            if f.endswith('.md') and f != f'{slug}/README.md':
                blobs[f[len(slug) + 1:]] = info.split()[2]
    names = sorted(blobs)
    data = subprocess.run(['git', 'cat-file', '--batch'], cwd=repo, input=''.join(f'{blobs[f]}\n' for f in names).encode(),
                          capture_output=True).stdout
    res = {}
    pos = 0
    for f in names:
        nl = data.index(b'\n', pos)
        size = int(data[pos:nl].split()[2])
        res[f] = data[nl + 1:nl + 1 + size].decode('utf-8')
        pos = nl + 1 + size + 1
    return res


def check_act(args) -> dict:
    a, repo, today = args
    b = build.build_act(a, True)
    if not b['versions']:
        return {'slug': a['slug'], 'versions': 0, 'bad': [], 'words': 0, 'letters': 0}
    slug = b['slug']
    bad = []
    letters = 0
    n = 0
    for v in b['versions']:
        if v['start'] > today:
            continue
        n += 1
        tag = f'{slug}/{v["start"]}'
        trailer = subprocess.run(['git', 'log', '-1', '--format=%(trailers:key=In-force,valueonly)', tag], cwd=repo,
                                 capture_output=True, text=True).stdout.strip()
        if trailer != v['start']:
            bad.append(f'{tag}: In-force {trailer!r}')
        raw = fetch.fetch(v['url'], True)
        parsed = build.read_version(raw)
        want = {build.file_name(k): u['text'] for k, u in parsed['units'].items()}
        got = files_at(repo, tag, slug)
        if want != got:
            miss = sorted(set(want) - set(got))[:3]
            extra = sorted(set(got) - set(want))[:3]
            diff = sorted(k for k in set(want) & set(got) if want[k] != got[k])[:3]
            bad.append(f'{tag}: files differ (missing {miss}, extra {extra}, changed {diff})')
        ok, detail = check_words.tile(raw, list(got.values()))
        if not ok:
            bad.append(f'{tag}: letters: {detail}')
        else:
            letters += int(re.match(r'\d+', detail).group(0))
    return {'slug': slug, 'versions': n, 'bad': bad, 'letters': letters}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(HERE.parents[2] / 'australia-northern-territory-law-build'))
    ap.add_argument('--today', required=True)
    ap.add_argument('--only', default=None)
    ap.add_argument('--no-record', action='store_true')
    args = ap.parse_args()
    acts = json.loads((HERE / '.cache' / 'index.json').read_text(encoding='utf-8'))
    if args.only:
        acts = [a for a in acts if a['slug'] in args.only.split(',')]
    with cf.ProcessPoolExecutor(build.WORKERS) as ex:
        res = list(ex.map(check_act, [(a, args.repo, args.today) for a in acts], chunksize=1))
    bad = [x for r in res for x in r['bad']]
    rec = {'acts': sum(1 for r in res if r['versions']), 'reprints': sum(r['versions'] for r in res),
           'mismatches': len(bad), 'letters': sum(r['letters'] for r in res), 'checked_on': args.today,
           'first_mismatches': bad[:50]}
    print(json.dumps(rec, indent=1))
    if not args.no_record:
        p = HERE / 'verification.json'
        ver = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        ver['check'] = rec
        p.write_text(json.dumps(ver, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
