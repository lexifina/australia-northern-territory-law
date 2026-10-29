# Northern Territory (Australia) law, change by change

The Acts of the Northern Territory Legislative Assembly in force today, as a git repository: 382 Acts, each with every
reprint NT Legislation keeps from the first one this record can read. Each amending Act is one commit per day it took
effect, across all the Acts it changed that day, credited to that Act and dated that day, at noon in Darwin. The text
is the text of the reprints on [NT Legislation](https://legislation.nt.gov.au), published by the Office of
Parliamentary Counsel. **This is not an official version of Northern Territory legislation.**

## The questions, as git commands

| Question | Command |
| --- | --- |
| What did section 24 of the Bail Act say on 1 January 2010? | `python tools/in_force_on.py 2010-01-01 bail-act-1982 24` |
| The whole Bail Act as it stood on 1 January 2020 | `python tools/in_force_on.py 2020-01-01 bail-act-1982` |
| Section 1 of the Criminal Code (Schedule I) on 1 January 2015 | `python tools/in_force_on.py 2015-01-01 criminal-code-act-1983 "sch I 1"` |
| Section 24 of the Bail Act before and after the Bail Amendment Act 2015 | `git diff act/2015-2~1 act/2015-2 -- bail-act-1982/s-024.md` |
| Which Act wrote each line of section 24 of the Bail Act? | `git blame bail-act-1982/s-024.md` |
| Every change to section 4 of the Residential Tenancies Act, with the Act and the day | `git log --follow residential-tenancies-act-1999/s-004.md` |
| Everything one Act changed | `git show --stat act/2015-2` |
| What changed between two reprints | `git diff --stat liquor-act-2019/2021-11-19 liquor-act-2019/2023-02-16` |
| The commit that holds a day's text, and why it was made | `python tools/in_force_on.py 2015-06-01 bail-act-1982 --commit` |
| Every change with its Act, day and reprint | [`changes.csv`](changes.csv) |
| Changes two Acts could have made (credited to neither) | `git log --grep="Kind: two or more Acts"` |
| Changes no Act explains | `git log --grep="Kind: no Act named"` |
| Changes of letter case or punctuation only | `git log --oneline --grep="Kind: reprint style"` |
| Acts of 2024 that changed the Bail Act | `git log --format="%an %s" --author=", 2024)" -- bail-act-1982` |
| Every Act, its folder and reprints | [`ACTS.md`](ACTS.md), [`data/reprints.csv`](data/reprints.csv) |

## The dates, and which one we use

An NT Act has the day the Administrator assented to it and the day (or days) its provisions commenced. **Commits are
dated the day the change took effect**, at noon in Darwin: Australian Central Standard Time, UTC+9:30 all year (the
Territory has had no daylight saving since 1944). That day is the start day of the first reprint that shows the change:
NT Legislation lists each reprint of an Act with the day it is "as in force at". The assent day is in an `Assented:`
trailer and the commencement, in the words of the reprint's list of legislation, in `Commenced:`; where those words do
not name the commit's day, a `Date-note:` says so (an Act that commenced in stages, or a reprint made after the
commencement). Every commit carries an `In-force: YYYY-MM-DD` trailer. To ask what the law was on a day with plain git,
use the end of that day: `git rev-list -1 --before="2015-04-15T23:59:59+09:30" main -- bail-act-1982`.
`tools/in_force_on.py` does this by the trailer.

Git cannot record a date before 1970: commits for reprints in force before then are dated 1 January 1970, one second
apart, and the real day is in `In-force:`.

Where two Acts took effect between two reprints, both changes are dated by the later reprint: this record never dates
a text that no reprint printed. Each reprint day has a tag (`bail-act-1982/2015-04-15`), and each reprint is checked
against the official file.

## What "author" means

The author of a commit is the Act that made the change, with an address ending `@legislation.nt.invalid` that cannot
exist: `Bail Amendment Act 2015 (No. 2, 2015)`, so `git blame` names the Act. Each reprint's endnotes have a list of
amendments, a row per provision naming the Acts that inserted, amended, substituted or repealed it ("amd No. 2, 2015,
s 4"); a change is credited to the Act that is new in that provision's row (or in the row of the Part or Division
heading over it, or, when the provision's row did not change, the only Act the reprint added to its list of
legislation). Rules, each labelled in the commit:

- Where two or more Acts are new in the row, the one whose commencement names the reprint's day is credited if it is
  the only one; otherwise the change is credited to none of them. It is committed by `NT Legislation (Office of
  Parliamentary Counsel, Northern Territory)`, with the Acts named in `Candidate:` trailers.
- Changes of letter case, punctuation or spacing in the publisher's style, and changes the endnotes do not explain
  (renumbering, text moved between provisions, editorial changes), are committed by the publisher and labelled.
- An Act starts at its first reprint. If that reprint's list of amendments is empty, it is the Act as made and is
  authored by the Act itself; otherwise it is authored by the publisher, and the amendments made before it are in its
  text, not separated out.
- One Act that took effect on one day for several Acts is one commit touching all of them (tag `act/2015-2`, or
  `act/<year>-<number>/<day>` for an Act that took effect on several days).
- A reprint whose text did not change is an empty commit, so every reprint has its tag.
- Blame credits whole lines. Where two Acts changed words of one line, the later one is credited with the line; use
  `git log -p --word-diff` for the words.

## How it is checked

`tools/check_repo.py` reads every reprint of every Act back from git at its tag and compares it with the official
file: file by file with the text parsed from the reprint, and letter by letter (letters and digits) with the whole
reprint read by `tools/check_words.py`, an independent reader that shares no code with the parser. `tools/test_repo.py`
checks that the commands above work. The results are in [`REPRODUCE.md`](REPRODUCE.md).

## Files

Each Act is a folder named after its title (`bail-act-1982/`). Each section is one file named after its number:
`bail-act-1982/s-024.md` is section 24, `s-024A.md` section 24A. `intro.md` is the title and long title. A schedule's
heading and text outside its clauses is `sch-2.md`, and its numbered clauses `sch-2/01.md`; the Criminal Code is
Schedule I of the Criminal Code Act 1983, so its sections are `criminal-code-act-1983/sch-I/01.md` onwards (schedule clauses and sections take two digits: `sch-I/154.md`). The
headings of the Chapters, Parts and Divisions that begin with a section are `##` lines at the top of its file; its
number and heading are the `#` line; then one line per paragraph (subsection, paragraph, definition, note, penalty).
Table rows are one line with cells joined by ` | `. A repealed provision is removed.

## What it does not show

- Subordinate legislation (regulations, rules, by-laws), and repealed Acts: only the Acts in force on the build day.
- An Act before the first reprint its history listing keeps, and reprints of a predecessor Act of the same name
  that the listing mixes in ([`data/left-out.csv`](data/left-out.csv)).
- The Supreme Court (Rules of Procedure) Act 1987, published only as a scanned PDF whose text layer is unreadable
  OCR, and the Appropriation (2026-2027) Act 2026 (as made, with no "as in force at" day). Seven Stamp Duty Act
  reprints exist only as RTF; they are read (`tools/rtfdoc.py`), but six of them (1995-2004) are reprints of the
  former Stamp Duty Act (No. 48 of 1978), a predecessor with the same name, and are left out like its Word reprints.
  The seventh, as in force at 1 July 2010, is in the history.
- The text of a day between two reprints other than the earlier reprint's.
- Which of two Acts of one period made a change, where both are new in the provision's row (above).
- The endnotes, tables of provisions and covers of the reprints, and the Territory's arms.
- Provisions that amend other Acts are kept as text; their effect on those Acts is in those Acts' own history.

The authorised text is the one on NT Legislation.

## How it is built

`tools/fetch.py` reads the Acts list, each Act's page and history listing, and every reprint into a cache, and
`tools/build.py` writes this repository from scratch; the same data gives the same hashes. Every step, the software,
and what an AI model did are in [`REPRODUCE.md`](REPRODUCE.md); the sources and the legal basis for reuse in
[`SOURCES.md`](SOURCES.md). [`corpus.json`](corpus.json) describes the corpus for tools that read several of
Lexifina's law repositories. After a rebuild, `git fetch --force --tags` replaces stale tags in an existing clone.

## Licence

Source: NT Legislation (https://legislation.nt.gov.au), Office of Parliamentary Counsel, Northern Territory. Copyright
in Northern Territory legislation is owned by the Northern Territory, which permits anyone to publish it if the
publication does not claim to be an official version, does not use the Territory's arms, and reproduces the material
accurately in a context that does not mislead. The text has been changed (split into files by provision, white space
normalised, history reconstructed); see [`LICENSE`](LICENSE). **This repository is private.**
