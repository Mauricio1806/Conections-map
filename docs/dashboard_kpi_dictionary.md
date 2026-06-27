# KPI Dictionary — LinkedIn Network Intelligence Dashboard

This document defines every KPI visible in the dashboard.

---

## Core Network KPIs

| KPI | Label in Dashboard | Formula | Interpretation |
|-----|-------------------|---------|---------------|
| `total_connections` | Total Connections | `count(classified_connections)` | All valid rows after cleaning |
| `high_priority` | High Priority | `count(score >= 70)` | Most strategically valuable contacts |
| `medium_priority` | Medium Priority | `count(40 <= score < 70)` | Worth maintaining, not urgent |
| `low_priority` | Low Priority | `count(score < 40)` | Deprioritize or archive |
| `high_priority_pct` | High Priority % | `high / total * 100` | Benchmark: >5% is excellent |

---

## Persona Group KPIs

| KPI | Personas Included | Why It Matters |
|-----|------------------|----------------|
| `recruiters_total` | Recruiter, Sourcer | Direct job pipeline — recruiters share your profile with hiring managers |
| `talent_hr_total` | Talent Acquisition, HR | Secondary pipeline — they know open roles and decision-makers |
| `hiring_managers_total` | Hiring Manager, Engineering Manager | Decision-makers who approve job offers |
| `data_leaders_total` | Data Engineering Manager, Head of Data, Director, Executive | Highest-value referrals and direct hiring authority |
| `data_peers_total` | Data Engineer, Analytics Engineer, Data Analyst, BI Analyst, Data Scientist, ML/AI | Peers — valuable for referrals, not for direct hiring |

---

## Market KPIs

| KPI | Market Definition | Strategic Relevance |
|-----|------------------|---------------------|
| `brazil_count` | Brazil-based company signals | Local network — low USD job potential but useful for referrals |
| `latam_usd_count` | LATAM companies paying USD or nearshore platforms | **HIGH** — short-term USD job priority |
| `us_nearshore_count` | US/Canada companies with LATAM contractor culture | **HIGH** — short-term USD job priority |
| `spain_eu_count` | Spain and Portugal specifically | **MEDIUM** — medium-term Spain move |
| `europe_count` | Germany, Netherlands, Ireland, Romania, etc. | **MEDIUM** — medium-term Europe diversification |
| `unknown_count` | No market keyword matched | **DATA LIMITATION** — see Data Quality page |
| `unknown_pct` | unknown / total * 100 | Normal to be 70-85% given LinkedIn export limitations |

---

## Strategic Scores

### USD Opportunity Score (0-100)

Measures how ready your network is to help you land a remote USD job from Brazil.

```
score = min(100,
  min(30, recruiters_in_usd / 60 * 30) +
  min(20, ta_in_usd / 40 * 20) +
  min(20, hiring_mgrs_in_usd / 30 * 20) +
  min(15, data_leaders_in_usd / 20 * 15) +
  min(15, high_priority_usd / 30 * 15)
)
```

| Score | Interpretation |
|-------|---------------|
| 0-20 | Not ready — significant outreach needed |
| 21-40 | Early stage — foundation exists but gaps are large |
| 41-60 | Developing — active USD opportunities possible |
| 61-80 | Strong — ready for active job search |
| 81-100 | Excellent — multiple USD opportunities likely |

### Spain Readiness Score (0-100)

Measures how ready your network is for a Spain/Europe relocation.

| Score | Interpretation |
|-------|---------------|
| 0-20 | Not started — begin Spain/EU network now |
| 21-40 | Early stage — some contacts, needs significant growth |
| 41-60 | Developing — visible in Spain/EU market |
| 61-80 | Strong — ready for active Spain/EU job search |
| 81-100 | Excellent — Spain/EU network comparable to local candidates |

### Market Readiness Score (0-100)

```
market_readiness = usd_opportunity_score * 0.6 + spain_readiness_score * 0.4
```

Weighted 60/40 because your short-term priority (USD) is more urgent than medium-term (Spain).

---

## Priority Score (per connection, 0-100)

| Factor | Weight | Description |
|--------|--------|-------------|
| Persona relevance | 35 pts | Recruiter=35, TA/Head of Data=33-28, Others graduated down |
| Strategic market | 30 pts | US_CANADA_NEARSHORE=30, LATAM_USD=28, down to UNKNOWN=3 |
| Seniority level | 15 pts | Executive/Founder=15, Director=13, Manager=12, down |
| Recency | 10 pts | <30 days=10, <90 days=7, <365 days=4, older=0 |
| Company signal | 10 pts | Tech/data/nearshore keywords in company name |

---

## Network Concentration Risk Flags

| Flag | Trigger | Recommended Action |
|------|---------|--------------------|
| Brazil-heavy | >20% of identified connections are Brazil | Maintain but stop growing generic Brazil network |
| High unknown market | >70% unknown | Expected — not a bug, LinkedIn limitation |
| Generic HR | >20% are HR/recruiting | Focus on strategic personas, not generic HR |
| Data peer heavy | >25% are data peers | Peers can't hire you — shift focus to leaders |

---

## Gap Analysis

| Column | Definition |
|--------|-----------|
| `current_count` | How many connections you have in this market/persona |
| `target_count` | Strategic target based on your goals |
| `gap_count` | `max(0, target - current)` |
| `gap_percentage` | `gap / target * 100` |
| `urgency_level` | Critical (>80%), High (60-80%), Medium (30-60%), Low (>0%), Saturated (0%) |

---

## Action Plan Timeframes

| Plan | Focus | Market Priority |
|------|-------|----------------|
| 30-Day | LATAM USD + US/CA Nearshore recruiters and hiring managers | LATAM_USD, US_CANADA_NEARSHORE |
| 60-Day | Extend USD pipeline + begin Spain/EU | All USD markets + SPAIN_EU |
| 90-Day | Full strategic coverage | All markets |
