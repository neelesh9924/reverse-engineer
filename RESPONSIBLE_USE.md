# Responsible use

Reverse Engineer is a desktop front end for REA. It adds no analysis of its own: everything it
does, it does through REA's tools, so REA's
[disclaimer](https://github.com/morluto/rea/blob/main/DISCLAIMER.md) applies to everything you
do with the studio. Read it. This page summarises it and adds what is specific to the studio.

## What it is for

Lawful analysis: security research, malware and incident analysis, vulnerability research and
coordinated disclosure, CTFs and education, interoperability, software preservation, and
debugging or auditing software you own or are authorised to examine.

## What it does not help with

As with REA itself, the studio is not for, and its Claude instructions decline:

- circumventing DRM, licence checks, anti-cheat systems or other technical protection measures;
- developing game cheats or tampering with online services;
- bypassing authentication, bot protection or access controls on systems you are not authorised
  to test, or accessing other people's accounts or data;
- copying proprietary software or reproducing its protected code and assets;
- evading the usage policies or safety measures of AI model providers;
- reselling or sharing AI accounts or API access.

## Your responsibility

- **Authorisation is yours to establish.** The studio does not check that you may analyse a
  target. Having a file, or being able to open a site, does not by itself give permission for
  every operation. Reverse engineering may be restricted by law where you are, or by the
  licence of the software you analyse.
- **Website subjects load a live site.** Observing a website means the studio opens it in a
  browser it controls, and REA's web tools read its pages, scripts and traffic. Have the site
  operator's permission for what you observe, and follow its terms.
- **Some tools change things.** REA marks some tools as able to change state: they can run a
  program, change a connected device, or write files. The studio asks you to confirm before it
  runs one from the Tools page, and Claude may run them only if you turn that on in Settings.
  Review what you start, and use a separate machine or VM for targets you do not trust: neither
  REA nor this studio is a sandbox.
- **Claude sends data to Anthropic.** When you ask Claude, your question, the conversation and
  every tool result Claude reads (strings, decompiled code, page text) are sent to Anthropic
  under your Claude account and its data settings. So is any file Claude opens: it can read the
  subject's work folder and the folder that holds the target (only the target itself when that
  folder is a general one such as Downloads, Desktop, Documents or your home folder). Keep a
  target in a folder of its own, and do not ask Claude about material you may not share with a
  third party. The Tools page runs REA on your PC and sends nothing to Claude or Anthropic,
  though tools marked "may reach outside" can talk to a connected device, a browser or website,
  or a program they start.
- **Results can be wrong.** Decompilation, heuristics and AI-assisted analysis can be
  incomplete or incorrect. Check the evidence and its stated limitations before relying on a
  conclusion. An analysis result gives you no rights to the original program or its output.

## No warranty

This software is provided "as is", without warranty of any kind, under the MIT License
([LICENSE](LICENSE)). REA, Claude Code and the analysis backends have their own licences and
terms.
