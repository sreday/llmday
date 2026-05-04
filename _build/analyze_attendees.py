"""
analyze_attendees.py  -  Attendee Analysis Playbook
=====================================================
This is the attendee analysis playbook for the conference series.
It reads Luma attendee CSV exports and produces audience profile stats
for use in sponsorship pages.

Works across conference brands (LLMday, SREday, DevOpsNotDead, etc.).
Drop in CSV files from any Luma event export and run:

    python3 _build/analyze_attendees.py path/to/event1.csv path/to/event2.csv ...

Output:
  - Human-readable stats printed to stdout
  - attendee_stats.json written next to this script
  - _event_template/_templates/sponsorship.html patched in place

The sponsorship template is patched automatically between the two sentinel
comments below. Do not remove or rename them:

    {# ── ATTENDEE PROFILE (static - update by running _build/analyze_attendees.py) ── #}
    ...
    {# ── SPEAKER COMPANIES (dynamic ...

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
      Professional researchers - employed at a university, lab, or
      company R&D unit. Distinct from students and from engineers
      whose job happens to involve some research component.

  "Other" must stay under 5% in every dimension.
      If it creeps above that, check the UNCLASSIFIED output printed
      at runtime and add new keyword rules to the relevant classifier.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INFLATION GUARDS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Three rules prevent company-level spikes from distorting the
TOP ATTENDEE COMPANIES ranking and the COMPANY SIZE breakdown.
Priority order (first match wins):

  0.5x  Host companies (HOST_COMPANIES / _HOST_RE)
        Companies that host our conferences at their offices naturally
        have inflated attendance — their employees are "at home".
        Derived from location_string in event metadata.yml files.
        When a new venue host is added, add it to HOST_COMPANIES.

  0.5x  Single-event domination (_find_single_event_companies)
        If a company's attendees all come from a single CSV (one event)
        while multiple CSVs are loaded, they get 0.5x weight. This
        prevents a local company sending 30 people to one conference
        from dominating the ranking over companies with steady presence
        across multiple events. The rule is skipped when only one CSV
        is loaded so it doesn't penalise every company.

  0.8x  Vendor companies (VENDOR_COMPANIES / _VENDOR_RE)
        Companies selling DevOps, SRE, LLM, observability, or cloud
        tools. Their employees attend partly for business development,
        slightly inflating headcount versus end-user companies.
        When a new tool vendor starts sponsoring, add it to
        VENDOR_COMPANIES.

  1.0x  Everyone else (end-user companies, consultancies, etc.)

Affected metrics: TOP ATTENDEE COMPANIES and COMPANY SIZE only.
Role breakdown, seniority, and all other stats are unweighted.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEDUPLICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Attendees are deduplicated by name (lowercased, stripped).
Email addresses must NEVER be used for deduplication or appear
anywhere in this script — attendee privacy is non-negotiable.
"""

import csv, re, sys, json, glob
from collections import Counter
from pathlib import Path


# ══════════════════════════════════════════════════════════════
# PER-CONFERENCE CONTENT
# Update TLDR when running for a new brand or after a significant
# shift in audience composition.
#
# TLDR writing rules:
#   - Focus on what attendees come for and enjoy, not what they don't
#   - Do not imply who holds buying power within their company
#   - Do not mention selling, buying, or pitching in any form
# ══════════════════════════════════════════════════════════════

TLDR = (
    "A hands-on crowd of engineers, ML practitioners, and technical leaders "
    "from startups and enterprises \u2014 actively shipping AI products into production. "
    "Senior practitioners who care about architecture, reliability, and cost, "
    "figuring out how to make agents work at scale."
)
assert len(TLDR) <= 300, f"TLDR is {len(TLDR)} chars \u2014 must be \u2264 300"

# ── "What they are working on" topics ─────────────────────────────────────
# Scored from talk titles + abstracts across all discovered talks.csv files.
# Top WORKING_ON_HIGHLIGHT topics are highlighted on the page; the rest are
# shown as plain pills. Review the output each season - keyword rules are a
# good starting point but can't reason about genuinely novel topics. Add new
# keywords to WORKING_ON_KEYWORDS when the landscape shifts.
WORKING_ON_HIGHLIGHT = 3   # top 3 highlighted, rest plain
WORKING_ON_MAX       = 10  # cap output at 10 pills

WORKING_ON_KEYWORDS = {
    "Agents & Automation":          ['agent', 'agentic', 'autonomous agent', 'orchestrat', 'multi-agent', 'autonom'],
    "Applied / Product AI":         ['product', 'ux', 'user interface', 'human-in-the-loop', 'coaching', 'application'],
    "Production & Reliability":     ['production', 'reliability', 'failure', 'incident', 'on-call', 'slo', 'sla', 'resilience'],
    "LLM Infrastructure & Cost":    ['infrastructure', 'gpu', 'cluster', 'cost', 'capacity', 'compute', 'latency', 'throughput'],
    "RAG & Retrieval":              ['rag', 'retrieval', 'knowledge graph', 'graphrag', 'vector search', 'retriev'],
    "LLM Evaluation & Testing":     ['evaluat', 'benchmark', 'testing', 'evals', 'verification', 'hallucin'],
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
}

ROLE_UNDER5_FALLBACK = {
    "Researcher":               "ML / AI / Data",
    "Independent / Job Seeker": "Business / Consulting / Product",
}

SIZE_UNDER5_FALLBACK = {
    "Scale-up (50-500)":          "Startup (<50)",
    "Solo Founder / Independent": "Startup (<50)",
}


# ══════════════════════════════════════════════════════════════
# ROLE CLASSIFIER
# Rules run top-to-bottom; first match wins.
# ══════════════════════════════════════════════════════════════

ROLE_RULES = [
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
    ("Independent / Job Seeker", lambda t: any(x in t for x in [
        'student', 'phd', 'mba', 'masters', "master's", 'ms cs',
        'grad assistant', 'graduate research', 'open to work',
        'job search', 'alumni engagement', 'data science ra',
        'freelance artist', 'unemployed',
    ]) or t.strip() in ['none']),
    ("Researcher", lambda t: any(x in t for x in [
        'research scientist', 'ai researcher', 'senior research',
        'graduate research assistant', 'research engineer',
        'associate professor', 'lecturer', 'professor',
        'lecturer and scientist', 'pełnomocnik rektora',
    ]) and not any(x in t for x in [
        'product', 'software engineer', 'ml engineer', 'data scientist',
    ])),
    ("Founder / Entrepreneur", lambda t: bool(
        re.search(r'\bfounder\b|co-founder|cofounder', t)
    )),
    ("C-Suite Executive", lambda t: any(x in t for x in [
        'ceo', 'cto', 'cio', 'cpo', 'chief', 'wiceprezes',
    ])),
    ("Director / VP / Head", lambda t: any(x in t for x in [
        'vp ', 'vp of', ' vp', 'vice president', 'director',
        'head of', 'gm &', 'general manager',
    ])),
    ("Engineering Lead / Manager", lambda t: any(x in t for x in [
        'engineering manager', 'platform engineering manager',
        'tech lead', 'team lead', 'sdm', 'engineering lead',
    ])),
    ("DevOps / SRE / Platform", lambda t: any(x in t for x in [
        'devops', 'sre', 'site reliability', 'dev sec ops', 'devsecops',
        'platform engineer', 'developer / sre', 'principal devops',
        'senior devops',
    ])),
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
    ("Product Manager", lambda t: any(x in t for x in [
        'product manager', 'product owner', 'chief product',
        'senior product', 'ai product manager', 'product partnerships',
        'product developer', 'product engineer',
    ]) or t.strip() in ['pm', 'cpo']),
    ("Consultant / Investor", lambda t: any(x in t for x in [
        'consultant', 'investor', 'angel investor', 'investment',
        'business analyst', 'deal origination', 'scout', 'funding',
        'energy consultant', 'marketing consultant', 'pre-sales',
        'entrepreneur', 'technical program manager', 'program manager',
    ]) or t.strip() in [
        'principal', 'partner', 'sr associate', 'owner',
        'analyst', 'architect',
    ]),
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

NO_COMPANY_VALUES = {
    'n/a', 'na', 'n\\a', '-', 'none', 'tbd', 'private', 'it company',
    'student', 'job searching', 'looking for', 'unemployed', 'job seeker',
    'self', '',
}

SOLO_SIGNALS = ['freelance', 'independent', 'self-employed']


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



# Conference host companies — their attendees are "at home" so we count
# them at 0.5× weight to avoid inflating the top-companies ranking.
# Patterns are matched with word boundaries against the lowercased company field.
HOST_COMPANIES = {
    'catawiki', 'ing', 'kyndryl', 'hcltech', 'ilert',
    'pagerduty', 'viam', 'criteo', 'xurrent', 'dynatrace',
    'maibornwolff', 'harness', 'datadog', 'gable', 'microsoft',
}
_HOST_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(h) for h in HOST_COMPANIES) + r')\b'
)

# Vendor companies — they sell DevOps / SRE / LLM / observability / cloud
# tools and attend conferences partly for business development. Counted at
# 0.8× weight. Host/single-event (0.5×) takes precedence when both apply.
VENDOR_COMPANIES = {
    # Observability & monitoring
    'datadog', 'splunk', 'elastic', 'new relic', 'newrelic', 'dynatrace',
    'grafana', 'honeycomb', 'coralogix', 'lightstep', 'sentry',
    'openobserve', 'victoriametrics',
    # Incident management & reliability
    'pagerduty', 'rootly', 'ilert', 'incidentfox', 'firehydrant',
    'jeli', 'xurrent', 'opsgenie',
    # DevOps, CI/CD & infrastructure
    'harness', 'cribl', 'hashicorp', 'spacelift', 'env0', 'circleci',
    'gitlab', 'jfrog', 'docker', 'red hat', 'redhat', 'vmware',
    # Cloud, data & platforms
    'cloudflare', 'mongodb', 'snowflake', 'databricks', 'cockroach',
    'aiven', 'clickhouse', 'confluent', 'imply',
    # Security
    'crowdstrike', 'snyk', 'chainguard', 'gitguardian', 'okta', 'synopsys',
    # Platform, cost & developer tools
    'cast ai', 'komodor', 'stormforge', 'port', 'cortex',
    'servicenow', 'jetbrains',
    # AI / LLM tools
    'anthropic', 'together ai', 'anyscale', 'huggingface',
    # Other sponsors / tool vendors
    'neubird', 'wildmoose', 'traversal', 'pomerium', 'kerno',
    'runwhen', 'mezmo', 'posthog', 'linearb', 'virtana',
}
_VENDOR_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(v) for v in VENDOR_COMPANIES) + r')\b'
)

# Display names for companies whose CSV values don't title-case cleanly
COMPANY_DISPLAY_NAMES = {
    # Acronyms / all-caps
    'aws': 'AWS', 'ibm': 'IBM', 'ing': 'ING', 'sap': 'SAP', 'hp': 'HP',
    'hcltech': 'HCLTech',
    # Brand casing
    'cast ai': 'CAST AI', 'pagerduty': 'PagerDuty', 'clickhouse': 'ClickHouse',
    'datadog': 'Datadog', 'openobserve': 'OpenObserve',
    'posthog': 'PostHog', 'ilert': 'iLert', 'rootly': 'Rootly',
    'spacelift': 'Spacelift', 'new relic': 'New Relic',
    'monday.com': 'Monday.com', 'victoriametrics': 'VictoriaMetrics',
    'linearb': 'LinearB',
    # Attendee-specific
    'pwc': 'PwC', 'ey': 'EY', 'n26': 'N26', 'lg': 'LG', 'gog.com': 'GOG.com',
}


def _find_single_event_companies(rows):
    """Return the set of display-name companies whose attendees all come from
    a single event CSV. Only meaningful when multiple CSVs are loaded."""
    all_events = {r.get('_source', '') for r in rows}
    if len(all_events) <= 1:
        return set()
    company_events = {}
    for r in rows:
        company = r.get('Company', '').strip()
        c_lower = company.lower()
        if not c_lower or c_lower in NO_COMPANY_VALUES or c_lower.startswith('looking for'):
            continue
        if any(x in c_lower for x in SOLO_SIGNALS):
            continue
        display = COMPANY_DISPLAY_NAMES.get(c_lower, company)
        company_events.setdefault(display, set()).add(r.get('_source', ''))
    return {d for d, evts in company_events.items() if len(evts) == 1}


def _company_weight(c_lower, display, single_event):
    """0.5x for host/single-event companies, 0.8x for vendors, else 1."""
    if _HOST_RE.search(c_lower) or display in single_event:
        return 0.5
    if _VENDOR_RE.search(c_lower):
        return 0.8
    return 1




def print_section(title, items):
    print(f"\n{'═'*52}")
    print(title)
    print('═'*52)
    for item in items:
        bar = "█" * (item["pct"] // 2)
        print(f"  {item['label']:<35} {item['pct']:>3}%  {bar}")


# ══════════════════════════════════════════════════════════════
# SPONSORSHIP TEMPLATE PATCHER
# ══════════════════════════════════════════════════════════════

def render_attendee_profile_block(tldr, role_stats, size_stats, senior_stats, topic_pills):
    """Render the static attendee profile HTML block.

    The "Top companies that have attended" card is hard-baked in the template
    and lives ABOVE the sentinel comment, so this function does not touch it.
    """

    def bar_item(label, p):
        label_html = label.replace('<', '&lt;')
        return (
            f'              <li class="sp-stats-bar-item">\n'
            f'                <div class="sp-stats-bar-header">'
            f'<span class="sp-stats-bar-name">{label_html}</span>'
            f'<span class="sp-stats-bar-count">{p}%</span></div>\n'
            f'                <div class="sp-stats-bar-track">'
            f'<div class="sp-stats-bar-fill" style="width:{p}%"></div></div>\n'
            f'              </li>'
        )

    def tag(label, hot):
        cls = 'sp-stats-tag hot' if hot else 'sp-stats-tag'
        label_html = label.replace('&', '&amp;')
        return f'              <span class="{cls}">{label_html}</span>'

    role_bars   = '\n'.join(bar_item(r['label'], r['pct']) for r in role_stats)
    size_bars   = '\n'.join(bar_item(r['label'], r['pct']) for r in size_stats)
    senior_bars = '\n'.join(bar_item(r['label'], r['pct']) for r in senior_stats)
    topic_tags  = '\n'.join(tag(p['label'], p['highlight']) for p in topic_pills)

    return (
        '{# ── ATTENDEE PROFILE (static - update by running _build/analyze_attendees.py) ── #}\n'
        '        <div class="sp-stats-grid-2">\n'
        '\n'
        '          <div class="sp-stats-card">\n'
        '            <div class="sp-stats-card-title">Role breakdown</div>\n'
        '            <ul class="sp-stats-bar-list">\n'
        f'{role_bars}\n'
        '            </ul>\n'
        '          </div>\n'
        '\n'
        '          <div class="sp-stats-card">\n'
        '            <div class="sp-stats-card-title">Attendee seniority</div>\n'
        '            <ul class="sp-stats-bar-list">\n'
        f'{senior_bars}\n'
        '            </ul>\n'
        '          </div>\n'
        '\n'
        '          <div class="sp-stats-card">\n'
        '            <div class="sp-stats-card-title">Company size</div>\n'
        '            <ul class="sp-stats-bar-list">\n'
        f'{size_bars}\n'
        '            </ul>\n'
        '          </div>\n'
        '\n'
        '          <div class="sp-stats-card">\n'
        '            <div class="sp-stats-card-title">What they are working on</div>\n'
        '            <div class="sp-stats-tag-cloud">\n'
        f'{topic_tags}\n'
        '            </div>\n'
        '          </div>\n'
        '\n'
        '        </div>\n'
        '\n'
        '        <hr class="sp-stats-sep">\n'
        '\n'
        '        '
    )


def patch_sponsorship_template(repo_root, tldr, role_stats, size_stats, senior_stats, topic_pills):
    """
    Patch the static attendee profile block in
    _event_template/_templates/sponsorship.html in place.

    Rewrites content between the two sentinel comments:
      START: {# ── ATTENDEE PROFILE (static ...
      END:   {# ── SPEAKER COMPANIES (dynamic ...
    """
    template_path = repo_root / '_event_template' / '_templates' / 'sponsorship.html'

    if not template_path.exists():
        print(f"\n  ⚠  Template not found at {template_path} - skipping patch.")
        return

    original = template_path.read_text(encoding='utf-8')

    start_sentinel = '{# ── ATTENDEE PROFILE'
    end_sentinel   = '{# ── SPEAKER COMPANIES'

    start_idx = original.find(start_sentinel)
    end_idx   = original.find(end_sentinel)

    if start_idx == -1 or end_idx == -1:
        print("\n  ⚠  Sentinel comments not found in template - skipping patch.")
        print("     Expected markers:")
        print(f"       '{start_sentinel}...'")
        print(f"       '{end_sentinel}...'")
        return

    new_block = render_attendee_profile_block(
        tldr, role_stats, size_stats, senior_stats, topic_pills
    )

    patched = original[:start_idx] + new_block + original[end_idx:]
    template_path.write_text(patched, encoding='utf-8')
    print(f"\n  ✓  Sponsorship template patched: {template_path}")


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
        for r in loaded:
            r['_source'] = Path(p).stem
        rows.extend(loaded)

    # De-duplicate by name - treat all CSVs as one combined event.
    # Keep the first occurrence (earliest file loaded).
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

    # ── Identify inflated companies (host / single-event) ──────
    single_event = _find_single_event_companies(rows)

    # ── Classify ──────────────────────────────────────────────
    raw_role_counts = Counter(classify_role(r.get('Job Title', ''))       for r in rows)
    senior_counts   = Counter(classify_seniority(r.get('Job Title', ''))  for r in rows)

    # Company size uses 0.5x weight for host / single-event companies
    size_counts = Counter()
    for r in rows:
        company = r.get('Company', '').strip()
        c_lower = company.lower()
        display = COMPANY_DISPLAY_NAMES.get(c_lower, company)
        label = classify_company_size(company)
        size_counts[label] += _company_weight(c_lower, display, single_event)

    role_counts = Counter()
    for label, count in raw_role_counts.items():
        display_label = ROLE_DISPLAY_MERGE.get(label, label)
        role_counts[display_label] += count

    def merge_under5(counts, fallback_map, denom=None):
        if denom is None:
            denom = total
        changed = True
        while changed:
            changed = False
            for label, count in list(counts.items()):
                if pct(count, denom) < 5 and label in fallback_map:
                    target = fallback_map[label]
                    counts[target] += counts.pop(label)
                    print(f"  Merged '{label}' ({pct(count, denom)}%) into '{target}' (under 5% rule)")
                    changed = True
        return counts

    role_counts = merge_under5(role_counts, ROLE_UNDER5_FALLBACK)
    size_counts = merge_under5(size_counts, SIZE_UNDER5_FALLBACK, sum(size_counts.values()))

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
        print(f"\n  Unclassified job titles ({len(unclassified)}) - add rules for these:")
        for t in sorted(set(unclassified)):
            print(f"    {t}")

    # ── Build stats ───────────────────────────────────────────
    role_stats   = top_n(role_counts,   total)
    size_total   = sum(size_counts.values())
    size_stats   = top_n(size_counts,   size_total)
    senior_stats = top_n(senior_counts, total)

    # ── Topics from talks.csv ─────────────────────────────────
    repo_root = Path(__file__).parent.parent
    topic_pills, talks_files = extract_topics(repo_root)
    if talks_files:
        print(f"\n  Found {len(talks_files)} talks.csv file(s) for topic extraction.")
    else:
        print("\n  No talks.csv files found - skipping topic extraction.")
        print(f"  Expected location: {repo_root}/20*/_db/talks.csv")

    stats = {
        "total_attendees_sampled": total,
        "tldr":            TLDR,
        "role_breakdown":  role_stats,
        "company_size":    size_stats,
        "seniority":       senior_stats,
        "working_on":      topic_pills,
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
        print("  Review these - keyword rules are a starting point, not ground truth.")
        print("  Add missing emerging topics to WORKING_ON_KEYWORDS if needed.\n")
        for p in topic_pills:
            tag = "★ " if p['highlight'] else "  "
            print(f"  {tag}{p['label']:<38} {p['count']} talks")

    # ── Write JSON ────────────────────────────────────────────
    out_path = Path(__file__).parent / "attendee_stats.json"
    with open(out_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"\n  Stats written to {out_path}")

    # ── Patch sponsorship template in place ───────────────────
    patch_sponsorship_template(
        repo_root, TLDR, role_stats, size_stats, senior_stats, topic_pills
    )
    print("  Done. Commit and push to deploy.\n")


if __name__ == "__main__":
    main()
