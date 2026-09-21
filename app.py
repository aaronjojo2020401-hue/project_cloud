import os, random, sqlite3
from datetime import date, timedelta
from flask import Flask, render_template_string as render, request, redirect, session, flash, g
from werkzeug.security import generate_password_hash as gen, check_password_hash as chk

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me")
DB = os.environ.get("DB_PATH", "attendease.db")
LEAVE_DAYS = 30  # yearly leave allowance per person

# ---------- Database ----------
def q(sql, *args, one=False):
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    cur = g.db.execute(sql, args)
    g.db.commit()
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows

@app.teardown_appcontext
def close_db(e):
    if "db" in g:
        g.db.close()

def init():  # create tables and demo data on first run
    with sqlite3.connect(DB) as c:
        c.executescript(f"""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, username TEXT UNIQUE,
            password TEXT, role TEXT, balance INTEGER DEFAULT {LEAVE_DAYS});
        CREATE TABLE IF NOT EXISTS attendance(user_id INTEGER, day TEXT, UNIQUE(user_id, day));
        CREATE TABLE IF NOT EXISTS leaves(id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, start_date TEXT,
            end_date TEXT, days INTEGER, reason TEXT, status TEXT DEFAULT 'Pending');""")
        if not c.execute("SELECT 1 FROM users").fetchone():
            demo = [("Alice Johnson", "employee", "emp123", "employee"), ("Ben Carter", "ben", "ben123", "employee"),
                    ("Carol Smith", "manager", "mgr123", "manager"), ("Dan Miller", "hr", "hr123", "hr")]
            for name, user, pw, role in demo:
                c.execute("INSERT INTO users(name,username,password,role) VALUES(?,?,?,?)", (name, user, gen(pw), role))
            for uid in range(1, 5):
                for d in range(1, 7):
                    if random.random() < 0.8:
                        c.execute("INSERT INTO attendance VALUES(?,?)", (uid, str(date.today() - timedelta(d))))

init()

def auth(*roles):  # returns logged-in user (optionally restricted to roles) or None
    u = q("SELECT * FROM users WHERE id=?", session.get("uid"), one=True)
    return u if u and (not roles or u["role"] in roles) else None

# ---------- Routes ----------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        f = request.form
        u = q("SELECT * FROM users WHERE username=? AND role=?", f["username"], f["role"], one=True)
        if u and chk(u["password"], f["password"]):
            session["uid"] = u["id"]
            return redirect("/dashboard")
        flash("Wrong username or password for the selected role.")
    return render(PAGE, me=None)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/dashboard")
def dashboard():
    me = auth()
    if not me:
        return redirect("/")
    today = str(date.today())
    days = [str(date.today() - timedelta(d)) for d in range(6, -1, -1)]
    n = dict(q("SELECT day, COUNT(*) FROM attendance GROUP BY day"))
    return render(PAGE, me=me, today=today, total=LEAVE_DAYS, days=days, counts=[n.get(d, 0) for d in days],
        present=q("SELECT 1 FROM attendance WHERE user_id=? AND day=?", me["id"], today, one=True),
        mine=q("SELECT * FROM leaves WHERE user_id=? ORDER BY id DESC", me["id"]),
        pending=q("""SELECT l.*, u.name FROM leaves l JOIN users u ON u.id=l.user_id
                     WHERE l.status='Pending' AND u.id!=? AND (?='hr' OR u.role='employee')""", me["id"], me["role"]),
        users=q("""SELECT u.name, u.username, u.role, u.balance,
                   (SELECT COUNT(*) FROM attendance a WHERE a.user_id=u.id AND a.day=?) AS today
                   FROM users u ORDER BY u.id""", today))

@app.post("/checkin")
def checkin():
    me = auth()
    if me:
        q("INSERT OR IGNORE INTO attendance VALUES(?,?)", me["id"], str(date.today()))
        flash("Attendance marked for today.")
    return redirect("/dashboard")

@app.post("/leave")
def leave():
    me, f = auth(), request.form
    if me:
        days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days + 1
        if days < 1 or days > me["balance"]:
            flash("Check your dates. The end date must not be before the start date, and days must fit your balance.")
        else:
            hr = me["role"] == "hr"  # HR/admin is the top role, so their leave is recorded as approved
            q("INSERT INTO leaves(user_id,kind,start_date,end_date,days,reason,status) VALUES(?,?,?,?,?,?,?)",
              me["id"], f["kind"], f["start"], f["end"], days, f["reason"], "Approved" if hr else "Pending")
            if hr:
                q("UPDATE users SET balance=balance-? WHERE id=?", days, me["id"])
            flash("Leave recorded and approved automatically." if hr else "Leave request sent for approval.")
    return redirect("/dashboard")

@app.post("/decide/<int:id>/<status>")
def decide(id, status):
    me = auth("manager", "hr")
    r = me and status in ("Approved", "Rejected") and q(
        "SELECT l.*, u.role, u.balance FROM leaves l JOIN users u ON u.id=l.user_id WHERE l.id=? AND l.status='Pending'",
        id, one=True)
    # managers decide employee requests, HR decides everyone's, nobody decides their own
    if r and r["user_id"] != me["id"] and (me["role"] == "hr" or r["role"] == "employee"):
        if status == "Approved" and r["days"] > r["balance"]:
            flash("Employee no longer has enough leave balance.")
        else:
            q("UPDATE leaves SET status=? WHERE id=?", status, id)
            if status == "Approved":
                q("UPDATE users SET balance=balance-? WHERE id=?", r["days"], r["user_id"])
            flash("Request " + status.lower() + ".")
    return redirect("/dashboard")

@app.post("/add")
def add():
    f = request.form
    if auth("hr"):
        try:
            q("INSERT INTO users(name,username,password,role) VALUES(?,?,?,?)",
              f["name"], f["username"], gen(f["password"]), f["role"])
            flash("Account created for " + f["name"] + ".")
        except sqlite3.IntegrityError:
            flash("That username is already taken.")
    return redirect("/dashboard")

# ---------- Page template ----------
PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>AttendEase</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel=stylesheet>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3"></script>
<style>
body{background:#f2f5f6}nav{background:#0f4c5c}
.btn-primary{--bs-btn-bg:#0f4c5c;--bs-btn-border-color:#0f4c5c;--bs-btn-hover-bg:#0a3641;--bs-btn-hover-border-color:#0a3641}
.btn-outline-primary{--bs-btn-color:#0f4c5c;--bs-btn-border-color:#0f4c5c;--bs-btn-hover-bg:#0f4c5c;
--bs-btn-hover-border-color:#0f4c5c;--bs-btn-active-bg:#0f4c5c;--bs-btn-active-border-color:#0f4c5c}
</style></head><body>
<nav class="navbar navbar-dark mb-4"><div class=container>
 <span class="navbar-brand fw-semibold">AttendEase <small class="fw-normal opacity-75">Attendance and leave management</small></span>
 {% if me %}<span class=text-white>{{ me.name }} ({{ me.role }}) <a href=/logout class="btn btn-sm btn-light ms-2">Log out</a></span>{% endif %}
</div></nav>
<div class="container pb-5">
{% for m in get_flashed_messages() %}<div class="alert alert-info">{{ m }}</div>{% endfor %}

{% if not me %}
<div class="row justify-content-center"><div class=col-md-5><div class="card shadow-sm"><div class="card-body p-4">
 <h4 class=mb-3>Sign in</h4>
 <form method=post>
  <div class="btn-group w-100 mb-3">
  {% for r, l in [('employee','Employee'), ('manager','Manager'), ('hr','HR / Admin')] %}
   <input type=radio class=btn-check name=role id={{ r }} value={{ r }} {{ 'checked' if loop.first }}>
   <label class="btn btn-outline-primary" for={{ r }}>{{ l }}</label>
  {% endfor %}
  </div>
  <input name=username class="form-control mb-2" placeholder=Username required>
  <input name=password type=password class="form-control mb-3" placeholder=Password required>
  <button class="btn btn-primary w-100">Sign in</button>
 </form>
 <hr><small class=text-muted>Demo logins: employee / emp123, manager / mgr123, hr / hr123</small>
</div></div></div></div>

{% else %}
<div class="row g-3 mb-3">
 <div class=col-md-4><div class="card h-100"><div class=card-body>
  <h6>Today, {{ today }}</h6>
  {% if present %}<span class="badge bg-success">Attendance marked</span>
  {% else %}<form method=post action=/checkin><button class="btn btn-primary">Mark attendance</button></form>{% endif %}
  <hr><h6>Leave days remaining</h6><div class=display-6>{{ me.balance }} <small class="fs-6 text-muted">of {{ total }} days</small></div>
 </div></div></div>
 <div class=col-md-8><div class="card h-100"><div class=card-body>
  <h6>Employees present, last 7 days</h6><canvas id=chart></canvas>
 </div></div></div>
</div>

<div class="card mb-3"><div class=card-body>
 <h6>Request leave</h6>
 {% if me.role == 'hr' %}<p class="small text-muted">As HR / Admin, your leave is approved automatically and recorded.</p>{% endif %}
 <form method=post action=/leave class="row g-2">
  <div class=col-md-2><select name=kind class=form-select><option>Annual<option>Sick<option>Casual</select></div>
  <div class=col-md-2><input type=date name=start min={{ today }} class=form-control required></div>
  <div class=col-md-2><input type=date name=end min={{ today }} class=form-control required></div>
  <div class=col-md-4><input name=reason class=form-control placeholder=Reason required></div>
  <div class=col-md-2><button class="btn btn-primary w-100">Send request</button></div>
 </form>
</div></div>

<div class="card mb-3"><div class=card-body>
 <h6>My leave requests</h6>
 <div class=table-responsive><table class="table table-sm mb-0">
  <tr><th>Type<th>From<th>To<th>Days<th>Reason<th>Status</tr>
  {% for l in mine %}<tr><td>{{ l.kind }}<td>{{ l.start_date }}<td>{{ l.end_date }}<td>{{ l.days }}<td>{{ l.reason }}
   <td><span class="badge bg-{{ 'success' if l.status=='Approved' else 'danger' if l.status=='Rejected' else 'warning text-dark' }}">{{ l.status }}</span></tr>
  {% else %}<tr><td colspan=6 class=text-muted>No requests yet. Use the form above to send one.</tr>{% endfor %}
 </table></div>
</div></div>

{% if me.role != 'employee' %}
<div class="card mb-3"><div class=card-body>
 <h6>Requests waiting for your decision</h6>
 <div class=table-responsive><table class="table table-sm mb-0">
  <tr><th>Employee<th>Type<th>Dates<th>Days<th>Reason<th></tr>
  {% for l in pending %}<tr><td>{{ l.name }}<td>{{ l.kind }}<td>{{ l.start_date }} to {{ l.end_date }}<td>{{ l.days }}<td>{{ l.reason }}
   <td class=text-nowrap>
    <form method=post action=/decide/{{ l.id }}/Approved class=d-inline><button class="btn btn-sm btn-success">Approve</button></form>
    <form method=post action=/decide/{{ l.id }}/Rejected class=d-inline><button class="btn btn-sm btn-outline-danger">Reject</button></form></tr>
  {% else %}<tr><td colspan=6 class=text-muted>Nothing waiting. New requests will show up here.</tr>{% endfor %}
 </table></div>
</div></div>

<div class="card mb-3"><div class=card-body>
 <h6>Staff directory</h6>
 <div class=table-responsive><table class="table table-sm mb-0">
  <tr><th>Name<th>Username<th>Role<th>Days remaining<th>Present today</tr>
  {% for u in users %}<tr><td>{{ u.name }}<td>{{ u.username }}<td>{{ u.role }}<td>{{ u.balance }}<td>{{ 'Yes' if u.today else 'No' }}</tr>{% endfor %}
 </table></div>
</div></div>
{% endif %}

{% if me.role == 'hr' %}
<div class=card><div class=card-body>
 <h6>Add a staff account</h6>
 <form method=post action=/add class="row g-2">
  <div class=col-md-3><input name=name class=form-control placeholder="Full name" required></div>
  <div class=col-md-3><input name=username class=form-control placeholder=Username required></div>
  <div class=col-md-2><input name=password type=password class=form-control placeholder=Password required></div>
  <div class=col-md-2><select name=role class=form-select><option value=employee>Employee<option value=manager>Manager<option value=hr>HR / Admin</select></div>
  <div class=col-md-2><button class="btn btn-primary w-100">Create account</button></div>
 </form>
</div></div>
{% endif %}

<script>
new Chart(document.getElementById('chart'), {type:'line',
 data:{labels:{{ days|tojson }}, datasets:[{label:'Present', data:{{ counts|tojson }}, borderColor:'#0f4c5c',
  backgroundColor:'rgba(15,76,92,.15)', fill:true, tension:.3}]},
 options:{scales:{y:{beginAtZero:true, ticks:{precision:0}}}}});
</script>
{% endif %}
</div></body></html>"""

if __name__ == "__main__":
    app.run(debug=True)
