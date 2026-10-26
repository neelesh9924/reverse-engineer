# Notice

Reverse Engineer is released under the MIT License (see [LICENSE](LICENSE)).

## REA (rea-agents)

Reverse Engineer is a desktop front end for **REA** ("Reverse Engineer Anything", the
`rea-agents` package), a separate open-source project by morluto, released under the MIT
License: <https://github.com/morluto/rea>.

REA is **not included** in this repository. You clone and build it yourself, in its own
folder, and the studio starts it from there. REA's license, its
[disclaimer](https://github.com/morluto/rea/blob/main/DISCLAIMER.md), its security policy and
the licenses of the third-party analysis tools it uses (Ghidra, JADX, Frida and others) apply
to that checkout and to those tools, not to this repository.

The Tools page and the forms on it are generated at run time from the tool catalogue in your
own REA build (`dist/mcp-catalog.json`); none of REA's code or catalogue is copied here. The
test stand-ins in `tests/` (`support.py`, `fake_rea.py`) reproduce the shape of a few of REA's
tool schemas and results so the studio can be tested without REA.

## Claude and Anthropic

Claude and Claude Code are products of Anthropic. "Claude" and "Anthropic" are trademarks of
Anthropic, PBC. The studio runs the Claude Code program you install and sign in to yourself;
your use of Claude is governed by your agreement with Anthropic.

## No affiliation

This project is independent. It is not affiliated with, sponsored by or endorsed by the REA
project, its authors, or Anthropic. Names are used only to say what the studio works with.
