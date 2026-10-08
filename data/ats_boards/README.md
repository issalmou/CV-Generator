# Bundled ATS board lists

One file per slug-based ATS provider. Each line is **one public board token**
(the slug in `boards.greenhouse.io/<token>`, `jobs.lever.co/<token>`,
`jobs.ashbyhq.com/<token>`, `jobs.smartrecruiters.com/<Token>`,
`ats.rippling.com/<token>`). Blank lines and `#` comments are ignored.

These are **best-effort** lists of companies known to expose a public board on
that ATS. They are **not** guaranteed live — a company may have migrated ATS or
changed its slug. A token that 404s / is blocked is **silently skipped** by the
provider (only *every* board failing marks the provider unavailable), so a stale
entry costs one wasted request, nothing more.

**Never add a fabricated slug.** If you cannot confirm a company has a public
board on this ATS, do not add it.

## Precedence & merging

For provider `X`, the effective board list is, in order, de-duplicated
(case-insensitively) and capped at `ATS_MAX_BOARDS`:

1. this bundled file — unless `ATS_USE_BUNDLED_BOARDS=false`
2. the file at `X_BOARDS_FILE` (operator's own list), if set
3. the `X_BOARDS` env var (CSV) — operator additions

Every token is validated (`^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`, or the ATS's
case rule) before it is used; an invalid one is dropped with a log line.

## Workday / Oracle HCM / TalentBrew / Phenom

Not listed here — those are **tenant-specific** (a full board URL or a bespoke
POST payload per company). Configure them via `WORKDAY_TENANTS`,
`ORACLE_HCM_SITES`, `TALENTBREW_BOARDS`, `PHENOM_BOARDS` (or the matching
`*_FILE`).
