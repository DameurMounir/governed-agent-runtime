# LangGraph integration security baseline

**Research date:** 2026-08-15
**Scope:** direct packages used by `agent/02-langgraph-governed-execution`

## Selected direct versions

| Package | Pin | Reason |
| --- | ---: | --- |
| `langgraph` | `1.2.10` | Current reviewed direct pin used by the implementation candidate; above the `1.0.10` msgpack advisory floor. |
| `langgraph-checkpoint` | `4.2.0` | Current reviewed direct pin; includes the JSON/checkpoint deserialization hardening published for CVE-2026-48775. |
| `langgraph-checkpoint-sqlite` | `3.1.1` | Current reviewed direct pin; above the `3.0.1` metadata-filter SQL-injection advisory floor. |

The CI verifies these exact direct versions after installation. Transitive packages are still
resolved by the Python package installer and remain subject to `pip-audit`; this branch does not
claim a cross-platform, cryptographically hashed lockfile.

## Reviewed advisory floors

| Advisory | Affected | Patched floor | Branch response |
| --- | --- | --- | --- |
| GHSA-g48c-2wqr-h844 / CVE-2026-28277 | `langgraph <= 1.0.9` | `1.0.10` | Pin `1.2.10`; strict msgpack mode. |
| GHSA-wwqv-p2pp-99h5 / CVE-2025-64439 | `langgraph-checkpoint < 3.0.0` | `3.0.0` | Pin `4.2.0`; no permissive JSON module loading. |
| GHSA-mhr3-j7m5-c7c9 / CVE-2026-27794 | `langgraph-checkpoint < 4.0.0` | `4.0.0` | Pin `4.2.0`; `pickle_fallback=False`; no cache backend. |
| GHSA-fjqc-hq36-qh5p / CVE-2026-48775 | `langgraph-checkpoint <= 4.1.0` | `4.1.1` | Pin `4.2.0`; builtins-only graph state. |
| GHSA-9rwj-6rc7-p77c | `langgraph-checkpoint-sqlite < 3.0.1` | `3.0.1` | Pin `3.1.1`; no untrusted metadata-filter interface. |

## Runtime controls

- `LANGGRAPH_STRICT_MSGPACK=true` in CI and documented operator commands;
- `JsonPlusSerializer(pickle_fallback=False)`;
- `allowed_json_modules=None` and `allowed_msgpack_modules=None`;
- JSON-safe builtins in checkpoint state;
- private checkpoint and journal paths;
- no dynamic module or plugin discovery;
- no LangGraph cache backend;
- no untrusted checkpoint search/filter API;
- resume decisions bound to the active thread, workflow, and step;
- a separate execution journal for handler idempotency and in-doubt blocking.

## Primary sources

- LangGraph PyPI project and release metadata;
- LangGraph Checkpoint PyPI project and serializer security guidance;
- LangGraph SQLite Checkpoint PyPI project;
- LangGraph interrupt and persistence documentation;
- GitHub Security Advisory Database and NVD entries named above.

These sources establish package behavior and minimum patched versions. They do not establish that
this repository is production-ready; that claim remains explicitly excluded.
