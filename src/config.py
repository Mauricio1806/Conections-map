"""
config.py – Central configuration for the LinkedIn Connections Heatmap project.
All paths, categories, classification keywords, and strategic targets live here.
"""

import os
from pathlib import Path

# ─── Project Root ──────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent

# ─── Data Paths ────────────────────────────────────────────────────────────────
DATA_RAW_DIR      = ROOT_DIR / "data" / "raw"
DATA_PROCESSED_DIR = ROOT_DIR / "data" / "processed"
OUTPUTS_DIR       = ROOT_DIR / "outputs"
REPORTS_DIR       = ROOT_DIR / "reports"
CONFIG_DIR        = ROOT_DIR / "config"

# Create directories if they don't exist
for d in [DATA_RAW_DIR, DATA_PROCESSED_DIR, OUTPUTS_DIR, REPORTS_DIR, CONFIG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Input File Names (with fallback support) ──────────────────────────────────
CONNECTIONS_FILENAMES   = ["Connections.csv", "Connections", "connections.csv", "connections"]
COMPANY_FILENAMES       = ["Company Follows.csv", "Company Follows", "company_follows.csv"]
INVITATIONS_FILENAMES   = ["Invitations.csv", "Invitations", "invitations.csv"]

# ─── Output File Names ─────────────────────────────────────────────────────────
CLASSIFIED_CSV              = OUTPUTS_DIR / "classified_connections.csv"
HEATMAP_PERSONA_CSV         = OUTPUTS_DIR / "network_heatmap_by_persona.csv"
HEATMAP_AREA_CSV            = OUTPUTS_DIR / "network_heatmap_by_area.csv"
HEATMAP_SENIORITY_CSV       = OUTPUTS_DIR / "network_heatmap_by_seniority.csv"
HEATMAP_MARKET_CSV          = OUTPUTS_DIR / "network_heatmap_by_market.csv"
HEATMAP_COMPANY_CSV         = OUTPUTS_DIR / "network_heatmap_by_company.csv"
STRATEGIC_GAP_CSV           = OUTPUTS_DIR / "strategic_gap_report.csv"
DASHBOARD_XLSX              = REPORTS_DIR / "dashboard_ready.xlsx"
DAILY_REPORT_MD             = REPORTS_DIR / "daily_network_report.md"
STRATEGIC_REPORT_MD         = REPORTS_DIR / "strategic_gap_report.md"

# ─── Persona Categories ────────────────────────────────────────────────────────
PERSONAS = [
    "Recruiter", "Talent Acquisition", "Sourcer", "HR",
    "Hiring Manager", "Engineering Manager", "Data Engineering Manager",
    "Head of Data", "Director", "Executive", "Founder", "Partner",
    "Data Engineer", "Analytics Engineer", "Data Analyst", "BI Analyst",
    "Data Scientist", "Machine Learning / AI", "Software Engineer",
    "Product", "Project / Program Manager", "Operations", "Consultant", "Other"
]

# ─── Area Categories ───────────────────────────────────────────────────────────
AREAS = [
    "Recruiting", "Data Engineering", "Analytics", "BI", "Data Science / AI",
    "Software Engineering", "Product", "Management", "Consulting",
    "Operations", "HR", "Other"
]

# ─── Seniority Categories ──────────────────────────────────────────────────────
SENIORITY_LEVELS = [
    "Intern", "Junior", "Mid", "Senior", "Lead",
    "Manager", "Director", "Executive", "Founder", "Unknown"
]

# ─── Strategic Market Categories ──────────────────────────────────────────────
STRATEGIC_MARKETS = [
    "BRAZIL", "LATAM_USD", "US_CANADA_NEARSHORE", "SPAIN_EU", "EUROPE", "UNKNOWN"
]

# ─── Keyword Rules for Persona Classification ──────────────────────────────────
PERSONA_KEYWORDS = {
    "Recruiter": [
        "recruiter", "recrutador", "reclutador", "headhunter", "head hunter",
        "talent partner", "sourcing partner", "staffing", "talent scout",
        "global recruiter", "tech recruiter", "technical recruiter",
        "chasseur de talents", "cacador de talentos",
    ],
    "Talent Acquisition": [
        "talent acquisition", "aquisição de talentos", "atracción de talento",
        "atração de talentos", "atraccion de talento", "ta specialist",
        "ta lead", "ta partner", "ta manager", "talent lead", "talent manager",
        "talent specialist",
    ],
    "Sourcer": [
        "sourcer", "sourcing", "sourcing specialist",
    ],
    "HR": [
        "recursos humanos", "rrhh", "human resources", "hr business partner",
        "hrbp", "hr manager", "hr generalist", "hr specialist", "hr analyst",
        "people partner", "people analyst", "gente e gestão", "gestão de pessoas",
        "people operations", "people & culture", "people and culture",
        "analista de rh", "analista de recursos", "gerente de rh", "coordinador de rh",
        "analista de gestão", "analista de talento", "analista de seleção",
        "analista de seleccion", "auxiliar de rh", "asistente de rh",
        "jefe de recursos", "jefe de rrhh", "encargado de rr. hh",
        "encargado de recursos", "responsable de talento",
        "responsable de reclutamiento",
        "development and culture", "desarrollo organizacional",
        "cultura organizacional",
    ],
    "Hiring Manager": [
        "hiring manager", "engineering manager", "software manager",
        "tech manager", "manager de engenharia", "gerente de engenharia",
    ],
    "Engineering Manager": [
        "engineering manager", "gerente de engenharia", "manager de engenharia",
        "software engineering manager", "tech lead manager",
    ],
    "Data Engineering Manager": [
        "data engineering manager", "gerente de engenharia de dados",
        "head of data engineering", "data manager", "manager de dados",
        "manager de engenharia de dados",
    ],
    "Head of Data": [
        "head of data", "head of analytics", "chief data officer", "cdo",
        "head of bi", "head of business intelligence", "vp data", "vp of data",
        "vp analytics", "director of data", "director of analytics",
        "director de datos", "director de analítica",
    ],
    "Director": [
        "director", "diretora", "diretor", "vp ", "vice president",
        "vice-president", "sr director", "senior director",
    ],
    "Executive": [
        "ceo", "cto", "coo", "cio", "cpo", "chief executive",
        "chief technology", "chief operating", "chief product",
        "chief information", "president", "co-founder",
    ],
    "Founder": [
        "founder", "co-founder", "cofundador", "fundador",
        "proprietário", "owner", "chairman",
    ],
    "Partner": [
        " partner", "socio", "managing partner", "general partner",
    ],
    "Data Engineer": [
        "data engineer", "engenheiro de dados", "ingeniero de datos",
        "analytics engineer", "data pipeline", "data platform",
        "dados engineer", "staff data", "senior data engineer",
        "lead data engineer", "principal data engineer",
    ],
    "Analytics Engineer": [
        "analytics engineer", "analytic engineer", "dbt", "analytics engineering",
    ],
    "Data Analyst": [
        "data analyst", "analista de dados", "analista de data",
        "analyst de datos", "business analyst", "bi analyst",
        "senior analyst", "data analysis",
    ],
    "BI Analyst": [
        "bi analyst", "business intelligence analyst", "power bi",
        "tableau analyst", "looker", "bi developer", "bi engineer",
        "bi specialist",
    ],
    "Data Scientist": [
        "data scientist", "cientista de dados", "cientifico de datos",
        "machine learning engineer", "ml engineer", "ai engineer",
        "artificial intelligence", "deep learning", "research scientist",
    ],
    "Machine Learning / AI": [
        "machine learning", "ml engineer", "ai engineer",
        "artificial intelligence", "deep learning", "llm", "nlp",
        "computer vision", "ai specialist", "ai researcher",
        "generative ai", "agentic", "llmops",
    ],
    "Software Engineer": [
        "software engineer", "engenheiro de software", "ingeniero de software",
        "developer", "desenvolvedor", "full stack", "fullstack", "backend",
        "frontend", "mobile developer", "swe", "software developer",
        "platform engineer", "devops", "sre", "site reliability",
    ],
    "Product": [
        "product manager", "product owner", "gerente de produto",
        "product lead", "head of product", "vp product",
    ],
    "Project / Program Manager": [
        "project manager", "program manager", "pmo", "gerente de projeto",
        "delivery manager", "scrum master", "agile coach",
    ],
    "Operations": [
        "operations manager", "operations coordinator", "operations analyst",
        "supply chain", "logistics", "gerente de operações",
        "coordinador de operaciones",
    ],
    "Consultant": [
        "consultant", "consultor", "consultora", "consulting",
        "advisory", "advisor", "assessor",
    ],
}

# ─── Area Keyword Rules ────────────────────────────────────────────────────────
AREA_KEYWORDS = {
    "Recruiting": [
        "recruiter", "talent acquisition", "sourcer", "headhunter",
        "reclutador", "recrutador", "atração de talentos", "atraccion",
        "talent lead", "staffing",
    ],
    "Data Engineering": [
        "data engineer", "engenheiro de dados", "data pipeline",
        "analytics engineer", "data platform", "dbt", "spark", "airflow",
        "kafka", "data architecture",
    ],
    "Analytics": [
        "data analyst", "analytics", "analista de dados", "business analyst",
        "analítica", "analysis",
    ],
    "BI": [
        "bi analyst", "business intelligence", "power bi", "tableau",
        "looker", "qlik", "bi developer",
    ],
    "Data Science / AI": [
        "data scientist", "machine learning", "ai engineer",
        "artificial intelligence", "deep learning", "nlp", "llm",
        "generative ai", "ml engineer",
    ],
    "Software Engineering": [
        "software engineer", "desenvolvedor", "developer", "full stack",
        "backend", "frontend", "devops", "sre", "platform engineer",
    ],
    "Product": [
        "product manager", "product owner", "head of product",
        "product lead",
    ],
    "Management": [
        "engineering manager", "head of", "director", "vp ", "cto", "ceo",
        "coo", "chief", "gerente", "manager", "director",
    ],
    "Consulting": [
        "consultant", "consultor", "advisory", "advisor",
    ],
    "Operations": [
        "operations", "supply chain", "logistics", "delivery coordinator",
        "operações",
    ],
    "HR": [
        "recursos humanos", "human resources", "hr ", "rrhh", "hrbp",
        "people partner", "gente e gestão", "gestão de pessoas",
        "seleção", "recrutamento",
    ],
}

# ─── Seniority Keyword Rules ───────────────────────────────────────────────────
SENIORITY_KEYWORDS = {
    "Intern": [
        "intern", "estagiário", "estagiaria", "practicante",
        "trainee", "apprentice",
    ],
    "Junior": [
        "junior", "jr.", "jr ", "júnior", "auxiliar", "assistente",
        "assistant", "asistente", "analista jr",
    ],
    "Mid": [
        "pleno", "mid", "mid-level", "analista", "analyst", "analyst pl",
        "analyst pleno", "specialist", "especialista",
    ],
    "Senior": [
        "senior", "sr.", "sr ", "sênior", "sénior",
    ],
    "Lead": [
        "lead", "staff", "principal", "líder", "lider", "tech lead",
    ],
    "Manager": [
        "manager", "gerente", "coordinator", "coordinador", "coordenador",
        "supervisor", "jefe", "chefe", "responsable",
    ],
    "Director": [
        "director", "diretora", "diretor", "head of",
        "vp ", "vice president",
    ],
    "Executive": [
        "ceo", "cto", "coo", "cio", "cpo", "chief", "president",
        "executive",
    ],
    "Founder": [
        "founder", "cofundador", "co-founder", "fundador",
        "proprietário", "owner",
    ],
}

# ─── Strategic Market Keyword Rules ───────────────────────────────────────────
MARKET_KEYWORDS = {
    "BRAZIL": [
        "brasil", "brazil", "brazilian", "são paulo", "rio de janeiro",
        "belo horizonte", "curitiba", "porto alegre",
        # BR company signals
        "itau", "itaú", "nubank", "petrobras", "vale", "bradesco",
        "santander brasil", "zup innovation", "c6 bank", "movile",
        "totvs", "rdstation", "resultados digitais", "contabilizei",
        "semantix", "dataside", "indicium", "pagbank", "pagseguro",
        "stone", "ifood", "rappi brasil", "magazine luiza",
        "magalu", "xp inc", "btg pactual",
    ],
    "LATAM_USD": [
        "latam", "latin america", "latinoamerica", "latin", "nearshore latam",
        "colombia", "mexican", "argentina", "chile", "peru", "ecuador",
        "venezuela", "panama", "costa rica", "uruguay", "paraguay",
        "agileengine", "toptal", "crossover", "andela", "lemon.io",
        "turing", "remote latam", "latamcent", "nimble.la", "blue people",
        "wizeline", "gorilla logic", "unosquare", "3pillar",
        "modis", "softserve", "kaizen softworks",
    ],
    "US_CANADA_NEARSHORE": [
        "united states", "usa", "u.s.", "u.s.a", "new york", "san francisco",
        "seattle", "chicago", "austin", "boston", "los angeles",
        "canada", "toronto", "vancouver", "montreal",
        "amazon", "google", "microsoft", "apple", "meta", "netflix",
        "aws", "stripe", "shopify", "twilio", "datadog", "snowflake",
        "databricks", "dbt labs", "fivetran", "airbyte",
        "hireclout", "rsm us", "crosslead", "andela", "toptal",
        "nerd bench", "parallax staffing", "medialab",
    ],
    "SPAIN_EU": [
        "spain", "españa", "espana", "madrid", "barcelona", "valencia",
        "sevilla", "bilbao", "malaga",
        "portugal", "lisbon", "lisboa", "porto", "portugal",
        "erni", "seidor", "stratesys", "indra", "capgemini spain",
        "deloitte spain", "accenture spain", "ntt data spain",
        "plain concepts", "logicalis spain", "luxoft spain",
        "emergya", "minsait", "datasmarts",
    ],
    "EUROPE": [
        "germany", "deutschland", "berlin", "munich", "münchen",
        "amsterdam", "netherlands", "holland", "rotterdam",
        "ireland", "dublin", "uk", "united kingdom", "london",
        "paris", "france", "italy", "italia", "milan",
        "romania", "bucharest", "poland", "warsaw",
        "adesso", "schwarz digits", "deutsche telekom",
        "booking.com", "adyen", "philips", "shell",
        "sap", "siemens", "zalando", "delivery hero",
    ],
}

# ─── Strategic Targets (for Gap Analysis) ─────────────────────────────────────
# Format: {market: {persona: {short_term_target, medium_term_target}}}
STRATEGIC_TARGETS = {
    "US_CANADA_NEARSHORE": {
        "Recruiter":         {"short_term": 80, "medium_term": 100},
        "Talent Acquisition":{"short_term": 60, "medium_term": 80},
        "Hiring Manager":    {"short_term": 50, "medium_term": 70},
        "Engineering Manager":{"short_term": 40, "medium_term": 60},
        "Head of Data":      {"short_term": 30, "medium_term": 50},
        "Director":          {"short_term": 25, "medium_term": 40},
        "Data Engineering Manager":{"short_term": 30, "medium_term": 50},
    },
    "LATAM_USD": {
        "Recruiter":         {"short_term": 80, "medium_term": 100},
        "Talent Acquisition":{"short_term": 60, "medium_term": 80},
        "Hiring Manager":    {"short_term": 50, "medium_term": 70},
        "Data Engineering Manager":{"short_term": 30, "medium_term": 50},
        "Head of Data":      {"short_term": 30, "medium_term": 50},
        "Director":          {"short_term": 20, "medium_term": 40},
    },
    "SPAIN_EU": {
        "Recruiter":         {"short_term": 20, "medium_term": 80},
        "Talent Acquisition":{"short_term": 15, "medium_term": 60},
        "Hiring Manager":    {"short_term": 15, "medium_term": 60},
        "Head of Data":      {"short_term": 10, "medium_term": 50},
        "Director":          {"short_term": 10, "medium_term": 40},
        "Data Engineering Manager":{"short_term": 10, "medium_term": 40},
    },
    "EUROPE": {
        "Recruiter":         {"short_term": 10, "medium_term": 60},
        "Talent Acquisition":{"short_term": 10, "medium_term": 50},
        "Head of Data":      {"short_term": 5,  "medium_term": 40},
        "Director":          {"short_term": 5,  "medium_term": 30},
        "Data Engineering Manager":{"short_term": 5, "medium_term": 30},
    },
    "BRAZIL": {
        "Recruiter":         {"short_term": 50, "medium_term": 50},
        "Data Engineer":     {"short_term": 30, "medium_term": 30},
        "Head of Data":      {"short_term": 30, "medium_term": 30},
        "Data Engineering Manager":{"short_term": 20, "medium_term": 20},
    },
}

# ─── Priority Score Weights ────────────────────────────────────────────────────
# These weights combine to form the final 0-100 priority score.

HIGH_VALUE_PERSONAS = {
    "Recruiter", "Talent Acquisition", "Sourcer",
    "Hiring Manager", "Engineering Manager",
    "Data Engineering Manager", "Head of Data", "Director", "Executive",
}

HIGH_VALUE_MARKETS = {"US_CANADA_NEARSHORE", "LATAM_USD", "SPAIN_EU", "EUROPE"}

HIGH_VALUE_AREA_KEYWORDS = [
    "nearshore", "remote", "data", "cloud", "staffing", "technology",
    "international", "consulting", "it ", "tech", "software", "platform",
    "engineering", "analytics",
]

# Company signal keywords that suggest international/USD hiring relevance
INTL_COMPANY_SIGNALS = [
    "global", "international", "remote", "nearshore", "offshore",
    "distributed", "staff augmentation", "outsourcing", "consulting",
    "it solutions", "tech solutions", "software solutions",
    "data solutions", "cloud", "saas", "platform",
]

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
