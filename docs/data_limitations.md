# Data Limitations

## What LinkedIn Exports — and What They Don't

### What You Get in Connections.csv

| Column | Availability | Notes |
|--------|-------------|-------|
| First Name | Always | May have special characters |
| Last Name | Always | May be truncated |
| URL | Always | LinkedIn profile URL |
| Email Address | Partial | Only if connection opted to share email |
| Company | Usually | Current employer only, may be blank |
| Position | Usually | Current job title only, may be blank |
| Connected On | Always | Date of connection acceptance |

### What You Do NOT Get

| Data Point | Why Missing | Impact |
|-----------|-------------|--------|
| Location (city/country) | Not exported by LinkedIn | Market inference relies on keywords only |
| Connection's industry | Not exported | Cannot filter by industry directly |
| Mutual connections | Not exported | Cannot analyze referral network |
| Message history | Not in this export | Cannot analyze engagement depth |
| Profile photo | Not exported | N/A for analysis |
| Previous companies | Not exported | Only current employer available |
| Skills | Not exported | Cannot match on skills |
| Education | Not exported | Cannot filter by university |
| Connection strength | Not exported | Cannot distinguish close vs. distant contacts |

---

## Market Inference Limitations

### How Market Is Inferred

Since location is not available, market inference uses:

1. **Company name keyword matching** — e.g., "AgileEngine" → LATAM_USD
2. **Job title keyword matching** — e.g., "LATAM" in title → LATAM_USD
3. **Brand signals** — e.g., "Deutsche Telekom" → EUROPE
4. **Language signals** (limited) — e.g., Spanish position titles

### Why ~82% Are "UNKNOWN"

This is expected and normal. Most company names and job titles contain no
explicit geographic signals. A company called "Pulse Client Experts" gives
no location signal even if it operates exclusively in Brazil.

### Confidence Levels

| Confidence | Meaning | How Assigned |
|-----------|---------|-------------|
| 0.0 | No inference | No keyword matched → UNKNOWN |
| 0.5 | Weak inference | 1 keyword matched |
| 0.7 | Medium inference | 2 keywords matched |
| 0.9 | Strong inference | 3+ keywords matched |

### Common False Positives

| Example | Classified As | Reality |
|---------|--------------|---------|
| Company contains "SAP" | EUROPE | May be a SAP partner in Bolivia |
| "Los Angeles" in name | US_CANADA_NEARSHORE | May refer to something else |
| "Vale" in company | BRAZIL | Could be Vale the mining company OR a different word |

### How to Interpret Gap Analysis

The classified subset (1,963 connections with market signal) represents a
**lower-bound estimate** of your strategic network. Many UNKNOWN connections
may actually be in your target markets — their data just didn't contain
enough keywords for inference.

**Do not assume:** UNKNOWN = irrelevant  
**Correct interpretation:** UNKNOWN = unclassified, may or may not be relevant

---

## What This Tool Cannot Do

- Scrape LinkedIn for additional data
- Access profile pages not in the export
- Detect location from language alone (unreliable)
- Access InMail or messaging history
- Access LinkedIn's People You May Know suggestions
- Access job posting data
- Access LinkedIn Premium signals

---

## Improving Data Quality

### Short-Term (without scraping)

1. **Manually tag high-priority connections** — add a column `notes` to the CSV
2. **Use LinkedIn search filters** to understand distribution (manual counting)
3. **Export fresh data regularly** — LinkedIn updates data every time you export

### Medium-Term

1. Consider LinkedIn's official API (requires developer access + approval)
2. Export and tag incrementally as you build the network

---

## Privacy and GDPR Notes

- Your connection export contains personal data (names, emails, URLs)
- Do not commit raw export files to public repositories
- The `.gitignore` in this project already excludes `data/raw/*.csv`
- LinkedIn's export is for personal use only
- Do not distribute or resell your connections' data
