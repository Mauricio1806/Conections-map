# -*- coding: utf-8 -*-
"""
company_normalizer.py
=====================
Normalizes company names for dictionary lookup.
- Strips legal suffixes (LTDA, Inc, LLC, GmbH, S.A., etc.)
- Strips region noise words when at end of name
- Handles common abbreviations and variants

Returns a normalized form suitable for fuzzy matching against dictionaries.
The original company_clean is always preserved — normalization is only for lookup.
"""

import re
import unicodedata

# Legal / noise suffixes to strip (order matters — strip from longest to shortest)
_NOISE_SUFFIXES = [
    # legal forms
    r"\b(ltda\.?|limitada|limited|ltd\.?)\b",
    r"\b(inc\.?|incorporated)\b",
    r"\b(llc|l\.l\.c\.)\b",
    r"\b(gmbh|ag|kg)\b",
    r"\b(s\.?a\.?s?|s/a)\b",
    r"\b(sl|s\.l\.)\b",
    r"\b(bv|b\.v\.)\b",
    r"\b(corp\.?|corporation)\b",
    r"\bplc\b",
    r"\bspa\b",
    r"\bnv\b",
    # company-type words when standalone at end
    r"\b(group|grp|holding|holdings|ventures|ventures)\b",
    # region words when they appear as suffix only (preserve for inference first)
    r"\b(brasil|brazil)\b",
    r"\b(latam|latin america)\b",
    r"\b(europe|europa)\b",
    r"\b(global)\b",
    # generic business words at end
    r"\b(solutions|technology|technologies|tech|services|service|consulting|consultoria|consultancy)\b",
    r"\b(digital|sistemas|sistemas de informacao|sistemas de informação)\b",
]

# Compile as a single combined pattern to strip sequentially
_SUFFIX_RE = re.compile("|".join(_NOISE_SUFFIXES), re.IGNORECASE)

# Known company name aliases → canonical form
COMPANY_ALIASES: dict[str, str] = {
    # staffing
    "tcs": "tata consultancy services",
    "ge": "general electric",
    "aws": "amazon web services",
    "amazon web services (aws)": "amazon web services",
    "amazon web services": "amazon web services",
    "meta platforms": "meta",
    "facebook": "meta",
    "alphabet": "google",
    "ntt data europe & latam": "ntt data",
    "ntt data europe and latam": "ntt data",
    "ntt data europe & latam s.l.": "ntt data",
    "pagegroup": "michael page",
    "page group": "michael page",
    "kelly services": "kelly",
    "lhh recruitment solutions": "lhh",
    "manpowergroup": "manpower",
    "adecco group": "adecco",
    "gi group holding": "gi group",
    # tech
    "ge vernova": "ge vernova",
    "ge renewable energy": "ge renewable energy",
    "ibm corporation": "ibm",
    "international business machines": "ibm",
    "microsoft corporation": "microsoft",
    "apple inc": "apple",
    "sap se": "sap",
    "oracle corporation": "oracle",
    "alphabet inc": "google",
    "amazon.com": "amazon",
    # consulting
    "ernst & young": "ey",
    "ernst and young": "ey",
    "pricewaterhousecoopers": "pwc",
    "price waterhouse coopers": "pwc",
    "deloitte touche tohmatsu": "deloitte",
    "boston consulting group": "bcg",
    "booz allen hamilton": "booz allen",
    "tata consultancy services": "tcs",
    # brazil
    "grupo boticario": "o boticario",
    "grupo boticário": "o boticario",
    "o boticário": "o boticario",
    "magazine luiza": "magalu",
    "magalu": "magalu",
    "itaú unibanco": "itau",
    "itaú": "itau",
    "itau unibanco": "itau",
    "banco do brasil s.a.": "banco do brasil",
    "petroleo brasileiro": "petrobras",
    "petróleo brasileiro": "petrobras",
    "ge renewables": "ge renewable energy",
    "mercadolivre": "mercado livre",
    "mercado libre": "mercado livre",
    "ab inbev": "ambev",
    "inbev": "ambev",
    "anheuser-busch inbev": "ambev",
    "senai": "senai cimatec",
    # latam
    "lm solucoes de mobilidade": "lm mobilidade",
    "lm soluções de mobilidade": "lm mobilidade",
    "globant s.a.": "globant",
    "ci&t software": "ci&t",
}


def _remove_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def normalize(company: str) -> str:
    """
    Return a normalized company name suitable for dictionary lookup.
    Does NOT modify the original stored value — use only for matching.
    """
    if not company or not isinstance(company, str):
        return ""

    text = company.strip().lower()

    # Apply known alias first (before stripping suffixes)
    if text in COMPANY_ALIASES:
        return COMPANY_ALIASES[text]

    # Strip legal / region / generic noise suffixes
    prev = None
    while prev != text:
        prev = text
        text = _SUFFIX_RE.sub("", text)
        text = re.sub(r"\s+", " ", text).strip().rstrip(".,&-")

    # Apply alias again after stripping
    if text in COMPANY_ALIASES:
        return COMPANY_ALIASES[text]

    return text


def normalize_for_search(company: str) -> str:
    """Lower-cased, accent-stripped, whitespace-collapsed form for substring search."""
    n = normalize(company)
    return _remove_accents(n)


def tokens(company: str) -> set[str]:
    """Return word tokens of the normalized form (length >= 3)."""
    norm = normalize_for_search(company)
    return {t for t in re.split(r"[\s\-&/|.]+", norm) if len(t) >= 3}
