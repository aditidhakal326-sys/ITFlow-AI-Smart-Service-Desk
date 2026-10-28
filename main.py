
import html
import json
import sqlite3
import textwrap
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime

import streamlit as st


# ==================================================
# 1. APPLICATION SETTINGS
# ==================================================

DB = "tickets.db"
MODEL = "qwen3:0.6b"

TEAMS = {
    "Account Access": "IT Help Desk",
    "Reporting Issue": "Data & Reporting",
    "Network Issue": "Network Support",
    "Hardware Issue": "IT Hardware",
    "Software Issue": "Application Support",
    "Other": "IT Help Desk",
}

CATEGORIES = list(TEAMS)
PRIORITIES = ["Low", "Medium", "High", "Critical"]
STATUSES = ["Open", "In Progress", "Resolved"]

st.set_page_config(
    page_title="ITFlow AI | Smart Service Desk",
    page_icon="🧠",
    layout="wide"
)


# ==================================================
# 2. HELPER FUNCTIONS
# ==================================================

def html_block(markup):
    cleaned = textwrap.dedent(markup).strip()
    st.markdown(cleaned, unsafe_allow_html=True)


def esc(value):
    return html.escape(str(value), quote=True)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect():
    return sqlite3.connect(DB)


# ==================================================
# 3. DATABASE SETUP
# ==================================================

def init_database():

    with connect() as con:

        # Existing ticket table
        con.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                category TEXT,
                priority TEXT,
                assigned_team TEXT,
                created_at TEXT,
                status TEXT NOT NULL DEFAULT 'Open'
            )
        """)

        columns = [
            row[1]
            for row in con.execute(
                "PRAGMA table_info(tickets)"
            )
        ]

        if "status" not in columns:
            con.execute("""
                ALTER TABLE tickets
                ADD COLUMN status TEXT NOT NULL DEFAULT 'Open'
            """)

        # Existing human review history table
        con.execute("""
            CREATE TABLE IF NOT EXISTS ticket_review_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                ai_category TEXT,
                ai_priority TEXT,
                previous_category TEXT,
                previous_priority TEXT,
                approved_category TEXT NOT NULL,
                approved_priority TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                changed_at TEXT NOT NULL,
                FOREIGN KEY(ticket_id) REFERENCES tickets(id)
            )
        """)

        # ------------------------------------------
        # NEW FEATURE: LOCAL TICKET ROUTING QUEUE
        # ------------------------------------------
        # The table starts empty.
        # No external notifications are sent yet.

        con.execute("""
            CREATE TABLE IF NOT EXISTS ticket_routing_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL UNIQUE,
                assigned_team TEXT NOT NULL,
                routing_status TEXT NOT NULL DEFAULT 'Pending',
                created_at TEXT NOT NULL,
                processed_at TEXT,
                error_message TEXT,
                FOREIGN KEY(ticket_id) REFERENCES tickets(id)
            )
        """)


        # Actual LOCAL delivery storage (no email/network delivery).
        con.execute("""
            CREATE TABLE IF NOT EXISTS team_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL UNIQUE,
                team TEXT NOT NULL,
                delivered_at TEXT NOT NULL,
                acknowledged_at TEXT,
                acknowledged_by TEXT,
                FOREIGN KEY(ticket_id) REFERENCES tickets(id)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS ticket_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(ticket_id) REFERENCES tickets(id)
            )
        """)


# ==================================================
# 4. RETRIEVE DATABASE INFORMATION
# ==================================================

def get_tickets():

    with connect() as con:
        return con.execute("""
            SELECT
                id,
                description,
                category,
                priority,
                assigned_team,
                created_at,
                status
            FROM tickets
            ORDER BY id DESC
        """).fetchall()


def get_history(ticket_id):

    with connect() as con:
        return con.execute("""
            SELECT
                action,
                ai_category,
                ai_priority,
                previous_category,
                previous_priority,
                approved_category,
                approved_priority,
                note,
                changed_at
            FROM ticket_review_history
            WHERE ticket_id = ?
            ORDER BY id DESC
        """, (ticket_id,)).fetchall()


# ==================================================
# 5. SAVE A HUMAN-REVIEWED TICKET
# ==================================================

def save_ticket(
    description,
    category,
    priority,
    ai_category,
    ai_priority
):

    if category not in CATEGORIES or priority not in PRIORITIES:
        raise ValueError("Invalid category or priority")

    timestamp = now()

    with connect() as con:

        cursor = con.execute("""
            INSERT INTO tickets (
                description,
                category,
                priority,
                assigned_team,
                created_at,
                status
            )
            VALUES (?, ?, ?, ?, ?, 'Open')
        """, (
            description,
            category,
            priority,
            TEAMS[category],
            timestamp
        ))

        ticket_id = cursor.lastrowid

        changed = (
            category != ai_category
            or priority != ai_priority
        )

        note = (
            "Reviewer corrected AI suggestion"
            if changed
            else "Reviewer confirmed AI suggestion"
        )

        con.execute("""
            INSERT INTO ticket_review_history (
                ticket_id,
                action,
                ai_category,
                ai_priority,
                previous_category,
                previous_priority,
                approved_category,
                approved_priority,
                note,
                changed_at
            )
            VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
        """, (
            ticket_id,
            "Created and reviewed",
            ai_category,
            ai_priority,
            category,
            priority,
            note,
            timestamp
        ))

        # Queue the approved ticket in the same SQLite transaction.
        # This is a local record only; no notifications are sent.
        con.execute("""
            INSERT INTO ticket_routing_queue (
                ticket_id, assigned_team, routing_status, created_at
            ) VALUES (?, ?, 'Pending', ?)
        """, (ticket_id, TEAMS[category], timestamp))

    return ticket_id


# ==================================================
# 6. UPDATE TICKET STATUS
# ==================================================

def update_status(ticket_id, status):

    if status not in STATUSES:
        raise ValueError("Invalid status")

    with connect() as con:
        con.execute("""
            UPDATE tickets
            SET status = ?
            WHERE id = ?
        """, (status, ticket_id))


# ==================================================
# 7. CORRECT A SAVED TICKET
# ==================================================

def correct_ticket(
    ticket_id,
    category,
    priority,
    reason
):

    if category not in CATEGORIES or priority not in PRIORITIES:
        raise ValueError("Invalid category or priority")

    if not reason.strip():
        raise ValueError("Please provide a correction reason")

    with connect() as con:

        previous = con.execute("""
            SELECT category, priority
            FROM tickets
            WHERE id = ?
        """, (ticket_id,)).fetchone()

        if previous is None:
            raise ValueError("Ticket not found")

        if previous == (category, priority):
            return False

        delivered = con.execute("""
            SELECT team FROM team_deliveries WHERE ticket_id = ?
        """, (ticket_id,)).fetchone()
        if delivered and delivered[0] != TEAMS[category]:
            raise ValueError(
                "This ticket has already been delivered to a team. "
                "Resolve its delivery assignment before changing the category."
            )

        con.execute("""
            UPDATE tickets
            SET category = ?,
                priority = ?,
                assigned_team = ?
            WHERE id = ?
        """, (
            category,
            priority,
            TEAMS[category],
            ticket_id
        ))

        con.execute("""
            INSERT INTO ticket_review_history (
                ticket_id,
                action,
                ai_category,
                ai_priority,
                previous_category,
                previous_priority,
                approved_category,
                approved_priority,
                note,
                changed_at
            )
            VALUES (
                ?,
                'Saved ticket corrected',
                NULL,
                NULL,
                ?, ?, ?, ?, ?, ?
            )
        """, (
            ticket_id,
            previous[0],
            previous[1],
            category,
            priority,
            reason.strip(),
            now()
        ))

        # Keep a pending request aligned with the approved category.
        # Processed records are left intact for manual review.
        con.execute("""
            UPDATE ticket_routing_queue
            SET assigned_team = ?
            WHERE ticket_id = ? AND routing_status = 'Pending'
        """, (TEAMS[category], ticket_id))

    return True


# ==================================================
# ROUTING QUEUE — LOCAL SIMULATION ONLY
# ==================================================

ROUTING_STATUSES = ["Pending", "Routed", "Failed"]


def get_routing_queue():
    with connect() as con:
        return con.execute("""
            SELECT q.id, q.ticket_id, q.assigned_team,
                   q.routing_status, q.created_at,
                   q.processed_at, q.error_message,
                   t.description, t.priority, t.status
            FROM ticket_routing_queue AS q
            JOIN tickets AS t ON t.id = q.ticket_id
            ORDER BY q.id DESC
        """).fetchall()


def queue_existing_ticket(ticket_id):
    # Only allow reviewed, unresolved tickets that aren't already queued.
    with connect() as con:
        ticket = con.execute("""
            SELECT assigned_team, status FROM tickets WHERE id = ?
        """, (ticket_id,)).fetchone()
        if ticket is None:
            raise ValueError("Ticket not found.")
        if ticket[1] == "Resolved":
            raise ValueError("Resolved tickets cannot be newly queued.")
        reviewed = con.execute("""
            SELECT 1 FROM ticket_review_history
            WHERE ticket_id = ? LIMIT 1
        """, (ticket_id,)).fetchone()
        if reviewed is None:
            raise ValueError("Only reviewed tickets can be queued.")

        try:
            con.execute("""
                INSERT INTO ticket_routing_queue
                (ticket_id, assigned_team, routing_status, created_at)
                VALUES (?, ?, 'Pending', ?)
            """, (ticket_id, ticket[0], now()))
        except sqlite3.IntegrityError as exc:
            raise ValueError("This ticket is already in the queue.") from exc


def update_routing_status(queue_id, new_status, failure_reason=""):
    if new_status not in ROUTING_STATUSES:
        raise ValueError("Invalid routing status.")
    if new_status == "Failed" and not failure_reason.strip():
        raise ValueError("Enter a failure reason first.")

    with connect() as con:
        queue = con.execute("""
            SELECT routing_status FROM ticket_routing_queue
            WHERE id = ?
        """, (queue_id,)).fetchone()
        if queue is None:
            raise ValueError("Routing queue item not found.")

        con.execute("""
            UPDATE ticket_routing_queue
            SET routing_status = ?, processed_at = ?, error_message = ?
            WHERE id = ?
        """, (
            new_status,
            now() if new_status != "Pending" else None,
            failure_reason.strip() if new_status == "Failed" else None,
            queue_id
        ))


# ==================================================
# LOCAL DELIVERY, ACKNOWLEDGMENT, AND AUDIT
# ==================================================

def deliver_pending_tickets():
    """Atomically move approved pending queue items into persistent local inboxes."""
    delivered_ids = []
    with connect() as con:
        pending = con.execute("""
            SELECT q.id, q.ticket_id, t.assigned_team
            FROM ticket_routing_queue AS q
            JOIN tickets AS t ON t.id = q.ticket_id
            WHERE q.routing_status = 'Pending'
              AND t.status != 'Resolved'
            ORDER BY q.id ASC
        """).fetchall()
        for queue_id, ticket_id, team in pending:
            # Unique ticket ID makes repeated processing safe.
            cursor = con.execute("""
                INSERT OR IGNORE INTO team_deliveries
                (ticket_id, team, delivered_at)
                VALUES (?, ?, ?)
            """, (ticket_id, team, now()))
            if cursor.rowcount == 1:
                delivered_ids.append(ticket_id)
                con.execute("""
                    INSERT INTO ticket_events
                    (ticket_id, event_type, details, created_at)
                    VALUES (?, 'Delivered locally', ?, ?)
                """, (ticket_id, f"Delivered to {team} inbox", now()))
            con.execute("""
                UPDATE ticket_routing_queue
                SET assigned_team = ?, routing_status = 'Routed',
                    processed_at = ?, error_message = NULL
                WHERE id = ?
            """, (team, now(), queue_id))
    return delivered_ids


def get_team_deliveries(team=None):
    with connect() as con:
        return con.execute("""
            SELECT d.ticket_id, d.team, d.delivered_at,
                   d.acknowledged_at, d.acknowledged_by,
                   t.description, t.category, t.priority, t.status
            FROM team_deliveries AS d
            JOIN tickets AS t ON t.id = d.ticket_id
            WHERE (? IS NULL OR d.team = ?)
            ORDER BY (d.acknowledged_at IS NOT NULL), d.id DESC
        """, (team, team)).fetchall()


def acknowledge_delivery(ticket_id, team, agent):
    agent = agent.strip()
    if not agent:
        raise ValueError("Enter your name to acknowledge the ticket.")
    if len(agent) > 100:
        raise ValueError("Name is too long.")
    with connect() as con:
        delivery = con.execute("""
            SELECT team, acknowledged_at
            FROM team_deliveries WHERE ticket_id = ?
        """, (ticket_id,)).fetchone()
        if delivery is None or delivery[0] != team:
            raise ValueError("Ticket is not delivered to the selected team.")
        if delivery[1] is not None:
            raise ValueError("This ticket is already acknowledged.")
        con.execute("""
            UPDATE team_deliveries
            SET acknowledged_at = ?, acknowledged_by = ?
            WHERE ticket_id = ? AND acknowledged_at IS NULL
        """, (now(), agent, ticket_id))
        con.execute("""
            UPDATE tickets SET status = 'In Progress'
            WHERE id = ? AND status = 'Open'
        """, (ticket_id,))
        con.execute("""
            INSERT INTO ticket_events
            (ticket_id, event_type, details, created_at)
            VALUES (?, 'Acknowledged', ?, ?)
        """, (ticket_id, f"{agent} acknowledged for {team}", now()))


def finish_delivered_ticket(ticket_id, team, agent, resolution):
    agent = agent.strip()
    resolution = resolution.strip()
    if not agent or not resolution:
        raise ValueError("Enter both your name and a resolution note.")
    if len(resolution) > 2000:
        raise ValueError("Resolution note is too long.")
    with connect() as con:
        delivery = con.execute("""
            SELECT acknowledged_at FROM team_deliveries
            WHERE ticket_id = ? AND team = ?
        """, (ticket_id, team)).fetchone()
        if delivery is None or delivery[0] is None:
            raise ValueError("A delivered ticket must be acknowledged first.")
        status = con.execute(
            "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if status is None or status[0] == 'Resolved':
            raise ValueError("Ticket is already resolved or missing.")
        con.execute("UPDATE tickets SET status='Resolved' WHERE id=?", (ticket_id,))
        con.execute("""
            INSERT INTO ticket_events
            (ticket_id, event_type, details, created_at)
            VALUES (?, 'Resolved', ?, ?)
        """, (ticket_id, f"{agent}: {resolution}", now()))


def get_ticket_events(ticket_id):
    with connect() as con:
        return con.execute("""
            SELECT event_type, details, created_at
            FROM ticket_events WHERE ticket_id = ?
            ORDER BY id DESC
        """, (ticket_id,)).fetchall()


# ==================================================
# 8. PRIORITY SAFETY CHECK
# ==================================================

def adjust_priority(description, priority):

    text = description.lower()

    company = any(
        phrase in text
        for phrase in [
            "entire company",
            "entire company's",
            "company-wide",
            "all employees",
            "whole organization"
        ]
    )

    outage = any(
        phrase in text
        for phrase in [
            "network is down",
            "internet is down",
            "system outage",
            "systems are down",
            "operations have completely stopped",
            "business operations have stopped"
        ]
    )

    if company and outage:
        return "Critical"

    workaround = any(
        phrase in text
        for phrase in [
            "i can use an external keyboard",
            "i can use another keyboard",
            "i can still work",
            "i have a workaround",
            "i can use an alternative"
        ]
    )

    minor = any(
        phrase in text
        for phrase in [
            "occasionally",
            "sometimes",
            "minor inconvenience"
        ]
    )

    broader = any(
        phrase in text
        for phrase in [
            "entire department",
            "whole department",
            "multiple employees",
            "multiple users",
            "everyone",
            "all employees",
            "business operations",
            "cannot work",
            "unable to work",
            "production down",
            "payroll"
        ]
    )

    if (
        priority == "Medium"
        and workaround
        and minor
        and not broader
    ):
        return "Low"

    return priority


# ==================================================
# 9. OLLAMA AI CLASSIFICATION
# ==================================================

def classify_ticket(description):

    prompt = """
You classify IT support requests.

Reply ONLY with a valid JSON object containing:
- category
- priority

CATEGORIES:

Account Access:
Login failures, rejected credentials,
password problems, locked accounts.

Reporting Issue:
Power BI, dashboards, reports, refresh errors.

Network Issue:
Wi-Fi, VPN, internet connectivity,
network outages.

Hardware Issue:
Laptops, keyboards, monitors, printers,
physical equipment problems.

Software Issue:
Application crashes, installations,
software errors.

Other:
Problems not fitting these categories.

IMPORTANT:
Do not classify a laptop shutdown as Network Issue
unless network failure is explicitly described.

PRIORITY:

Low:
Minor inconvenience.
Employee can still work using a workaround.

Medium:
One employee cannot complete an important task.
No practical workaround is available.

High:
Multiple employees or important business processes
are significantly affected.

Critical:
Company-wide outage or major operations stopped.

Do not invent business impact.
If impact is unclear, choose Medium.

Allowed categories:
Account Access, Reporting Issue, Network Issue,
Hardware Issue, Software Issue, Other.

Allowed priorities:
Low, Medium, High, Critical.

Example:
{
    "category": "Network Issue",
    "priority": "Medium"
}

Return JSON only.

TICKET:
""" + description

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "format": "json",
        "stream": False,
        "think": False,
        "keep_alive": "5m",
        "options": {
            "temperature": 0,
            "num_predict": 120,
            "num_ctx": 2048
        }
    }

    request = urllib.request.Request(
        "http://localhost:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=120
        ) as response:
            output = json.load(response)

        result = json.loads(
            output["message"]["content"]
        )

        category = result["category"]
        priority = result["priority"]

    except (
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        KeyError,
        TypeError
    ) as error:
        raise RuntimeError(
            f"Ollama classification failed: {error}"
        ) from error

    if category not in CATEGORIES:
        raise RuntimeError(
            f"Unexpected AI category: {category}"
        )

    if priority not in PRIORITIES:
        raise RuntimeError(
            f"Unexpected AI priority: {priority}"
        )

    priority = adjust_priority(
        description,
        priority
    )

    return category, priority


# ==================================================
# 10. CUSTOM UI DESIGN
# ==================================================

st.markdown("""
<style>
.block-container {
    max-width:1320px;
    padding-top:1.4rem;
    padding-bottom:3rem;
}

.itflow-hero {
    background:linear-gradient(
        115deg,#0e2438 0%,#183e53 57%,#0d817c 100%
    );
    border-radius:22px;
    padding:35px 38px;
    color:#fff;
    margin-bottom:25px;
    box-shadow:0 12px 32px rgba(10,39,55,.12);
}

.itflow-eyebrow {
    letter-spacing:2px;
    text-transform:uppercase;
    font-size:12px;
    font-weight:800;
    color:#a8f2eb;
    margin-bottom:13px;
}

.itflow-title {
    font-size:clamp(28px,3vw,42px);
    line-height:1.2;
    font-weight:800;
    color:white;
    margin-bottom:12px;
}

.itflow-subtitle {
    font-size:15px;
    color:#d3e8ed;
    max-width:760px;
    line-height:1.8;
}

.itflow-tags {
    display:flex;
    flex-wrap:wrap;
    gap:9px;
    margin-top:20px;
}

.itflow-tag {
    border:1px solid rgba(255,255,255,.26);
    border-radius:22px;
    padding:7px 12px;
    color:white;
    background:rgba(255,255,255,.09);
    font-size:12px;
}

.itflow-kicker {
    font-size:11px;
    letter-spacing:1.6px;
    color:#0d8d87;
    text-transform:uppercase;
    font-weight:800;
    margin-bottom:4px;
}

.itflow-section {
    font-size:24px;
    color:#18344c;
    font-weight:800;
    margin-bottom:5px;
}

.itflow-description {
    font-size:13px;
    color:#75849a;
    margin-bottom:18px;
}

.itflow-kpi {
    background:white;
    border:1px solid #e4eaf0;
    border-top:4px solid var(--accent);
    border-radius:16px;
    padding:21px;
    min-height:125px;
    margin-bottom:12px;
    box-shadow:0 5px 16px rgba(15,35,55,.035);
}

.itflow-kpi-label {
    font-size:12px;
    color:#6d7d91;
    font-weight:650;
}

.itflow-kpi-num {
    font-size:31px;
    color:#16354d;
    font-weight:800;
    margin:8px 0 3px;
}

.itflow-kpi-note {
    font-size:11px;
    color:#8994a3;
}

.itflow-chart {
    background:white;
    border:1px solid #e4eaf0;
    border-radius:16px;
    padding:21px;
    margin-bottom:16px;
    box-shadow:0 5px 16px rgba(15,35,55,.035);
}

.itflow-chart-title {
    font-size:16px;
    font-weight:800;
    color:#16354d;
}

.itflow-chart-sub {
    font-size:12px;
    color:#8b98a7;
    margin-bottom:20px;
}

.itflow-bar-label {
    display:flex;
    justify-content:space-between;
    gap:14px;
    font-size:12px;
    color:#536a7d;
    font-weight:600;
    margin-bottom:7px;
}

.itflow-track {
    height:10px;
    background:#ecf1f5;
    border-radius:50px;
    overflow:hidden;
    margin-bottom:17px;
}

.itflow-fill {
    height:100%;
    border-radius:50px;
}

.itflow-ring {
    width:185px;
    height:185px;
    display:flex;
    align-items:center;
    justify-content:center;
    border-radius:50%;
    margin:12px auto 24px;
}

.itflow-hole {
    width:125px;
    height:125px;
    border-radius:50%;
    background:white;
    display:flex;
    flex-direction:column;
    align-items:center;
    justify-content:center;
    color:#17354d;
}

.itflow-ring-total {
    font-size:30px;
    font-weight:800;
}

.itflow-ring-note {
    font-size:11px;
    color:#8794a4;
}

.itflow-legend {
    display:flex;
    justify-content:space-between;
    align-items:center;
    padding:8px 0;
    border-bottom:1px solid #eef2f6;
    font-size:12px;
    color:#536a7d;
}

.itflow-swatch {
    display:inline-block;
    width:10px;
    height:10px;
    border-radius:50%;
    margin-right:9px;
}

.itflow-footer {
    text-align:center;
    color:#8091a5;
    font-size:12px;
    padding:28px 0;
}

.stButton button {
    border-radius:9px;
}

div[data-testid="stExpander"] {
    border-radius:12px;
}

button[kind="primary"],
button[data-testid="stBaseButton-primary"] {
    background:#0f998d !important;
    border:1px solid #0f998d !important;
    color:white !important;
}

button[kind="primary"]:hover,
button[data-testid="stBaseButton-primary"]:hover {
    background:#087f76 !important;
    border-color:#087f76 !important;
}

button[role="tab"][aria-selected="true"] {
    color:#0f998d !important;
    border-bottom-color:#0f998d !important;
}

@media(max-width:700px) {
    .itflow-hero {
        padding:24px 20px;
    }

    .itflow-chart {
        padding:15px;
    }

    .itflow-kpi {
        padding:15px;
        min-height:100px;
    }
}
</style>
""", unsafe_allow_html=True)


# ==================================================
# 11. UI HELPER FUNCTIONS
# ==================================================

def section(kicker, title, subtitle=""):

    html_block(
        f'<div class="itflow-kicker">{esc(kicker)}</div>'
        f'<div class="itflow-section">{esc(title)}</div>'
        f'<div class="itflow-description">{esc(subtitle)}</div>'
    )


def kpi(label, number, note, accent):

    html_block(
        f'<div class="itflow-kpi" style="--accent:{accent}">'
        f'<div class="itflow-kpi-label">{esc(label)}</div>'
        f'<div class="itflow-kpi-num">{esc(number)}</div>'
        f'<div class="itflow-kpi-note">{esc(note)}</div>'
        '</div>'
    )


def bars(
    title,
    subtitle,
    counts,
    colors,
    fixed_colors=None
):

    rows = []

    maximum = max(
        counts.values(),
        default=0
    ) or 1

    sorted_counts = sorted(
        counts.items(),
        key=lambda item: item[1],
        reverse=True
    )

    for index, (name, amount) in enumerate(sorted_counts):

        width = max(
            0,
            min(100, 100 * amount / maximum)
        )

        color = (fixed_colors or {}).get(
            name,
            colors[index % len(colors)]
        )

        rows.append(
            f'<div class="itflow-bar-label">'
            f'<span>{esc(name)}</span>'
            f'<strong>{amount}</strong>'
            '</div>'
            '<div class="itflow-track">'
            f'<div class="itflow-fill" '
            f'style="width:{width:.1f}%;'
            f'background:{color}"></div>'
            '</div>'
        )

    content = (
        "".join(rows)
        if rows
        else "<p>No ticket data yet.</p>"
    )

    html_block(
        '<div class="itflow-chart">'
        f'<div class="itflow-chart-title">{esc(title)}</div>'
        f'<div class="itflow-chart-sub">{esc(subtitle)}</div>'
        f'{content}'
        '</div>'
    )


def ring_chart(title, subtitle, counts):

    palette = [
        "#0f998d",
        "#3b82f6",
        "#f59e0b",
        "#8b5cf6"
    ]

    total = sum(counts.values())

    sections = []
    legend = []
    current = 0

    for index, (name, amount) in enumerate(counts.items()):

        color = palette[index % len(palette)]

        percent = (
            100 * amount / total
            if total else 0
        )

        if percent:
            sections.append(
                f"{color} {current:.2f}% "
                f"{current + percent:.2f}%"
            )

        current += percent

        legend.append(
            '<div class="itflow-legend">'
            '<span>'
            f'<span class="itflow-swatch" '
            f'style="background:{color}"></span>'
            f'{esc(name)}'
            '</span>'
            f'<strong>{amount} · {percent:.0f}%</strong>'
            '</div>'
        )

    gradient = (
        f'conic-gradient({", ".join(sections)})'
        if sections
        else "#edf2f7"
    )

    html_block(
        '<div class="itflow-chart">'
        f'<div class="itflow-chart-title">{esc(title)}</div>'
        f'<div class="itflow-chart-sub">{esc(subtitle)}</div>'
        f'<div class="itflow-ring" '
        f'style="background:{gradient}">'
        '<div class="itflow-hole">'
        f'<div class="itflow-ring-total">{total}</div>'
        '<div class="itflow-ring-note">Total tickets</div>'
        '</div>'
        '</div>'
        f'{"".join(legend)}'
        '</div>'
    )


# ==================================================
# 12. INITIALIZE DATABASE AND DISPLAY HEADER
# ==================================================

init_database()

html_block("""
<div class="itflow-hero">
<div class="itflow-eyebrow">ITFLOW AI · SMART SERVICE DESK</div>
<div class="itflow-title">Intelligent IT Support, Simplified.</div>
<div class="itflow-subtitle">
Analyze IT incidents using local AI, review recommendations,
manage ticket progress, and explore support analytics—all in one workspace.
</div>
<div class="itflow-tags">
<span class="itflow-tag">✦ Local AI</span>
<span class="itflow-tag">✓ Human Review</span>
<span class="itflow-tag">▣ Team Assignment</span>
<span class="itflow-tag">◉ Ticket Analytics</span>
</div>
</div>
""")

st.caption(
    "Navigate between ticket creation, management, "
    "analytics, and model evaluation."
)


# ==================================================
# 13. NAVIGATION TABS
# ==================================================

tab_new, tab_manage, tab_analytics, tab_evaluation, tab_routing = st.tabs([
    "✦ Create Ticket",
    "▤ Ticket Manager",
    "◉ Analytics",
    "▧ AI Evaluation",
    "↗ Routing Queue"
])


# ==================================================
# 14. CREATE TICKET TAB
# ==================================================

with tab_new:

    section(
        "WORKSPACE / NEW REQUEST",
        "Create an IT Support Ticket",
        "Describe the issue and review the AI suggestions before saving."
    )

    if "pending_ticket" not in st.session_state:
        st.session_state.pending_ticket = None

    if "last_saved_ticket" not in st.session_state:
        st.session_state.last_saved_ticket = None

    description_input = st.text_area(
        "Describe your IT issue",
        placeholder=(
            "Example: My laptop keeps shutting "
            "down unexpectedly."
        ),
        height=110
    )

    if st.button(
        "✦ Analyze Ticket with AI",
        type="primary"
    ):

        if not description_input.strip():
            st.warning("Enter a ticket description first.")

        else:
            st.session_state.pending_ticket = None
            st.session_state.last_saved_ticket = None

            try:
                with st.spinner(
                    "Ollama is analyzing the ticket..."
                ):
                    category, priority = classify_ticket(
                        description_input.strip()
                    )

                st.session_state.pending_ticket = {
                    "description": description_input.strip(),
                    "ai_category": category,
                    "ai_priority": priority
                }

            except RuntimeError as error:
                st.error(str(error))

    pending = st.session_state.pending_ticket

    if pending:

        section(
            "HUMAN-IN-THE-LOOP",
            "Review AI Recommendations",
            "Your changes will be saved only after confirmation."
        )

        st.write(
            "**Issue:**",
            pending["description"]
        )

        st.info(
            f'AI suggestion: '
            f'**{pending["ai_category"]}** · '
            f'**{pending["ai_priority"]}**'
        )

        with st.form("review_ticket_form"):

            c1, c2 = st.columns(2)

            with c1:
                approved_category = st.selectbox(
                    "Approved Category",
                    CATEGORIES,
                    index=CATEGORIES.index(
                        pending["ai_category"]
                    )
                )

            with c2:
                approved_priority = st.selectbox(
                    "Approved Priority",
                    PRIORITIES,
                    index=PRIORITIES.index(
                        pending["ai_priority"]
                    )
                )

            st.write(
                "**Assigned team:**",
                TEAMS[approved_category]
            )

            confirm = st.form_submit_button(
                "Confirm and Save Ticket",
                type="primary"
            )

        if confirm:

            try:
                ticket_id = save_ticket(
                    pending["description"],
                    approved_category,
                    approved_priority,
                    pending["ai_category"],
                    pending["ai_priority"]
                )

                st.session_state.last_saved_ticket = ticket_id
                st.session_state.pending_ticket = None

                st.rerun()

            except (ValueError, sqlite3.Error) as error:
                st.error(f"Could not save ticket: {error}")

        if st.button("Discard Unsaved Prediction"):
            st.session_state.pending_ticket = None
            st.rerun()

    if st.session_state.last_saved_ticket is not None:

        st.success(
            f'Ticket #{st.session_state.last_saved_ticket} '
            'confirmed and saved successfully.'
        )


# ==================================================
# 15. TICKET MANAGER TAB
# ==================================================

with tab_manage:

    tickets = get_tickets()

    section(
        "WORKSPACE / TICKET MANAGER",
        "Search & Manage Tickets",
        "Search, update status, correct classifications, and view review history."
    )

    query = st.text_input(
        "Search by ticket ID or description"
    )

    f1, f2 = st.columns(2)

    with f1:
        selected_status = st.selectbox(
            "Status",
            ["All Statuses"] + STATUSES
        )

    with f2:
        selected_priority = st.selectbox(
            "Priority",
            ["All Priorities"]
            + PRIORITIES
            + ["Needs Review"]
        )

    f3, f4 = st.columns(2)

    with f3:
        selected_category = st.selectbox(
            "Category",
            ["All Categories"] + CATEGORIES
        )

    with f4:
        selected_team = st.selectbox(
            "Assigned Team",
            ["All Teams"]
            + list(dict.fromkeys(TEAMS.values()))
        )

    filtered = []

    for ticket in tickets:

        (
            ticket_id,
            issue,
            category,
            priority,
            team,
            created_at,
            status
        ) = ticket

        search_value = query.strip().lower()

        if (
            search_value
            and search_value not in str(ticket_id)
            and search_value not in issue.lower()
        ):
            continue

        if (
            selected_status != "All Statuses"
            and status != selected_status
        ):
            continue

        if (
            selected_priority != "All Priorities"
            and priority != selected_priority
        ):
            continue

        if (
            selected_category != "All Categories"
            and category != selected_category
        ):
            continue

        if (
            selected_team != "All Teams"
            and team != selected_team
        ):
            continue

        filtered.append(ticket)

    st.caption(
        f"Showing {len(filtered)} "
        f"of {len(tickets)} tickets"
    )

    section(
        "TICKET RECORDS",
        "Submitted Tickets",
        "Expand a record to work with it."
    )

    if not filtered:
        st.info("No tickets match the selected filters.")

    for ticket in filtered:

        (
            ticket_id,
            issue,
            category,
            priority,
            team,
            created_at,
            status
        ) = ticket

        with st.expander(
            f"#{ticket_id} · {category} · "
            f"{priority} · {status}"
        ):

            st.write("**Description:**", issue)

            a, b = st.columns(2)

            with a:
                st.write("**Category:**", category)
                st.write("**Priority:**", priority)

            with b:
                st.write("**Assigned Team:**", team)
                st.write("**Status:**", status)

            st.caption(f"Created: {created_at}")

            # Update status
            with st.form(f"status_form_{ticket_id}"):

                new_status = st.selectbox(
                    "Update Ticket Status",
                    STATUSES,
                    index=STATUSES.index(status)
                )

                status_submit = st.form_submit_button(
                    "Save Status"
                )

            if status_submit:

                try:
                    update_status(ticket_id, new_status)
                    st.rerun()

                except (ValueError, sqlite3.Error) as error:
                    st.error(str(error))

            # Correct classification
            st.markdown(
                "#### Correct Saved Category or Priority"
            )

            with st.form(f"correction_form_{ticket_id}"):

                c1, c2 = st.columns(2)

                with c1:
                    corrected_category = st.selectbox(
                        "Correct Category",
                        CATEGORIES,
                        index=(
                            CATEGORIES.index(category)
                            if category in CATEGORIES
                            else 0
                        )
                    )

                with c2:
                    corrected_priority = st.selectbox(
                        "Correct Priority",
                        PRIORITIES,
                        index=(
                            PRIORITIES.index(priority)
                            if priority in PRIORITIES
                            else 1
                        )
                    )

                reason = st.text_area(
                    "Reason for correction"
                )

                st.write(
                    "**Updated team:**",
                    TEAMS[corrected_category]
                )

                correction_submit = st.form_submit_button(
                    "Save Classification Correction"
                )

            if correction_submit:

                try:
                    if correct_ticket(
                        ticket_id,
                        corrected_category,
                        corrected_priority,
                        reason
                    ):
                        st.rerun()

                    else:
                        st.info("No changes to save.")

                except (ValueError, sqlite3.Error) as error:
                    st.error(str(error))

            # Review history
            with st.expander(
                "Prediction and Review History"
            ):

                records = get_history(ticket_id)

                if not records:
                    st.info(
                        "No review history recorded "
                        "for this ticket."
                    )

                for record in records:

                    (
                        action,
                        ai_cat,
                        ai_prio,
                        before_cat,
                        before_prio,
                        after_cat,
                        after_prio,
                        note,
                        changed_at
                    ) = record

                    st.write(
                        "**Action:**",
                        action,
                        " · **Date:**",
                        changed_at
                    )

                    st.write(
                        "**Original AI:**",
                        ai_cat or "Not recorded",
                        "·",
                        ai_prio or "Not recorded"
                    )

                    if before_cat is not None:
                        st.write(
                            "**Previous saved:**",
                            before_cat,
                            "·",
                            before_prio
                        )

                    st.write(
                        "**Approved:**",
                        after_cat,
                        "·",
                        after_prio
                    )

                    st.write(
                        "**Reason:**",
                        note
                    )

                    st.divider()


# ==================================================
# 16. ANALYTICS TAB
# ==================================================

with tab_analytics:

    tickets = get_tickets()

    counts = Counter(
        ticket[6] for ticket in tickets
    )

    section(
        "OPERATIONS / OVERVIEW",
        "Service Desk Overview",
        "Ticket counts from your SQLite database."
    )

    cols = st.columns(4)

    overview_metrics = [
        (
            "Total Tickets",
            len(tickets),
            "All recorded requests",
            "#3b82f6"
        ),
        (
            "Open Tickets",
            counts["Open"],
            "Awaiting assistance",
            "#f59e0b"
        ),
        (
            "In Progress",
            counts["In Progress"],
            "Currently being worked on",
            "#8b5cf6"
        ),
        (
            "Resolved",
            counts["Resolved"],
            "Completed requests",
            "#10b981"
        )
    ]

    for col, metric in zip(cols, overview_metrics):
        with col:
            kpi(*metric)

    st.divider()

    section(
        "INTELLIGENCE / ANALYTICS",
        "IT Support Analytics",
        "Live summaries of all tickets, regardless of active filters."
    )

    all_tickets = get_tickets()

    cat_counts = Counter(t[2] for t in all_tickets)
    prio_counts = Counter(t[3] for t in all_tickets)
    team_counts = Counter(t[4] for t in all_tickets)
    status_counts = Counter(t[6] for t in all_tickets)

    total = len(all_tickets)
    resolved = status_counts["Resolved"]

    resolution_rate = (
        100 * resolved / total
        if total else 0
    )

    m1, m2, m3, m4 = st.columns(4)

    analytics_metrics = [
        (
            "Total Tickets",
            total,
            "Saved in SQLite",
            "#3b82f6"
        ),
        (
            "Unresolved",
            status_counts["Open"]
            + status_counts["In Progress"],
            "Open and in progress",
            "#f59e0b"
        ),
        (
            "Critical Priority",
            prio_counts["Critical"],
            "All saved Critical tickets",
            "#ef4444"
        ),
        (
            "Resolution Rate",
            f"{resolution_rate:.1f}%",
            "Resolved / total",
            "#10b981"
        )
    ]

    for col, metric in zip(
        [m1, m2, m3, m4],
        analytics_metrics
    ):
        with col:
            kpi(*metric)

    ch1, ch2 = st.columns(2)

    with ch1:
        ring_chart(
            "Ticket Status Distribution",
            "Progress of all saved requests",
            {
                status: status_counts[status]
                for status in STATUSES
            }
        )

    with ch2:
        bars(
            "Tickets by Priority",
            "Distribution of reported severity",
            prio_counts,
            [
                "#10b981",
                "#3b82f6",
                "#f59e0b",
                "#ef4444"
            ],
            {
                "Low": "#10b981",
                "Medium": "#3b82f6",
                "High": "#f59e0b",
                "Critical": "#ef4444",
                "Needs Review": "#94a3b8"
            }
        )

    ch3, ch4 = st.columns(2)

    with ch3:
        bars(
            "Tickets by Category",
            "Most common types of issues",
            cat_counts,
            [
                "#0f998d",
                "#3b82f6",
                "#8b5cf6",
                "#f59e0b"
            ]
        )

    with ch4:
        bars(
            "Tickets by Assigned Team",
            "Ticket allocation to support teams",
            team_counts,
            [
                "#3b82f6",
                "#0f998d",
                "#8b5cf6",
                "#f59e0b"
            ]
        )

    st.markdown("#### Resolution Progress")

    st.write(
        f"{resolved} out of "
        f"{total} tickets resolved"
    )

    st.progress(
        resolved / total
        if total else 0
    )

    st.caption(
        "Analytics include all saved tickets. "
        "Tickets can be delivered to local SQLite team inboxes. "
        "No external routing is performed."
    )


# ==================================================
# 17. AI EVALUATION TAB
# ==================================================

with tab_evaluation:

    section(
        "AI QUALITY / EVALUATION",
        "AI Accuracy Evaluation",
        "Five predefined sample cases; not a real-world reliability estimate."
    )

    test_cases = [
        (
            "My account is locked after several login attempts.",
            "Account Access",
            "Medium"
        ),
        (
            "I cannot connect to my company VPN.",
            "Network Issue",
            "Medium"
        ),
        (
            "My Power BI report will not refresh.",
            "Reporting Issue",
            "Medium"
        ),
        (
            "My laptop keyboard occasionally stops responding, "
            "but I can use an external keyboard.",
            "Hardware Issue",
            "Low"
        ),
        (
            "The entire company's network is down. "
            "All employees cannot work and business "
            "operations have completely stopped.",
            "Network Issue",
            "Critical"
        )
    ]

    if st.button("Run AI Accuracy Test"):

        correct_category = 0
        correct_priority = 0
        completed = 0

        progress = st.progress(0)

        for index, (
            description,
            expected_category,
            expected_priority
        ) in enumerate(test_cases):

            with st.expander(
                f"Test {index + 1}",
                expanded=True
            ):

                st.write("**Ticket:**", description)

                try:
                    category, priority = classify_ticket(
                        description
                    )

                    completed += 1

                    if category == expected_category:
                        correct_category += 1

                    if priority == expected_priority:
                        correct_priority += 1

                    st.write(
                        "**Expected category:**",
                        expected_category,
                        "**Predicted:**",
                        category
                    )

                    st.write(
                        "**Expected priority:**",
                        expected_priority,
                        "**Final:**",
                        priority
                    )

                    if (
                        category == expected_category
                        and priority == expected_priority
                    ):
                        st.success("Both results match.")

                    else:
                        st.warning(
                            "One or more results differ."
                        )

                except RuntimeError as error:
                    st.error(str(error))

            progress.progress(
                (index + 1) / len(test_cases)
            )

        c1, c2, c3 = st.columns(3)

        with c1:
            kpi(
                "Evaluated",
                f"{completed} / 5",
                "Successful calls",
                "#3b82f6"
            )

        with c2:
            kpi(
                "Category Accuracy",
                f"{100 * correct_category / 5:.1f}%",
                "Predefined examples",
                "#0f998d"
            )

        with c3:
            kpi(
                "Final Priority Accuracy",
                f"{100 * correct_priority / 5:.1f}%",
                "Includes Python safeguards",
                "#8b5cf6"
            )

        st.caption(
            "Model predictions are not guaranteed. "
            "This test does not save tickets."
        )


# ==================================================
# ROUTING QUEUE TAB — SIMULATED, NO EXTERNAL DELIVERY
# ==================================================

with tab_routing:
    section(
        "OPERATIONS / LOCAL ROUTING",
        "Ticket Routing Queue",
        "Monitor approved tickets before connecting a delivery workflow."
    )
    st.info(
        "Manual routing statuses are simulation only. Use Process Pending Deliveries "
        "below to persist tickets in their local team inboxes; no email is sent."
    )

    queue = get_routing_queue()
    queue_counts = Counter(item[3] for item in queue)
    for col, label, count, note, color in zip(
        st.columns(4),
        ["Total Requests", "Pending", "Routed", "Failed"],
        [len(queue), queue_counts["Pending"],
         queue_counts["Routed"], queue_counts["Failed"]],
        ["Local queue", "Not processed", "Simulation only", "Needs review"],
        ["#3b82f6", "#f59e0b", "#10b981", "#ef4444"]
    ):
        with col:
            kpi(label, count, note, color)

    # ----------------------------------------------
    # REAL PERSISTENT LOCAL DELIVERY WORKFLOW
    # ----------------------------------------------
    st.divider()
    section(
        "OPERATIONS / DELIVERY",
        "Local Delivery Processor",
        "Persist Pending requests in their team inboxes and mark them Routed."
    )
    st.caption(
        "Local SQLite delivery only: no external ticket system, "
        "email, team account, or push notification."
    )
    if st.button("Deliver All Pending Tickets", type="primary"):
        try:
            delivered = deliver_pending_tickets()
            st.success(
                f"Processed pending tickets. "
                f"{len(delivered)} new local inbox deliveries created."
            )
            st.rerun()
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Delivery failed: {error}")

    st.divider()
    section(
        "OPERATIONS / TEAM INBOX",
        "Team Inbox",
        "Only tickets delivered to the selected team's local inbox appear here."
    )
    inbox_teams = list(dict.fromkeys(TEAMS.values()))
    team_col, filter_col = st.columns(2)
    with team_col:
        selected_inbox_team = st.selectbox(
            "Support team inbox", inbox_teams,
            key="delivery_inbox_team"
        )
    with filter_col:
        delivery_filter = st.selectbox(
            "Inbox filter",
            ["All", "Needs Acknowledgment", "Acknowledged", "Resolved"],
            key="delivery_inbox_filter"
        )
    deliveries = get_team_deliveries(selected_inbox_team)
    if delivery_filter == "Needs Acknowledgment":
        deliveries = [d for d in deliveries if not d[3]]
    elif delivery_filter == "Acknowledged":
        deliveries = [d for d in deliveries if d[3] and d[8] != "Resolved"]
    elif delivery_filter == "Resolved":
        deliveries = [d for d in deliveries if d[8] == "Resolved"]

    a, b, c = st.columns(3)
    a.metric("Showing", len(deliveries))
    b.metric("Awaiting acknowledgment", sum(not d[3] for d in deliveries))
    c.metric("Resolved", sum(d[8] == "Resolved" for d in deliveries))

    if not deliveries:
        st.info("No delivered tickets match this team and filter.")

    for delivery in deliveries:
        (ticket_id, team, delivered_at, acknowledged_at,
         acknowledged_by, issue, category, priority, ticket_status) = delivery
        with st.expander(
            f"Ticket #{ticket_id} · {priority} · {ticket_status} · "
            f"{'Acknowledged' if acknowledged_at else 'New'}"
        ):
            st.write("**Issue:**", issue)
            st.write("**Category:**", category)
            st.write("**Assigned team:**", team)
            st.write("**Delivered at:**", delivered_at)
            st.write("**Ticket status:**", ticket_status)
            if acknowledged_at:
                st.write("**Acknowledged by:**", acknowledged_by)
                st.write("**Acknowledged at:**", acknowledged_at)
            else:
                with st.form(f"ack_delivery_{ticket_id}"):
                    agent = st.text_input("Your name (demo)")
                    acknowledge = st.form_submit_button("Acknowledge Ticket")
                if acknowledge:
                    try:
                        acknowledge_delivery(ticket_id, team, agent)
                        st.rerun()
                    except (ValueError, sqlite3.Error) as error:
                        st.error(str(error))

            if acknowledged_at and ticket_status != "Resolved":
                with st.form(f"resolve_delivery_{ticket_id}"):
                    closer = st.text_input("Resolved by (demo)")
                    note = st.text_area("Resolution note")
                    complete = st.form_submit_button("Mark Ticket Resolved")
                if complete:
                    try:
                        finish_delivered_ticket(ticket_id, team, closer, note)
                        st.rerun()
                    except (ValueError, sqlite3.Error) as error:
                        st.error(str(error))

            with st.expander("Local event history"):
                events = get_ticket_events(ticket_id)
                if not events:
                    st.caption("No local delivery events recorded yet.")
                for event_type, details, event_at in events:
                    st.write(f"**{event_type}** · {event_at}")
                    st.caption(details)

    st.divider()
    st.markdown("### Add an Existing Reviewed Ticket")
    queued_ids = {item[1] for item in queue}
    available = [
        ticket for ticket in get_tickets()
        if ticket[0] not in queued_ids
        and ticket[6] != "Resolved"
        and get_history(ticket[0])
    ]

    if available:
        choices = {
            f"#{ticket[0]} · {ticket[2]} · {ticket[4]}": ticket[0]
            for ticket in available
        }
        choice = st.selectbox("Reviewed ticket", list(choices))
        if st.button("Add to Routing Queue"):
            try:
                queue_existing_ticket(choices[choice])
                st.success("Ticket added to the local routing queue.")
                st.rerun()
            except (ValueError, sqlite3.Error) as error:
                st.error(str(error))
    else:
        st.caption("No eligible, unqueued and unresolved reviewed tickets.")

    st.divider()
    st.markdown("### Routing Requests")
    routing_filter = st.selectbox(
        "Routing status filter",
        ["All"] + ROUTING_STATUSES
    )
    matching = [
        item for item in queue
        if routing_filter == "All" or item[3] == routing_filter
    ]

    if not matching:
        st.info("No routing records match the selected status.")

    for item in matching:
        (queue_id, ticket_id, team, routing_status, queued_at,
         processed_at, error_message, issue, priority, ticket_status) = item

        with st.expander(
            f"Ticket #{ticket_id} · {team} · {priority} · {routing_status}"
        ):
            st.write("**Issue:**", issue)
            st.write("**Assigned team:**", team)
            st.write("**Ticket status:**", ticket_status)
            st.write("**Queued at:**", queued_at)
            if processed_at:
                st.write("**Last processed at:**", processed_at)
            if error_message:
                st.warning(f"Last failure: {error_message}")

            with st.form(f"routing_{queue_id}"):
                selected_result = st.selectbox(
                    "Manual status override (simulation only)",
                    ROUTING_STATUSES,
                    index=ROUTING_STATUSES.index(routing_status)
                )
                reason = st.text_area("Failure reason (if Failed)")
                submit_result = st.form_submit_button("Save Routing Result")

            if submit_result:
                try:
                    update_routing_status(queue_id, selected_result, reason)
                    st.rerun()
                except (ValueError, sqlite3.Error) as error:
                    st.error(str(error))


# ==================================================
# 18. FOOTER
# ==================================================

html_block("""
<div class="itflow-footer">
<strong>ITFlow AI</strong> · Smart Service Desk
<div>Python · Streamlit · SQLite · Local Ollama</div>
<div>AI predictions and priorities require human review.</div>
</div>
""")
