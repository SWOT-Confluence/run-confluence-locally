"""Deterministic run monitoring for Confluence runs.

- gate:   synchronous checks the driver runs after canary arrays, batches and modules
- watch:  the monitor, a background process of the driver job
- finish: the end-of-run audit job (afterany on the driver)
- audit:  post-run meta-validation (coverage funnel, integrity, downloads, jobs, baseline diff)
- guide:  RUN_GUIDE.md, a briefing for people reviewing a run
- status / locate: quick views of a run

CLI: python -m confluence.monitor --help
"""
