from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "dev-secret"  # ok for local dev
DB_PATH = "gradebook.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                max_points REAL NOT NULL CHECK (max_points > 0),
                weight REAL NOT NULL DEFAULT 1 CHECK (weight > 0)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS grades (
                student_id INTEGER NOT NULL,
                assignment_id INTEGER NOT NULL,
                points_earned REAL NOT NULL CHECK (points_earned >= 0),
                PRIMARY KEY (student_id, assignment_id),
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE
            )
            """
        )


def letter_grade(percent: float) -> str:
    if percent >= 93: return "A"
    if percent >= 90: return "A-"
    if percent >= 87: return "B+"
    if percent >= 83: return "B"
    if percent >= 80: return "B-"
    if percent >= 77: return "C+"
    if percent >= 73: return "C"
    if percent >= 70: return "C-"
    if percent >= 67: return "D+"
    if percent >= 63: return "D"
    if percent >= 60: return "D-"
    return "F"


def compute_totals() -> List[dict]:
    """
    Weighted percent:
      sum(weight * earned/max) / sum(weight) * 100
    Missing grades count as 0 for that assignment.
    """
    with get_db() as conn:
        students = conn.execute("SELECT id, name FROM students ORDER BY name").fetchall()
        assignments = conn.execute(
            "SELECT id, max_points, weight FROM assignments ORDER BY id"
        ).fetchall()

        # Preload grades into dict: (student_id, assignment_id) -> earned
        grade_rows = conn.execute("SELECT student_id, assignment_id, points_earned FROM grades").fetchall()
        earned_map: Dict[Tuple[int, int], float] = {
            (r["student_id"], r["assignment_id"]): float(r["points_earned"]) for r in grade_rows
        }

    results = []
    total_weight = sum(float(a["weight"]) for a in assignments)
    for s in students:
        if total_weight == 0 or len(assignments) == 0:
            pct = 0.0
        else:
            num = 0.0
            for a in assignments:
                earned = earned_map.get((s["id"], a["id"]), 0.0)
                maxp = float(a["max_points"])
                w = float(a["weight"])
                frac = min(max(earned / maxp, 0.0), 1.0)  # clamp 0..1
                num += w * frac
            pct = (num / total_weight) * 100.0

        results.append(
            {
                "student_id": s["id"],
                "name": s["name"],
                "percent": round(pct, 2),
                "letter": letter_grade(pct),
            }
        )
    return results


@app.before_request
def _startup():
    init_db()


@app.route("/")
def index():
    totals = compute_totals()
    return render_template("index.html", totals=totals)


@app.route("/students", methods=["GET", "POST"])
def students():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Student name is required.", "error")
            return redirect(url_for("students"))

        with get_db() as conn:
            conn.execute("INSERT INTO students (name) VALUES (?)", (name,))
        flash("Student added.", "ok")
        return redirect(url_for("students"))

    with get_db() as conn:
        rows = conn.execute("SELECT id, name FROM students ORDER BY name").fetchall()
    return render_template("students.html", students=rows)


@app.route("/students/delete/<int:student_id>", methods=["POST"])
def delete_student(student_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
    flash("Student deleted.", "ok")
    return redirect(url_for("students"))


@app.route("/assignments", methods=["GET", "POST"])
def assignments():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        max_points = request.form.get("max_points")
        weight = request.form.get("weight") or "1"

        try:
            max_points_f = float(max_points)
            weight_f = float(weight)
        except (TypeError, ValueError):
            flash("Max points and weight must be numbers.", "error")
            return redirect(url_for("assignments"))

        if not title:
            flash("Assignment title is required.", "error")
            return redirect(url_for("assignments"))
        if max_points_f <= 0 or weight_f <= 0:
            flash("Max points and weight must be > 0.", "error")
            return redirect(url_for("assignments"))

        with get_db() as conn:
            conn.execute(
                "INSERT INTO assignments (title, max_points, weight) VALUES (?, ?, ?)",
                (title, max_points_f, weight_f),
            )
        flash("Assignment added.", "ok")
        return redirect(url_for("assignments"))

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, max_points, weight FROM assignments ORDER BY id"
        ).fetchall()
    return render_template("assignments.html", assignments=rows)


@app.route("/assignments/delete/<int:assignment_id>", methods=["POST"])
def delete_assignment(assignment_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
    flash("Assignment deleted.", "ok")
    return redirect(url_for("assignments"))


@app.route("/grades", methods=["GET", "POST"])
def grades():
    with get_db() as conn:
        students = conn.execute("SELECT id, name FROM students ORDER BY name").fetchall()
        assignments = conn.execute(
            "SELECT id, title, max_points FROM assignments ORDER BY id"
        ).fetchall()

        grade_rows = conn.execute(
            """
            SELECT student_id, assignment_id, points_earned
            FROM grades
            """
        ).fetchall()

    grade_map: Dict[Tuple[int, int], float] = {
        (r["student_id"], r["assignment_id"]): float(r["points_earned"]) for r in grade_rows
    }

    if request.method == "POST":
        student_id = int(request.form["student_id"])
        assignment_id = int(request.form["assignment_id"])
        pts = request.form.get("points_earned", "").strip()

        try:
            pts_f = float(pts)
        except ValueError:
            flash("Points earned must be a number.", "error")
            return redirect(url_for("grades"))

        # Get max points to validate range
        maxp = None
        for a in assignments:
            if a["id"] == assignment_id:
                maxp = float(a["max_points"])
                break

        if maxp is None:
            flash("Assignment not found.", "error")
            return redirect(url_for("grades"))

        if pts_f < 0 or pts_f > maxp:
            flash(f"Points earned must be between 0 and {maxp}.", "error")
            return redirect(url_for("grades"))

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO grades (student_id, assignment_id, points_earned)
                VALUES (?, ?, ?)
                ON CONFLICT(student_id, assignment_id)
                DO UPDATE SET points_earned = excluded.points_earned
                """,
                (student_id, assignment_id, pts_f),
            )
        flash("Grade saved.", "ok")
        return redirect(url_for("grades"))

    totals = compute_totals()
    return render_template(
        "grades.html",
        students=students,
        assignments=assignments,
        grade_map=grade_map,
        totals=totals,
    )


if __name__ == "__main__":
    app.run(debug=True)
