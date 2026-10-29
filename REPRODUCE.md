# How this history was made, and how to make it again

The build writes this file; it describes the build that made this commit.

## Short version

With the retrieved files in place (the list of Acts and reprints, `index.json`, and every reprint, each matching its
sha256 in `data/reprints.csv`):

```sh
pip install olefile
python -X utf8 tools/build.py --today 2026-10-29 --repo ../rebuilt
python -X utf8 tools/check_repo.py --today 2026-10-29 --repo ../rebuilt
python -X utf8 tools/test_repo.py --repo ../rebuilt
```

Outside Lexifina's own folders, set `GITLAW_PUBLIC_REBUILD=1` before running `tools/build.py`. How the files were
retrieved is not part of this record.

## Source

NT Legislation, <https://legislation.nt.gov.au>, kept by the Office of Parliamentary Counsel, Northern Territory; retrieved 2026-10-28.
- The Acts in force, from the list of Acts by title (382 Acts built).
- Each Act's history listing: every reprint with its start and end ("as in force at") days and a Word file.
  `index.json` holds the list and the history listings as read on the retrieval day (sha256 `3e4901180ad70ac439714960e4bc8f6150c97762edb95916c1b14c09e655e91a`).
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

Counts (on or before 2026-10-29): 3701 reprints; commits by kind {"act": 1178, "candidates": 82, "start": 382, "style": 94, "unchanged": 11, "unexplained": 194};
provision changes by kind {"act": 35697, "candidates": 281, "style": 879, "unexplained": 1244}. `data/labelled.csv` lists every labelled change.

Dates: noon in Darwin, UTC+9:30 all year (the Northern Territory has no daylight saving). Every commit carries
`In-force:`; amending-Act commits also carry `Assented:` and `Commenced:` (the list of legislation's words) and a
`Date-note:` when that commencement does not name the reprint's day (a reprint made later than the commencement, or a
staged commencement). Days before 1970 are clamped to 1970-01-01, seconds apart. Reprints dated after the build day go
on the `future` branch, never merged.

Left out (58 notes in `data/left-out.csv`): reprints of a predecessor Act with the same name that the history
listing mixes in (by Act number), among them six RTF reprints of the former Stamp Duty Act; the Supreme Court (Rules
of Procedure) Act 1987, published only as a scanned PDF (6 pages, Canon scanner, 2016) whose OCR text layer is
garbled (letters misread, "Act 7987" for 1987, side notes interleaved with the text), so no reader of it could give
the Act's words; and the
Appropriation (2026-2027) Act 2026, as made, with no "as in force at" day.

## Checks

- check_repo.py: 3701 reprints of 382 Acts, 0 mismatches; 340177617 letters and digits tiled.
- Rebuild from the cache into an empty folder: identical: the last law commit `c29064d2d428b67dad51869f273902b64153fe38` and all 1941 law commits and 4879 tags had the same hashes.
- test_repo.py: 6 of 6 passed.

## Software and people

Python 3.12.10, git version 2.46.0.windows.1, olefile 0.47. No model runs at build time; no text is written by a model. The builder, reader and
checks were written by an AI agent, Claude Opus 5.5 (model id `claude-opus-5-5`), working for Lexifina, from the source's own data and documentation.
