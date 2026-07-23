"""Load a realistic 10-document student journey (plan.md §14).

Reviewers usually arrive at an empty app. This seeds the "Load Demo Profile"
dataset — a 2023 Python certificate through a 2026 resume and portfolio —
designed so the knowledge graph and the RAG answer card have real material:
a Python skill hub links the cert, the ML project, the internship and the
resume into the exact chain plan.md §3 names, and the documents share enough
full-text vocabulary that Layer-B `similar_to` edges (cosine > 0.75) actually
form. Short synthetic blurbs top out around 0.606 and draw no dashed edges, so
every document below is written at real length.

Insert path is modelled on Module 2's storage, minus Gemini: `insert_document`
+ `add_document` directly, with categories/skills authored by hand rather than
inferred. No original files are written — every demo document is fileless
(`text_entry` or `url`, `original_path = ""`), so nothing here touches the
preservation/download path. Career paths are NOT seeded: they come from the
graph's "Infer career paths" button (a real Gemini call), and this dataset is
tuned so that inference lands on AI/ML Engineer.

Run standalone (from the repo root, with the scratch-storage env vars set so it
does not write the real store):

    PYTHONPATH=backend python -m seed.seed_demo

or trigger it from the UI's "Load Demo Profile" button (POST /api/seed-demo).

Idempotent: document ids are deterministic (`demo-*`), and a re-run clears the
previously seeded demo documents first, so it never stacks duplicates and never
touches a reviewer's own uploads.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

# Make the backend package importable whether this runs as `python -m
# seed.seed_demo` from the repo root or is imported by the FastAPI route.
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from ai import embeddings  # noqa: E402
from config import settings  # noqa: E402
from db import database  # noqa: E402

USER = "demo"

# Every demo document is stamped with the same upload timestamp — the seed is
# one action, and effective_date sorting keys off extracted_date, not this.
_SEEDED_AT = "2026-07-23T00:00:00"


# The journey. Ordered chronologically for readability; `date` is the extracted
# (real) date, so date_source stays "extracted" and nothing is flagged assumed.
# Skills are chosen so the shared-skill hubs form the plan.md §3 chain — the
# `Python` and `Machine Learning` hubs are what light up on the money-click.
DOCS = [
    dict(
        id="demo-python-cert",
        title="Python Programming — Coursera Certificate",
        category="Certifications",
        document_type="certificate",
        file_type="text_entry",
        date="2023-05",
        skills=["Python", "Programming", "Data Structures"],
        orgs=["Coursera", "University of Michigan"],
        people=["Dr. Charles Severance"],
        tags=["python", "certificate", "foundational"],
        summary=(
            "Coursera 'Python for Everybody' specialization certificate, issued "
            "May 2023. Covers Python syntax, data structures, and file/web data "
            "handling — the foundation the rest of the journey builds on."
        ),
        raw_text=(
            "Certificate of Completion\n"
            "Python for Everybody Specialization\n"
            "Awarded to the learner on 15 May 2023 by Coursera, offered by the "
            "University of Michigan and taught by Dr. Charles Severance.\n\n"
            "This five-course specialization covered the fundamentals of "
            "programming in Python: variables, expressions, conditional "
            "execution, functions, and loops; core data structures including "
            "lists, dictionaries, and tuples; string and file processing; "
            "regular expressions; and working with data from the web through "
            "HTTP requests, JSON, and web scraping. The final capstone applied "
            "these skills to retrieving, processing, and visualizing real "
            "datasets.\n\n"
            "Skills demonstrated: Python programming, data structures, file and "
            "text processing, and basic data analysis. This certificate marks "
            "the learner's first formal credential in software development and "
            "establishes the Python foundation used across every later project, "
            "internship, and portfolio piece in this profile."
        ),
    ),
    dict(
        id="demo-marksheet-sem3",
        title="Semester 3 Marksheet",
        category="Academics",
        document_type="other",
        file_type="text_entry",
        date="2023-11",
        skills=["Statistics", "Mathematics", "Probability", "Linear Algebra"],
        orgs=["State University", "Department of Computer Science"],
        people=[],
        tags=["academics", "marksheet", "semester-3"],
        summary=(
            "Official Semester 3 academic marksheet from the B.Tech Computer "
            "Science program — coursework in statistics, probability, and "
            "linear algebra that underpins the later machine-learning work."
        ),
        raw_text=(
            "State University — Department of Computer Science\n"
            "Statement of Marks, Semester 3, Academic Year 2023-24\n\n"
            "Bachelor of Technology in Computer Science and Engineering.\n\n"
            "Courses and grades this semester:\n"
            "- Probability and Statistics — A\n"
            "- Linear Algebra — A-\n"
            "- Data Structures and Algorithms — A\n"
            "- Discrete Mathematics — B+\n"
            "- Database Management Systems — A-\n"
            "- Object-Oriented Programming — A\n\n"
            "Semester GPA: 8.9 / 10. The probability, statistics, and linear "
            "algebra coursework provided the mathematical grounding for the "
            "machine-learning projects and internship that follow, while the "
            "data structures and DBMS courses reinforced the Python programming "
            "and SQL used throughout the profile."
        ),
    ),
    dict(
        id="demo-club-lead",
        title="Data Science Club — Lead Appointment Letter",
        category="Achievements",
        document_type="other",
        file_type="text_entry",
        date="2024-02",
        skills=["Leadership", "Python", "Public Speaking", "Data Analysis"],
        orgs=["Data Science Club", "State University"],
        people=["Prof. Anita Rao"],
        tags=["leadership", "achievement", "club"],
        summary=(
            "Appointment as Lead of the university Data Science Club for 2024. "
            "Organized workshops teaching Python and data analysis to peers — "
            "the kind of undocumented achievement the written-response input "
            "exists to capture."
        ),
        raw_text=(
            "Data Science Club, State University\n"
            "Appointment Letter — Club Lead, 2024\n\n"
            "Dear member,\n\n"
            "On behalf of the faculty advisor Prof. Anita Rao, we are pleased to "
            "appoint you as Lead of the Data Science Club for the 2024 academic "
            "year, in recognition of your consistent contribution and technical "
            "ability.\n\n"
            "As Lead you organized and delivered a series of five hands-on "
            "workshops introducing over sixty students to Python programming, "
            "data cleaning with pandas, exploratory data analysis, and simple "
            "predictive models. You coordinated guest talks, mentored first-year "
            "members on their first projects, and represented the club at the "
            "inter-college technical festival.\n\n"
            "This leadership role developed skills in public speaking, team "
            "coordination, and teaching, on top of the Python and data-analysis "
            "expertise that anchors the rest of your work. We thank you for your "
            "service and wish you continued success."
        ),
    ),
    dict(
        id="demo-ml-project",
        title="Machine Learning Pipeline — Project Report",
        category="Projects",
        document_type="project_report",
        file_type="text_entry",
        date="2024-06",
        skills=["Python", "Machine Learning", "pandas", "scikit-learn"],
        orgs=["State University"],
        people=["Prof. Anita Rao"],
        tags=["machine-learning", "project", "python", "pandas"],
        summary=(
            "End-to-end machine-learning pipeline built in Python with pandas "
            "and scikit-learn for a customer-churn classification task — the "
            "project that turned the Python certificate into applied ML skill."
        ),
        raw_text=(
            "Machine Learning Pipeline — Project Report\n"
            "Course project, supervised by Prof. Anita Rao, June 2024.\n\n"
            "Objective: build a complete, reproducible machine-learning pipeline "
            "to predict customer churn from a telecom dataset of roughly 7,000 "
            "records.\n\n"
            "Approach. The pipeline was written entirely in Python. Raw data was "
            "loaded and cleaned with pandas — handling missing values, encoding "
            "categorical features, and normalizing numeric columns. Exploratory "
            "data analysis with matplotlib surfaced the strongest churn "
            "predictors. Using scikit-learn, I trained and compared logistic "
            "regression, random forest, and gradient-boosting classifiers, "
            "tuning hyperparameters with cross-validated grid search and "
            "evaluating on precision, recall, and ROC-AUC.\n\n"
            "Results. The gradient-boosting model reached 0.86 ROC-AUC on the "
            "held-out test set. The final pipeline was packaged as a reusable "
            "scikit-learn Pipeline object so preprocessing and inference run as "
            "one step.\n\n"
            "Skills applied: Python, pandas, scikit-learn, machine learning, "
            "model evaluation, and data cleaning. This project is the direct "
            "application of the Python programming certificate and the "
            "statistics coursework, and became the anchor project cited in the "
            "internship application that followed."
        ),
    ),
    dict(
        id="demo-github-pipeline",
        title="GitHub — churn-ml-pipeline",
        category="Projects",
        document_type="portfolio",
        file_type="url",
        source_url="https://github.com/demo-student/churn-ml-pipeline",
        date="2024-07",
        skills=["Python", "Machine Learning", "pandas", "scikit-learn", "Git"],
        orgs=["GitHub"],
        people=[],
        tags=["github", "repository", "machine-learning"],
        summary=(
            "Public GitHub repository hosting the churn ML pipeline: Python, "
            "pandas, and scikit-learn, with a documented README and reproducible "
            "training scripts. The code behind the ML project report."
        ),
        raw_text=(
            "GitHub repository: demo-student/churn-ml-pipeline\n"
            "Primary language: Python (94%). Also: Jupyter Notebook, Makefile.\n"
            "Topics: machine-learning, python, pandas, scikit-learn, "
            "classification.\n"
            "Stars: 34 · Forks: 7 · License: MIT · Created July 2024.\n\n"
            "README. An end-to-end machine-learning pipeline for customer-churn "
            "prediction, extracted from a university project into a clean, "
            "reproducible repository. The code loads and cleans the dataset with "
            "pandas, engineers features, and trains scikit-learn classifiers "
            "(logistic regression, random forest, gradient boosting) behind a "
            "single Pipeline object. A Makefile wires up `make train` and `make "
            "evaluate`; results and plots are written to an artifacts folder.\n\n"
            "The repository demonstrates Python software engineering alongside "
            "machine learning: modular scripts, a requirements file, unit tests "
            "for the preprocessing steps, and version control with Git. It is "
            "the public, runnable counterpart to the machine-learning project "
            "report and the strongest single artifact in the portfolio."
        ),
    ),
    dict(
        id="demo-internship-offer",
        title="Internship Offer Letter — XYZ Corp",
        category="Internships",
        document_type="internship_letter",
        file_type="text_entry",
        date="2025-01",
        skills=["Python", "SQL", "Data Analysis", "Machine Learning"],
        orgs=["XYZ Corp", "Data Science Team"],
        people=["Rakesh Menon"],
        tags=["internship", "offer", "data-science"],
        summary=(
            "Offer letter for a six-month Data Science Internship at XYZ Corp, "
            "citing the candidate's Python and machine-learning project work as "
            "the basis for selection."
        ),
        raw_text=(
            "XYZ Corp — Data Science Team\n"
            "Internship Offer Letter, January 2025\n\n"
            "Dear Candidate,\n\n"
            "We are delighted to offer you the position of Data Science Intern "
            "at XYZ Corp for a six-month term beginning January 2025, reporting "
            "to Rakesh Menon, Lead Data Scientist.\n\n"
            "Your application stood out for its strong Python foundation and the "
            "end-to-end machine-learning pipeline you built for customer-churn "
            "prediction, which demonstrated exactly the applied data-science "
            "skills our team needs. During the internship you will work with "
            "Python and SQL to build data-processing pipelines, analyze product "
            "usage data, and support the development and evaluation of machine-"
            "learning models in production.\n\n"
            "Skills expected and developed in this role: Python, SQL, data "
            "analysis, and machine learning. We were impressed by your project "
            "portfolio and your leadership of the university Data Science Club, "
            "and we look forward to welcoming you to the team."
        ),
    ),
    dict(
        id="demo-internship-completion",
        title="Internship Completion Certificate — XYZ Corp",
        category="Internships",
        document_type="internship_letter",
        file_type="text_entry",
        date="2025-06",
        skills=["Python", "SQL", "Machine Learning", "Data Analysis"],
        orgs=["XYZ Corp", "Data Science Team"],
        people=["Rakesh Menon"],
        tags=["internship", "completion", "data-science"],
        summary=(
            "Certificate confirming successful completion of the six-month XYZ "
            "Corp data-science internship, describing Python/SQL pipeline work "
            "and machine-learning models shipped to production."
        ),
        raw_text=(
            "XYZ Corp — Data Science Team\n"
            "Internship Completion Certificate, June 2025\n\n"
            "This is to certify that the intern successfully completed a six-"
            "month Data Science Internship at XYZ Corp, from January to June "
            "2025, under the supervision of Rakesh Menon, Lead Data Scientist.\n\n"
            "Over the internship the intern built and maintained data-processing "
            "pipelines in Python, wrote and optimized SQL queries against the "
            "product analytics warehouse, and contributed to two machine-"
            "learning models — a churn-prediction model and a lead-scoring model "
            "— that were deployed to production and are actively used by the "
            "growth team. The intern also automated a weekly reporting workflow "
            "that previously took a full day of manual effort.\n\n"
            "Skills demonstrated: Python, SQL, machine learning, data analysis, "
            "and production engineering. The intern showed strong ownership and "
            "technical judgment and is warmly recommended for data-science and "
            "machine-learning roles."
        ),
    ),
    dict(
        id="demo-hackathon",
        title="Hackathon Winner Certificate — TechFest",
        category="Achievements",
        document_type="certificate",
        file_type="text_entry",
        date="2025-09",
        skills=["Machine Learning", "Python", "Teamwork"],
        orgs=["TechFest", "State University"],
        people=[],
        tags=["hackathon", "award", "achievement"],
        summary=(
            "First-place award at the TechFest 2025 hackathon for an ML-powered "
            "project built in 36 hours — team recognition on top of the "
            "individual internship and project work."
        ),
        raw_text=(
            "TechFest 2025 — National Hackathon\n"
            "Certificate of Achievement: First Place\n\n"
            "Awarded to the team for winning first place among 120 competing "
            "teams at the TechFest 2025 hackathon, hosted by State University in "
            "September 2025.\n\n"
            "In 36 hours the team designed and built an accessibility assistant "
            "that used a machine-learning model to caption and summarize images "
            "for visually impaired users. The prototype was implemented in "
            "Python, with a scikit-learn and PyTorch model behind a lightweight "
            "web front end. Judges recognized the project for its technical "
            "execution, real-world usefulness, and polished demo.\n\n"
            "Skills demonstrated: machine learning, Python, rapid prototyping, "
            "and teamwork under time pressure. This award complements the "
            "internship and project portfolio with proof of strong collaborative "
            "engineering."
        ),
    ),
    dict(
        id="demo-resume",
        title="Updated Resume — 2026",
        category="Academics",
        document_type="resume",
        file_type="text_entry",
        date="2026-01",
        skills=["Python", "Machine Learning", "SQL", "pandas", "scikit-learn"],
        orgs=["State University", "XYZ Corp"],
        people=[],
        tags=["resume", "cv", "final-year"],
        summary=(
            "Final-year resume consolidating the whole journey: Python and ML "
            "skills, the churn pipeline project, the XYZ Corp internship, the "
            "hackathon win, and Data Science Club leadership."
        ),
        raw_text=(
            "Resume — Final Year, B.Tech Computer Science (2026)\n\n"
            "Summary. Final-year Computer Science student specializing in "
            "machine learning and data science, with a Coursera Python "
            "certification, a six-month industry internship, a hackathon win, "
            "and a public ML project on GitHub.\n\n"
            "Technical skills: Python, SQL, machine learning, pandas, scikit-"
            "learn, data analysis, and Git.\n\n"
            "Experience. Data Science Intern, XYZ Corp (Jan–Jun 2025): built "
            "Python and SQL data pipelines and shipped two machine-learning "
            "models to production. Lead, university Data Science Club (2024): "
            "organized five Python and data-analysis workshops for sixty-plus "
            "students.\n\n"
            "Projects. Customer-churn ML pipeline in Python with pandas and "
            "scikit-learn (0.86 ROC-AUC), open-sourced on GitHub. TechFest 2025 "
            "hackathon winner: an ML image-captioning accessibility assistant.\n\n"
            "Education. B.Tech Computer Science, State University — strong "
            "coursework in probability, statistics, and linear algebra.\n\n"
            "Certifications. Python for Everybody (Coursera, 2023). This resume "
            "ties together every document in the profile into one narrative "
            "pointing toward a machine-learning engineering career."
        ),
    ),
    dict(
        id="demo-portfolio",
        title="Portfolio Website",
        category="Projects",
        document_type="portfolio",
        file_type="url",
        source_url="https://demo-student.dev",
        date="2026-03",
        skills=["React", "JavaScript", "Python", "Machine Learning"],
        orgs=[],
        people=[],
        tags=["portfolio", "website", "projects"],
        summary=(
            "Personal portfolio site showcasing the ML project, internship, and "
            "achievements — a React front end over a Python backend, tying the "
            "profile together for recruiters."
        ),
        raw_text=(
            "Portfolio Website — demo-student.dev\n\n"
            "A personal portfolio site presenting the student's projects, "
            "skills, and experience to recruiters and collaborators. Built as a "
            "single-page application in React and JavaScript, with a small "
            "Python FastAPI backend serving project metadata and a contact "
            "form.\n\n"
            "Featured work. The customer-churn machine-learning pipeline (Python, "
            "pandas, scikit-learn) with links to the GitHub repository and a "
            "write-up of the approach and results. The TechFest hackathon-winning "
            "accessibility assistant. A summary of the XYZ Corp data-science "
            "internship and the machine-learning models shipped there.\n\n"
            "About. Final-year Computer Science student focused on machine "
            "learning and data science, comfortable across Python for modelling "
            "and React/JavaScript for building interfaces. Skills highlighted: "
            "Python, machine learning, React, JavaScript, and data analysis. The "
            "site is the public front door to everything else in this profile."
        ),
    ),
]


def _clear_demo(user_id: str) -> None:
    """Remove previously seeded demo documents so a re-run is idempotent.

    Scoped to `demo-*` ids only — a reviewer who uploaded their own documents
    (which get random ids) keeps them; only this seed's rows are replaced.
    Embeddings are cleared per doc first, then the SQLite rows in one pass.
    """
    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM documents WHERE user_id = ? AND id LIKE 'demo-%'",
            (user_id,),
        ).fetchall()
        ids = [row["id"] for row in rows]
        for doc_id in ids:
            conn.execute("DELETE FROM entities WHERE document_id = ?", (doc_id,))
            conn.execute("DELETE FROM tags WHERE document_id = ?", (doc_id,))
        conn.execute(
            "DELETE FROM documents WHERE user_id = ? AND id LIKE 'demo-%'",
            (user_id,),
        )

    for doc_id in ids:
        embeddings.delete_document(doc_id)


def load_demo(user_id: str = USER) -> dict[str, object]:
    """Seed the demo profile. Returns {"seeded": n, "user_id": ...}.

    Safe to call repeatedly and safe on a fresh install — it ensures the storage
    dirs and schema exist first, then replaces any prior demo rows.
    """
    settings.ensure_dirs()
    database.init_db()
    _clear_demo(user_id)

    for doc in DOCS:
        raw = doc["raw_text"]
        # Fileless documents pin the SHA-256 of their text, not a file (CLAUDE.md).
        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        database.insert_document(
            doc_id=doc["id"],
            user_id=user_id,
            filename=doc["title"],
            original_path="",
            file_type=doc["file_type"],
            source_url=doc.get("source_url", ""),
            checksum=checksum,
            raw_text=raw,
            upload_date=_SEEDED_AT,
            document_type=doc["document_type"],
            category=doc["category"],
            title=doc["title"],
            summary=doc["summary"],
            extracted_date=doc["date"],
            confidence=0.9,
            skills=doc["skills"],
            organizations=doc["orgs"],
            people=doc.get("people", []),
            tags=doc.get("tags", []),
        )
        embeddings.add_document(
            doc_id=doc["id"], user_id=user_id, title=doc["title"], raw_text=raw
        )

    return {"seeded": len(DOCS), "user_id": user_id}


if __name__ == "__main__":
    result = load_demo()
    print(f"Seeded {result['seeded']} demo documents into {settings.db_path}")
