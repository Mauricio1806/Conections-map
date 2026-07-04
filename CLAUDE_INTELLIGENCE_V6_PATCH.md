Continue from the existing project. Do not rebuild from scratch.

This is a focused intelligence and UX improvement patch for exactly three areas:

1. Lead Reactivation filtering and "Needs my response" accuracy
2. Reduce Needs Company Mapping using cross-contact and message intelligence
3. Improve Action Plan search filter recipes

Do not change unrelated business logic.

STRICT PRESERVATION RULES:
- Do not change Opportunity Bucket V5 architecture except to improve mapping resolution.
- Do not change the existing strategic 90% LATAM/USD + 10% Spain/EU short-term allocation.
- Do not remove Lead Reactivation.
- Do not remove Top Contacts.
- Do not remove outreach_adjusted_score.
- Do not change privacy rules.
- Do not expose raw messages.
- Do not expose email addresses.
- Do not expose phone numbers.
- Do not expose attachments.
- messages.csv remains local/private/untracked.
- Production remains static GitHub Pages:
  docs/index.html
- Production URL:
  https://mauricio1806.github.io/Conections-map/

============================================================
PART 1 — FIX "NEEDS MY RESPONSE" CLASSIFICATION
============================================================

Current problem:
The Lead Reactivation page shows 347 "Needs my response".

This is likely inflated because the current logic appears to rely too heavily on:
- last sender = other person

That is not enough.

A message from the other person may be:
- generic thanks
- emoji
- auto-reply
- career site redirect
- process closure
- rejection
- old irrelevant message
- "keep in touch"
- informational only

Improve src/message_intelligence.py and related lead reactivation logic.

Create explicit response intelligence fields:

- needs_my_response
- needs_response_confidence
- needs_response_reason
- response_intent_score
- last_message_is_substantive
- last_message_is_question
- last_message_is_request
- last_message_is_auto_reply
- last_message_is_generic_ack
- last_message_is_process_closure
- last_message_is_opportunity_signal
- manual_review_required
- last_sender_type
- conversation_recency_band

Do not expose raw message content publicly.

============================================================
PART 2 — RESPONSE INTENT DETECTION
============================================================

"Needs my response — Confirmed" should require:

A. Other person is last sender

AND

B. Message contains a substantive actionable signal, for example:

Question / request patterns:
- ?
- can you
- could you
- would you
- please send
- please share
- let me know
- confirm
- available
- availability
- when are you available
- what time
- schedule
- calendar
- meeting
- call
- interview
- screening
- technical interview
- send your CV
- send your resume
- share your CV
- share your resume
- updated CV
- updated resume
- compensation
- salary expectation
- notice period
- start date
- interested
- are you interested
- location
- relocate
- contractor
- citizenship
- work authorization

Portuguese:
- você pode
- poderia
- me envie
- compartilhe
- confirma
- disponibilidade
- quando você pode
- agenda
- reunião
- entrevista
- currículo
- pretensão
- aviso prévio
- início
- tem interesse

Spanish:
- puedes
- podrías
- envíame
- comparte
- confirma
- disponibilidad
- cuándo puedes
- agenda
- reunión
- entrevista
- CV
- salario
- interesado

Classify:
needs_response_confidence = HIGH

============================================================
PART 3 — DO NOT MARK AS NEEDS RESPONSE
============================================================

Do NOT classify as Needs my response when last message is only:

Generic acknowledgements:
- thanks
- thank you
- great
- perfect
- sounds good
- noted
- okay
- ok
- welcome
- my pleasure
- keep in touch
- best wishes
- good luck

Portuguese:
- obrigado
- obrigada
- perfeito
- combinado
- beleza
- sucesso
- boa sorte

Spanish:
- gracias
- perfecto
- entendido
- suerte

Emoji-only or reaction-only:
- ❤️
- 👍
- 🙏
- 👏
- 😊
- similar reaction-only content

Auto replies:
- automatic reply
- auto reply
- high volume
- apply via career site
- submit your CV
- talent database
- profiles cannot be reviewed here
- due to high volume

Process closure:
- unfortunately
- not selected
- decided to move forward
- process closed
- position closed
- não avançamos
- não seguiremos
- encerramos
- otra persona
- posición cerrada

For these:
needs_my_response = false

Unless a separate actionable instruction exists.

============================================================
PART 4 — RECENCY LOGIC
============================================================

Use conversation age.

Suggested bands:
- 0–7 days = CURRENT
- 8–30 days = RECENT
- 31–90 days = AGING
- 91–180 days = STALE
- >180 days = HISTORICAL

Rules:

Confirmed Needs Response:
- substantive request/question
- preferably <= 90 days

If > 90 days:
Do not call it "Needs my response" by default.
Classify as:
- Stale unanswered
- Dormant warm
- Historical reactivation candidate

If > 180 days:
Only keep actionable if there was:
- interview
- CV request
- role shared
- meaningful recruiter interaction
- previous process
- hiring manager interaction

============================================================
PART 5 — NEW LEAD REACTIVATION CATEGORIES
============================================================

Create/refine:

- Needs my response — Confirmed
- Needs my response — Likely
- Ambiguous — Review
- Hot reactivation
- Warm reactivation
- Dormant warm
- Career site follow-up
- Previous process reusable
- Follow-up candidate
- No response
- Closed / no action
- Ignore

Create a confidence score:
0–100

Example:
90–100 = confirmed
70–89 = likely
45–69 = ambiguous review
<45 = no immediate response required

============================================================
PART 6 — LEAD REACTIVATION PAGE FILTERS
============================================================

The current page needs real filters.

Add a filter bar with:

- Search name/company
- Conversation status
- Lead category
- Lead temperature
- Persona
- Opportunity bucket
- Last sender
- Needs my response:
  - All
  - Confirmed only
  - Likely
  - No
- Replied to me:
  - All
  - Yes
  - No
- Ghosted me:
  - All
  - Yes
  - No
- Auto reply only:
  - All
  - Yes
  - No
- Positive signal:
  - All
  - Yes
  - No
- Previous interview/process:
  - All
  - Yes
  - No
- Days since last message:
  - 0–7
  - 8–30
  - 31–90
  - 91–180
  - 180+
- Minimum reactivation score
- This Week Queue only

Add:
- Apply
- Reset

Filters must work client-side on the static GitHub Pages dashboard.

============================================================
PART 7 — LEAD TABLE IMPROVEMENTS
============================================================

Add sanitized fields to the Lead Reactivation table:

- Name
- Company
- Persona
- Opportunity bucket
- Category
- Temperature
- Status
- Last sender
- Last message date
- Days since last message
- Needs response confidence
- Reactivation score
- Recommended action
- Why action
- LinkedIn URL

Do not expose raw message content.

Optional:
Show a very short sanitized intent label:
- Asked for availability
- Asked for CV
- Interview scheduling
- Generic acknowledgement
- Auto reply
- Opportunity discussion
- No response
- Process closed

============================================================
PART 8 — CREATE AMBIGUOUS MANUAL REVIEW QUEUE
============================================================

Do not force every conversation into a confident category.

Create:
outputs/message_review_queue.csv

Fields:
- other_person_name
- company_clean
- persona
- last_message_date
- days_since_last_message
- inferred_status
- needs_response_confidence
- response_intent_score
- sanitized_intent_label
- reason
- manual_status
- manual_action

Only include ambiguous cases.

Target:
User should manually review a small queue, not 3,423 conversations.

============================================================
PART 9 — REDUCE NEEDS COMPANY MAPPING
============================================================

Current:
Needs Company Mapping = 2,499

Improve this substantially.

Do not invent exact geographic location.

Goal:
Reduce Needs Company Mapping by using cross-contact evidence.

Create/update:
src/company_resolution_v6.py
or integrate into existing opportunity engine cleanly.

Add these fields:
- company_resolution_source
- company_resolution_confidence
- company_evidence_count
- company_dominant_bucket
- company_dominant_bucket_share
- cross_contact_propagation_used
- message_signal_used
- manual_review_required

============================================================
PART 10 — SAME-COMPANY PROPAGATION
============================================================

For every normalized company:

Aggregate all contacts from the same company.

If other contacts at the same normalized company have high-confidence opportunity buckets:

Example:
Company X:
- 12 contacts total
- 8 classified LATAM_USD
- 2 GLOBAL_CONSULTING
- 2 NEEDS_MAPPING

Do not keep the 2 unresolved automatically.

Use dominant bucket propagation when:

- at least 2 high-confidence classified contacts
AND
- dominant bucket share >= 70%

Then assign unresolved contacts:
opportunity_bucket = dominant bucket
confidence = 0.72
reason = "same-company dominant bucket propagation"

If:
- support >= 5 contacts
- dominant share >= 80%

confidence = 0.80

Do not propagate when evidence is highly mixed.

============================================================
PART 11 — COMPANY ALIAS PROPAGATION
============================================================

Strengthen company normalization.

Examples:
- NTT DATA Europe & LATAM
- NTT DATA Brasil
- NTT Data
=> NTT DATA

- Tata Consultancy Services
- TCS
=> TCS

- Hays
- Hays Talent Solutions
=> HAYS

- Michael Page
- PageGroup
=> PAGEGROUP

- Randstad Brasil
- Randstad
=> RANDSTAD

- Capgemini Engineering
- Capgemini
=> CAPGEMINI

- Accenture Brasil
- Accenture
=> ACCENTURE

Preserve region tokens before alias normalization for evidence.

============================================================
PART 12 — USE MESSAGE HISTORY AS LOCAL MAPPING EVIDENCE
============================================================

Use messages.csv locally/private only.

Do not publish raw content.

For contacts in Needs Mapping, inspect message history for regional opportunity signals:

LATAM:
- LATAM
- Latin America
- South America
- Brazil
- Brasil
- Argentina
- Colombia
- Mexico
- Chile
- Uruguay
- Peru
- nearshore

US/Canada:
- USA
- US role
- United States
- Canada
- US timezone
- EST
- CST
- contractor
- USD

Spain/EU:
- Spain
- España
- Madrid
- Barcelona
- Portugal
- Lisbon
- Lisboa
- Porto
- Europe
- EU
- Germany
- Netherlands
- Ireland

Use only as supporting evidence.

Do not expose matched raw text publicly.

Add reason examples:
- "message history contains LATAM opportunity signal"
- "message history contains US contractor signal"
- "message history contains Spain/EU signal"

Confidence:
0.60–0.75 depending on number and strength of signals.

============================================================
PART 13 — USE PERSONA + COMPANY CATEGORY FALLBACK
============================================================

If region remains unknown but company category is clear:

Recruiting/staffing:
=> GLOBAL_STAFFING

Consulting:
=> GLOBAL_CONSULTING

Technology/product:
=> GLOBAL_TECH

Strategic persona with meaningful professional context:
=> GLOBAL_OPPORTUNITY

Only keep NEEDS_COMPANY_MAPPING when:
- company exists
- category unclear
- cross-contact evidence inconclusive
- message signal absent
- no region/title evidence

============================================================
PART 14 — NEEDS MAPPING TARGET
============================================================

Current count:
2,499

Goal:
Reduce it substantially without fabricating geography.

Target:
- preferably below 10% of total network
- aspirational 5–10%
- if target cannot be reached honestly, report exact reason

Do not fake classification just to hit the number.

Generate:
outputs/company_resolution_v6_audit.csv
outputs/company_mapping_backlog_v6.csv
outputs/company_propagation_audit.csv

============================================================
PART 15 — DATA QUALITY UPDATE
============================================================

Update Data Quality cards:

- Opportunity Bucket Coverage
- High Confidence Classification
- Medium Confidence Classification
- Needs Company Mapping
- Low Value Unresolved
- Cross-Contact Resolved
- Message-Assisted Resolved
- Manual Mapping Remaining

Show how many contacts were resolved by:
- exact dictionary
- title/company keyword
- company category
- same-company propagation
- message signal
- language signal
- manual override

This makes Data Quality transparent.

============================================================
PART 16 — ACTION PLAN SEARCH FILTER RECIPES
============================================================

Current Action Plan search packs are improved, but filter guidance is still too generic.

For every search tier:
- Broad
- Precision
- Persona
- Company

Add its own specific filter recipe.

Do NOT use the same filter text for all four tiers.

Example LATAM Recruiters:

BROAD:
Query:
data engineer recruiter LATAM

Filters:
- People
- 2nd degree
- Locations: Brazil, Argentina, Colombia, Mexico
- Do not restrict company
Purpose:
market discovery

PRECISION:
Query:
"Data Engineer" AND Recruiter AND LATAM

Filters:
- People
- 2nd degree
- Locations: Brazil, Argentina, Colombia, Mexico, Chile
- Current company: staffing, consulting, nearshore firms
- Actively hiring when available
Purpose:
daily outreach

PERSONA:
Query:
"Talent Acquisition" AND "Data Engineer" AND LATAM

Filters:
- People
- 2nd degree
- Job title/persona focus: Talent Acquisition, Technical Recruiter, IT Recruiter
- Locations: LATAM countries
Purpose:
high-quality recruiter targeting

COMPANY:
Query:
Recruiter AND "Data Engineer" AND Hays

Filters:
- People
- 2nd degree
- Current company: exact target company
- Optional location: target region
Purpose:
account-based networking

============================================================
PART 17 — FILTER RECIPE FIELDS
============================================================

Each search option must display:

- Search tier
- Query
- Purpose
- People/Jobs search type
- Connection degree
- Location filters
- Current company filters
- Industry suggestions
- Actively hiring suggestion when available
- Expected persona
- Expected precision
- When to use
- Open Search
- Copy Query

Add a compact expandable section:
"Recommended LinkedIn Filters"

============================================================
PART 18 — SMART SEARCH ADVICE
============================================================

Add a practical note:

"Do not encode every criterion into the keyword query. Use the query for intent and LinkedIn filters for degree, geography, company and persona refinement."

Add per-tier recommendations:

Broad:
Use for discovery only.

Precision:
Default daily search.

Persona:
Use when Broad/Precision results contain irrelevant profiles.

Company:
Use for account-based networking and known target firms.

============================================================
PART 19 — PRESERVE CURRENT STRATEGY
============================================================

Keep:

Next 60 days:
- 90% LATAM/USD / South America / US-nearshore
- 10% Spain/EU exploratory

Action Plan week logic remains.

Do not change strategic allocation.

============================================================
PART 20 — MOBILE RESPONSIVENESS
============================================================

All new filters must work on mobile.

Lead Reactivation filters:
- stack vertically <= 768px
- full width controls
- touch-friendly buttons

Search filter recipes:
- expandable
- query wraps
- no page-wide overflow

============================================================
PART 21 — RUNTIME SAFETY
============================================================

Do not reintroduce global fatal errors.

Preserve:
- fail-safe runtime
- extension error suppression
- fatalAppError architecture
- loading watchdog

Run:
python src/privacy_check.py
python src/validate_static_dashboard_runtime.py

If node exists:
node --check docs/assets/app.js

============================================================
PART 22 — RUN
============================================================

Run:

python src/build_network_heatmap.py
python src/build_strategy_layer.py
python src/generate_static_dashboard.py
python src/privacy_check.py
python src/validate_static_dashboard_runtime.py

============================================================
PART 23 — VERIFY
============================================================

Verify:

LEAD REACTIVATION:
- Filters work
- Needs my response can be filtered
- Confirmed vs Likely exists
- Generic acknowledgements are not Needs Response
- Auto replies are not Needs Response
- Old stale conversations are not automatically Needs Response
- Ambiguous review queue exists

MAPPING:
- Needs Company Mapping reduced honestly
- Same-company propagation works
- Company aliases work
- Message signals assist classification
- Audit outputs exist
- Hays remains correctly classified
- NTT DATA correctly normalized
- No fake exact geography is invented

ACTION PLAN:
- Broad/Precision/Persona/Company remain
- Each tier has its own filter recipe
- Filters are visible and useful
- Queries stay short
- People search URLs work

PRIVACY:
- no raw messages
- no emails
- no phone numbers
- no attachments
- messages.csv not committed

RUNTIME:
- dashboard loads
- fail-safe remains active
- validation passes

============================================================
PART 24 — COMMIT AND PUSH
============================================================

git add .
git commit -m "improve lead response intelligence company mapping and search filters"
git push
