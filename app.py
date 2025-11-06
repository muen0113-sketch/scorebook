# app.py
from __future__ import annotations
import os
from datetime import date, datetime, timedelta
from io import BytesIO

from flask import (
    Flask, render_template, request, redirect, url_for, flash, send_file
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, text

# ---------------------------
# Flask & DB 初始化
# ---------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///scorebook.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ---------------------------
# 資料模型
# ---------------------------
class Subject(db.Model):
    __tablename__ = "subjects"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False, unique=True)

class Score(db.Model):
    __tablename__ = "scores"
    id = db.Column(db.Integer, primary_key=True)
    the_date = db.Column(db.Date, nullable=False, index=True)
    subject_id = db.Column(
        db.Integer,
        db.ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
    )
    score = db.Column(db.Float, nullable=True)
    rank = db.Column(db.Integer, nullable=True)
    note = db.Column(db.Text, nullable=True)  # 備註

    subject = db.relationship(
        "Subject",
        backref=db.backref("scores", cascade="all, delete-orphan")
    )

    __table_args__ = (
        UniqueConstraint("the_date", "subject_id", name="uq_score_date_subject"),
    )

# ---------------------------
# 小工具
# ---------------------------
WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]

def str_to_date(s: str | None) -> date:
    if not s:
        return date.today()
    return datetime.strptime(s, "%Y-%m-%d").date()

# ---------------------------
# DB 初始化（Flask 3 不用 before_first_request，改成啟動時手動呼叫）
# ---------------------------
def init_db():
    db.create_all()
    # 軟升級：若沒有 note 欄位則嘗試新增（已存在會丟例外→忽略）
    try:
        db.session.execute(text("ALTER TABLE scores ADD COLUMN note TEXT"))
        db.session.commit()
    except Exception:
        db.session.rollback()
    # 初始科目
    if Subject.query.count() == 0:
        for name in ["國文", "英文", "數學", "自然", "社會"]:
            db.session.add(Subject(name=name))
        db.session.commit()

# ---------------------------
# Routes
# ---------------------------
@app.route("/")
def root():
    return redirect(url_for("scores_view", d=date.today().strftime("%Y-%m-%d")))

@app.route("/scores")
def scores_view():
    d = str_to_date(request.args.get("d"))
    first_day = d.replace(day=1)
    next_month = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
    prev_month = (first_day - timedelta(days=1)).replace(day=1)

    subjects = Subject.query.order_by(Subject.name.asc()).all()
    raw_scores = Score.query.filter_by(the_date=d).all()
    scores_map = {s.subject_id: s for s in raw_scores}

    weekday = WEEKDAY_ZH[d.weekday()] if d.weekday() < 7 else WEEKDAY_ZH[6]

    return render_template(
        "index.html",
        d=d,
        weekday=weekday,
        subjects=subjects,
        scores_map=scores_map,
        first_day=first_day,
        prev_month=prev_month,
        next_month=next_month,
    )

@app.post("/scores/save")
def save_scores():
    d = str_to_date(request.form.get("the_date"))
    ids = request.form.getlist("score_subject_ids")

    for sid_str in ids:
        sid = int(sid_str)
        score_val = request.form.get(f"score[{sid}]")
        rank_val = request.form.get(f"rank[{sid}]")
        note_val = request.form.get(f"note[{sid}]")

        score_f = float(score_val) if score_val not in (None, "") else None
        rank_i = int(rank_val) if rank_val not in (None, "") else None
        note_s = (note_val or "").strip() or None

        row = Score.query.filter_by(the_date=d, subject_id=sid).first()
        if (score_f is None) and (rank_i is None) and (note_s is None):
            if row:
                db.session.delete(row)
        else:
            if not row:
                row = Score(the_date=d, subject_id=sid)
                db.session.add(row)
            row.score = score_f
            row.rank = rank_i
            row.note = note_s

    db.session.commit()
    flash("已更新當日成績/排名/備註。", "success")
    return redirect(url_for("scores_view", d=d.strftime("%Y-%m-%d")))

@app.post("/subjects/add")
def add_subject():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("科目名稱不可為空。", "error")
        return redirect(request.referrer or url_for("root"))

    exists = Subject.query.filter(Subject.name == name).first()
    if exists:
        flash("科目已存在。", "error")
    else:
        db.session.add(Subject(name=name))
        db.session.commit()
        flash(f"已新增科目：{name}", "success")
    return redirect(request.referrer or url_for("root"))

@app.post("/subjects/<int:sid>/delete")
def delete_subject(sid: int):
    subj = Subject.query.get_or_404(sid)
    db.session.delete(subj)
    db.session.commit()
    flash(f"已刪除科目：{subj.name}", "success")
    return redirect(request.referrer or url_for("root"))

@app.get("/export/pdf")
def export_pdf():
    d = str_to_date(request.args.get("d"))
    subjects = Subject.query.order_by(Subject.name.asc()).all()
    raw_scores = Score.query.filter_by(the_date=d).all()
    scores_map = {s.subject_id: s for s in raw_scores}

    # 產生 PDF
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm

    mem = BytesIO()
    c = canvas.Canvas(mem, pagesize=A4)
    width, height = A4

    title = f"家教學生成績 · {d.strftime('%Y-%m-%d')}"
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20 * mm, height - 20 * mm, title)

    c.setFont("Helvetica-Bold", 11)
    y = height - 30 * mm
    line_h = 8 * mm
    c.drawString(20 * mm, y, "科目")
    c.drawString(60 * mm, y, "分數")
    c.drawString(85 * mm, y, "名次")
    c.drawString(110 * mm, y, "備註")
    y -= line_h
    c.setFont("Helvetica", 11)

    for subj in subjects:
        row = scores_map.get(subj.id)
        score_str = "" if not row or row.score is None else str(row.score)
        rank_str = "" if not row or row.rank is None else str(row.rank)
        note_str = "" if not row or not row.note else row.note

        if y < 20 * mm:
            c.showPage()
            c.setFont("Helvetica-Bold", 14)
            c.drawString(20 * mm, height - 20 * mm, title + "（續）")
            c.setFont("Helvetica-Bold", 11)
            y = height - 30 * mm
            c.drawString(20 * mm, y, "科目")
            c.drawString(60 * mm, y, "分數")
            c.drawString(85 * mm, y, "名次")
            c.drawString(110 * mm, y, "備註")
            y -= line_h
            c.setFont("Helvetica", 11)

        c.drawString(20 * mm, y, subj.name)
        c.drawString(60 * mm, y, score_str)
        c.drawString(85 * mm, y, rank_str)

        # 備註簡易換行
        max_chars = 40
        if len(note_str) > max_chars:
            lines = [note_str[i:i + max_chars] for i in range(0, len(note_str), max_chars)]
            c.drawString(110 * mm, y, lines[0])
            for ln in lines[1:]:
                y -= (line_h - 2)
                c.drawString(110 * mm, y, ln)
        else:
            c.drawString(110 * mm, y, note_str)

        y -= line_h

    c.showPage()
    c.save()
    mem.seek(0)
    filename = f"scorebook-{d.strftime('%Y%m%d')}.pdf"
    return send_file(mem, as_attachment=True, download_name=filename, mimetype="application/pdf")

# ---------------------------
# 入口
# ---------------------------
if __name__ == "__main__":
    # Flask 3：啟動前手動初始化資料庫
    with app.app_context():
        init_db()
    app.run(debug=True)
