<!--
Keep this template's structure even if you trim the prose. The Verification
and Governance-note sections exist because reviewers on this repo check them
directly — an empty or missing one is treated as "not verified"/"not
considered", not "not applicable".
-->

## Summary

- <what changed>
- <why>

Closes #<issue> <!-- delete this line if there is no issue to close -->

## Governance note

<!--
Only needed if this PR touches SCOPE.md, docs/product/ROADMAP.md,
docs/product/CAPABILITY_MAP.md, docs/product/CAPABILITY_DAG.md, or
docs/architecture/OPEN_DECISIONS.md (AGENTS.md's governance boundary).
State the explicit governance-only authorization this PR relies on (an
issue using the governance-closeout template, or "this PR is that
governance-only issue"). Delete this whole section if the PR does not
touch those files.
-->

## Verification

<!-- List the exact commands you ran and their result, not just "tests pass". -->

- `python -m pytest <paths>` — <result>
- `python -m compileall -q src tests tools`
- `python tools/check_markdown_links.py`
- `git diff --check`

## Notes

<!-- Anything a reviewer needs but doesn't fit above: known limitations
intentionally deferred to a follow-up issue, base-branch rationale if not
`main`, etc. Delete if empty. -->
