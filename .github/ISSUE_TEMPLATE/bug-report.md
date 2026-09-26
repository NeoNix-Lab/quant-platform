---
name: "Bug report"
about: A concrete, reproducible defect in already-merged code (not a design question).
title: "[bug] <short description>"
labels: []
assignees: ''
---

## Summary

<One sentence: the claim that is wrong, and where.>

## Reproduction

<Exact steps, command, or minimal script that reproduces it. Include actual
vs. expected output. If this was found during code review, link the PR/
comment it came from instead of re-deriving it.>

## Impact

<Who/what is affected, and how severe — e.g. "silently drops real data with
no diagnostic" vs. "cosmetic log message".>

## Suggested fix

<If known. It's fine to leave this open-ended and let whoever picks it up
decide, but say so explicitly rather than leaving it blank.>

## Authority

<Which ADR/contract/SCOPE.md governs the correct behavior here, if any. If
none does — this is purely an implementation bug, not a semantic question —
say so, since that changes who needs to review the fix.>

## Linking (optional)

If this bug was found while working a specific scope/epic, add it to that
scope's **Milestone** and link it as a native GitHub **sub-issue** of the
epic — otherwise leave both unset; not every bug belongs to a Wave.
