# Market Inference Methodology

## Why Exact Location Is Unavailable

LinkedIn's bulk data export (`Connections.csv`) contains only these fields:

| Field | Example |
|-------|---------|
| First Name | João |
| Last Name | Silva |
| URL | linkedin.com/in/joaosilva |
| Email Address | (only if shared) |
| Company | Accenture |
| Position | Senior Data Engineer |
| Connected On | 2024-03-15 |

**Location (city, country, region) is never included.** It is only visible on
individual profile pages and cannot be bulk-exported under LinkedIn's Terms of Service.

This is a fundamental platform limitation, not a bug in this tool.

---

## How Market Inference Works

Since location is unavailable, this tool infers a **likely opportunity market**
based on available signals. The inference is not about where the person lives —
it's about which market context their connections suggest.

### Inference Hierarchy

```
Priority 1 (confidence 0.95): Manual company override
  └─ config/company_market_overrides.yml
  └─ outputs/company_market_mapping_template.csv (user-filled)

Priority 2 (confidence 0.95): Manual CSV file
  └─ User fills the manual_market column in mapping template

Priority 3 (confidence 0.85): Company keyword match
  └─ "agileengine" → LATAM_USD
  └─ "madrid" in company name → SPAIN_EU

Priority 4 (confidence 0.75): Title/position keyword match
  └─ "nearshore Canada" in title → US_CANADA_NEARSHORE
  └─ "Spain" in title → SPAIN_EU

Priority 5 (confidence 0.70): Global company category
  └─ Accenture → GLOBAL_CONSULTING
  └─ AgileEngine → GLOBAL_STAFFING
  └─ Snowflake → GLOBAL_TECH

Priority 6 (confidence 0.00): UNKNOWN
  └─ No signal found in company or title
```

### V2 Market Labels

| Market | Meaning |
|--------|---------|
| `BRAZIL` | Connections with strong Brazil company/title signals |
| `LATAM_USD` | LATAM companies paying USD / nearshore staffing platforms |
| `US_CANADA_NEARSHORE` | US/Canada companies with LATAM nearshore hiring patterns |
| `SPAIN_EU` | Spain and Portugal specifically |
| `EUROPE` | Other European markets (Germany, Netherlands, Ireland, etc.) |
| `GLOBAL_STAFFING` | Staffing/nearshore companies operating in multiple markets |
| `GLOBAL_TECH` | Large tech companies hiring globally |
| `GLOBAL_CONSULTING` | Consulting firms with global delivery models |
| `UNKNOWN` | No geographic signal found |

### market_type Values

| Value | Meaning | Confidence |
|-------|---------|-----------|
| `MANUAL_COMPANY_OVERRIDE` | Company matched in company_market_overrides.yml | 0.95 |
| `MANUAL_FILE_OVERRIDE` | Company manually filled in mapping CSV | 0.95 |
| `COMPANY_KEYWORD` | Company name contains geographic keyword | 0.85 |
| `TITLE_KEYWORD` | Job title contains geographic keyword | 0.75 |
| `GLOBAL_COMPANY` | Known global company category | 0.70 |
| `UNKNOWN` | No signal | 0.00 |

---

## Why UNKNOWN Exists

UNKNOWN simply means the inference engine found no useful signal. It does NOT mean:
- The person is irrelevant
- The person is not in your target market
- The person should be excluded from outreach

A company named "Digital Ventures Solutions" gives zero geographic signal even if
it's a LATAM-focused nearshore firm based in Austin, TX. Their employees would appear
as UNKNOWN in your network.

**Key insight:** The UNKNOWN group contains people in all your target markets. They
just don't have visible geographic keywords in the data LinkedIn exports.

---

## How to Reduce UNKNOWN

### Method 1: Fill the Mapping Template (Recommended)

1. Open `outputs/company_market_mapping_template.csv`
2. This file contains the top 300 UNKNOWN companies by connection count
3. Fill the `manual_market` column for companies you recognize
4. Save the file
5. Re-run the pipeline — all matching companies will be reclassified

**Example:**
```
company_clean,connection_count,top_persona,manual_market
Pulse Client Experts,47,Recruiter,LATAM_USD
Digital Ventures,23,Data Engineer,BRAZIL
Stellar Tech Solutions,18,Recruiter,US_CANADA_NEARSHORE
```

### Method 2: Add to company_market_overrides.yml

Edit `config/company_market_overrides.yml` and add the company:
```yaml
overrides:
  pulse client experts: {market: LATAM_USD, category: GLOBAL_STAFFING}
```

This gives confidence 0.95 (highest possible).

---

## Why Scores Are Confidence-Adjusted

### The Problem with Raw Scores

A raw score counts all inferred connections regardless of confidence:
- A company matched only by a vague keyword gets the same weight as a manual override
- With 82% UNKNOWN, raw scores are inflated and unreliable

### The Solution: Adjusted Scores

Adjusted scores only count connections with confidence >= 0.70:
- Manual overrides (0.95) ✓
- Company keyword matches (0.85) ✓
- Title keyword matches (0.75) ✓
- Global company category (0.70) ✓
- UNKNOWN connections (0.00) ✗ (excluded)

### Capping Rules

| Condition | Rule |
|-----------|------|
| UNKNOWN% >= 50% | Cap adjusted scores (they are estimates) |
| USD high-conf contacts < 30 | Cap USD adjusted score at 70 |
| Spain high-conf contacts < 20 | Cap Spain adjusted score at 60 |

---

## Improving Inference Over Time

Each time you:
1. Add a manual override → 1-50 connections reclassified
2. Fill mapping template rows → 10-300 connections reclassified
3. Export fresh LinkedIn data → new connections analyzed

Run the full pipeline weekly:
```bash
python src/build_network_heatmap.py
python src/build_strategy_layer.py
python src/generate_static_dashboard.py
```

As you grow your network with more targeted connections (LATAM USD recruiters,
Spain/EU recruiters), the high-confidence count increases naturally, and adjusted
scores become more meaningful.
