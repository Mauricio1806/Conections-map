Continue from the existing project. Do not rebuild from scratch.

This is a surgical corrective patch for EXACTLY THREE unfinished items.

Do not respond with a plan.
Do not respond with "I kicked off a background agent".
Do not only modify backend files and claim success.
Implement the changes now, run the pipeline, inspect the generated public dashboard data, validate the UI wiring, then commit and push only if acceptance criteria pass.

CURRENT PROBLEMS VERIFIED VISUALLY:

1. Lead Reactivation:
   Backend intelligence improved from 347 to 81 Needs My Response, but the UI still has no practical filtering.
   KPI cards are static.
   Clicking "Needs My Response", "Hot Reactivation", "Warm Reactivation", etc. does nothing.
   There is no useful filter bar to retrieve the contacts behind each KPI.

2. Company Mapping:
   Needs Company Mapping only changed:
   2,499 -> 2,273
   23.2% -> 21.1%

   This is only 226 contacts resolved.
   The residual is still far too high.
   A stronger multi-pass company resolution engine is required.

3. Top Contacts:
   The page already visually has a dropdown labeled "OUTREACH".
   Do NOT add a duplicate filter.
   Fix/rename/populate it as a real "Outreach Status" filter using actual outreach_status values from the dataset.

STRICT PRESERVATION RULES:

- Keep Opportunity Bucket V5 architecture.
- Keep Lead Reactivation message intelligence.
- Keep Top Contacts ranking.
- Keep outreach_adjusted_score.
- Keep 90% LATAM/USD + 10% Spain/EU strategy.
- Keep Action Plan.
- Keep privacy logic.
- Keep fail-safe runtime architecture.
- Keep mobile responsiveness.
- Do not expose raw messages.
- Do not expose email.
- Do not expose phone.
- Do not expose attachments.
- messages.csv remains private and gitignored.
- Production remains static GitHub Pages:
  docs/index.html

Production URL:
https://mauricio1806.github.io/Conections-map/

(See PART 1 through PART 22 as specified by the user for full detail: real client-side Lead Reactivation filter bar + clickable KPI cards + result counts + table columns; Company Resolution V7 multi-pass fixpoint engine with fuzzy clustering, same-company propagation tiers, company-level message evidence aggregation, category/persona-cohort fallback, Pareto backlog, and honest before/after metrics; Top Contacts existing OUTREACH dropdown renamed to "Outreach Status" and populated from real outreach_status values with working filtering. Programmatic acceptance assertions must pass before commit; commit and push only then.)
