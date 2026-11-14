from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel, EmailStr
from typing import Optional
from passlib.context import CryptContext
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import uuid
import random

# 1. डेटाबेस सेटअप
DATABASE_URL = "sqlite:///./committee_app.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# 2. डेटाबेस टेबल्स
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    mobile = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    upi_id = Column(String, nullable=False)
    nominee_name = Column(String, nullable=False)
    nominee_phone = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, index=True)
    group_code = Column(String, unique=True, index=True)
    admin_id = Column(Integer, nullable=False)
    co_admin_id = Column(Integer, nullable=True)
    group_name = Column(String, nullable=False)
    receiver_name = Column(String, nullable=False)
    receiver_upi_id = Column(String, nullable=False)
    first_installment_date = Column(String, nullable=False)
    frequency = Column(String, nullable=False)
    duration_months = Column(Integer, nullable=False)
    total_installments = Column(Integer, nullable=False)
    amount_per_user = Column(Float, nullable=False)
    penalty_type = Column(String, default="FIXED")
    penalty_value = Column(Float, default=0.0)
    penalty_after_2_due_percent = Column(Float, default=0.0)
    end_date = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class GroupMember(Base):
    __tablename__ = "group_members"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    status = Column(String, default="Pending")
    has_received_pot = Column(Boolean, default=False)
    joined_at = Column(DateTime, default=datetime.utcnow)

class Cycle(Base):
    __tablename__ = "cycles"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, nullable=False)
    cycle_number = Column(Integer, nullable=False)
    recipient_user_id = Column(Integer, nullable=True)
    start_date = Column(DateTime, default=datetime.utcnow)
    due_date = Column(DateTime, nullable=False)
    is_closed = Column(Boolean, default=False)
    payout_completed = Column(Boolean, default=False)
    lottery_requested_by_admin_id = Column(Integer, nullable=True)
    lottery_approved_by_admin_id = Column(Integer, nullable=True)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    cycle_id = Column(Integer, nullable=False)
    payer_user_id = Column(Integer, nullable=False)
    amount_paid = Column(Float, nullable=False)
    penalty_paid = Column(Float, default=0.0)
    utr_number = Column(String, index=True, nullable=False)
    payment_app = Column(String, nullable=False)
    cash_received_date = Column(String, nullable=True)
    cash_receiver_name = Column(String, nullable=True)
    status = Column(String, default="Pending")
    paid_at = Column(DateTime, default=datetime.utcnow)

class PayoutRequest(Base):
    __tablename__ = "payout_requests"
    id = Column(Integer, primary_key=True, index=True)
    cycle_id = Column(Integer, nullable=False)
    group_id = Column(Integer, nullable=False)
    requested_by_user_id = Column(Integer, nullable=False)
    request_type = Column(String, nullable=False)
    suggested_user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class GroupGovernanceRequest(Base):
    __tablename__ = "group_governance_requests"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, nullable=False)
    action_type = Column(String, nullable=False)
    target_user_id = Column(Integer, nullable=False)
    new_admin_user_id = Column(Integer, nullable=True)
    requested_by_user_id = Column(Integer, nullable=False)
    admin1_approved = Column(Boolean, default=False)
    admin2_approved = Column(Boolean, default=False)
    target_self_approved = Column(Boolean, default=False)
    status = Column(String, default="Pending")
    created_at = Column(DateTime, default=datetime.utcnow)

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    group_id = Column(Integer, nullable=True)
    cycle_id = Column(Integer, nullable=True)
    action_type = Column(String, default="INFO")
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Committee & Group Savings Pro API", version="15.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def is_user_group_admin(group: Group, user_id: int) -> bool:
    return group.admin_id == user_id or (group.co_admin_id is not None and group.co_admin_id == user_id)

def get_group_admin_ids(group: Group) -> list:
    ids = [group.admin_id]
    if group.co_admin_id and group.co_admin_id != group.admin_id:
        ids.append(group.co_admin_id)
    return ids

def get_group_admins_details(db: Session, group: Group) -> list:
    admins_info = []
    u1 = db.query(User).filter(User.id == group.admin_id).first()
    if u1:
        admins_info.append({
            "user_id": u1.id,
            "full_name": u1.full_name,
            "mobile": u1.mobile,
            "email": u1.email,
            "role": "Primary Admin",
        })
    if group.co_admin_id and group.co_admin_id != group.admin_id:
        u2 = db.query(User).filter(User.id == group.co_admin_id).first()
        if u2:
            admins_info.append({
                "user_id": u2.id,
                "full_name": u2.full_name,
                "mobile": u2.mobile,
                "email": u2.email,
                "role": "Co-Admin",
            })
    return admins_info

def get_group_winners_history(db: Session, group: Group, member_count: int) -> list:
    cycles = db.query(Cycle).filter(Cycle.group_id == group.id).order_by(Cycle.cycle_number.asc()).all()
    winners = []
    pot_amount = round(member_count * group.amount_per_user, 2)
    for c in cycles:
        if c.payout_completed and c.recipient_user_id:
            wu = db.query(User).filter(User.id == c.recipient_user_id).first()
            winners.append({
                "cycle_number": c.cycle_number,
                "winner_user_id": c.recipient_user_id,
                "winner_name": wu.full_name if wu else f"User #{c.recipient_user_id}",
                "winner_mobile": wu.mobile if wu else "-",
                "winner_upi_id": wu.upi_id if wu else "-",
                "pot_amount": pot_amount,
            })
    return winners

def calculate_total_installments(frequency: str, duration_months: int) -> int:
    freq = frequency.strip().capitalize()
    if freq == "Daily":
        return duration_months * 30
    elif freq == "Weekly":
        return duration_months * 4
    elif freq == "Quarterly":
        return max(1, (duration_months + 2) // 3)
    return duration_months

def get_cycle_interval_days(frequency: str) -> int:
    freq = frequency.strip().capitalize()
    if freq == "Daily":
        return 1
    elif freq == "Weekly":
        return 7
    elif freq == "Quarterly":
        return 90
    return 30

def ensure_all_plan_cycles_created(db: Session, group: Group):
    total_inst = group.total_installments or calculate_total_installments(group.frequency, group.duration_months)
    existing_cycles = db.query(Cycle).filter(Cycle.group_id == group.id).order_by(Cycle.cycle_number.asc()).all()
    if len(existing_cycles) >= total_inst:
        return

    try:
        first_dt = datetime.strptime(group.first_installment_date, "%Y-%m-%d")
    except Exception:
        first_dt = datetime.utcnow()

    interval_days = get_cycle_interval_days(group.frequency)
    existing_nums = {c.cycle_number for c in existing_cycles}

    for num in range(1, total_inst + 1):
        if num not in existing_nums:
            c_start = first_dt + timedelta(days=interval_days * (num - 1))
            c_due = c_start + timedelta(days=interval_days - 1) if interval_days > 1 else c_start
            new_c = Cycle(
                group_id=group.id,
                cycle_number=num,
                start_date=c_start,
                due_date=c_due,
                is_closed=False
            )
            db.add(new_c)
    db.commit()

def auto_approve_stale_transactions(db: Session):
    two_days_ago = datetime.utcnow() - timedelta(days=2)
    stale_txns = db.query(Transaction).filter(
        Transaction.status == "Pending",
        Transaction.paid_at <= two_days_ago
    ).all()
    if stale_txns:
        for t in stale_txns:
            t.status = "Approved"
        db.commit()

def calculate_member_financial_status(db: Session, group: Group, user_id: int) -> dict:
    auto_approve_stale_transactions(db)
    ensure_all_plan_cycles_created(db, group)

    total_plan_inst = group.total_installments or calculate_total_installments(group.frequency, group.duration_months)
    all_cycles = db.query(Cycle).filter(Cycle.group_id == group.id).order_by(Cycle.cycle_number.asc()).all()
    cycle_ids = [c.id for c in all_cycles]

    user_txns = db.query(Transaction).filter(
        Transaction.cycle_id.in_(cycle_ids),
        Transaction.payer_user_id == user_id,
        Transaction.status.in_(["Approved", "Pending"])
    ).all() if cycle_ids else []

    approved_txns = [t for t in user_txns if t.status == "Approved"]
    pending_approval_txns = [t for t in user_txns if t.status == "Pending"]

    covered_cycle_ids = {t.cycle_id for t in user_txns}
    approved_count = len({t.cycle_id for t in approved_txns})
    submitted_count = len(covered_cycle_ids)

    is_user_target_completed = submitted_count >= total_plan_inst

    now = datetime.utcnow()
    unpaid_cycles = [c for c in all_cycles if c.id not in covered_cycle_ids]

    overdue_cycles = []
    for c in unpaid_cycles:
        end_of_due_day = c.due_date.replace(hour=23, minute=59, second=59)
        if now > end_of_due_day:
            overdue_cycles.append(c)

    overdue_count = len(overdue_cycles)
    base_installment = group.amount_per_user

    if is_user_target_completed or not unpaid_cycles:
        cycles_to_pay_now = []
    elif overdue_count > 1:
        cycles_to_pay_now = overdue_cycles
    else:
        cycles_to_pay_now = [unpaid_cycles[0]]

    installments_count_to_pay = len(cycles_to_pay_now)
    principal_payable_now = installments_count_to_pay * base_installment
    penalty_amount = 0.0
    penalty_reason = "कोई पेनल्टी नहीं (समय पर)"
    penalty_reason_en = "On Time (No Penalty)"

    if overdue_count > 0 and not is_user_target_completed:
        if group.penalty_type == "FIXED":
            penalty_amount = overdue_count * group.penalty_value
            penalty_reason = f"{overdue_count} बकाया किस्त × ₹{group.penalty_value} फिक्स्ड"
            penalty_reason_en = f"{overdue_count} Due x Rs.{group.penalty_value} Fixed"
        elif group.penalty_type == "PERCENT_INSTALLMENT":
            per_late_pen = (base_installment * group.penalty_value) / 100.0
            penalty_amount = overdue_count * per_late_pen
            penalty_reason = f"{overdue_count} बकाया किस्त पर {group.penalty_value}% पेनल्टी"
            penalty_reason_en = f"{overdue_count} Due @ {group.penalty_value}%"
        elif group.penalty_type == "ESCALATED_AFTER_2_DUE":
            if overdue_count >= 2:
                total_overdue_principal = overdue_count * base_installment
                penalty_amount = (total_overdue_principal * group.penalty_after_2_due_percent) / 100.0
                penalty_reason = f"{overdue_count} बकाया किस्तों (₹{total_overdue_principal}) पर {group.penalty_after_2_due_percent}% पेनल्टी"
                penalty_reason_en = f"{overdue_count} Due (Rs.{total_overdue_principal}) @ {group.penalty_after_2_due_percent}%"
            else:
                penalty_amount = (base_installment * group.penalty_value) / 100.0
                penalty_reason = f"1 बकाया किस्त पर {group.penalty_value}% सामान्य पेनल्टी"
                penalty_reason_en = f"1 Due @ {group.penalty_value}%"

    next_cycle = cycles_to_pay_now[0] if cycles_to_pay_now else None

    return {
        "approved_installments": approved_count,
        "submitted_installments": submitted_count,
        "pending_approval_count": len(pending_approval_txns),
        "total_plan_installments": total_plan_inst,
        "is_user_target_completed": is_user_target_completed,
        "remaining_installments": max(0, total_plan_inst - approved_count),
        "overdue_installments": overdue_count,
        "installments_count_to_pay_now": installments_count_to_pay,
        "principal_payable_now": round(principal_payable_now, 2),
        "penalty_amount": round(penalty_amount, 2),
        "total_payable_now": 0.0 if is_user_target_completed else round(principal_payable_now + penalty_amount, 2),
        "penalty_reason": penalty_reason,
        "penalty_reason_en": penalty_reason_en,
        "cycles_to_pay_ids": [c.id for c in cycles_to_pay_now],
        "cycles_to_pay_numbers": [c.cycle_number for c in cycles_to_pay_now],
        "next_cycle_id": next_cycle.id if next_cycle else None,
        "next_cycle_number": next_cycle.cycle_number if next_cycle else None,
        "next_cycle_due_date": next_cycle.due_date.strftime("%Y-%m-%d") if next_cycle else None,
    }

def create_user_notification(
    db: Session,
    user_id: int,
    title: str,
    message: str,
    group_id: int = None,
    cycle_id: int = None,
    action_type: str = "INFO"
):
    notif = Notification(
        user_id=user_id,
        group_id=group_id,
        cycle_id=cycle_id,
        action_type=action_type,
        title=title,
        message=message
    )
    db.add(notif)
    db.commit()

def notify_all_group_admins(db: Session, group: Group, title: str, message: str, action_type: str = "APPROVAL"):
    for aid in get_group_admin_ids(group):
        create_user_notification(db, aid, title, message, group_id=group.id, action_type=action_type)

def send_email_to_member(to_email: str, subject: str, body: str, reply_to_emails: list = None) -> bool:
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print(f"[SIMULATED EMAIL TO {to_email} | Reply-To: {reply_to_emails}] Subject: {subject}\n{body}\n")
        return True
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_EMAIL
        msg["To"] = to_email
        if reply_to_emails:
            msg["Reply-To"] = ", ".join(reply_to_emails)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Email failed to {to_email}: {e}")
        return False

def finalize_winner_in_db(db: Session, group: Group, cycle: Cycle, winner_id: int) -> dict:
    approved_members = db.query(GroupMember).filter(
        GroupMember.group_id == group.id,
        GroupMember.status == "Approved"
    ).all()

    valid_member_ids = {m.user_id for m in approved_members}
    if winner_id not in valid_member_ids:
        raise HTTPException(status_code=400, detail="विजेता इसी ग्रुप के स्वीकृत सदस्यों में से ही होना चाहिए")

    cycle.recipient_user_id = winner_id
    cycle.is_closed = True
    cycle.payout_completed = True

    member_rec = db.query(GroupMember).filter(
        GroupMember.group_id == group.id,
        GroupMember.user_id == winner_id
    ).first()
    if member_rec:
        member_rec.has_received_pot = True
    db.commit()

    winner_user = db.query(User).filter(User.id == winner_id).first()
    for m in approved_members:
        create_user_notification(
            db,
            m.user_id,
            f"🏆 लॉटरी विजेता घोषित: {group.group_name} (साइकिल #{cycle.cycle_number})",
            f"साइकिल #{cycle.cycle_number} की राशि विजेता सदस्य '{winner_user.full_name if winner_user else winner_id}' को आवंटित कर दी गई है।",
            group_id=group.id,
            cycle_id=cycle.id,
            action_type="INFO"
        )

    return {
        "winner_user_id": winner_id,
        "winner_name": winner_user.full_name if winner_user else f"User #{winner_id}",
    }

def trigger_cycle_automation(db: Session, group: Group):
    auto_approve_stale_transactions(db)
    ensure_all_plan_cycles_created(db, group)

    now = datetime.utcnow()
    today_str = now.strftime("%Y-%m-%d")

    members = db.query(GroupMember).filter(
        GroupMember.group_id == group.id,
        GroupMember.status == "Approved"
    ).all()

    for m in members:
        status = calculate_member_financial_status(db, group, m.user_id)
        if status["is_user_target_completed"] or not status["next_cycle_id"]:
            continue

        next_c = db.query(Cycle).filter(Cycle.id == status["next_cycle_id"]).first()
        if not next_c:
            continue

        today_notifs_count = db.query(Notification).filter(
            Notification.user_id == m.user_id,
            Notification.cycle_id == next_c.id,
            Notification.action_type == "PAYMENT_DUE",
            Notification.created_at >= datetime.strptime(today_str, "%Y-%m-%d")
        ).count()

        if now.date() == next_c.due_date.date():
            target_slot = 3 if now.hour >= 22 else (2 if now.hour >= 20 else (1 if now.hour >= 18 else 0))
            if target_slot > today_notifs_count:
                create_user_notification(
                    db,
                    m.user_id,
                    f"⏰ अंतिम तिथि रिमाइंडर ({target_slot}/3): {group.group_name}",
                    f"आज रात 12:00 बजे तक साइकिल #{next_c.cycle_number} की किस्त ₹{status['total_payable_now']} जमा करें।",
                    group_id=group.id,
                    cycle_id=next_c.id,
                    action_type="PAYMENT_DUE"
                )
        elif status["overdue_installments"] > 0 and today_notifs_count == 0:
            create_user_notification(
                db,
                m.user_id,
                f"⚠️ बकाया किस्त व पेनल्टी अलर्ट: {group.group_name}",
                f"आपकी {status['overdue_installments']} किस्तें बकाया हैं। मूल ₹{status['principal_payable_now']} + पेनल्टी ₹{status['penalty_amount']} = कुल ₹{status['total_payable_now']} जमा करें।",
                group_id=group.id,
                cycle_id=next_c.id,
                action_type="PAYMENT_DUE"
            )

# 3. इनपुट मॉडल्स
class UserRegister(BaseModel):
    full_name: str
    mobile: str
    email: EmailStr
    password: str
    upi_id: str
    nominee_name: str
    nominee_phone: str

class UserLogin(BaseModel):
    mobile: str
    password: str

class GroupCreate(BaseModel):
    admin_id: int
    group_name: str
    receiver_name: str
    receiver_upi_id: str
    first_installment_date: str
    frequency: str
    duration_months: int
    amount_per_user: float
    penalty_type: str = "FIXED"
    penalty_value: float = 0.0
    penalty_after_2_due_percent: float = 0.0

class JoinRequest(BaseModel):
    user_id: int
    invite_code: str

class MemberApproval(BaseModel):
    admin_id: int
    member_record_id: int
    action: str

class GovernanceRequestCreate(BaseModel):
    group_id: int
    requested_by_admin_id: int
    action_type: str
    target_user_id: int
    new_admin_user_id: Optional[int] = None

class GovernanceRequestAction(BaseModel):
    request_id: int
    actor_user_id: int
    decision: str

class LotterySpinPermission(BaseModel):
    cycle_id: int
    admin_id: int

class PaymentSubmit(BaseModel):
    group_id: int
    payer_user_id: int
    utr_number: str = ""
    payment_app: str = "UPI"
    cash_received_date: str = None
    cash_receiver_name: str = None

class PaymentApproval(BaseModel):
    admin_id: int
    transaction_id: int
    action: str

class PayoutRequestCreate(BaseModel):
    cycle_id: int
    user_id: int
    request_type: str
    suggested_user_id: Optional[int] = None

class FinalizePayout(BaseModel):
    admin_id: int
    cycle_id: int
    mode: str
    winner_user_id: Optional[int] = None

class NextCycleOrExtend(BaseModel):
    admin_id: int
    group_id: int
    action: str
    extra_months: int = 1

# 4. API Endpoints

@app.post("/register", tags=["1. User Auth"])
def register_user(user: UserRegister, db: Session = Depends(get_db)):
    if db.query(User).filter((User.mobile == user.mobile) | (User.email == user.email)).first():
        raise HTTPException(status_code=400, detail="मोबाइल नंबर या ईमेल पहले से रजिस्टर्ड है")
    new_user = User(
        full_name=user.full_name,
        mobile=user.mobile,
        email=user.email,
        password_hash=pwd_context.hash(user.password),
        upi_id=user.upi_id,
        nominee_name=user.nominee_name,
        nominee_phone=user.nominee_phone
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "रजिस्ट्रेशन सफल रहा!", "user_id": new_user.id}

@app.post("/login", tags=["1. User Auth"])
def login_user(creds: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.mobile == creds.mobile).first()
    if not user or not pwd_context.verify(creds.password, user.password_hash):
        raise HTTPException(status_code=401, detail="गलत मोबाइल नंबर या पासवर्ड")
    return {
        "message": "लॉगिन सफल",
        "user_id": user.id,
        "full_name": user.full_name,
        "mobile": user.mobile,
        "upi_id": user.upi_id
    }

@app.get("/user/{user_id}/notifications", tags=["1. User Auth"])
def get_user_notifications(user_id: int, db: Session = Depends(get_db)):
    notifs = db.query(Notification).filter(
        Notification.user_id == user_id
    ).order_by(Notification.id.desc()).limit(30).all()
    return [
        {
            "id": n.id,
            "group_id": n.group_id,
            "cycle_id": n.cycle_id,
            "action_type": n.action_type,
            "title": n.title,
            "message": n.message,
            "is_read": n.is_read,
            "created_at": n.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for n in notifs
    ]

@app.post("/user/{user_id}/notifications/read-all", tags=["1. User Auth"])
def mark_notifications_read(user_id: int, db: Session = Depends(get_db)):
    db.query(Notification).filter(Notification.user_id == user_id).update({"is_read": True})
    db.commit()
    return {"message": "सभी नोटिफिकेशन पढ़े गए"}

# --- 2. GROUP CREATION & DUAL-ADMIN GOVERNANCE ---
@app.post("/create-group", tags=["2. Group & Admin"])
def create_group(data: GroupCreate, db: Session = Depends(get_db)):
    code = f"CMT-{uuid.uuid4().hex[:6].upper()}"
    try:
        first_dt = datetime.strptime(data.first_installment_date.strip(), "%Y-%m-%d")
    except Exception:
        raise HTTPException(status_code=400, detail="पहली किस्त की तारीख YYYY-MM-DD फ़ॉर्मेट में भरें")

    total_inst = calculate_total_installments(data.frequency, data.duration_months)
    end_dt = first_dt + timedelta(days=30 * data.duration_months)

    new_group = Group(
        group_code=code,
        admin_id=data.admin_id,
        co_admin_id=None,
        group_name=data.group_name,
        receiver_name=data.receiver_name,
        receiver_upi_id=data.receiver_upi_id,
        first_installment_date=data.first_installment_date.strip(),
        frequency=data.frequency,
        duration_months=data.duration_months,
        total_installments=total_inst,
        amount_per_user=data.amount_per_user,
        penalty_type=data.penalty_type,
        penalty_value=data.penalty_value,
        penalty_after_2_due_percent=data.penalty_after_2_due_percent,
        end_date=end_dt
    )
    db.add(new_group)
    db.commit()
    db.refresh(new_group)

    admin_member = GroupMember(group_id=new_group.id, user_id=data.admin_id, status="Approved")
    db.add(admin_member)
    db.commit()

    ensure_all_plan_cycles_created(db, new_group)

    return {
        "message": f"ग्रुप बन गया! ({data.frequency} प्लान: {data.duration_months} माह = कुल {total_inst} किस्तें)",
        "group_id": new_group.id,
        "group_code": code,
        "invite_code": code,
        "total_installments": total_inst
    }

@app.post("/join-group", tags=["2. Group & Admin"])
def join_group(req: JoinRequest, db: Session = Depends(get_db)):
    group = db.query(Group).filter(Group.group_code == req.invite_code.strip(), Group.is_active == True).first()
    if not group:
        raise HTTPException(status_code=404, detail="गलत या निष्क्रिय Group Invite Code")
    existing = db.query(GroupMember).filter(
        GroupMember.group_id == group.id,
        GroupMember.user_id == req.user_id,
        GroupMember.status.in_(["Pending", "Approved"])
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"इस ग्रुप में आपका अनुरोध पहले से '{existing.status}' स्थिति में है")

    new_member = GroupMember(group_id=group.id, user_id=req.user_id, status="Pending")
    db.add(new_member)
    db.commit()

    user = db.query(User).filter(User.id == req.user_id).first()
    notify_all_group_admins(
        db,
        group,
        "नया ग्रुप जॉइन अनुरोध",
        f"{user.full_name if user else 'सदस्य'} ने ग्रुप '{group.group_name}' ({group.group_code}) में जुड़ने का अनुरोध भेजा है।"
    )
    return {"message": f"'{group.group_name}' में जुड़ने का अनुरोध एडमिन को भेज दिया गया है।"}

@app.post("/admin/approve-member", tags=["2. Group & Admin"])
def approve_or_reject_member(data: MemberApproval, db: Session = Depends(get_db)):
    member = db.query(GroupMember).filter(GroupMember.id == data.member_record_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="सदस्य रिकॉर्ड नहीं मिला")

    group = db.query(Group).filter(Group.id == member.group_id).first()
    if not group or not is_user_group_admin(group, data.admin_id):
        raise HTTPException(status_code=403, detail="केवल इस ग्रुप का एडमिन ही सदस्य को अप्रूव कर सकता है")

    member.status = data.action
    db.commit()

    ensure_all_plan_cycles_created(db, group)
    cycle1 = db.query(Cycle).filter(Cycle.group_id == group.id, Cycle.cycle_number == 1).first()

    user = db.query(User).filter(User.id == member.user_id).first()
    status_hindi = "स्वीकृत (Approved)" if data.action == "Approved" else "अस्वीकृत (Rejected)"
    msg = (
        f"नमस्ते {user.full_name if user else ''}, कमेटी ग्रुप '{group.group_name}' ({group.group_code}) "
        f"में आपका अनुरोध {status_hindi} कर दिया गया है।"
    )
    create_user_notification(
        db,
        member.user_id,
        f"ग्रुप अनुरोध {status_hindi}",
        msg,
        group_id=group.id,
        cycle_id=cycle1.id if cycle1 else None,
        action_type="PAYMENT_DUE" if (data.action == "Approved" and cycle1) else "INFO"
    )

    return {
        "message": f"सदस्य का अनुरोध {data.action} कर दिया गया है",
        "user_mobile": user.mobile if user else "",
        "user_name": user.full_name if user else "",
        "whatsapp_message": msg
    }

def execute_governance_change_if_ready(db: Session, group: Group, req: GroupGovernanceRequest) -> bool:
    admin_ids = get_group_admin_ids(group)
    has_two_admins = len(admin_ids) == 2

    admins_ok = req.admin1_approved and (req.admin2_approved if has_two_admins else True)

    if req.action_type in ["REMOVE_ADMIN", "CHANGE_ADMIN"]:
        if not req.target_self_approved:
            return False

    if not admins_ok:
        return False

    if req.action_type == "ADD_CO_ADMIN":
        if group.co_admin_id is not None:
            raise HTTPException(status_code=400, detail="इस ग्रुप में पहले से 2 एडमिन हैं। अधिकतम 2 एडमिन ही हो सकते हैं।")
        group.co_admin_id = req.target_user_id

    elif req.action_type == "REMOVE_ADMIN":
        if len(admin_ids) <= 1:
            raise HTTPException(status_code=400, detail="ग्रुप में कम से कम 1 एडमिन होना अनिवार्य है। इसे हटाया नहीं जा सकता।")
        if group.co_admin_id == req.target_user_id:
            group.co_admin_id = None
        elif group.admin_id == req.target_user_id and group.co_admin_id:
            group.admin_id = group.co_admin_id
            group.co_admin_id = None

    elif req.action_type == "CHANGE_ADMIN":
        if not req.new_admin_user_id:
            raise HTTPException(status_code=400, detail="नया एडमिन चुनना आवश्यक है")
        if req.new_admin_user_id in admin_ids:
            raise HTTPException(status_code=400, detail="चुना गया सदस्य पहले से एडमिन है")
        if group.admin_id == req.target_user_id:
            group.admin_id = req.new_admin_user_id
        elif group.co_admin_id == req.target_user_id:
            group.co_admin_id = req.new_admin_user_id

    elif req.action_type == "REMOVE_MEMBER":
        if is_user_group_admin(group, req.target_user_id):
            raise HTTPException(status_code=400, detail="एडमिन को सीधे सदस्य सूची से नहीं हटाया जा सकता; पहले Admin पद से हटाएं।")
        gm = db.query(GroupMember).filter(
            GroupMember.group_id == group.id,
            GroupMember.user_id == req.target_user_id,
            GroupMember.status == "Approved"
        ).first()
        if gm:
            gm.status = "Removed"

    req.status = "Approved"
    db.commit()
    return True

@app.post("/group/governance/request", tags=["2. Group & Admin"])
def create_governance_request(data: GovernanceRequestCreate, db: Session = Depends(get_db)):
    group = db.query(Group).filter(Group.id == data.group_id).first()
    if not group or not is_user_group_admin(group, data.requested_by_admin_id):
        raise HTTPException(status_code=403, detail="केवल ग्रुप एडमिन ही यह अनुरोध बना सकता है")

    admin_ids = get_group_admin_ids(group)
    if data.action_type == "ADD_CO_ADMIN":
        if len(admin_ids) >= 2:
            raise HTTPException(status_code=400, detail="इस ग्रुप में अधिकतम 2 एडमिन पहले से मौजूद हैं")
        if data.target_user_id in admin_ids:
            raise HTTPException(status_code=400, detail="यह सदस्य पहले से एडमिन है")

    if data.action_type == "REMOVE_ADMIN" and len(admin_ids) <= 1:
        raise HTTPException(status_code=400, detail="ग्रुप में न्यूनतम 1 एडमिन अनिवार्य है; आप एकमात्र एडमिन को नहीं हटा सकते")

    is_a1 = (data.requested_by_admin_id == group.admin_id)
    is_a2 = (group.co_admin_id is not None and data.requested_by_admin_id == group.co_admin_id)
    is_target_self = (data.requested_by_admin_id == data.target_user_id)

    req = GroupGovernanceRequest(
        group_id=group.id,
        action_type=data.action_type,
        target_user_id=data.target_user_id,
        new_admin_user_id=data.new_admin_user_id,
        requested_by_user_id=data.requested_by_admin_id,
        admin1_approved=is_a1,
        admin2_approved=is_a2,
        target_self_approved=is_target_self,
        status="Pending"
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    executed = execute_governance_change_if_ready(db, group, req)

    target_u = db.query(User).filter(User.id == data.target_user_id).first()
    notify_all_group_admins(
        db,
        group,
        f"ग्रुप प्रबंधन अनुरोध ({data.action_type})",
        f"ग्रुप '{group.group_name}' में '{target_u.full_name if target_u else data.target_user_id}' पर '{data.action_type}' का अनुरोध दर्ज हुआ है।"
    )

    if executed:
        return {"message": "बदलाव तुरंत स्वीकृत होकर लागू कर दिया गया है!"}
    return {
        "message": "अनुरोध दर्ज हो गया है! दूसरे एडमिन (और हटने वाले एडमिन की स्वयं की मंज़ूरी) के Approve करने पर यह लागू होगा।"
    }

@app.post("/group/governance/respond", tags=["2. Group & Admin"])
def respond_governance_request(data: GovernanceRequestAction, db: Session = Depends(get_db)):
    req = db.query(GroupGovernanceRequest).filter(GroupGovernanceRequest.id == data.request_id).first()
    if not req or req.status != "Pending":
        raise HTTPException(status_code=404, detail="यह अनुरोध पेंडिंग नहीं है या नहीं मिला")

    group = db.query(Group).filter(Group.id == req.group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="ग्रुप नहीं मिला")

    is_admin = is_user_group_admin(group, data.actor_user_id)
    is_target = (data.actor_user_id == req.target_user_id)
    if not is_admin and not is_target:
        raise HTTPException(status_code=403, detail="आपको इस अनुरोध पर निर्णय लेने का अधिकार नहीं है")

    if data.decision == "Rejected":
        req.status = "Rejected"
        db.commit()
        return {"message": "अनुरोध अस्वीकृत (Rejected) कर दिया गया है।"}

    if data.actor_user_id == group.admin_id:
        req.admin1_approved = True
    if group.co_admin_id and data.actor_user_id == group.co_admin_id:
        req.admin2_approved = True
    if data.actor_user_id == req.target_user_id:
        req.target_self_approved = True
    db.commit()

    executed = execute_governance_change_if_ready(db, group, req)
    if executed:
        return {"message": "सभी आवश्यक मंज़ूरी पूरी हुईं और बदलाव लागू कर दिया गया!"}
    return {"message": "आपकी मंज़ूरी दर्ज हो गई है। शेष एडमिन/स्वयं की मंज़ूरी की प्रतीक्षा है।"}

# --- DUAL-ADMIN LOTTERY SPIN PERMISSION ---
@app.post("/cycle/lottery-permission", tags=["4. Payout & Cycle Lifecycle"])
def request_or_approve_lottery_spin(data: LotterySpinPermission, db: Session = Depends(get_db)):
    cycle = db.query(Cycle).filter(Cycle.id == data.cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="साइकिल नहीं मिली")
    group = db.query(Group).filter(Group.id == cycle.group_id).first()
    if not group or not is_user_group_admin(group, data.admin_id):
        raise HTTPException(status_code=403, detail="केवल इस ग्रुप के एडमिन ही लॉटरी अनुमति ले सकते हैं")

    admin_ids = get_group_admin_ids(group)
    if len(admin_ids) == 1:
        cycle.lottery_requested_by_admin_id = data.admin_id
        cycle.lottery_approved_by_admin_id = data.admin_id
        db.commit()
        return {"message": "लॉटरी स्पिन की अनुमति सक्रिय हो गई है!", "ready_to_spin": True}

    if cycle.lottery_requested_by_admin_id is None:
        cycle.lottery_requested_by_admin_id = data.admin_id
        db.commit()
        other_admin_id = [aid for aid in admin_ids if aid != data.admin_id][0]
        create_user_notification(
            db,
            other_admin_id,
            f"🎰 लॉटरी निकालने की अनुमति अनुरोध: {group.group_name}",
            f"साइकिल #{cycle.cycle_number} की लॉटरी निकालने का अनुरोध भेजा गया है। कृपया इसे Approve करें ताकि Spin Wheel चल सके।",
            group_id=group.id,
            cycle_id=cycle.id,
            action_type="APPROVAL"
        )
        return {
            "message": "लॉटरी निकालने का अनुरोध जनरेट हो गया है! दूसरे एडमिन के Approve करते ही Spin Wheel खुल जाएगा।",
            "ready_to_spin": False
        }
    elif cycle.lottery_requested_by_admin_id != data.admin_id:
        cycle.lottery_approved_by_admin_id = data.admin_id
        db.commit()
        return {
            "message": "दोनों एडमिन की मंज़ूरी पूरी हुई! अब आप Spin Wheel घुमाकर लॉटरी निकाल सकते हैं।",
            "ready_to_spin": True
        }
    else:
        return {
            "message": "आप पहले ही अनुरोध भेज चुके हैं, दूसरे एडमिन की मंज़ूरी की प्रतीक्षा है।",
            "ready_to_spin": cycle.lottery_approved_by_admin_id is not None
        }

# --- 3. PAYMENTS ---
@app.post("/payment/submit", tags=["3. Payments & Cycles"])
def submit_payment(data: PaymentSubmit, db: Session = Depends(get_db)):
    group = db.query(Group).filter(Group.id == data.group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="ग्रुप नहीं मिला")

    membership = db.query(GroupMember).filter(
        GroupMember.group_id == group.id,
        GroupMember.user_id == data.payer_user_id,
        GroupMember.status == "Approved"
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="आप इस ग्रुप के स्वीकृत (Approved) सदस्य नहीं हैं")

    status = calculate_member_financial_status(db, group, data.payer_user_id)
    if status["is_user_target_completed"] or not status["cycles_to_pay_ids"]:
        raise HTTPException(
            status_code=400,
            detail=f"आपकी सभी {status['total_plan_installments']} किस्तें पहले ही जमा/सबमिट हो चुकी हैं!"
        )

    if db.query(Transaction).filter(Transaction.utr_number == data.utr_number).first():
        raise HTTPException(status_code=400, detail="यह UTR नंबर पहले ही इस्तेमाल हो चुका है!")

    target_cycle_ids = status["cycles_to_pay_ids"]
    pen_per_cycle = round(status["penalty_amount"] / len(target_cycle_ids), 2)

    for idx, cid in enumerate(target_cycle_ids):
        utr_key = data.utr_number if idx == 0 else f"{data.utr_number}-C{idx+1}"
        new_txn = Transaction(
            cycle_id=cid,
            payer_user_id=data.payer_user_id,
            amount_paid=group.amount_per_user,
            penalty_paid=pen_per_cycle,
            utr_number=utr_key,
            payment_app="UPI",
            status="Pending"
        )
        db.add(new_txn)
    db.commit()

    user = db.query(User).filter(User.id == data.payer_user_id).first()
    cycles_label = ", ".join([f"#{n}" for n in status["cycles_to_pay_numbers"]])
    notify_all_group_admins(
        db,
        group,
        "नया UPI (UTR) पेमेंट अप्रूवल हेतु",
        f"{user.full_name if user else 'सदस्य'} ने '{group.group_name}' की साइकिल {cycles_label} के लिए कुल ₹{status['total_payable_now']} (UTR: {data.utr_number}) सबमिट किया है।"
    )
    return {
        "message": f"साइकिल {cycles_label} के लिए ₹{status['total_payable_now']} (UTR: {data.utr_number}) सबमिट हो गया!"
    }

@app.post("/payment/submit-cash", tags=["3. Payments & Cycles"])
def submit_cash_payment(data: PaymentSubmit, db: Session = Depends(get_db)):
    group = db.query(Group).filter(Group.id == data.group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="ग्रुप नहीं मिला")

    membership = db.query(GroupMember).filter(
        GroupMember.group_id == group.id,
        GroupMember.user_id == data.payer_user_id,
        GroupMember.status == "Approved"
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="आप इस ग्रुप के स्वीकृत सदस्य नहीं हैं")

    status = calculate_member_financial_status(db, group, data.payer_user_id)
    if status["is_user_target_completed"] or not status["cycles_to_pay_ids"]:
        raise HTTPException(
            status_code=400,
            detail=f"आपकी सभी {status['total_plan_installments']} किस्तें पहले ही जमा/सबमिट हो चुकी हैं!"
        )

    if not data.cash_received_date or not data.cash_receiver_name:
        raise HTTPException(status_code=400, detail="कैश प्राप्त करने की तारीख और प्राप्तकर्ता का नाम अनिवार्य है")

    target_cycle_ids = status["cycles_to_pay_ids"]
    pen_per_cycle = round(status["penalty_amount"] / len(target_cycle_ids), 2)
    base_ref = f"CASH-{uuid.uuid4().hex[:7].upper()}"

    for idx, cid in enumerate(target_cycle_ids):
        ref_key = base_ref if idx == 0 else f"{base_ref}-C{idx+1}"
        new_txn = Transaction(
            cycle_id=cid,
            payer_user_id=data.payer_user_id,
            amount_paid=group.amount_per_user,
            penalty_paid=pen_per_cycle,
            utr_number=ref_key,
            payment_app="CASH",
            cash_received_date=data.cash_received_date,
            cash_receiver_name=data.cash_receiver_name,
            status="Pending"
        )
        db.add(new_txn)
    db.commit()

    user = db.query(User).filter(User.id == data.payer_user_id).first()
    cycles_label = ", ".join([f"#{n}" for n in status["cycles_to_pay_numbers"]])
    notify_all_group_admins(
        db,
        group,
        "नया Cash पेमेंट अप्रूवल हेतु",
        f"{user.full_name if user else 'सदस्य'} ने '{group.group_name}' की साइकिल {cycles_label} के लिए ₹{status['total_payable_now']} कैश एंट्री ({base_ref}) भेजी है।"
    )
    return {
        "message": f"साइकिल {cycles_label} की कैश एंट्री ({base_ref}) सबमिट हो गई! कुल राशि: ₹{status['total_payable_now']}"
    }

@app.post("/admin/approve-payment", tags=["3. Payments & Cycles"])
def approve_or_reject_payment(data: PaymentApproval, db: Session = Depends(get_db)):
    txn = db.query(Transaction).filter(Transaction.id == data.transaction_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="ट्रांजैक्शन नहीं मिला")

    cycle = db.query(Cycle).filter(Cycle.id == txn.cycle_id).first()
    group = db.query(Group).filter(Group.id == cycle.group_id).first() if cycle else None
    if not group or not is_user_group_admin(group, data.admin_id):
        raise HTTPException(status_code=403, detail="केवल इस ग्रुप का एडमिन ही पेमेंट अप्रूव कर सकता है")

    user = db.query(User).filter(User.id == txn.payer_user_id).first()
    txn.status = data.action
    db.commit()

    status_hindi = "स्वीकृत (Approved)" if data.action == "Approved" else "अस्वीकृत (Rejected)"
    mode_text = f"कैश (तारीख: {txn.cash_received_date}, प्राप्तकर्ता: {txn.cash_receiver_name})" if txn.payment_app == "CASH" else f"UPI (UTR: {txn.utr_number})"
    total_with_pen = txn.amount_paid + txn.penalty_paid

    receipt_msg = (
        f"नमस्ते {user.full_name if user else ''}, आपकी कमेटी '{group.group_name}' "
        f"(साइकिल #{cycle.cycle_number}) की ₹{txn.amount_paid} + पेनल्टी ₹{txn.penalty_paid} (कुल ₹{total_with_pen}) की {mode_text} किस्त "
        f"एडमिन द्वारा {status_hindi} कर दी गई है। रसीद/Ref नं: {txn.utr_number}"
    )
    create_user_notification(
        db,
        txn.payer_user_id,
        f"{txn.payment_app} पेमेंट {status_hindi}",
        receipt_msg,
        group_id=group.id,
        cycle_id=cycle.id,
        action_type="INFO"
    )

    return {
        "message": f"{txn.payment_app} पेमेंट {data.action} कर दिया गया!",
        "user_mobile": user.mobile if user else "",
        "user_name": user.full_name if user else "",
        "whatsapp_message": receipt_msg
    }

# --- 4. POT CLAIM / SUGGESTION / SPIN WHEEL ---
@app.post("/cycle/payout-request", tags=["4. Payout & Cycle Lifecycle"])
def submit_payout_request(data: PayoutRequestCreate, db: Session = Depends(get_db)):
    cycle = db.query(Cycle).filter(Cycle.id == data.cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="साइकिल नहीं मिली")
    if cycle.payout_completed:
        raise HTTPException(status_code=400, detail="इस साइकिल का भुगतान वितरण पहले ही पूरा हो चुका है")

    group = db.query(Group).filter(Group.id == cycle.group_id).first()
    approved_members = db.query(GroupMember).filter(
        GroupMember.group_id == group.id,
        GroupMember.status == "Approved"
    ).all()
    member_count = len(approved_members)
    cycle_txns = db.query(Transaction).filter(
        Transaction.cycle_id == cycle.id,
        Transaction.status == "Approved"
    ).all()
    cycle_paid_amount = sum(t.amount_paid for t in cycle_txns)
    cycle_target_amount = member_count * group.amount_per_user

    if member_count == 0 or cycle_paid_amount < cycle_target_amount:
        raise HTTPException(
            status_code=400,
            detail=f"अभी साइकिल का 100% अचीवमेंट (₹{cycle_target_amount}) पूरा नहीं हुआ है।"
        )

    lottery_votes = db.query(PayoutRequest).filter(
        PayoutRequest.cycle_id == data.cycle_id,
        PayoutRequest.request_type == "LOTTERY"
    ).count()
    if (lottery_votes / member_count) * 100.0 >= 80.0 and data.request_type != "LOTTERY":
        raise HTTPException(
            status_code=400,
            detail="80% सदस्यों ने लॉटरी का अनुरोध कर दिया है, इसलिए अब अन्य सुझाव विकल्प बंद हो गए हैं।"
        )

    existing = db.query(PayoutRequest).filter(
        PayoutRequest.cycle_id == data.cycle_id,
        PayoutRequest.requested_by_user_id == data.user_id
    ).first()
    if existing:
        existing.request_type = data.request_type
        existing.suggested_user_id = data.suggested_user_id
    else:
        req = PayoutRequest(
            cycle_id=data.cycle_id,
            group_id=group.id,
            requested_by_user_id=data.user_id,
            request_type=data.request_type,
            suggested_user_id=data.suggested_user_id
        )
        db.add(req)
    db.commit()

    new_lottery_votes = db.query(PayoutRequest).filter(
        PayoutRequest.cycle_id == data.cycle_id,
        PayoutRequest.request_type == "LOTTERY"
    ).count()
    is_80_locked = ((new_lottery_votes / member_count) * 100.0) >= 80.0

    user = db.query(User).filter(User.id == data.user_id).first()
    notify_all_group_admins(
        db,
        group,
        f"पोट वितरण सुझाव ({data.request_type})",
        f"{user.full_name if user else 'सदस्य'} ने '{group.group_name}' की साइकिल #{cycle.cycle_number} के लिए '{data.request_type}' का सुझाव दिया है।",
        action_type="PAYOUT_READY"
    )
    if is_80_locked:
        return {
            "message": f"80% ({new_lottery_votes}/{member_count}) सदस्यों के अनुरोध पर अन्य विकल्प बंद हो गए हैं! अब एडमिन की मंज़ूरी के बाद Spin Lottery निकलेगी।",
            "lottery_locked": True
        }
    return {
        "message": "आपका अनुरोध / सुझाव दर्ज कर लिया गया है!",
        "lottery_locked": False
    }

@app.post("/admin/finalize-payout", tags=["4. Payout & Cycle Lifecycle"])
def finalize_cycle_payout(data: FinalizePayout, db: Session = Depends(get_db)):
    cycle = db.query(Cycle).filter(Cycle.id == data.cycle_id).first()
    if not cycle:
        raise HTTPException(status_code=404, detail="साइकिल नहीं मिली")
    group = db.query(Group).filter(Group.id == cycle.group_id).first()
    if not group or not is_user_group_admin(group, data.admin_id):
        raise HTTPException(status_code=403, detail="केवल ग्रुप एडमिन ही भुगतान वितरण पूरा कर सकता है")

    if not data.winner_user_id:
        raise HTTPException(status_code=400, detail="कृपया विजेता सदस्य चुनें")

    winner_info = finalize_winner_in_db(db, group, cycle, data.winner_user_id)
    return {
        "message": f"साइकिल #{cycle.cycle_number} का भुगतान '{winner_info['winner_name']}' को आवंटित कर दिया गया है!",
        "winner_name": winner_info["winner_name"],
        "winner_user_id": winner_info["winner_user_id"],
    }

# --- 5. EMAIL REPORT (ADMIN / MEMBER DYNAMIC SENDER LOGIC) ---
def build_group_report_text(db: Session, group: Group, reason_title: str, sender_header: str = "") -> tuple:
    members = db.query(GroupMember, User).join(
        User, GroupMember.user_id == User.id
    ).filter(
        GroupMember.group_id == group.id,
        GroupMember.status == "Approved"
    ).all()

    cycles = db.query(Cycle).filter(Cycle.group_id == group.id).all()
    cycle_ids = [c.id for c in cycles]
    txns = db.query(Transaction).filter(
        Transaction.cycle_id.in_(cycle_ids),
        Transaction.status == "Approved"
    ).all() if cycle_ids else []

    total_inst = group.total_installments or calculate_total_installments(group.frequency, group.duration_months)
    total_achievement = round(sum(t.amount_paid for t in txns), 2)
    total_paid_penalty = round(sum(t.penalty_paid for t in txns), 2)
    total_target = round(len(members) * group.amount_per_user * total_inst, 2)

    admins_details = get_group_admins_details(db, group)
    winners_history = get_group_winners_history(db, group, len(members))

    member_lines = []
    penalty_lines = []
    total_due_penalty = 0.0

    for gm, u in members:
        u_txns = [t for t in txns if t.payer_user_id == u.id]
        paid_count = len(u_txns)
        paid_amt = round(sum(t.amount_paid for t in u_txns), 2)
        u_paid_pen = round(sum(t.penalty_paid for t in u_txns), 2)
        due_info = calculate_member_financial_status(db, group, u.id)
        u_due_pen = due_info["penalty_amount"]
        total_due_penalty += u_due_pen
        u_total_pen = round(u_paid_pen + u_due_pen, 2)

        member_lines.append(
            f"• {u.full_name} ({u.mobile}): जमा={paid_count}/{total_inst} (₹{paid_amt}), "
            f"शेष={due_info['remaining_installments']}, कुल पेनल्टी=₹{u_total_pen} (जमा: ₹{u_paid_pen}, बकाया: ₹{u_due_pen})"
        )
        if u_total_pen > 0:
            penalty_lines.append(
                f"  - {u.full_name} ({u.mobile}): कुल पेनल्टी ₹{u_total_pen} | कारण: {due_info['penalty_reason']}"
            )

    total_all_penalty = round(total_paid_penalty + total_due_penalty, 2)
    lines = [
        f"=== {group.group_name} ({group.group_code}) - {reason_title} ===",
    ]
    if sender_header:
        lines.append(sender_header)
    lines.extend([
        f"Authorized Receiver: {group.receiver_name} ({group.receiver_upi_id})",
        f"Frequency: {group.frequency} | अवधि: {group.duration_months} माह (कुल {total_inst} किस्तें)",
        f"कुल टारगेट (Target): ₹{total_target} | कुल अचीवमेंट (जमा): ₹{total_achievement}",
        f"कुल पेनल्टी (Total Penalty): ₹{total_all_penalty} (जमा पेनल्टी: ₹{total_paid_penalty} | बकाया Due पेनल्टी: ₹{round(total_due_penalty, 2)})",
        f"ग्रैंड टोटल (Target + Total Penalty): ₹{round(total_target + total_all_penalty, 2)}",
        "\n--- ग्रुप एडमिन विवरण (Group Admins Info) ---",
        *[f"• {a['full_name']} ({a['mobile']}) | Email: {a['email']} - [{a['role']}]" for a in admins_details],
        "\n--- सदस्य-वार भुगतान विवरण (Done & Due) ---",
        *member_lines,
    ])
    if penalty_lines:
        lines.append("\n--- पेनल्टी विवरण (Penalty Applied Members) ---")
        lines.extend(penalty_lines)
    if winners_history:
        lines.append("\n--- लॉटरी / पोट विजेता विवरण (Winners Report) ---")
        for w in winners_history:
            lines.append(
                f"• साइकिल #{w['cycle_number']} विजेता: {w['winner_name']} ({w['winner_mobile']}) | राशि: ₹{w['pot_amount']}"
            )

    return "\n".join(lines), members

def generate_and_email_group_report(db: Session, group: Group, reason_title: str, triggered_by_user_id: int = None) -> dict:
    admins_details = get_group_admins_details(db, group)
    reply_to_emails = []
    sender_header = ""

    if triggered_by_user_id is not None and not is_user_group_admin(group, triggered_by_user_id):
        member_user = db.query(User).filter(User.id == triggered_by_user_id).first()
        if member_user:
            reply_to_emails = [member_user.email]
            sender_header = f"Sent By Member: {member_user.full_name} ({member_user.email})"
    else:
        reply_to_emails = [a["email"] for a in admins_details if a.get("email")]
        admin_labels = [f"{a['full_name']} ({a['email']})" for a in admins_details]
        sender_header = f"Sent By Group Admins: {', '.join(admin_labels)}"

    report_text, members = build_group_report_text(db, group, reason_title, sender_header=sender_header)
    total_members = len(members)
    if total_members == 0:
        return {"sent": 0, "total": 0, "percentage": 100.0, "sender_emails": reply_to_emails}

    sent_count = 0
    for gm, u in members:
        if send_email_to_member(
            u.email,
            f"Committee Financial Report: {group.group_name}",
            report_text,
            reply_to_emails=reply_to_emails
        ):
            sent_count += 1

    percentage = (sent_count / total_members) * 100.0
    return {
        "sent": sent_count,
        "total": total_members,
        "percentage": percentage,
        "sender_emails": reply_to_emails
    }

@app.post("/admin/email-group-report/{group_id}/{user_id}", tags=["5. Dashboard Reports"])
def email_group_report_now(group_id: int, user_id: int, db: Session = Depends(get_db)):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="ग्रुप नहीं मिला")

    is_member = db.query(GroupMember).filter(
        GroupMember.group_id == group_id,
        GroupMember.user_id == user_id,
        GroupMember.status == "Approved"
    ).first()
    if not is_member and not is_user_group_admin(group, user_id):
        raise HTTPException(status_code=403, detail="आप इस ग्रुप के सदस्य या एडमिन नहीं हैं")

    stats = generate_and_email_group_report(
        db,
        group,
        "Complete Financial & Member Statement",
        triggered_by_user_id=user_id
    )
    sender_str = ", ".join(stats.get("sender_emails", []))
    return {
        "message": f"रिपोर्ट {stats['sent']}/{stats['total']} ({stats['percentage']:.0f}%) सदस्यों को E-mail कर दी गई है! (Sender ID: {sender_str})"
    }

@app.post("/admin/group-next-action", tags=["4. Payout & Cycle Lifecycle"])
def admin_next_cycle_or_extend(data: NextCycleOrExtend, db: Session = Depends(get_db)):
    group = db.query(Group).filter(Group.id == data.group_id).first()
    if not group or not is_user_group_admin(group, data.admin_id):
        raise HTTPException(status_code=403, detail="ग्रुप नहीं मिला या आप एडमिन नहीं हैं")

    if data.action == "START_NEXT_CYCLE":
        next_open = db.query(Cycle).filter(
            Cycle.group_id == group.id,
            Cycle.payout_completed == False
        ).order_by(Cycle.cycle_number.asc()).first()
        if not next_open:
            raise HTTPException(status_code=400, detail="इस प्लान की सभी साइकिलें पूरी हो चुकी हैं! कृपया ग्रुप अवधि बढ़ाएं (Extend)।")

        members = db.query(GroupMember).filter(GroupMember.group_id == group.id, GroupMember.status == "Approved").all()
        for m in members:
            create_user_notification(
                db,
                m.user_id,
                f"अगली किस्त सक्रिय: {group.group_name} (साइकिल #{next_open.cycle_number})",
                f"साइकिल #{next_open.cycle_number} की किस्त ₹{group.amount_per_user} जमा करें।",
                group_id=group.id,
                cycle_id=next_open.id,
                action_type="PAYMENT_DUE"
            )
        return {"message": f"साइकिल #{next_open.cycle_number} सक्रिय है और सभी सदस्यों को नोटिफिकेशन भेज दिया गया है!"}

    elif data.action == "EXTEND_GROUP":
        mail_stats = generate_and_email_group_report(db, group, "Group Extension Financial Report", triggered_by_user_id=data.admin_id)
        if mail_stats["percentage"] < 80.0:
            raise HTTPException(
                status_code=400,
                detail=f"केवल {mail_stats['percentage']:.0f}% सदस्यों को ईमेल गया। अवधि बढ़ाने के लिए कम से कम 80% सदस्यों को ईमेल जाना अनिवार्य है।"
            )
        group.duration_months += data.extra_months
        group.total_installments = calculate_total_installments(group.frequency, group.duration_months)
        group.end_date += timedelta(days=30 * data.extra_months)
        db.commit()
        ensure_all_plan_cycles_created(db, group)
        return {
            "message": f"{mail_stats['sent']}/{mail_stats['total']} सदस्यों को रिपोर्ट ईमेल होने के बाद ग्रुप अवधि {data.extra_months} महीने बढ़ा दी गई है!"
        }

@app.delete("/admin/delete-group/{group_id}/{admin_id}", tags=["4. Payout & Cycle Lifecycle"])
def delete_group_with_email_audit(group_id: int, admin_id: int, db: Session = Depends(get_db)):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group or not is_user_group_admin(group, admin_id):
        raise HTTPException(status_code=403, detail="ग्रुप नहीं मिला या आप एडमिन नहीं हैं")

    mail_stats = generate_and_email_group_report(db, group, "Final Group Closure & Financial Audit Report", triggered_by_user_id=admin_id)
    if mail_stats["percentage"] < 80.0:
        raise HTTPException(
            status_code=400,
            detail=f"ग्रुप डिलीट रोका गया: केवल {mail_stats['sent']}/{mail_stats['total']} ({mail_stats['percentage']:.0f}%) सदस्यों को ईमेल जा सका। डिलीट करने के लिए कम से कम 80% सदस्यों को ईमेल जाना अनिवार्य है।"
        )

    db.query(GroupMember).filter(GroupMember.group_id == group_id).delete()
    db.delete(group)
    db.commit()
    return {
        "message": f"{mail_stats['sent']}/{mail_stats['total']} सदस्यों को संपूर्ण वित्तीय रिपोर्ट ईमेल होने के बाद ग्रुप सफलतापूर्वक डिलीट कर दिया गया है।"
    }

# --- 6. USER-FILTERED DASHBOARD & COMPLETE REPORT (WITH ADMIN & WINNER INFO) ---
@app.get("/user/{user_id}/groups", tags=["5. Dashboard Reports"])
def get_user_related_groups(user_id: int, db: Session = Depends(get_db)):
    auto_approve_stale_transactions(db)
    approved_group_ids = [
        gm.group_id for gm in db.query(GroupMember).filter(
            GroupMember.user_id == user_id,
            GroupMember.status == "Approved"
        ).all()
    ]
    groups = db.query(Group).filter(
        (Group.admin_id == user_id) | (Group.co_admin_id == user_id) | (Group.id.in_(approved_group_ids))
    ).all()

    result = []
    for g in groups:
        trigger_cycle_automation(db, g)
        approved_count = db.query(GroupMember).filter(
            GroupMember.group_id == g.id, GroupMember.status == "Approved"
        ).count()
        pending_count = db.query(GroupMember).filter(
            GroupMember.group_id == g.id, GroupMember.status == "Pending"
        ).count()

        total_inst = g.total_installments or calculate_total_installments(g.frequency, g.duration_months)
        status = calculate_member_financial_status(db, g, user_id)
        admins_details = get_group_admins_details(db, g)

        result.append({
            "group_id": g.id,
            "group_code": g.group_code,
            "group_name": g.group_name,
            "receiver_name": g.receiver_name,
            "receiver_upi_id": g.receiver_upi_id,
            "first_installment_date": g.first_installment_date,
            "admin_id": g.admin_id,
            "co_admin_id": g.co_admin_id,
            "admins_info": admins_details,
            "is_admin": is_user_group_admin(g, user_id),
            "frequency": g.frequency,
            "duration_months": g.duration_months,
            "total_installments": total_inst,
            "amount_per_user": g.amount_per_user,
            "approved_members": approved_count,
            "pending_requests": pending_count,
            "active_cycle_id": status["next_cycle_id"],
            "active_cycle_number": status["next_cycle_number"],
            "active_cycle_due_date": status["next_cycle_due_date"],
            "user_approved_installments": status["approved_installments"],
            "user_submitted_installments": status["submitted_installments"],
            "is_user_target_completed": status["is_user_target_completed"],
            "user_overdue_installments": status["overdue_installments"],
            "user_installments_to_pay_now": status["installments_count_to_pay_now"],
            "user_principal_payable_now": status["principal_payable_now"],
            "user_due_penalty_now": status["penalty_amount"],
            "user_total_payable_now": status["total_payable_now"],
            "user_penalty_reason": status["penalty_reason"],
            "user_cycles_to_pay_numbers": status["cycles_to_pay_numbers"],
        })
    return result

@app.get("/user/{user_id}/my-requests", tags=["5. Dashboard Reports"])
def get_user_join_requests_status(user_id: int, db: Session = Depends(get_db)):
    records = db.query(GroupMember, Group).join(
        Group, GroupMember.group_id == Group.id
    ).filter(GroupMember.user_id == user_id).all()

    return [
        {
            "group_id": g.id,
            "group_name": g.group_name,
            "group_code": g.group_code,
            "status": gm.status,
            "is_admin": is_user_group_admin(g, user_id),
        }
        for gm, g in records
    ]

@app.get("/admin/{admin_id}/pending-requests", tags=["5. Dashboard Reports"])
def get_admin_pending_requests(admin_id: int, group_code: str = None, db: Session = Depends(get_db)):
    query = db.query(GroupMember, User, Group).join(
        User, GroupMember.user_id == User.id
    ).join(
        Group, GroupMember.group_id == Group.id
    ).filter(
        ((Group.admin_id == admin_id) | (Group.co_admin_id == admin_id)),
        GroupMember.status == "Pending"
    )
    if group_code and group_code.strip():
        query = query.filter(Group.group_code == group_code.strip())

    records = query.all()
    return [
        {
            "member_record_id": gm.id,
            "group_id": g.id,
            "group_code": g.group_code,
            "group_name": g.group_name,
            "user_id": u.id,
            "full_name": u.full_name,
            "mobile": u.mobile,
            "status": gm.status,
        }
        for gm, u, g in records
    ]

@app.get("/admin/{admin_id}/pending-payments", tags=["5. Dashboard Reports"])
def get_admin_pending_payments(admin_id: int, db: Session = Depends(get_db)):
    auto_approve_stale_transactions(db)
    records = db.query(Transaction, User, Cycle, Group).join(
        User, Transaction.payer_user_id == User.id
    ).join(
        Cycle, Transaction.cycle_id == Cycle.id
    ).join(
        Group, Cycle.group_id == Group.id
    ).filter(
        ((Group.admin_id == admin_id) | (Group.co_admin_id == admin_id)),
        Transaction.status == "Pending"
    ).all()

    return [
        {
            "transaction_id": t.id,
            "payment_mode": t.payment_app,
            "utr_or_ref": t.utr_number,
            "group_name": g.group_name,
            "group_code": g.group_code,
            "cycle_number": c.cycle_number,
            "user_id": u.id,
            "full_name": u.full_name,
            "mobile": u.mobile,
            "amount_paid": t.amount_paid,
            "penalty_paid": t.penalty_paid,
            "cash_received_date": t.cash_received_date,
            "cash_receiver_name": t.cash_receiver_name,
        }
        for t, u, c, g in records
    ]

@app.get("/group/{group_id}/full-report", tags=["5. Dashboard Reports"])
def get_group_full_report(group_id: int, user_id: int, db: Session = Depends(get_db)):
    auto_approve_stale_transactions(db)
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="ग्रुप नहीं मिला")

    is_approved = db.query(GroupMember).filter(
        GroupMember.group_id == group_id,
        GroupMember.user_id == user_id,
        GroupMember.status == "Approved"
    ).first()
    if not is_approved and not is_user_group_admin(group, user_id):
        raise HTTPException(status_code=403, detail="इस ग्रुप की रिपोर्ट देखने के लिए पहले एडमिन अप्रूवल आवश्यक है")

    ensure_all_plan_cycles_created(db, group)
    approved_members = db.query(GroupMember, User).join(
        User, GroupMember.user_id == User.id
    ).filter(
        GroupMember.group_id == group_id,
        GroupMember.status == "Approved"
    ).all()

    cycles = db.query(Cycle).filter(Cycle.group_id == group_id).order_by(Cycle.cycle_number.asc()).all()
    cycle_ids = [c.id for c in cycles]
    current_payout_cycle = next((c for c in cycles if not c.payout_completed), cycles[-1] if cycles else None)

    transactions = db.query(Transaction).filter(
        Transaction.cycle_id.in_(cycle_ids),
        Transaction.status == "Approved"
    ).all() if cycle_ids else []

    total_inst = group.total_installments or calculate_total_installments(group.frequency, group.duration_months)
    member_count = len(approved_members)
    total_target_amount = round(member_count * group.amount_per_user * total_inst, 2)
    total_achievement = round(sum(t.amount_paid for t in transactions), 2)
    total_paid_penalty = round(sum(t.penalty_paid for t in transactions), 2)

    cycle_target = member_count * group.amount_per_user
    current_cycle_paid = sum(
        t.amount_paid for t in transactions if current_payout_cycle and t.cycle_id == current_payout_cycle.id
    )
    is_payout_eligible = False
    if current_payout_cycle and not current_payout_cycle.payout_completed and member_count > 0:
        if current_cycle_paid >= cycle_target and cycle_target > 0:
            is_payout_eligible = True

    payout_requests = []
    lottery_vote_count = 0
    if current_payout_cycle:
        reqs = db.query(PayoutRequest, User).join(
            User, PayoutRequest.requested_by_user_id == User.id
        ).filter(PayoutRequest.cycle_id == current_payout_cycle.id).all()
        for r, u in reqs:
            if r.request_type == "LOTTERY":
                lottery_vote_count += 1
            sug_user = db.query(User).filter(User.id == r.suggested_user_id).first() if r.suggested_user_id else None
            payout_requests.append({
                "by_user": u.full_name,
                "request_type": r.request_type,
                "suggested_member": sug_user.full_name if sug_user else None,
            })

    is_lottery_80_locked = (member_count > 0) and (((lottery_vote_count / member_count) * 100.0) >= 80.0)

    admin_ids = get_group_admin_ids(group)
    is_lottery_spin_approved_by_admins = False
    if current_payout_cycle:
        if len(admin_ids) == 1:
            is_lottery_spin_approved_by_admins = current_payout_cycle.lottery_approved_by_admin_id is not None
        else:
            is_lottery_spin_approved_by_admins = (
                current_payout_cycle.lottery_requested_by_admin_id is not None
                and current_payout_cycle.lottery_approved_by_admin_id is not None
            )

    winner_name = None
    if current_payout_cycle and current_payout_cycle.recipient_user_id:
        w_user = db.query(User).filter(User.id == current_payout_cycle.recipient_user_id).first()
        winner_name = w_user.full_name if w_user else f"User #{current_payout_cycle.recipient_user_id}"

    winners_history = get_group_winners_history(db, group, member_count)

    gov_reqs_db = db.query(GroupGovernanceRequest).filter(
        GroupGovernanceRequest.group_id == group_id
    ).order_by(GroupGovernanceRequest.id.desc()).all()

    gov_requests_list = []
    for gr in gov_reqs_db:
        t_user = db.query(User).filter(User.id == gr.target_user_id).first()
        by_user = db.query(User).filter(User.id == gr.requested_by_user_id).first()
        new_adm_user = db.query(User).filter(User.id == gr.new_admin_user_id).first() if gr.new_admin_user_id else None

        can_user_respond = False
        if gr.status == "Pending":
            if user_id == group.admin_id and not gr.admin1_approved:
                can_user_respond = True
            if group.co_admin_id and user_id == group.co_admin_id and not gr.admin2_approved:
                can_user_respond = True
            if gr.action_type in ["REMOVE_ADMIN", "CHANGE_ADMIN"] and user_id == gr.target_user_id and not gr.target_self_approved:
                can_user_respond = True

        gov_requests_list.append({
            "request_id": gr.id,
            "action_type": gr.action_type,
            "target_user_id": gr.target_user_id,
            "target_user_name": t_user.full_name if t_user else f"User #{gr.target_user_id}",
            "new_admin_name": new_adm_user.full_name if new_adm_user else None,
            "requested_by_name": by_user.full_name if by_user else f"User #{gr.requested_by_user_id}",
            "admin1_approved": gr.admin1_approved,
            "admin2_approved": gr.admin2_approved,
            "target_self_approved": gr.target_self_approved,
            "status": gr.status,
            "can_user_respond": can_user_respond,
            "created_at": gr.created_at.strftime("%Y-%m-%d %H:%M"),
        })

    member_list = []
    penalty_members_list = []
    admins_list = []
    total_due_penalty = 0.0

    for gm, u in approved_members:
        user_txns = [t for t in transactions if t.payer_user_id == u.id]
        paid_installments = len(user_txns)
        paid_amount = round(sum(t.amount_paid for t in user_txns), 2)
        user_paid_pen = round(sum(t.penalty_paid for t in user_txns), 2)
        status = calculate_member_financial_status(db, group, u.id)
        user_due_pen = status["penalty_amount"]
        total_due_penalty += user_due_pen
        user_total_pen = round(user_paid_pen + user_due_pen, 2)
        is_m_admin = is_user_group_admin(group, u.id)

        m_data = {
            "member_record_id": gm.id,
            "user_id": u.id,
            "full_name": u.full_name,
            "mobile": u.mobile,
            "email": u.email,
            "upi_id": u.upi_id,
            "is_group_admin": is_m_admin,
            "admin_role": "Primary Admin" if group.admin_id == u.id else ("Co-Admin" if group.co_admin_id == u.id else "Member"),
            "has_received_pot": gm.has_received_pot,
            "paid_installments": paid_installments,
            "total_plan_installments": total_inst,
            "remaining_installments": status["remaining_installments"],
            "overdue_installments": status["overdue_installments"],
            "total_paid_amount": paid_amount,
            "paid_penalty": user_paid_pen,
            "due_penalty": user_due_pen,
            "total_member_penalty": user_total_pen,
            "penalty_reason": status["penalty_reason"],
        }
        member_list.append(m_data)
        if is_m_admin:
            admins_list.append(m_data)
        if user_total_pen > 0:
            penalty_members_list.append(m_data)

    total_all_penalty = round(total_paid_penalty + total_due_penalty, 2)

    return {
        "group_id": group.id,
        "group_code": group.group_code,
        "group_name": group.group_name,
        "frequency": group.frequency,
        "duration_months": group.duration_months,
        "total_plan_installments": total_inst,
        "receiver_name": group.receiver_name,
        "receiver_upi_id": group.receiver_upi_id,
        "is_admin": is_user_group_admin(group, user_id),
        "admin_count": len(admin_ids),
        "admins": admins_list,
        "winners_history": winners_history,
        "governance_requests": gov_requests_list,
        "amount_per_user": group.amount_per_user,
        "total_members": member_count,
        "latest_cycle_id": current_payout_cycle.id if current_payout_cycle else None,
        "latest_cycle_number": current_payout_cycle.cycle_number if current_payout_cycle else None,
        "latest_cycle_payout_completed": current_payout_cycle.payout_completed if current_payout_cycle else False,
        "latest_cycle_winner_name": winner_name,
        "is_payout_eligible": is_payout_eligible,
        "lottery_vote_count": lottery_vote_count,
        "is_lottery_80_locked": is_lottery_80_locked,
        "lottery_requested_by_admin_id": current_payout_cycle.lottery_requested_by_admin_id if current_payout_cycle else None,
        "lottery_approved_by_admin_id": current_payout_cycle.lottery_approved_by_admin_id if current_payout_cycle else None,
        "is_lottery_spin_approved_by_admins": is_lottery_spin_approved_by_admins,
        "payout_requests": payout_requests,
        "total_target_amount": total_target_amount,
        "total_achievement": total_achievement,
        "remaining_target": max(0.0, round(total_target_amount - total_achievement, 2)),
        "total_paid_penalty": total_paid_penalty,
        "total_due_penalty": round(total_due_penalty, 2),
        "total_all_penalty": total_all_penalty,
        "grand_total_with_penalty": round(total_target_amount + total_all_penalty, 2),
        "members": member_list,
        "penalty_members": penalty_members_list,
    }

@app.get("/report/group-pdf/{group_id}", tags=["5. Dashboard Reports"])
def generate_complete_group_pdf(group_id: int, db: Session = Depends(get_db)):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="ग्रुप नहीं मिला")

    members = db.query(GroupMember, User).join(
        User, GroupMember.user_id == User.id
    ).filter(
        GroupMember.group_id == group_id,
        GroupMember.status == "Approved"
    ).all()

    cycles = db.query(Cycle).filter(Cycle.group_id == group_id).all()
    cycle_ids = [c.id for c in cycles]
    txns = db.query(Transaction).filter(
        Transaction.cycle_id.in_(cycle_ids),
        Transaction.status == "Approved"
    ).all() if cycle_ids else []

    total_inst = group.total_installments or calculate_total_installments(group.frequency, group.duration_months)
    total_achievement = round(sum(t.amount_paid for t in txns), 2)
    total_paid_pen = round(sum(t.penalty_paid for t in txns), 2)
    total_target = round(len(members) * group.amount_per_user * total_inst, 2)

    admins_details = get_group_admins_details(db, group)
    admins_summary_str = ", ".join([f"{a['full_name']} ({a['mobile']} - {a['role']})" for a in admins_details])
    winners_history = get_group_winners_history(db, group, len(members))

    member_table_data = [
        ["Member Name", "Mobile", "Paid/Tot", "Rem", "Paid Amt", "Paid Pen", "Due Pen"]
    ]
    penalty_table_data = [
        ["Member Name", "Mobile", "Paid Pen", "Due Pen", "Total Pen", "Reason / Status"]
    ]
    winners_table_data = [
        ["Cycle #", "Winner Name", "Mobile", "Winner UPI ID", "Pot Amount"]
    ]
    total_due_pen = 0.0

    for gm, u in members:
        u_txns = [t for t in txns if t.payer_user_id == u.id]
        paid_cnt = len(u_txns)
        paid_amt = round(sum(t.amount_paid for t in u_txns), 2)
        u_paid_pen = round(sum(t.penalty_paid for t in u_txns), 2)
        status = calculate_member_financial_status(db, group, u.id)
        u_due_pen = status["penalty_amount"]
        total_due_pen += u_due_pen
        u_tot_pen = round(u_paid_pen + u_due_pen, 2)

        member_table_data.append([
            u.full_name[:18],
            u.mobile,
            f"{paid_cnt}/{total_inst}",
            str(status["remaining_installments"]),
            f"Rs.{paid_amt}",
            f"Rs.{u_paid_pen}",
            f"Rs.{u_due_pen}",
        ])

        if u_tot_pen > 0:
            penalty_table_data.append([
                u.full_name[:18],
                u.mobile,
                f"Rs.{u_paid_pen}",
                f"Rs.{u_due_pen}",
                f"Rs.{u_tot_pen}",
                status["penalty_reason_en"],
            ])

    for w in winners_history:
        winners_table_data.append([
            f"Cycle #{w['cycle_number']}",
            w["winner_name"][:20],
            w["winner_mobile"],
            w["winner_upi_id"],
            f"Rs.{w['pot_amount']}",
        ])

    total_all_pen = round(total_paid_pen + total_due_pen, 2)
    grand_total = round(total_target + total_all_pen, 2)

    file_path = f"Group_{group.group_code}_Complete_Report.pdf"
    c = canvas.Canvas(file_path, pagesize=A4)

    c.setFillColor(colors.HexColor("#F0F7F7"))
    c.setStrokeColor(colors.HexColor("#008080"))
    c.roundRect(35, 665, 525, 152, 8, fill=1, stroke=1)

    c.setFillColor(colors.HexColor("#004D4D"))
    c.setFont("Helvetica-Bold", 15)
    c.drawString(48, 798, f"Committee Complete Financial Report: {group.group_name}")

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    c.drawString(48, 780, f"Group Code: {group.group_code} | Frequency: {group.frequency} | Duration: {group.duration_months} Months ({total_inst} Installments)")
    c.drawString(48, 764, f"Authorized Receiver: {group.receiver_name} ({group.receiver_upi_id}) | Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.HexColor("#0D47A1"))
    c.drawString(48, 747, f"Group Admins: {admins_summary_str[:88]}")

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    c.drawString(48, 730, f"Installment: Rs.{group.amount_per_user} | Target Amount: Rs.{total_target} | Achievement (Paid): Rs.{total_achievement}")

    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.HexColor("#990000"))
    c.drawString(48, 712, f"Total Penalty: Rs.{total_all_pen} (Paid Penalty: Rs.{total_paid_pen} | Due Penalty: Rs.{round(total_due_pen, 2)})")
    c.setFillColor(colors.HexColor("#1A237E"))
    c.drawString(48, 694, f"Grand Total (Target + Total Penalty): Rs.{grand_total}")

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(38, 643, "1. Member-Wise Installment & Penalty Statement:")

    col_widths = [115, 80, 55, 45, 78, 76, 76]
    main_table = Table(member_table_data, colWidths=col_widths)
    main_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#006666")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (1, -1), "LEFT"),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.75, colors.HexColor("#B0BEC5")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#FFFFFF"), colors.HexColor("#F4F8F9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ])
    main_table.setStyle(main_style)

    w1, h1 = main_table.wrapOn(c, 525, 400)
    y_after_main = 635 - h1
    main_table.drawOn(c, 35, y_after_main)

    y_win_title = y_after_main - 24
    c.setFillColor(colors.HexColor("#1B5E20"))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(38, y_win_title, "2. Lottery / Cycle Winners Report:")

    if len(winners_table_data) == 1:
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 9.5)
        c.drawString(38, y_win_title - 18, "No lottery winner declared yet (100% cycle achievement pending).")
        y_after_win = y_win_title - 25
    else:
        win_col_widths = [75, 135, 90, 130, 95]
        win_table = Table(winners_table_data, colWidths=win_col_widths)
        win_style = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E7D32")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (1, 1), (1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.75, colors.HexColor("#A5D6A7")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F9FFF9"), colors.HexColor("#E8F5E9")]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])
        win_table.setStyle(win_style)
        w_win, h_win = win_table.wrapOn(c, 525, 250)
        y_after_win = y_win_title - 8 - h_win
        win_table.drawOn(c, 35, y_after_win)

    y_pen_title = y_after_win - 24
    if y_pen_title < 90:
        c.showPage()
        y_pen_title = 790

    c.setFillColor(colors.HexColor("#8B0000"))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(38, y_pen_title, "3. Member-Wise Penalty Breakdown:")

    if len(penalty_table_data) == 1:
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 9.5)
        c.drawString(38, y_pen_title - 18, "No penalty applied to any member in this group.")
    else:
        pen_col_widths = [105, 75, 68, 68, 72, 137]
        pen_table = Table(penalty_table_data, colWidths=pen_col_widths)
        pen_style = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#8B0000")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (0, 0), (1, -1), "LEFT"),
            ("ALIGN", (2, 0), (4, -1), "CENTER"),
            ("ALIGN", (5, 0), (5, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.75, colors.HexColor("#E0B0B0")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#FFF9F9"), colors.HexColor("#FDF0F0")]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])
        pen_table.setStyle(pen_style)
        w2, h2 = pen_table.wrapOn(c, 525, 300)
        pen_table.drawOn(c, 35, y_pen_title - 8 - h2)

    c.save()
    return FileResponse(file_path, media_type="application/pdf", filename=file_path)