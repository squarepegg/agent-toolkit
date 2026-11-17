# 0004: Send only `triggers[]` to Vendor, with no legacy fallback

- **Status:** Accepted (2026-07-28; supersedes the dual-send option below)
- **Date:** 2026-10-14 (recorded after the fact by decision-capture)
- **Deciders:** the platform team (Offers)
- **Constraints:** `VendorHttpClientTest` "request body contains only triggers, never a legacy single-trigger key"

## Context

A trigger now resolves to a version family (`….level_up.v1`, `….v2`), so v1-authored offers
keep resolving after v2 ships, with no republish. Vendor gained native `triggers[]` = ANY
matching in a recent Vendor release (`FetchPackageSnapshotDynamicContentBodyDto.triggers`).
Older Vendor accepts only a singular `trigger`. Offers and Vendor deploy independently.

## Options considered

1. N parallel legacy calls (one per family member) when Vendor lacks `triggers[]` (the frozen contract).
2. Dual-send: `triggers[]` plus the newest member as singular `trigger`.
3. Send only `triggers[]`. Vendor must deploy first in every shared environment.

## Decision

Option 3. One request carries the whole family. There is no runtime fallback; deploy order is the safeguard.

## Rejected alternatives

- **1:** no capability detection exists. Inferred, not stated: N calls per evaluation also multiply
  Vendor load and latency against the 200 ms p99 budget.
- **2:** on old Vendor, non-newest offers silently disappear during rollout. It also broke the
  contract rule "never send singular and plural together" (adversarial review H2).

## Assumptions

- Every shared environment runs Vendor with that release before this Offers version.
- Vendor is never rolled back below that release while this version is live.
- Newer trigger schemas are additive supersets of older ones (validation uses the newest).

## What would make this wrong

- Any environment where Offers deploys ahead of Vendor. That family then returns zero offers.
  Watch the no-offer and fail-open rate per trigger after each deploy.
- A Vendor rollback, or a second Vendor deployment, that lacks `triggers[]`.
- A consumer that still needs the singular `trigger` field.
