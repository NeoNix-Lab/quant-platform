# Server/deck compatibility rule

Status: operational rule of ADR-0072 section 6 (DG-K2 atom `S06`), applying ADR-0066 section 1 and ADR-0067 section 3
per message family.

## The rule

1. **Use.** A deck can use a server when, for **every message family the deck speaks**, the version it speaks is the
   version the server composes. Compatibility is a wire-family commitment (ADR-0066 section 1: a change to a required
   field or to the meaning of an accepted family needs a new family version; ADR-0067 section 3: an unexpected version is a
   local typed `wire_incompatible`, never a downgrade or a probe).
2. **Accepting a result.** The server additionally accepts a result only when the deck's commit equals the server's commit
   (ADR-0072 section 5: `deck_code_identity` and `RunIdentity.code_identity` equal the manifest's `git_identity`, which is the
   commit the API recorded at startup).
3. **The package version decides nothing.** `pyproject.toml`'s version, a tag name or a release label is not evidence of
   compatibility in either direction.

Rule 1 is checked by the wire: a family or version the server does not compose is refused. Rule 2 is checked twice: by the
deck's workload module before any work (it refuses a tree that is dirty or not at the manifest's commit) and by the server's
import (it refuses a bundle whose code identity is not the commit it recorded).

## The families

The versions below are the ones in the code at this repository's commit; the code is the authority, this table is a reading aid.

| Family | Version | Spoken by the deck handoff | Constant |
| --- | --- | --- | --- |
| Admitted input | `j02-admitted-input-v2` | yes | `ADMITTED_INPUT_FAMILY` in `clients/deck/deck_handoff_client.py` |
| Result import | `j02-result-import-v1` | yes | `RESULT_IMPORT_FAMILY` |
| Framed results (J14) | `j14-framed-result-v1` | yes (a transport, see below) | `J14_FAMILY` |
| Strategy | `j02-strategy-v1` | no | `J02_STRATEGY_FAMILY` in `src/quant_platform/application/strategy_transport.py` |
| Validation | `j02-validation-v1` | no | `J02_VALIDATION_FAMILY` in `validation_transport.py` |
| Training | `j02-training-v1` | no | `J02_TRAINING_FAMILY` in `training_transport.py` |
| Job | `j02-job-v1` | no | `J02_JOB_FAMILY` in `job_transport.py` |

A server composes the application families named in `QP_API_FAMILIES` (`strategy`, `validation`, `training`, `jobs`,
`admitted-input`, `result-import`); a family it does not compose is unsupported for every deck. `j14-framed-result-v1` is
**not** a value of `QP_API_FAMILIES` (it is refused as an unknown family): it is the framed-transfer transport, composed
implicitly with `admitted-input`, which is what the deck's upload and result delivery use.
`j02-admitted-input-v1` was withdrawn (ADR-0064 Amendment 1) and is accepted by no server.

## What an operator checks

- **Before a deck is used against a server:** the application families the deck's client speaks (`admitted-input` and
  `result-import`) are in the server's `QP_API_FAMILIES`, at the versions above; J14 needs no entry of its own, it comes with `admitted-input`. A mismatch is refused on the first call (the client raises `WireIncompatible` for a reply it cannot accept).
- **Before a run:** the deck's checkout is the server's commit (`git rev-parse HEAD` on both, or the `git_identity` of an
  admission). After a promotion of the server, make a new admission: an earlier one names the old commit.
- **Not a check:** the package version, the Python version of either side, or the date of the last deployment.

## Not covered

A deck on a commit other than the server's is not supported in v1 (ADR-0072 section 5 defers it until a workload needs it, and
it would need a required field and so a new family version). Compatibility between two servers, or between several decks and one
server beyond the above, is not addressed.
