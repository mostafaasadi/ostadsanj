from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
import orjson
import weasyprint
import urllib.parse
from app.db import Base, engine, SessionLocal, get_db
from app.models import Message, Professor, Job, MessageProfessor, Annotation
from app.services.telegram_parser import iter_telegram_messages
from app.services.professor_matcher import run_matching
from app.services.llm_annotator import run_llm_analysis, analyze_message, get_parent_text, run_auto_review
from app.services.analysis_logger import log, get_logs, clear
from app.services import ollama_client, arvan_client
from app.services.professor_analytics import (
    get_all_professors_ranking,
    get_professor_full_profile,
    calculate_professor_score,
    generate_professor_summary
)
from app.utils import to_jalali, to_jalali_date, to_jalali_year_month
from app.config import (
    IMPORT_BATCH_SIZE,
    MIN_MENTIONS_FOR_SCORE,
    MIN_UNIQUE_USERS_FOR_SCORE,
    PROVIDER_LABELS,
    DEFAULT_PROVIDER
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Professor Analytics Dashboard")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

if FRONTEND_DIR.exists():
    if (FRONTEND_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_index():
    return FileResponse(FRONTEND_DIR / "index.html")

@app.get("/index.html")
def read_index_html():
    return FileResponse(FRONTEND_DIR / "index.html")

@app.get("/professors.html")
def read_professors():
    return FileResponse(FRONTEND_DIR / "professors.html")

@app.get("/professor-profile.html")
def read_professor_profile_page():
    return FileResponse(FRONTEND_DIR / "professor-profile.html")

@app.get("/import.html")
def read_import():
    return FileResponse(FRONTEND_DIR / "import.html")

@app.get("/analysis.html")
def read_analysis():
    return FileResponse(FRONTEND_DIR / "analysis.html")

@app.get("/messages.html")
def read_messages():
    return FileResponse(FRONTEND_DIR / "messages.html")


class AnalysisRequest(BaseModel):
    batch_size: int = 50
    continuous: bool = False
    workers: int = 3
    provider: str = DEFAULT_PROVIDER
    model: str | None = None

class ImportLocalRequest(BaseModel):
    path: str

class ProfessorUpdate(BaseModel):
    name: str | None = None
    department: str | None = None
    aliases: list[str] | None = None

class ProfessorCreate(BaseModel):
    name: str
    department: str | None = None
    aliases: list[str] = []

class ProfessorBulkCreate(BaseModel):
    professors: list[ProfessorCreate]

class ResetMatchingRequest(BaseModel):
    mode: str = "unmatched"

class ResetAnalysisRequest(BaseModel):
    mode: str = "failed"

class MatchReviewRequest(BaseModel):
    approved: bool

class MatchReviewItem(BaseModel):
    message_id: int
    professor_id: int

class MatchBulkReviewRequest(BaseModel):
    approve: list[MatchReviewItem] = []
    reject: list[MatchReviewItem] = []

class ReportPdfRequest(BaseModel):
    scope: str
    only_opinions: bool
    include_no_analysis: bool
    with_chain: bool


def run_import(job_id: int, file_path: str):
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if not job:
            return
        job.status = "running"
        job.error = "بارگذاری شناسه‌های موجود..."
        db.commit()

        existing_ids = set(
            r[0] for r in db.query(Message.telegram_id).filter(Message.telegram_id.isnot(None)).all()
        )
        seen_ids = set()
        batch = []
        done = 0
        skipped = 0
        scanned = 0

        for row in iter_telegram_messages(file_path):
            scanned += 1
            telegram_id = row.get("telegram_id")
            if telegram_id is not None:
                if telegram_id in seen_ids or telegram_id in existing_ids:
                    skipped += 1
                    continue
                seen_ids.add(telegram_id)
            batch.append(Message(**row))
            if len(batch) >= IMPORT_BATCH_SIZE:
                db.bulk_save_objects(batch)
                db.commit()
                done += len(batch)
                job = db.get(Job, job_id)
                if not job or job.status == "cancelled":
                    return
                job.done = done + skipped
                job.total = done + skipped
                job.error = f"پیشرفت: new:{done} duplicate:{skipped}"
                db.commit()
                batch = []
            if scanned % 1000 == 0:
                job = db.get(Job, job_id)
                if not job or job.status == "cancelled":
                    return
                job.total = scanned
                job.done = scanned
                job.error = f"پیشرفت: new:{done} duplicate:{skipped} scanned:{scanned}"
                db.commit()

        if batch:
            db.bulk_save_objects(batch)
            db.commit()
            done += len(batch)

        job = db.get(Job, job_id)
        if not job or job.status == "cancelled":
            return
        job.status = "done"
        job.done = done + skipped
        job.total = done + skipped
        job.error = f"new:{done} duplicate_skipped:{skipped}"
        db.commit()
        log("success", f"ایمپورت کامل شد | new:{done} duplicate:{skipped}", "import")
    except Exception as e:
        db.rollback()
        job = db.get(Job, job_id)
        if job:
            job.status = "error"
            job.error = str(e)[:2000]
            db.commit()
        log("error", f"ایمپورت خطا: {e}", "import")
    finally:
        db.close()

def _reply_chain(db: Session, message: Message, max_depth: int = 3) -> list:
    chain = []
    current = message
    for _ in range(max_depth):
        if current.reply_to_message_id is None:
            break
        parent = db.query(Message).filter(Message.telegram_id == current.reply_to_message_id).first()
        if not parent:
            break
        chain.append({"text": parent.text_clean[:400], "date": to_jalali(parent.date)})
        current = parent
    chain.reverse()
    return chain


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/providers/health")
def providers_health():
    health = {
        "ollama": ollama_client.check_health(),
        "arvan": arvan_client.check_health()
    }
    return {
        "providers": health,
        "labels": PROVIDER_LABELS,
    }


@app.get("/api/models")
def list_models(refresh: bool = False):
    from app.services import model_registry
    ollama_models = []
    if ollama_client.check_health():
        ollama_models = [
            {"id": m, "name": m, "provider": "ollama", "input_per_1m": 0, "output_per_1m": 0}
            for m in ollama_client.get_available_models()
        ]
    arvan = model_registry.get_chat_models(force=refresh)
    return {
        "default_provider": DEFAULT_PROVIDER,
        "ollama_available": ollama_client.check_health(),
        "arvan_available": arvan_client.check_health(),
        "ollama_models": ollama_models,
        "arvan_models": [
            {
                "id": m["id"],
                "name": m["id"],
                "provider": m["provider"],
                "context": m["context"],
                "input_per_1m": m["input_per_1m"],
                "output_per_1m": m["output_per_1m"],
            }
            for m in arvan
        ],
    }


@app.post("/api/models/refresh")
def refresh_models():
    from app.services import model_registry
    models = model_registry.get_models(force=True)
    return {
        "count": len(models),
        "chat": len(model_registry.get_chat_models()),
    }


@app.get("/api/ollama/health")
def ollama_health():
    is_healthy = ollama_client.check_health()
    models = ollama_client.get_available_models() if is_healthy else []
    return {"connected": is_healthy, "models": models, "current_model": ollama_client.OLLAMA_CHAT_MODEL}


@app.post("/api/import/local")
def import_local(payload: ImportLocalRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    file_path = Path(payload.path)
    if not file_path.exists():
        raise HTTPException(status_code=400, detail="File not found")
    job = Job(name="import_telegram", status="pending", total=0, done=0)
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(run_import, job.id, str(file_path))
    return {"job_id": job.id, "status": "pending"}


@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(Job).order_by(Job.created_at.desc()).limit(20).all()
    return [
        {
            "id": job.id, "name": job.name, "status": job.status,
            "total": job.total, "done": job.done, "error": job.error,
            "created_at": to_jalali(job.created_at), "updated_at": to_jalali(job.updated_at)
        }
        for job in jobs
    ]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id, "name": job.name, "status": job.status,
        "total": job.total, "done": job.done, "error": job.error,
        "created_at": to_jalali(job.created_at), "updated_at": to_jalali(job.updated_at)
    }


@app.delete("/api/jobs/cancel-running")
def cancel_running_jobs(db: Session = Depends(get_db)):
    running_jobs = db.query(Job).filter(Job.status == "running").all()
    for job in running_jobs:
        job.status = "cancelled"
    db.commit()
    return {"message": f"{len(running_jobs)} jobs cancelled", "cancelled_count": len(running_jobs)}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "running":
        job.status = "cancelled"
        db.commit()
        return {"message": "Job cancelled", "job_id": job_id}
    db.delete(job)
    db.commit()
    return {"message": "Job deleted", "job_id": job_id}


@app.get("/api/overview")
def overview(db: Session = Depends(get_db)):
    total_messages = db.query(func.count(Message.id)).scalar() or 0
    text_messages = db.query(func.count(Message.id)).filter(Message.char_len > 0).scalar() or 0
    professors_count = db.query(func.count(Professor.id)).scalar() or 0
    matched_links = db.query(func.count(MessageProfessor.id)).scalar() or 0
    annotations_count = db.query(func.count(Annotation.id)).scalar() or 0
    unique_authors = db.query(func.count(func.distinct(Message.author_hash))).filter(Message.author_hash.isnot(None)).scalar() or 0
    monthly_messages = db.query(
        func.strftime("%Y-%m", Message.date).label("month"),
        func.count(Message.id).label("count")
    ).filter(Message.date.isnot(None)).group_by("month").order_by("month").all()
    monthly_data = [
        {"month": to_jalali_year_month(datetime.strptime(f"{m}-01", "%Y-%m-%d")), "count": c}
        for m, c in monthly_messages if m
    ]
    return {
        "counts": {
            "total_messages": total_messages, "text_messages": text_messages,
            "unique_authors": unique_authors, "professors": professors_count,
            "matched_message_professor_links": matched_links, "annotations": annotations_count
        },
        "monthly_messages": monthly_data,
        "thresholds": {
            "min_mentions_for_score": MIN_MENTIONS_FOR_SCORE,
            "min_unique_users_for_score": MIN_UNIQUE_USERS_FOR_SCORE
        }
    }


@app.get("/api/messages/recent")
def recent_messages(limit: int = 20, db: Session = Depends(get_db)):
    messages = db.query(Message).filter(Message.char_len > 0).order_by(Message.date.desc()).limit(limit).all()
    return [
        {
            "id": m.id, "telegram_id": m.telegram_id, "reply_to_message_id": m.reply_to_message_id,
            "date": to_jalali(m.date),
            "text_preview": m.text_clean[:150] + "..." if len(m.text_clean) > 150 else m.text_clean,
            "char_len": m.char_len,
            "processed_matching": m.processed_matching, "processed_llm": m.processed_llm
        }
        for m in messages
    ]


@app.post("/api/professors")
def create_professor(payload: ProfessorCreate, db: Session = Depends(get_db)):
    professor = Professor(
        name=payload.name,
        department=payload.department,
        aliases_json=orjson.dumps(payload.aliases).decode()
    )
    db.add(professor)
    db.commit()
    db.refresh(professor)
    return {"id": professor.id, "name": professor.name}


@app.post("/api/professors/bulk")
def create_professors_bulk(payload: ProfessorBulkCreate, db: Session = Depends(get_db)):
    created = []
    for prof_data in payload.professors:
        professor = Professor(
            name=prof_data.name,
            department=prof_data.department,
            aliases_json=orjson.dumps(prof_data.aliases).decode()
        )
        db.add(professor)
        created.append(professor)
    db.commit()
    for prof in created:
        db.refresh(prof)
    return {"count": len(created), "ids": [p.id for p in created]}


@app.get("/api/professors")
def list_professors(db: Session = Depends(get_db)):
    professors = db.query(Professor).all()
    result = []
    for p in professors:
        try:
            aliases = orjson.loads(p.aliases_json)
        except Exception:
            aliases = []
        result.append({
            "id": p.id, "name": p.name, "department": p.department,
            "aliases": aliases, "created_at": to_jalali(p.created_at)
        })
    return result


@app.get("/api/professors/with-stats")
def list_professors_with_stats(db: Session = Depends(get_db)):
    professors = db.query(Professor).all()
    result = []
    for p in professors:
        try:
            aliases = orjson.loads(p.aliases_json)
        except Exception:
            aliases = []
        mp_links = db.query(MessageProfessor).filter(MessageProfessor.professor_id == p.id).all()
        message_ids = [mp.message_id for mp in mp_links]
        if message_ids:
            messages = db.query(Message).filter(Message.id.in_(message_ids)).all()
            unique_authors = len(set(m.author_hash for m in messages if m.author_hash))
            dates = [m.date for m in messages if m.date]
            first_date = min(dates) if dates else None
            last_date = max(dates) if dates else None
            mention_count = len(messages)
        else:
            unique_authors = 0
            first_date = None
            last_date = None
            mention_count = 0
        low_data = mention_count < MIN_MENTIONS_FOR_SCORE or unique_authors < MIN_UNIQUE_USERS_FOR_SCORE
        result.append({
            "id": p.id, "name": p.name, "department": p.department,
            "aliases": aliases, "created_at": to_jalali(p.created_at),
            "mention_count": mention_count, "unique_users": unique_authors,
            "first_date": to_jalali_date(first_date), "last_date": to_jalali_date(last_date),
            "low_data": low_data
        })
    return result


@app.get("/api/professors/ranking")
def professors_ranking(db: Session = Depends(get_db)):
    try:
        ranking = get_all_professors_ranking(db)
        return {"professors": ranking, "total": len(ranking)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.post("/api/professors/match")
def match_professors(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job = Job(name="match_professors", status="pending", total=0, done=0)
    db.add(job)
    db.commit()
    db.refresh(job)

    def run_match(job_id: int):
        db_local = SessionLocal()
        try:
            j = db_local.get(Job, job_id)
            j.status = "running"
            db_local.commit()
            result = run_matching(db_local, job_id=job_id)
            j = db_local.get(Job, job_id)
            j.status = "done"
            j.total = result["total"]
            j.done = result["total"]
            j.error = f"matched:{result['matched']} reply_matched:{result.get('reply_matched', 0)}"
            db_local.commit()
        except Exception as e:
            db_local.rollback()
            j = db_local.get(Job, job_id)
            if j:
                j.status = "error"
                j.error = str(e)[:2000]
                db_local.commit()
        finally:
            db_local.close()

    background_tasks.add_task(run_match, job.id)
    return {"job_id": job.id, "status": "pending"}


@app.post("/api/professors/reset-matching")
def reset_matching(payload: ResetMatchingRequest, db: Session = Depends(get_db)):
    if payload.mode == "all":
        count = db.query(Message).update({"processed_matching": False})
        db.query(MessageProfessor).delete()
    else:
        unmatched = db.query(Message).filter(
            Message.processed_matching == True,
            ~Message.id.in_(db.query(MessageProfessor.message_id).distinct())
        ).all()
        message_ids = [m.id for m in unmatched]
        count = 0
        if message_ids:
            count = db.query(Message).filter(Message.id.in_(message_ids)).update(
                {"processed_matching": False}, synchronize_session=False
            )
    db.commit()
    return {"message": f"{count} messages reset", "mode": payload.mode, "count": count}


@app.get("/api/professors/{professor_id}")
def get_professor(professor_id: int, db: Session = Depends(get_db)):
    professor = db.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="استاد یافت نشد")
    try:
        aliases = orjson.loads(professor.aliases_json)
    except Exception:
        aliases = []
    return {"id": professor.id, "name": professor.name, "department": professor.department, "aliases": aliases}


@app.put("/api/professors/{professor_id}")
def update_professor(professor_id: int, payload: ProfessorUpdate, db: Session = Depends(get_db)):
    professor = db.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="Professor not found")
    if payload.name is not None:
        professor.name = payload.name
    if payload.department is not None:
        professor.department = payload.department
    if payload.aliases is not None:
        professor.aliases_json = orjson.dumps(payload.aliases).decode()
    db.commit()
    db.refresh(professor)
    return {"id": professor.id, "name": professor.name, "department": professor.department}


@app.delete("/api/professors/{professor_id}")
def delete_professor(professor_id: int, db: Session = Depends(get_db)):
    professor = db.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="Professor not found")
    db.query(MessageProfessor).filter(MessageProfessor.professor_id == professor_id).delete()
    db.query(Annotation).filter(Annotation.professor_id == professor_id).delete()
    db.delete(professor)
    db.commit()
    return {"deleted": True}


@app.get("/api/professors/{professor_id}/messages")
def get_professor_messages(professor_id: int, page: int = 1, limit: int = 50, method: str = "all", db: Session = Depends(get_db)):
    offset = (page - 1) * limit
    query = (
        db.query(Message, MessageProfessor)
        .join(MessageProfessor, Message.id == MessageProfessor.message_id)
        .filter(MessageProfessor.professor_id == professor_id)
    )
    if method != "all":
        query = query.filter(MessageProfessor.match_method == method)
    total = query.count()
    results = query.order_by(Message.date.desc()).offset(offset).limit(limit).all()
    messages = []
    for message, mp in results:
        messages.append({
            "id": message.id, "telegram_id": message.telegram_id,
            "date": to_jalali(message.date), "text": message.text_clean[:400],
            "char_len": message.char_len, "match_method": mp.match_method,
            "matched_alias": mp.matched_alias, "confidence": mp.confidence,
            "needs_review": mp.needs_review, "approved": mp.approved
        })
    return {"total": total, "page": page, "limit": limit, "total_pages": (total + limit - 1) // limit, "messages": messages}


@app.get("/api/professors/{professor_id}/profile")
def professor_profile(professor_id: int, db: Session = Depends(get_db)):
    profile = get_professor_full_profile(db, professor_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Professor not found")
    return profile


@app.get("/api/professors/{professor_id}/score")
def professor_score(professor_id: int, db: Session = Depends(get_db)):
    professor = db.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="Professor not found")
    score = calculate_professor_score(db, professor_id)
    return {"professor_id": professor_id, "name": professor.name, "score": score}


@app.post("/api/professors/{professor_id}/summary/regenerate")
def regenerate_summary(professor_id: int, provider: str = "arvan", model: str = None, db: Session = Depends(get_db)):
    result = generate_professor_summary(db, professor_id, provider=provider, model=model)
    if not result:
        raise HTTPException(status_code=404, detail="خلاصه تولید نشد (داده کم یا خطا)")
    return result

def _run_summaries_all(job_id: int, provider: str, model: str):
    db = SessionLocal()
    try:
        j = db.get(Job, job_id)
        if j:
            j.status = "running"
            db.commit()
        ids = [pid for pid, in db.query(Professor.id).all()]
        done = 0
        for idx, pid in enumerate(ids):
            j = db.get(Job, job_id)
            if j and j.status == "cancelled":
                break
            try:
                score = calculate_professor_score(db, pid)
                if score["message_count"] >= MIN_MENTIONS_FOR_SCORE:
                    generate_professor_summary(db, pid, provider=provider, model=model)
                    done += 1
            except Exception as e:
                log("error", f"خلاصه استاد {pid} ناموفق: {e}", "summary")
            j = db.get(Job, job_id)
            if j:
                j.done = idx + 1
                j.error = f"summaries:{done}"
                db.commit()
        j = db.get(Job, job_id)
        if j:
            j.status = "done"
            j.error = f"summaries:{done}"
            db.commit()
        log("success", f"بازتولید خلاصه کامل شد: {done} استاد", "summary")
    except Exception as e:
        db.rollback()
        j = db.get(Job, job_id)
        if j:
            j.status = "error"
            j.error = str(e)[:2000]
            db.commit()
    finally:
        db.close()


@app.post("/api/summaries/regenerate-all")
def regenerate_all_summaries(background_tasks: BackgroundTasks, provider: str = "arvan", model: str = None, db: Session = Depends(get_db)):
    total = db.query(Professor).count()
    job = Job(name="summaries_all", status="pending", total=total, done=0)
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(_run_summaries_all, job.id, provider, model)
    return {"job_id": job.id, "total": total, "status": "pending"}


@app.patch("/api/matches/{message_id}/{professor_id}")
def review_match(message_id: int, professor_id: int, payload: MatchReviewRequest, db: Session = Depends(get_db)):
    mp = db.query(MessageProfessor).filter(
        MessageProfessor.message_id == message_id,
        MessageProfessor.professor_id == professor_id
    ).first()
    if not mp:
        raise HTTPException(status_code=404, detail="Match not found")
    mp.approved = payload.approved
    mp.needs_review = False
    if not payload.approved:
        db.query(Annotation).filter(
            Annotation.message_id == message_id,
            Annotation.professor_id == professor_id
        ).delete()
    db.commit()
    return {"success": True}


@app.get("/api/professors/{professor_id}/messages-report")
def professor_messages_report(professor_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(Message, MessageProfessor, Annotation)
        .join(MessageProfessor, MessageProfessor.message_id == Message.id)
        .outerjoin(
            Annotation,
            (Annotation.message_id == Message.id) & (Annotation.professor_id == MessageProfessor.professor_id)
        )
        .filter(MessageProfessor.professor_id == professor_id)
        .order_by(Message.date.desc())
        .all()
    )
    messages = []
    for m, mp, ann in rows:
        try:
            reactions = orjson.loads(m.reactions_json) if m.reactions_json else None
        except Exception:
            reactions = None
        messages.append({
            "message_id": m.id,
            "professor_id": professor_id,
            "date": to_jalali(m.date),
            "text": m.text_clean,
            "char_len": m.char_len,
            "match_method": mp.match_method,
            "matched_alias": mp.matched_alias,
            "needs_review": mp.needs_review,
            "approved": mp.approved,
            "reactions": reactions,
            "chain": _reply_chain(db, m) if mp.match_method == "reply_context" else [],
            "analysis": None if not ann else {
                "is_relevant": ann.is_relevant,
                "contains_opinion": ann.contains_opinion,
                "message_type": ann.message_type,
                "sentiment": ann.sentiment,
                "sentiment_score": ann.sentiment_score,
                "confidence": ann.confidence,
                "severity": ann.severity,
                "key_point": ann.key_point,
                "topics": orjson.loads(ann.topics_json) if ann.topics_json else [],
            },
        })
    return {"professor_id": professor_id, "total": len(messages), "messages": messages}


@app.get("/api/matches/pending-review")
def pending_review(db: Session = Depends(get_db)):
    rows = (
        db.query(Message, MessageProfessor, Professor, Annotation)
        .join(MessageProfessor, MessageProfessor.message_id == Message.id)
        .join(Professor, Professor.id == MessageProfessor.professor_id)
        .outerjoin(
            Annotation,
            (Annotation.message_id == Message.id) & (Annotation.professor_id == MessageProfessor.professor_id)
        )
        .filter(MessageProfessor.needs_review == True)
        .order_by(Professor.name, Message.char_len.desc())
        .all()
    )
    items = []
    for m, mp, prof, ann in rows:
        try:
            reactions = orjson.loads(m.reactions_json) if m.reactions_json else None
        except Exception:
            reactions = None
        items.append({
            "message_id": m.id,
            "professor_id": prof.id,
            "professor_name": prof.name,
            "date": to_jalali(m.date),
            "text": m.text_clean,
            "char_len": m.char_len,
            "match_method": mp.match_method,
            "reactions": reactions,
            "analysis": None if not ann else {
                "is_relevant": ann.is_relevant,
                "contains_opinion": ann.contains_opinion,
                "message_type": ann.message_type,
                "sentiment": ann.sentiment,
                "sentiment_score": ann.sentiment_score,
                "confidence": ann.confidence,
                "severity": ann.severity,
                "key_point": ann.key_point,
                "topics": orjson.loads(ann.topics_json) if ann.topics_json else [],
            },
        })
    return {"total": len(items), "items": items}


@app.post("/api/matches/bulk-review")
def bulk_review_matches(payload: MatchBulkReviewRequest, db: Session = Depends(get_db)):
    approved = 0
    rejected = 0
    for item in payload.approve:
        mp = db.query(MessageProfessor).filter(
            MessageProfessor.message_id == item.message_id,
            MessageProfessor.professor_id == item.professor_id
        ).first()
        if mp:
            mp.approved = True
            mp.needs_review = False
            approved += 1
    for item in payload.reject:
        mp = db.query(MessageProfessor).filter(
            MessageProfessor.message_id == item.message_id,
            MessageProfessor.professor_id == item.professor_id
        ).first()
        if mp:
            mp.approved = False
            mp.needs_review = False
            db.query(Annotation).filter(
                Annotation.message_id == item.message_id,
                Annotation.professor_id == item.professor_id
            ).delete()
            rejected += 1
    db.commit()
    return {"approved": approved, "rejected": rejected}

@app.get("/api/matches/pending-count")
def pending_count(db: Session = Depends(get_db)):
    count = db.query(func.count(MessageProfessor.id)).filter(
        MessageProfessor.needs_review == True
    ).scalar() or 0
    return {"count": count}

@app.post("/api/matches/auto-review")
def auto_review_matches(background_tasks: BackgroundTasks, provider: str = "arvan", model: str = None, threshold: float = 0.85, db: Session = Depends(get_db)):
    pending_count = db.query(func.count(MessageProfessor.id)).filter(
        MessageProfessor.needs_review == True
    ).scalar() or 0
    
    job = Job(name="auto_review", status="pending", total=pending_count, done=0)
    db.add(job)
    db.commit()
    db.refresh(job)
    
    def run_auto(job_id: int):
        db_local = SessionLocal()
        try:
            j = db_local.get(Job, job_id)
            j.status = "running"
            db_local.commit()
            result = run_auto_review(db_local, job_id=job_id, provider=provider, model=model, threshold=threshold)
            j = db_local.get(Job, job_id)
            j.status = "done"
            j.total = result["total"]
            j.done = result["total"]
            j.error = f"auto_ok:{result['auto_approved']} auto_no:{result['auto_rejected']} manual:{result['manual_remaining']}"
            db_local.commit()
        except Exception as e:
            db_local.rollback()
            j = db_local.get(Job, job_id)
            if j:
                j.status = "error"
                j.error = str(e)[:2000]
                db_local.commit()
        finally:
            db_local.close()
            
    background_tasks.add_task(run_auto, job.id)
    return {"job_id": job.id, "status": "pending", "total": pending_count}


@app.post("/api/analysis/run")
def run_analysis(payload: AnalysisRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if payload.provider == "arvan":
        if not arvan_client.check_health():
            raise HTTPException(status_code=503, detail="ArvanCloud unavailable")
    else:
        if not ollama_client.check_health():
            raise HTTPException(status_code=503, detail="Ollama unavailable")

    job = Job(name="llm_analysis", status="pending", total=0, done=0)
    db.add(job)
    db.commit()
    db.refresh(job)

    def run_analysis_task(job_id: int, batch_size: int, continuous: bool, workers: int, provider: str, model: str):
        db_local = SessionLocal()
        try:
            j = db_local.get(Job, job_id)
            j.status = "running"
            total_unprocessed = (
                db_local.query(Message)
                .join(MessageProfessor, Message.id == MessageProfessor.message_id)
                .filter(Message.processed_llm == False)
                .filter(Message.char_len > 5)
                .distinct()
                .count()
            )

            expected_summaries = (
                db_local.query(func.count(func.distinct(MessageProfessor.professor_id)))
                .join(Message, Message.id == MessageProfessor.message_id)
                .filter(Message.processed_llm == False)
                .filter(Message.char_len > 5)
                .scalar() or 0
            )
            j.total = total_unprocessed + expected_summaries
            j.error = "تحلیل پیام‌ها…"
            db_local.commit()


            log("info", f"Job {job_id} started | {total_unprocessed} messages | {PROVIDER_LABELS.get(provider, provider)}", "job")
            result = run_llm_analysis(
                db_local, batch_size=batch_size, continuous=continuous,
                job_id=job_id, workers=workers, provider=provider, model=model
            )
            j = db_local.get(Job, job_id)
            j.status = "done"
            j.done = result["analyzed"] + result.get("skipped", 0)
            j.error = f"ok:{result['analyzed']} err:{result['errors']} skip:{result.get('skipped', 0)} summaries:{result.get('summaries_generated', 0)}"
            db_local.commit()
            log("success", f"Job {job_id} completed", "job")
        except Exception as e:
            log("error", f"Job {job_id} error: {e}", "job")
            db_local.rollback()
            j = db_local.get(Job, job_id)
            if j:
                j.status = "error"
                j.error = str(e)[:2000]
                db_local.commit()
        finally:
            db_local.close()

    background_tasks.add_task(
        run_analysis_task, job.id, payload.batch_size, payload.continuous,
        payload.workers, payload.provider, payload.model
    )
    return {"job_id": job.id, "status": "pending", "provider": payload.provider, "model": payload.model}


@app.get("/api/analysis/stats")
def analysis_stats(db: Session = Depends(get_db)):
    total_analyzed = db.query(func.count(Annotation.id)).scalar() or 0
    sentiment_counts = {}
    for sentiment in ["positive", "negative", "neutral", "mixed"]:
        count = db.query(func.count(Annotation.id)).filter(Annotation.sentiment == sentiment).scalar() or 0
        sentiment_counts[sentiment] = count
    severity_counts = {}
    for severity in ["none", "low", "medium", "high"]:
        count = db.query(func.count(Annotation.id)).filter(Annotation.severity == severity).scalar() or 0
        severity_counts[severity] = count
    pending_count = (
        db.query(func.count(func.distinct(Message.id)))
        .join(MessageProfessor, Message.id == MessageProfessor.message_id)
        .filter(Message.processed_llm == False)
        .filter(Message.char_len > 5)
        .scalar() or 0
    )
    return {
        "total_analyzed": total_analyzed,
        "pending": pending_count,
        "sentiment_distribution": sentiment_counts,
        "severity_distribution": severity_counts
    }

@app.get("/api/analysis/cost-profile")
def cost_profile(db: Session = Depends(get_db)):
    from app.services import prompts as P
    system_tokens = P.approx_tokens(P.ANALYSIS_SYSTEM_PROMPT)
    sample = (
        db.query(Message, Professor.name)
        .join(MessageProfessor, Message.id == MessageProfessor.message_id)
        .join(Professor, MessageProfessor.professor_id == Professor.id)
        .filter(Message.char_len > 5)
        .order_by(Message.id.desc())
        .limit(200)
        .all()
    )
    user_tokens = []
    for m, prof_name in sample:
        parent_text = get_parent_text(db, m)
        user_tokens.append(P.approx_tokens(P.build_analysis_user_prompt(m.text_clean, prof_name, parent_text)))
    avg_user = sum(user_tokens) / len(user_tokens) if user_tokens else 0
    avg_kp_len = db.query(func.avg(func.length(Annotation.key_point))).filter(Annotation.key_point.isnot(None)).scalar() or 120
    summ_user = P.approx_tokens(P.build_summary_user_prompt({
        "name": "استاد نمونه",
        "stats": {"total": 120, "unique_users": 45, "positive": 60, "negative": 40, "neutral": 20},
        "aspects": [{"aspect": a, "avg": 0.4, "count": 25} for a in ["teaching_quality", "grading", "responsiveness"]],
        "topics": [("کیفیت تدریس", 30), ("نمره‌دهی", 25)],
        "key_points": ["ن" * int(avg_kp_len)] * 40,
    }))
    return {
        "ratio": P.TOKEN_RATIO,
        "analysis": {
            "system_tokens": system_tokens,
            "avg_user_tokens": round(avg_user),
            "avg_output_tokens": P.approx_tokens(P.SAMPLE_ANALYSIS_OUTPUT),
        },
        "summary": {
            "system_tokens": P.approx_tokens(P.SUMMARY_SYSTEM_PROMPT),
            "avg_user_tokens": summ_user,
            "avg_output_tokens": P.approx_tokens(P.SAMPLE_SUMMARY_OUTPUT),
        },
    }

@app.get("/api/analysis/maintenance-preview")
def maintenance_preview(db: Session = Depends(get_db)):
    opinions_messages = db.query(func.count(func.distinct(Annotation.message_id))).filter(Annotation.contains_opinion == True).scalar() or 0
    failed_messages = db.query(Message).filter(
        Message.processed_llm == True,
        ~Message.id.in_(db.query(Annotation.message_id).distinct())
    ).count()
    professors_enough = 0
    for (pid,) in db.query(Professor.id).all():
        score = calculate_professor_score(db, pid)
        if score["message_count"] >= MIN_MENTIONS_FOR_SCORE:
            professors_enough += 1
    full_reanalysis_messages = db.query(func.count(func.distinct(Message.id))).join(MessageProfessor, Message.id == MessageProfessor.message_id).filter(Message.char_len > 5).scalar() or 0
    
    # --- خط جدید اضافه شده ---
    pending_review = db.query(func.count(MessageProfessor.id)).filter(
        MessageProfessor.needs_review == True
    ).scalar() or 0
    
    return {
        "opinions_messages": opinions_messages,
        "failed_messages": failed_messages,
        "professors_enough": professors_enough,
        "full_reanalysis_messages": full_reanalysis_messages,
        "pending_review": pending_review,  # --- کلید جدید در خروجی ---
    }


@app.get("/api/analysis/logs")
def analysis_logs(limit: int = 100):
    return {"logs": get_logs(limit)}


@app.delete("/api/analysis/logs")
def clear_analysis_logs():
    clear()
    return {"success": True}


@app.post("/api/analysis/reset")
def reset_analysis(payload: ResetAnalysisRequest, db: Session = Depends(get_db)):
    if payload.mode == "all":
        count = db.query(Message).update({"processed_llm": False, "llm_error_count": 0})
        db.query(Annotation).delete()
        db.commit()
        return {"message": f"{count} messages reset for reprocessing", "count": count, "mode": "all"}
    elif payload.mode == "opinions":
        ids = [r[0] for r in db.query(Annotation.message_id)
               .filter(Annotation.contains_opinion == True).distinct().all()]
        if ids:
            db.query(Annotation).filter(Annotation.message_id.in_(ids))\
                .delete(synchronize_session=False)
            count = db.query(Message).filter(Message.id.in_(ids))\
                .update({"processed_llm": False, "llm_error_count": 0}, synchronize_session=False)
        else:
            count = 0
        db.query(Message)\
            .filter(Message.processed_llm == False, Message.char_len <= 5)\
            .update({"processed_llm": True}, synchronize_session=False)
        db.commit()
        return {"message": f"{count} opinion messages queued for re-judge", "count": count, "mode": "opinions"}
    else:
        count = db.query(Message).filter(
            Message.processed_llm == True,
            ~Message.id.in_(db.query(Annotation.message_id).distinct())
        ).update({"processed_llm": False, "llm_error_count": 0}, synchronize_session=False)
        db.commit()
        return {"message": f"{count} messages reset for reprocessing", "count": count, "mode": "failed"}
    

@app.post("/api/analysis/test")
def test_analysis(provider: str = DEFAULT_PROVIDER, model: str = None, db: Session = Depends(get_db)):
    msg = (
        db.query(Message)
        .join(MessageProfessor, Message.id == MessageProfessor.message_id)
        .filter(Message.char_len > 20)
        .filter(Message.char_len < 300)
        .first()
    )
    if not msg:
        return {"error": "No message found for testing"}
    mp = db.query(MessageProfessor).filter(MessageProfessor.message_id == msg.id).first()
    prof = db.get(Professor, mp.professor_id)
    parent_text = get_parent_text(db, msg)
    result = analyze_message(msg, prof.name, parent_text, provider=provider, model=model)
    return {
        "message_id": msg.id, "message_text": msg.text_clean[:300],
        "professor_name": prof.name, "provider": provider, "model": model,
        "analysis": result
    }


@app.post("/api/messages/report-pdf")
def generate_messages_pdf(payload: ReportPdfRequest, db: Session = Depends(get_db)):
    targets = []
    if payload.scope == "current":
        prof = db.query(Professor).first()
        if prof:
            targets = [prof]
    else:
        targets = db.query(Professor).all()

    html_parts = []
    
    css_styles = """
    @page { size: A4 landscape; margin: 10mm 8mm; }
    * { box-sizing: border-box; }
    html, body {
        background: #fff; font-family: 'Vazirmatn', Tahoma, sans-serif;
        direction: rtl; margin: 0; padding: 0; color: #111;
    }
    body { padding: 8mm; }
    .rp-prof-section { margin-bottom: 4mm; }
    .rp-prof-header {
        background: #eef2ff; border-right: 4px solid #7c3aed; border-radius: 4px;
        padding: 4px 8px; margin-bottom: 2mm; display: flex; justify-content: space-between; align-items: center;
        break-inside: avoid; page-break-inside: avoid;
        -webkit-print-color-adjust: exact; print-color-adjust: exact;
    }
    .rp-prof-name { font-size: 11px; font-weight: 800; color: #3730a3; }
    .rp-prof-dept { font-size: 8px; color: #64748b; margin-top: 1px; }
    .rp-prof-stats { font-size: 8px; color: #475569; font-weight: 600; }
    .rp-table { width: 100%; border-collapse: collapse; font-size: 8px; color: #111; }
    .rp-table th {
        background: #4338ca; color: #fff; font-weight: 800; padding: 3px 5px;
        text-align: right; font-size: 7.5px; border: 1px solid #3730a3;
        -webkit-print-color-adjust: exact; print-color-adjust: exact;
    }
    .rp-table td { padding: 3px 5px; border: 1px solid #d3d8e0; vertical-align: top; line-height: 1.5; }
    .rp-table tbody tr:nth-child(even) td { background: #f8fafc; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .rp-table tbody tr { break-inside: avoid; page-break-inside: avoid; }
    .rp-msg-text { font-size: 8px; line-height: 1.6; color: #1e293b; }
    .rp-chain {
        margin: 2px 0; padding-right: 6px; border-right: 2px solid #c7d2fe;
        font-size: 7px; color: #64748b; line-height: 1.5;
    }
    .rp-chain-item { margin-bottom: 2px; }
    .rp-chain-item:last-child { font-weight: 600; color: #4338ca; background: #eef2ff; padding: 1px 3px; border-radius: 2px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .rp-chain-lbl { font-size: 6px; font-weight: 700; color: #7c3aed; display: block; }
    .rp-decision { font-size: 7.5px; line-height: 1.6; }
    .rp-pill { display: inline-block; border-radius: 8px; padding: 1px 4px; font-size: 7px; font-weight: 700; margin-left: 2px; }
    .rpp-green { background: #d1fae5; color: #065f46; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .rpp-red { background: #fee2e2; color: #991b1b; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .rpp-slate { background: #e2e8f0; color: #334155; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .rpp-amber { background: #fef3c7; color: #92400e; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .rpp-violet { background: #ede9fe; color: #5b21b6; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .rp-kp { color: #6d28d9; font-weight: 700; font-size: 7px; margin-top: 2px; }
    .rp-topics { margin-top: 2px; }
    .rp-topic-tag {
        background: #f1f5f9; border-radius: 3px; padding: 0 3px;
        font-size: 6.5px; color: #475569; margin-left: 2px; display: inline-block;
        -webkit-print-color-adjust: exact; print-color-adjust: exact;
    }
    .rp-footer {
        margin-top: 4mm; padding-top: 2mm; border-top: 1px solid #d3d8e0;
        font-size: 7px; color: #64748b; display: flex; justify-content: space-between;
    }
    .rp-report-hero {
        background: linear-gradient(135deg, #4338ca 0%, #7c3aed 55%, #a855f7 100%);
        color: #fff; border-radius: 6px; padding: 6px 10px;
        display: flex; justify-content: space-between; align-items: center; margin-bottom: 4mm;
        -webkit-print-color-adjust: exact; print-color-adjust: exact;
        break-inside: avoid; page-break-inside: avoid;
    }
    """

    html_parts.append(f"""
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet" type="text/css" />
        <style>{css_styles}</style>
    </head>
    <body>
        <div class="rp-report-hero">
            <div style="font-size: 13px; font-weight: 800;">
                استادسنج
                <small style="display: block; font-size: 8px; font-weight: 400; opacity: .9; margin-top: 1px;">گزارش پیام‌ها و تصمیم‌های تحلیل | تولید خودکار</small>
            </div>
            <div style="font-size: 9px; opacity: .95;">تاریخ گزارش: {to_jalali(datetime.utcnow())}</div>
        </div>
    """)

    METHOD_FA = {"exact": "نام دقیق", "hashtag": "هشتگ", "reply_context": "زنجیره ریپلای"}

    for prof in targets:
        rows = (
            db.query(Message, MessageProfessor, Annotation)
            .join(MessageProfessor, MessageProfessor.message_id == Message.id)
            .outerjoin(Annotation, (Annotation.message_id == Message.id) & (Annotation.professor_id == MessageProfessor.professor_id))
            .filter(MessageProfessor.professor_id == prof.id)
            .order_by(Message.date.desc())
            .all()
        )
        
        list_data = []
        for m, mp, ann in rows:
            if payload.only_opinions and (not ann or not ann.contains_opinion):
                continue
            if not payload.include_no_analysis and not ann:
                continue
                
            reactions = 0
            if m.reactions_json:
                try:
                    reactions = sum(int(v) for v in orjson.loads(m.reactions_json).values())
                except:
                    pass

            chain_html = ""
            if payload.with_chain and mp.match_method == "reply_context":
                chain = []
                current = m
                for _ in range(3):
                    if current.reply_to_message_id is None:
                        break
                    parent = db.query(Message).filter(Message.telegram_id == current.reply_to_message_id).first()
                    if not parent:
                        break
                    chain.append({"text": parent.text_clean[:400], "date": to_jalali(parent.date)})
                    current = parent
                chain.reverse()
                if chain:
                    chain_items = "".join([f'<div class="rp-chain-item"><span class="rp-chain-lbl">{"پاسخ به:" if i == len(chain)-1 else "بالاتر:"} {c["date"]}</span>{c["text"]}</div>' for i, c in enumerate(chain)])
                    chain_html = f'<div class="rp-chain">{chain_items}</div>'

            decision_html = '<span class="rp-pill rpp-slate">بدون تحلیل</span>'
            if ann:
                if not ann.is_relevant:
                    decision_html = '<span class="rp-pill rpp-slate">غیرمرتبط</span>'
                else:
                    score = round((ann.sentiment_score or 0) * 100)
                    cls = {"positive": "rpp-green", "negative": "rpp-red", "mixed": "rpp-amber"}.get(ann.sentiment, "rpp-slate")
                    faS = {"positive": "مثبت", "negative": "منفی", "mixed": "مختلط", "neutral": "خنثی"}.get(ann.sentiment, ann.sentiment)
                    pills = [f'<span class="rp-pill {cls}">{faS} {score}</span>']
                    pills.append(f'<span class="rp-pill {"rpp-violet" if ann.contains_opinion else "rpp-slate"}">{"نظر" if ann.contains_opinion else "بدون نظر"}</span>')
                    if ann.severity in ["medium", "high"]:
                        pills.append(f'<span class="rp-pill rpp-red">شدت: {"بالا" if ann.severity == "high" else "متوسط"}</span>')
                    
                    extra = ""
                    if ann.key_point:
                        extra += f'<div class="rp-kp">{ann.key_point}</div>'
                    if ann.topics_json:
                        try:
                            topics = orjson.loads(ann.topics_json)[:3]
                            extra += f'<div class="rp-topics">{"".join([f"<span class=\"rp-topic-tag\">{t}</span>" for t in topics])}</div>'
                        except:
                            pass
                    decision_html = f'<div class="rp-decision">{"".join(pills)}{extra}</div>'

            list_data.append({
                "date": to_jalali(m.date),
                "method": METHOD_FA.get(mp.match_method, mp.match_method),
                "reactions": reactions,
                "chain": chain_html,
                "text": m.text_clean,
                "decision": decision_html
            })

        if not list_data:
            continue

        rows_html = "".join([f"""
            <tr>
                <td style="white-space:nowrap;font-size:7px;">{r["date"] or "-"}</td>
                <td style="font-size:7px;">{r["method"]}</td>
                <td style="text-align:center;font-size:7px;">{r["reactions"] if r["reactions"] > 0 else "-"}</td>
                <td>
                    {r["chain"]}
                    <div class="rp-msg-text">{r["text"]}</div>
                </td>
                <td>{r["decision"]}</td>
            </tr>
        """ for r in list_data])

        html_parts.append(f"""
        <div class="rp-prof-section">
            <div class="rp-prof-header">
                <div>
                    <div class="rp-prof-name">{prof.name}</div>
                    <div class="rp-prof-dept">{prof.department or "بدون دپارتمان"}</div>
                </div>
                <div class="rp-prof-stats">
                    {len(list_data)} پیام
                </div>
            </div>
            <table class="rp-table">
                <thead>
                    <tr>
                        <th style="width:50px;">تاریخ</th>
                        <th style="width:40px;">روش</th>
                        <th style="width:30px;">واکنش</th>
                        <th>متن پیام و زنجیره</th>
                        <th style="width:140px;">تصمیم سیستم</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
            <div class="rp-footer">
                <span>استادسنج — سامانه تحلیل نظرات دانشجویان</span>
                <span>پایان گزارش {prof.name}</span>
            </div>
        </div>
        """)

    html_parts.append("</body></html>")
    final_html = "".join(html_parts)

    pdf_file = weasyprint.HTML(string=final_html).write_pdf()
    
    filename = f"report-{payload.scope}.pdf"
    encoded_filename = urllib.parse.quote(filename)
    
    return Response(
        content=pdf_file,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"}
    )