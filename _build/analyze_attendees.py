"""
analyze_attendees.py  —  Attendee Analysis Playbook
=====================================================
This is the attendee analysis playbook for the conference series.
It reads Luma attendee CSV exports and produces audience profile stats
for use in sponsorship pages.

Works across conference brands (LLMday, SREday, DevOpsNotDead, etc.).
Drop in CSV files from any Luma event export and run:

    python3 _build/analyze_attendees.py path/to/event1.csv path/to/event2.csv ...

Output: human-readable stats printed to stdout + attendee_stats.json written
next to this script. Copy the numbers into the hardcoded static section of
sponsorship.html.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CATEGORY DESIGN NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Role labels are chosen to be accurate and professionally neutral:

  "Independent / Job Seeker"
      Covers attendees who are between roles, actively job-hunting,
      in full-time education, or otherwise not currently employed at
      an organisation. All share the same professional context: they
      attend to learn and network rather than to represent a company.
      Distinct from "Researcher", who is employed to do research.

  "Researcher"
      Professional researchers — employed at a university, lab, or
      company R&D unit. Distinct from students and from engineers
      whose job happens to involve some research component.

  "Other" must stay under 5% in every dimension.
      If it creeps above that, check the UNCLASSIFIED output printed
      at runtime and add new keyword rules to the relevant classifier.
"""

import csv, re, sys, json, glob
from collections import Counter
from pathlib import Path


# ══════════════════════════════════════════════════════════════
# PER-CONFERENCE CONTENT
# Update TLDR when running for a new brand or after a significant
# shift in audience composition.
# "What they are working on" pills are NOT here — they are generated
# dynamically at build time from talks.csv by generate.py.
#
# TLDR writing rules:
#   - Focus on what attendees come for and enjoy, not what they don't
#   - Do not imply who holds buying power within their company
#   - Do not mention selling, buying, or pitching in any form
# ══════════════════════════════════════════════════════════════

TLDR = (
    "A hands-on crowd of engineers and technical leaders from startups and enterprises, "
    "people who are actively shipping AI products. "
    "Senior practitioners who choose their own tools and shape how their teams work."
)

# ── "What they are working on" topics ─────────────────────────────────────
# Scored from talk titles + abstracts across all discovered talks.csv files.
# Top WORKING_ON_HIGHLIGHT topics are highlighted on the page; the rest are
# shown as plain pills. Review the output each season — keyword rules are a
# good starting point but can't reason about genuinely novel topics. Add new
# keywords to WORKING_ON_KEYWORDS when the landscape shifts.
WORKING_ON_HIGHLIGHT = 3   # top 3 highlighted, rest plain
WORKING_ON_MAX       = 10  # cap output at 10 pills

WORKING_ON_KEYWORDS = {
    "Agents & Automation":          ['agent', 'agentic', 'autonomous agent', 'orchestrat', 'multi-agent', 'autonom'],
    "Applied / Product AI":         ['product', 'ux', 'user interface', 'human-in-the-loop', 'coaching', 'application'],
    "Production & Reliability":     ['production', 'reliability', 'failure', 'incident', 'on-call', 'slo', 'sla', 'resilience'],
    "LLM Infrastructure & Cost":   ['infrastructure', 'gpu', 'cluster', 'cost', 'capacity', 'compute', 'latency', 'throughput'],
    "RAG & Retrieval":              ['rag', 'retrieval', 'knowledge graph', 'graphrag', 'vector search', 'retriev'],
    "LLM Evaluation & Testing":    ['evaluat', 'benchmark', 'testing', 'evals', 'verification', 'hallucin'],
    "Open Source & Edge":           ['open.source', 'open source', 'edge', 'on.prem', 'private.*llm', 'local model', 'self-host'],
    "Prompt & Context Engineering": ['prompt', 'context engineering', 'context window', 'few-shot', 'system prompt'],
    "Multimodal & Specialized AI":  ['multimodal', 'vision', 'vlm', 'voice', 'neural', 'wearable', 'medical', 'healthcare', 'radiolog'],
    "AI Ethics & Governance":       ['ethics', 'ai act', 'regulation', 'governance', 'bias', 'responsible', 'safety'],
    "Fine-tuning & Training":       ['fine-tun', 'finetuning', 'grpo', 'rlhf', 'training', 'distillat', 'reinforcement'],
    "AI Security":                  ['security', 'secure', 'vulnerab', 'attack', 'prompt injection', 'malicious'],
    "MLOps & Observability":        ['mlops', 'observabil', 'monitor', 'tracing', 'opentelemetry', 'telemetry', 'sampling'],
    "Coding Agents & DevEx":        ['coding agent', 'code generat', 'developer product', 'developer experienc', 'vibe cod'],
}

# ── Role display merge ─────────────────────────────────────────────────────
# The classifier produces ~12 granular role buckets (useful for debugging and
# catching edge cases). For the sponsorship page we cap at 6 display categories
# — marketing readers don't need that level of detail.
# Map granular label → display label. Anything not listed passes through as-is.
ROLE_DISPLAY_MERGE = {
    "Software Engineer":          "Software / Platform / DevOps",
    "DevOps / SRE / Platform":    "Software / Platform / DevOps",
    "C-Suite Executive":          "Executive / Founder / Leadership",
    "Founder / Entrepreneur":     "Executive / Founder / Leadership",
    "Director / VP / Head":       "Executive / Founder / Leadership",
    "Engineering Lead / Manager": "Executive / Founder / Leadership",
    "Operations / Business":      "Business / Consulting / Product",
    "Consultant / Investor":      "Business / Consulting / Product",
    "Product Manager":            "Business / Consulting / Product",
    # "ML / AI / Data"            → keep as-is
    # "Independent / Job Seeker"  → keep as-is
    # "Researcher"                → keep as-is
}

# ── Under-5% merge-up rules ────────────────────────────────────────────────
# Any display category that ends up below 5% of total is absorbed into the
# specified target. Applied after ROLE_DISPLAY_MERGE. Add entries here
# whenever a new small bucket appears in future data.
ROLE_UNDER5_FALLBACK = {
    "Researcher":               "ML / AI / Data",
    "Independent / Job Seeker": "Business / Consulting / Product",
}

# Same rule for company size: if a bucket falls under 5%, absorb it here.
SIZE_UNDER5_FALLBACK = {
    "Scale-up (50-500)":        "Startup (<50)",
    "Solo Founder / Independent": "Startup (<50)",
}


# ══════════════════════════════════════════════════════════════
# ROLE CLASSIFIER
# Rules run top-to-bottom; first match wins.
# ══════════════════════════════════════════════════════════════

ROLE_RULES = [

    # ── ML / AI / Data ─────────────────────────────────────────
    # Check before the generic software-engineer catch-all below.
    ("ML / AI / Data", lambda t: any(x in t for x in [
        'ml engineer', 'machine learning', 'mlops', 'nlp',
        'ai engineer', 'ai developer', 'ai software', 'ai security',
        'ai and automation', 'ai/ml', 'ai contract', 'generative ai',
        'prompt engineer', 'applied scientist', 'cross strategy ai',
        'ai healthcare', 'data scientist', 'data science', 'data analyst',
        'data engineer', 'data architect', 'data manager', 'data platform',
        'big data', 'lead data', 'senior data', 'associate data',
        'scientific data', 'research scientist', 'ai researcher',
        'principal research', 'princ research', 'member of technical staff',
    ]) and not any(x in t for x in [
        'product manager', 'director of', 'head of', 'vp of',
    ])),

    # ── Independent / Job Seeker ───────────────────────────────
    # Attendees who are in full-time education, actively seeking
    # their next role, or otherwise unaffiliated with an employer.
    ("Independent / Job Seeker", lambda t: any(x in t for x in [
        'student', 'phd', 'mba', 'masters', "master's", 'ms cs',
        'grad assistant', 'graduate research', 'open to work',
        'job search', 'alumni engagement', 'data science ra',
        'freelance artist', 'unemployed',
    ]) or t.strip() in ['none']),

    # ── Researcher ─────────────────────────────────────────────
    # Professional researchers at universities, labs, or R&D units.
    # Does not include engineers whose role involves some research.
    ("Researcher", lambda t: any(x in t for x in [
        'research scientist', 'ai researcher', 'senior research',
        'graduate research assistant', 'research engineer',
        'associate professor', 'lecturer', 'professor',
        'lecturer and scientist', 'pełnomocnik rektora',
    ]) and not any(x in t for x in [
        'product', 'software engineer', 'ml engineer', 'data scientist',
    ])),

    # ── Founder / Entrepreneur ─────────────────────────────────
    ("Founder / Entrepreneur", lambda t: bool(
        re.search(r'\bfounder\b|co-founder|cofounder', t)
    )),

    # ── C-Suite Executive ──────────────────────────────────────
    ("C-Suite Executive", lambda t: any(x in t for x in [
        'ceo', 'cto', 'cio', 'cpo', 'chief', 'wiceprezes',
    ])),

    # ── Director / VP / Head ───────────────────────────────────
    ("Director / VP / Head", lambda t: any(x in t for x in [
        'vp ', 'vp of', ' vp', 'vice president', 'director',
        'head of', 'gm &', 'general manager',
    ])),

    # ── Engineering Lead / Manager ─────────────────────────────
    ("Engineering Lead / Manager", lambda t: any(x in t for x in [
        'engineering manager', 'platform engineering manager',
        'tech lead', 'team lead', 'sdm', 'engineering lead',
    ])),

    # ── DevOps / SRE / Platform ────────────────────────────────
    ("DevOps / SRE / Platform", lambda t: any(x in t for x in [
        'devops', 'sre', 'site reliability', 'dev sec ops', 'devsecops',
        'platform engineer', 'developer / sre', 'principal devops',
        'senior devops',
    ])),

    # ── Software Engineer ──────────────────────────────────────
    ("Software Engineer", lambda t: any(x in t for x in [
        'software engineer', 'software developer', 'software architect',
        'full stack', 'backend', 'frontend', 'front-end', 'front end',
        'founding engineer', 'senior engineer', 'staff engineer',
        'principal engineer', 'principal software', 'staff software',
        'staff protocol', 'staff security', 'security architect',
        'cybersecurity', 'cloud architect', 'cloud data',
        'solutions architect', 'solution architect', 'solutions engineer',
        'senior solutions', 'r&d engineer', 'integration engineer',
        'android developer', 'web designer', 'system analyst',
        'qa engineer', 'test developer', 'senior developer',
        'senior backend', 'senior software', 'senior specialist',
        'sr engineer', 'enterprise technologist', 'developer advocate',
        'staff developer', 'field cto', 'fractional cto',
        'it project', 'it consultant', 'software eng',
        ' engineer', ' developer', ' architect', 'swe', 'fde',
    ]) and not any(x in t for x in [
        'product manager', 'business', 'market', 'psycholog',
        'accountant', 'coordinator', 'operations', 'lecturer',
        'professor', 'teacher', 'redaktor', 'data analyst',
        'data engineer', 'ai engineer', 'ml engineer',
        'machine learning', 'prompt engineer',
    ])),

    # ── Product Manager ────────────────────────────────────────
    ("Product Manager", lambda t: any(x in t for x in [
        'product manager', 'product owner', 'chief product',
        'senior product', 'ai product manager', 'product partnerships',
        'product developer', 'product engineer',
    ]) or t.strip() in ['pm', 'cpo']),

    # ── Consultant / Investor ──────────────────────────────────
    ("Consultant / Investor", lambda t: any(x in t for x in [
        'consultant', 'investor', 'angel investor', 'investment',
        'business analyst', 'deal origination', 'scout', 'funding',
        'energy consultant', 'marketing consultant', 'pre-sales',
        'entrepreneur', 'technical program manager', 'program manager',
    ]) or t.strip() in [
        'principal', 'partner', 'sr associate', 'owner',
        'analyst', 'architect',
    ]),

    # ── Operations / Business ──────────────────────────────────
    ("Operations / Business", lambda t: any(x in t for x in [
        'operations', 'coordinator', 'admin', 'specialist',
        'project manager', 'project coordinator', 'psycholog',
        'accountant', 'media', 'redaktor', 'marketing', 'growth',
        'education', 'deputy', 'organizer', 'promotion', 'recruiter',
        'business transformation', 'digital transformation',
        'tech marketing', 'ai software evangelist', 'prod support',
        'site manager',
    ]) or t.strip() in ['team', 'manager', 'fde', 'se', 'ai', 'engineer']),
]


def classify_role(raw_title):
    t = raw_title.lower().strip()
    for label, fn in ROLE_RULES:
        try:
            if fn(t):
                return label
        except Exception:
            pass
    return "Other"


# ══════════════════════════════════════════════════════════════
# COMPANY SIZE CLASSIFIER
# ══════════════════════════════════════════════════════════════

# Well-known organisations reliably above ~500 employees.
KNOWN_ENTERPRISE = {
    'google', 'microsoft', 'amazon', 'aws', 'meta', 'apple', 'ibm',
    'oracle', 'salesforce', 'cisco', 'intel', 'nvidia', 'adobe', 'sap',
    'vmware', 'hp', 'dell', 'accenture', 'deloitte', 'pwc', 'kpmg',
    'ey', 'mckinsey', 'jpmorgan', 'goldman', 'morgan stanley',
    'bank of america', 'wells fargo', 'citigroup', 'barclays', 'hsbc',
    'bloomberg', 'spotify', 'netflix', 'airbnb', 'uber', 'lyft',
    'paypal', 'stripe', 'visa', 'mastercard', 'servicenow', 'workday',
    'splunk', 'datadog', 'cloudflare', 'pagerduty', 'elastic', 'mongodb',
    'snowflake', 'databricks', 'palantir', 'twilio', 'okta', 'crowdstrike',
    'harness', 'cribl', 'synopsys', 'relativity', 'ironclad', 'viam',
    'packt', 'bosch', 'siemens', 'volkswagen', 'bmw', 'toyota',
    'samsung', 'lg', 'sony', 'tata', 'infosys', 'wipro', 'cognizant',
    'capgemini', 'atos', 'walmart', 'vanguard', 'intuit', 'dropbox',
    'red hat', 'roche', 'huawei', 'allianz', 'capital one', 's&p global',
    'thoughtworks', 'inpost', 'monday.com', 'akamai', 'softserve',
    'epam', 'tech mahindra',
}

KNOWN_SCALEUP = {
    'anyscale', 'anthropic', 'together ai', 'software mind', 'greenhouse',
    'n26', 'synerise', 'future processing', 'mobidev', 'tooploox',
    '365 data', 'mirantis', 'digitalocean', 'n-able', 'liveperson',
}

# Values that indicate the person did not supply a company name.
NO_COMPANY_VALUES = {
    'n/a', 'na', 'n\\a', '-', 'none', 'tbd', 'private', 'it company',
    'student', 'job searching', 'looking for', 'unemployed', 'job seeker',
    'self', '',
}

SOLO_SIGNALS = [
    'freelance', 'independent', 'self-employed',
]


def classify_company_size(company):
    c = company.lower().strip()
    if not c or any(c == s for s in NO_COMPANY_VALUES) or c.startswith('looking for'):
        return "Solo Founder / Independent"
    if any(x in c for x in SOLO_SIGNALS):
        return "Solo Founder / Independent"
    for name in KNOWN_ENTERPRISE:
        if name in c:
            return "Enterprise (500+)"
    for name in KNOWN_SCALEUP:
        if name in c:
            return "Scale-up (50-500)"
    if any(x in c for x in [
        'university', 'universi', 'college', 'institute', 'school',
        'akademia', 'politechnika', 'uczelnia',
    ]):
        return "Enterprise (500+)"
    return "Startup (<50)"


# ══════════════════════════════════════════════════════════════
# SENIORITY CLASSIFIER
# ══════════════════════════════════════════════════════════════

def classify_seniority(raw_title):
    t = raw_title.lower().strip()
    if any(x in t for x in [
        'ceo', 'cto', 'cio', 'cpo', 'chief', 'president', 'founder',
        'co-founder', 'cofounder', 'vp ', 'vp of', ' vp',
        'vice president', 'partner', 'owner', 'wiceprezes',
        'field cto', 'fractional cto', 'angel investor', 'investor',
        'general manager', 'gm &', 'director', 'head of',
    ]):
        return "Executive"
    if any(x in t for x in [
        'manager', 'tech lead', 'team lead', 'lead ', 'lead,', 'sdm',
        'nlp lead', 'ai/ml lead', 'engineering manager',
        'platform engineering manager', 'engineering lead',
        'head of data', 'head of ai', 'head of txn',
    ]):
        return "Lead / Manager"
    if any(x in t for x in [
        'senior', 'staff', 'principal', 'sr.', 'sr ', 'princ ',
    ]):
        return "Senior"
    if any(x in t for x in [
        'student', 'phd', 'mba', 'masters', "master's", 'grad',
        'junior', 'associate ', 'job search', 'open to work', 'alumni',
        'unemployed', 'none', 'freelance artist', 'data science ra',
        'ms cs', 'ms ', 'cornell masters', 'cs master',
    ]):
        return "Junior"
    return "Mid-Level"


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def extract_topics(repo_root):
    """Score topics from all talks.csv files found under repo_root."""
    talks_files = glob.glob(str(repo_root / '20*' / '_db' / 'talks.csv'))
    if not talks_files:
        return [], []

    scores = Counter()
    all_talks = []
    for path in talks_files:
        with open(path, encoding='utf-8-sig') as f:
            reader = csv.DictReader(line.replace('\0', '') for line in f)
            for row in reader:
                if 'declined' in row.get('status', '').lower():
                    continue
                text = (row.get('title', '') + ' ' + row.get('abstract', '')).lower()
                all_talks.append(text)

    for topic, keywords in WORKING_ON_KEYWORDS.items():
        for text in all_talks:
            for kw in keywords:
                if re.search(kw, text):
                    scores[topic] += 1
                    break

    ranked = sorted(scores.items(), key=lambda x: -x[1])[:WORKING_ON_MAX]
    pills = [
        {'label': label, 'highlight': i < WORKING_ON_HIGHLIGHT, 'count': count}
        for i, (label, count) in enumerate(ranked)
    ]
    return pills, talks_files


def pct(n, total):
    return round(n / total * 100) if total else 0


def read_csv(path):
    rows = []
    with open(path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(line.replace('\0', '') for line in f)
        for row in reader:
            rows.append(row)
    return rows


def top_n(counts, total, exclude_other=True):
    items = sorted(counts.items(), key=lambda x: -x[1])
    if exclude_other:
        items = [(k, v) for k, v in items if k != "Other"]
    return [{"label": k, "pct": pct(v, total)} for k, v in items]


def print_section(title, items):
    print(f"\n{'═'*52}")
    print(title)
    print('═'*52)
    for item in items:
        bar = "█" * (item["pct"] // 2)
        print(f"  {item['label']:<35} {item['pct']:>3}%  {bar}")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    paths = sys.argv[1:]
    if not paths:
        print("Usage: python3 analyze_attendees.py event1.csv event2.csv ...")
        sys.exit(1)

    rows = []
    for p in paths:
        loaded = read_csv(p)
        print(f"  Loaded {len(loaded)} rows from {Path(p).name}")
        rows.extend(loaded)

    # De-duplicate by name — the same person may attend multiple events.
    # Keep the first occurrence (earliest event loaded).
    seen_names = set()
    deduped = []
    dupes = 0
    for r in rows:
        name = r.get('name', '').strip().lower()
        if name and name in seen_names:
            dupes += 1
            continue
        if name:
            seen_names.add(name)
        deduped.append(r)
    rows = deduped

    total = len(rows)
    print(f"\nTotal attendees: {total} unique ({dupes} duplicate(s) removed)")

    # ── Classify ──────────────────────────────────────────────
    raw_role_counts = Counter(classify_role(r.get('Job Title', ''))      for r in rows)
    size_counts     = Counter(classify_company_size(r.get('Company', '')) for r in rows)
    senior_counts   = Counter(classify_seniority(r.get('Job Title', '')) for r in rows)

    # Merge granular roles into display categories (max 6 for the page)
    role_counts = Counter()
    for label, count in raw_role_counts.items():
        display_label = ROLE_DISPLAY_MERGE.get(label, label)
        role_counts[display_label] += count

    # Merge up any display category that falls under 5%
    def merge_under5(counts, fallback_map):
        changed = True
        while changed:
            changed = False
            for label, count in list(counts.items()):
                if pct(count, total) < 5 and label in fallback_map:
                    target = fallback_map[label]
                    counts[target] += counts.pop(label)
                    print(f"  Merged '{label}' ({pct(count, total)}%) into '{target}' (under 5% rule)")
                    changed = True
        return counts

    role_counts = merge_under5(role_counts, ROLE_UNDER5_FALLBACK)
    size_counts = merge_under5(size_counts, SIZE_UNDER5_FALLBACK)

    # ── Warn if Other > 5% ────────────────────────────────────
    other_count = raw_role_counts.get("Other", 0)
    other_pct   = other_count / total * 100 if total else 0
    if other_pct > 5:
        print(f"\n  WARNING: Role 'Other' = {other_pct:.1f}% (above 5% threshold)")
        print("  Add keyword rules to ROLE_RULES to bring it down.")

    # ── List unclassified titles for debugging ─────────────────
    unclassified = [
        r.get('Job Title', '') for r in rows
        if classify_role(r.get('Job Title', '')) == "Other"
    ]
    if unclassified:
        print(f"\n  Unclassified job titles ({len(unclassified)}) — add rules for these:")
        for t in sorted(set(unclassified)):
            print(f"    {t}")

    # ── Build stats ───────────────────────────────────────────
    role_stats   = top_n(role_counts,   total)
    size_stats   = top_n(size_counts,   total)
    senior_stats = top_n(senior_counts, total)

    # ── Topics from talks.csv ─────────────────────────────────
    repo_root = Path(__file__).parent.parent
    topic_pills, talks_files = extract_topics(repo_root)
    if talks_files:
        print(f"\n  Found {len(talks_files)} talks.csv file(s) for topic extraction.")
    else:
        print("\n  No talks.csv files found — skipping topic extraction.")
        print(f"  Expected location: {repo_root}/20*/_db/talks.csv")

    stats = {
        "total_attendees_sampled": total,
        "tldr":           TLDR,
        "role_breakdown": role_stats,
        "company_size":   size_stats,
        "seniority":      senior_stats,
        "working_on":     topic_pills,
    }

    # ── Print summary ─────────────────────────────────────────
    print_section("ROLE BREAKDOWN",  role_stats)
    print_section("COMPANY SIZE",    size_stats)
    print_section("SENIORITY",       senior_stats)

    print(f"\n{'═'*52}")
    print("TLDR")
    print('═'*52)
    print(f"  {TLDR}")

    if topic_pills:
        print(f"\n{'═'*52}")
        print("WHAT THEY ARE WORKING ON")
        print('═'*52)
        print("  Review these — keyword rules are a starting point, not ground truth.")
        print("  Add missing emerging topics to WORKING_ON_KEYWORDS if needed.\n")
        for p in topic_pills:
            tag = "★ " if p['highlight'] else "  "
            print(f"  {tag}{p['label']:<38} {p['count']} talks")

    # ── Write JSON ────────────────────────────────────────────
    out_path = Path(__file__).parent / "attendee_stats.json"
    with open(out_path, "w") as f:
        json.dump(stats, f, indent=2)

    print(f"\n  Stats written to {out_path}")
    print("  Copy numbers into the static section of sponsorship.html.")
    print("  Update TLDR at the top of this file when audience composition")
    print("  changes significantly between seasons.\n")


if __name__ == "__main__":
    main()
