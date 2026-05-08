from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel   
from typing import Optional, List 
from datetime import datetime, timedelta
import database
import secrets
from fastapi.responses import Response

app = FastAPI(title="Lakshmi Narasimha Hallmarking API")

@app.get("/")
def health_check():
    return {"status": "awake and ready"}

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

def get_db():
    db = database.SessionLocal()
    try: yield db
    finally: db.close()

# --- STARTUP EVENT (Creates the Master Admin) ---
@app.on_event("startup")
def startup_event():
    db = database.SessionLocal()
    admin_exists = db.query(database.AppUser).filter(database.AppUser.role == "admin").first()
    if not admin_exists:
        default_admin = database.AppUser(username="admin", password="admin123", role="admin")
        db.add(default_admin)
        db.commit()
    db.close()

# --- SECURITY & ROLES ---
class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(database.AppUser).filter(database.AppUser.username == req.username, database.AppUser.password == req.password).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    # Issue a secure login token for this session
    token = secrets.token_hex(16)
    user.token = token
    db.commit()
    return {"access_token": token, "role": user.role, "username": user.username}

def get_current_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Please log in.")
    token = authorization.split(" ")[1]
    user = db.query(database.AppUser).filter(database.AppUser.token == token).first()
    if not user: raise HTTPException(status_code=401, detail="Session expired.")
    return user

def require_admin(current_user: database.AppUser = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only Admins can perform this action.")
    return current_user

# --- USER MANAGEMENT (Admin Only) ---
class UserCreate(BaseModel):
    username: str
    password: str
    role: str

@app.post("/users/")
def create_user(user: UserCreate, db: Session = Depends(get_db), admin: database.AppUser = Depends(require_admin)):
    existing = db.query(database.AppUser).filter(database.AppUser.username == user.username).first()
    if existing: raise HTTPException(status_code=400, detail="Username already exists")
    new_user = database.AppUser(username=user.username, password=user.password, role=user.role)
    db.add(new_user)
    db.commit()
    return {"message": "User created"}

@app.get("/users/")
def get_users(db: Session = Depends(get_db), admin: database.AppUser = Depends(require_admin)):
    users = db.query(database.AppUser).all()
    return [{"id": u.id, "username": u.username, "role": u.role} for u in users]

@app.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), admin: database.AppUser = Depends(require_admin)):
    user = db.query(database.AppUser).filter(database.AppUser.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="User not found")
    if user.username == "admin": raise HTTPException(status_code=400, detail="Cannot delete Master Admin")
    db.delete(user)
    db.commit()
    return {"message": "User deleted"}

# --- PYDANTIC SCHEMAS ---
class ItemInput(BaseModel):
    description: str; quantity: int; purity: str; weight: float

class JobCardInput(BaseModel):
    customer_id: int; request_number: str; items: List[ItemInput]; custom_date: Optional[str] = None

class ItemGrading(BaseModel):
    item_id: int; hm: int; rej: int; melt: int; rtn: int

class BillPayload(BaseModel):
    customer_id: int; request_number: str; results: List[ItemGrading]; custom_date: Optional[str] = None

class ItemUpdate(BaseModel):
    description: str; quantity: int; purity: str; weight: float

class NewItemCreate(BaseModel):
    description: str; quantity: int; purity: str; weight: float

# --- CUSTOMER ENDPOINTS ---
@app.post("/customers/")
def create_customer(business_name: str, phone: str, address: str, license_number: str, gstin: Optional[str] = None, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    # THE FIX: If gstin is an empty string, turn it into Python's 'None' (which becomes SQL NULL)
    final_gstin = gstin if gstin and gstin.strip() != "" else None
    
    new_customer = database.Customer(business_name=business_name, phone=phone, address=address, license_number=license_number, gstin=final_gstin)
    db.add(new_customer)
    db.commit() 
    db.refresh(new_customer)
    return new_customer

@app.get("/customers/")
def get_all_customers(db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    return db.query(database.Customer).order_by(database.Customer.business_name.asc()).all()

@app.put("/customers/{customer_id}")
def update_customer(customer_id: int, data: dict, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    customer = db.query(database.Customer).filter(database.Customer.id == customer_id).first()
    if not customer: raise HTTPException(status_code=404, detail="Jeweler not found")
    
    customer.business_name = data.get("business_name")
    customer.phone = data.get("phone")
    customer.address = data.get("address")
    customer.license_number = data.get("license_number")
    
    # THE FIX: If gstin is an empty string, turn it into Python's 'None'
    incoming_gstin = data.get("gstin")
    customer.gstin = incoming_gstin if incoming_gstin and incoming_gstin.strip() != "" else None
    
    db.commit()
    return {"message": "Jeweler updated successfully"}

# Notice: require_admin replaces get_current_user here!
@app.delete("/customers/{customer_id}")
def delete_customer(customer_id: int, db: Session = Depends(get_db), admin: database.AppUser = Depends(require_admin)):
    customer = db.query(database.Customer).filter(database.Customer.id == customer_id).first()
    if not customer: raise HTTPException(status_code=404, detail="Jeweler not found.")
    existing_jobs = db.query(database.JobCard).filter(database.JobCard.customer_id == customer_id).first()
    if existing_jobs: raise HTTPException(status_code=400, detail="Cannot delete this Jeweler because they have existing Job Cards or Bills in the system.")
    db.delete(customer)
    db.commit()
    return {"message": "Jeweler deleted successfully"}

# --- JOB CARD ENDPOINTS ---

@app.get("/check_request_no/{request_no}")
def check_request_no(request_no: str, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    # Instantly checks if this Request No has ever been used
    exists = db.query(database.JobCard).filter(database.JobCard.request_number == request_no).first()
    return {"exists": bool(exists)}

@app.post("/jobcards/")
def create_job_card(data: JobCardInput, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    # THE WALL: Block duplicate Request Numbers from being saved
    existing_req = db.query(database.JobCard).filter(database.JobCard.request_number == data.request_number).first()
    if existing_req:
        raise HTTPException(status_code=400, detail=f"Request Number '{data.request_number}' has already been used! Please type the correct one.")

    final_date = datetime.utcnow()
    if data.custom_date:
        try: final_date = datetime.strptime(data.custom_date, "%Y-%m-%dT%H:%M")
        except ValueError: pass

    start_of_day = final_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = final_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    daily_count = db.query(database.JobCard).filter(database.JobCard.date_received >= start_of_day, database.JobCard.date_received <= end_of_day).count()
    seq = daily_count + 1
    receipt_no_str = f"C-{final_date.strftime('%d%m%Y')}-{seq}"

    new_job = database.JobCard(customer_id=data.customer_id, request_number=data.request_number, date_received=final_date, receipt_no=receipt_no_str)
    db.add(new_job)
    db.commit(); db.refresh(new_job)
    
    for item in data.items:
        new_item = database.JobItem(job_card_id=new_job.id, item_description=item.description, quantity=item.quantity, declared_purity=item.purity, weight_grams=item.weight)
        db.add(new_item)
    db.commit()
    return {"job_card_id": new_job.id, "receipt_no": new_job.receipt_no, "request_number": new_job.request_number, "message": "Saved."}
@app.get("/pending_requests/{customer_id}")
def get_pending_requests(customer_id: int, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    jobs = db.query(database.JobCard.request_number).filter(database.JobCard.customer_id == customer_id, database.JobCard.status == "Pending").distinct().all()
    return [j[0] for j in jobs if j[0]]

@app.get("/pending_items/{request_no}")
def get_pending_items(request_no: str, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    pending_jobs = db.query(database.JobCard).filter(database.JobCard.request_number == request_no, database.JobCard.status == "Pending").all()
    items = []
    for job in pending_jobs:
        job_items = db.query(database.JobItem).filter(database.JobItem.job_card_id == job.id).all()
        for i in job_items:
            items.append({"item_id": i.id, "request_number": job.request_number, "description": i.item_description, "quantity": i.quantity, "purity": i.declared_purity, "weight": i.weight_grams})
    return items

@app.put("/update_item/{item_id}")
def update_item(item_id: int, item: ItemUpdate, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    db_item = db.query(database.JobItem).filter(database.JobItem.id == item_id).first()
    if not db_item: raise HTTPException(status_code=404, detail="Item not found")
    db_item.item_description = item.description; db_item.quantity = item.quantity; db_item.declared_purity = item.purity; db_item.weight_grams = item.weight
    db.commit()
    return {"message": "Item updated successfully"}

@app.post("/add_item_to_request/{request_number}")
def add_item_to_request(request_number: str, item: NewItemCreate, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    job = db.query(database.JobCard).filter(database.JobCard.request_number == request_number).first()
    if not job: raise HTTPException(status_code=404, detail="Job not found")
    new_item = database.JobItem(job_card_id=job.id, item_description=item.description, quantity=item.quantity, weight_grams=item.weight, declared_purity=item.purity)
    db.add(new_item)
    db.commit()
    return {"message": "Item added successfully"}

# Notice: require_admin replaces get_current_user here!
@app.delete("/delete_request/{request_no}")
def delete_request(request_no: str, db: Session = Depends(get_db), admin: database.AppUser = Depends(require_admin)):
    pending_jobs = db.query(database.JobCard).filter(database.JobCard.request_number == request_no, database.JobCard.status == "Pending").all()
    if not pending_jobs: raise HTTPException(status_code=404, detail="Pending request not found")
    
    for job in pending_jobs:
        db.query(database.JobItem).filter(database.JobItem.job_card_id == job.id).delete()
        db.delete(job)
    db.commit()
    db.execute(text("SELECT setval(pg_get_serial_sequence('job_cards', 'id'), coalesce(max(id), 1), max(id) IS NOT null) FROM job_cards;"))
    db.commit()
    return {"message": "Request deleted and sequence reset"}

@app.get("/print_receipt_data/{identifier}")
def get_receipt_data(identifier: str, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    # Find the job by either the Request Number or the Receipt Number
    job = db.query(database.JobCard).filter(
        (database.JobCard.request_number == identifier) | 
        (database.JobCard.receipt_no == identifier)
    ).first()
    
    if not job: raise HTTPException(status_code=404, detail="Receipt not found")
    
    customer = db.query(database.Customer).filter(database.Customer.id == job.customer_id).first()
    
    # Grab all items submitted under this receipt
    if job.receipt_no:
        jobs = db.query(database.JobCard).filter(database.JobCard.receipt_no == job.receipt_no).all()
    else:
        jobs = db.query(database.JobCard).filter(database.JobCard.request_number == job.request_number).all()
        
    items = []
    for j in jobs:
        j_items = db.query(database.JobItem).filter(database.JobItem.job_card_id == j.id).order_by(database.JobItem.id.asc()).all()
        for i in j_items:
            items.append({
                "description": i.item_description,
                "quantity": i.quantity,
                "purity": i.declared_purity,
                "weight": i.weight_grams
            })
    
    def to_ist(utc_dt):
        if not utc_dt: return datetime.now()
        return utc_dt + timedelta(hours=5, minutes=30)
        
    return {
        "receipt_no": job.receipt_no or f"HMD{job.id}",
        "request_no": job.request_number,
        "customer_name": customer.business_name if customer else "Unknown",
        "customer_address": customer.address if customer else "",
        "time_string": to_ist(job.date_received).strftime("%d-%m-%Y %I:%M %p"),
        "items": items
    }

# --- INVOICE ENDPOINTS ---
@app.post("/generate_invoice/")
def generate_invoice(payload: BillPayload, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    pending_jobs = db.query(database.JobCard).filter(database.JobCard.customer_id == payload.customer_id, database.JobCard.request_number == payload.request_number, database.JobCard.status == "Pending").all()
    if not pending_jobs: raise HTTPException(status_code=404, detail="No pending jobs for this Request Number.")

    final_date = datetime.utcnow()
    if payload.custom_date:
        try: final_date = datetime.strptime(payload.custom_date, "%Y-%m-%dT%H:%M")
        except ValueError: pass
    
    start_of_day = final_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = final_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    daily_count = db.query(database.Invoice).filter(database.Invoice.created_at >= start_of_day, database.Invoice.created_at <= end_of_day).count()
    seq = daily_count + 1
    bill_no_str = f"INV-{final_date.strftime('%d%m%Y')}-{seq}"

    total_pieces = 0
    for res in payload.results:
        db_item = db.query(database.JobItem).filter(database.JobItem.id == res.item_id).first()
        if db_item:
            db_item.hm = res.hm; db_item.rej = res.rej; db_item.melt = res.melt; db_item.rtn = res.rtn
            total_pieces += db_item.quantity
            
    calculated_amount = total_pieces * 45.0
    final_amount = max(calculated_amount, 200.0)
    
    new_invoice = database.Invoice(customer_id=payload.customer_id, service_description=f"Assaying & Hallmarking ({total_pieces} items)", taxable_amount=final_amount, total_amount=final_amount, created_at=final_date, bill_no=bill_no_str)
    db.add(new_invoice)
    db.commit(); db.refresh(new_invoice)
    
    for job in pending_jobs:
        job.invoice_id = new_invoice.id; job.status = "Billed"
    db.commit() 
    return {"invoice_number": new_invoice.id, "bill_no": new_invoice.bill_no, "total_bill": final_amount}

@app.get("/find_invoice_by_request/{request_no}")
def find_invoice_by_request(request_no: str, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    job = db.query(database.JobCard).filter(database.JobCard.request_number == request_no).first()
    if not job or not job.invoice_id: raise HTTPException(status_code=404, detail="No billed invoice found for this Request No.")
    invoice = db.query(database.Invoice).filter(database.Invoice.id == job.invoice_id).first()
    bill_display = invoice.bill_no if invoice.bill_no else str(invoice.id)
    return {"invoice_id": bill_display}

@app.get("/print_invoice/{invoice_identifier}")
def print_invoice(invoice_identifier: str, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    invoice = db.query(database.Invoice).filter(database.Invoice.bill_no == invoice_identifier).first()
    if not invoice and invoice_identifier.isdigit():
        invoice = db.query(database.Invoice).filter(database.Invoice.id == int(invoice_identifier)).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    customer = db.query(database.Customer).filter(database.Customer.id == invoice.customer_id).first()
    item_list = []; request_numbers = []
    billed_jobs = db.query(database.JobCard).filter(database.JobCard.invoice_id == invoice.id).order_by(database.JobCard.date_received.asc()).all()
    
    def to_ist(utc_dt):
        if not utc_dt: return datetime.now()
        return utc_dt + timedelta(hours=5, minutes=30)

    job_time_ist = ""
    if billed_jobs and billed_jobs[0].date_received: job_time_ist = to_ist(billed_jobs[0].date_received).strftime("%d-%m-%Y %I:%M %p")
    bill_time_ist = to_ist(invoice.created_at).strftime("%d-%m-%Y %I:%M %p")
    if not job_time_ist: job_time_ist = bill_time_ist

    for job in billed_jobs:
        if job.request_number: request_numbers.append(job.request_number)
        items = db.query(database.JobItem).filter(database.JobItem.job_card_id == job.id).order_by(database.JobItem.id.asc()).all()
        for i in items: item_list.append({"description": i.item_description, "purity": i.declared_purity, "quantity": i.quantity, "hm": i.hm, "rej": i.rej, "melt": i.melt, "rtn": i.rtn})

    bill_no_display = invoice.bill_no if invoice.bill_no else f"#00{invoice.id}"
    return {
        "invoice_no": bill_no_display, "job_date_time": job_time_ist, "bill_date_time": bill_time_ist,
        "customer_name": customer.business_name or "Unknown", "customer_address": customer.address or "", 
        "customer_license": customer.license_number or "", "request_numbers": ", ".join(set(request_numbers)),
        "total_amount": round(invoice.total_amount or 0, 2), "items": item_list 
    }

# --- TRACKING PORTAL (Public - No Auth Required) ---
@app.get("/track/{identifier}")
def track_job(identifier: str, db: Session = Depends(get_db)):
    job = db.query(database.JobCard).filter(
        (database.JobCard.request_number == identifier) | 
        (database.JobCard.receipt_no == identifier)
    ).first()
    
    if not job: raise HTTPException(status_code=404, detail="Request Number not found. Please check and try again.")
    
    customer = db.query(database.Customer).filter(database.Customer.id == job.customer_id).first()
    items = db.query(database.JobItem).filter(database.JobItem.job_card_id == job.id).all()
    
    return {
        "request_no": job.request_number,
        "receipt_no": job.receipt_no,
        "status": job.status, # Returns "Pending" or "Billed"
        "customer": customer.business_name if customer else "Unknown",
        "date": job.date_received.strftime("%d-%m-%Y"),
        "total_items": sum(i.quantity for i in items)
    }

# --- REPORTS (WITH PAGINATION) ---
import math

@app.get("/report/{customer_id}")
def generate_report(customer_id: int, start_date: str, end_date: str, page: int = 1, limit: int = 50, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    start = datetime.strptime(start_date, "%Y-%m-%d"); end = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
    
    base_query = db.query(database.Invoice).filter(database.Invoice.customer_id == customer_id, database.Invoice.created_at >= start, database.Invoice.created_at <= end)
    
    total_records = base_query.count()
    total_pages = math.ceil(total_records / limit) if total_records > 0 else 1
    
    invoices = base_query.order_by(database.Invoice.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    customer = db.query(database.Customer).filter(database.Customer.id == customer_id).first()
    cust_name = customer.business_name if customer else "Unknown"
    
    report_data = []; grand_total = 0; total_pieces = 0
    # Also calculate the true grand total across ALL pages for the top summary
    all_invoices = base_query.all()
    for inv in all_invoices: grand_total += inv.total_amount
    
    for inv in invoices:
        jobs = db.query(database.JobCard).filter(database.JobCard.invoice_id == inv.id).all()
        req_nos = ", ".join([j.request_number for j in jobs if j.request_number])
        pcs = 0; item_descriptions = []
        for j in jobs:
            items = db.query(database.JobItem).filter(database.JobItem.job_card_id == j.id).all()
            for i in items: pcs += i.quantity; item_descriptions.append(f"{i.quantity}x {i.item_description}")
        item_details_str = ", ".join(item_descriptions)
        bill_no_display = inv.bill_no if inv.bill_no else f"#00{inv.id}"
        report_data.append({"date": inv.created_at.strftime("%d-%m-%Y"), "invoice_no": bill_no_display, "request_no": req_nos, "customer_name": cust_name, "item_details": item_details_str, "pieces": pcs, "amount": round(inv.total_amount, 2)})
        total_pieces += pcs
        
    return { "customer_name": cust_name, "report_data": report_data, "grand_total": round(grand_total, 2), "page_items": total_pieces, "current_page": page, "total_pages": total_pages }

@app.get("/report_all/")
def generate_master_report(start_date: str, end_date: str, page: int = 1, limit: int = 50, db: Session = Depends(get_db), admin: database.AppUser = Depends(require_admin)):
    start = datetime.strptime(start_date, "%Y-%m-%d"); end = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
    
    base_query = db.query(database.Invoice).filter(database.Invoice.created_at >= start, database.Invoice.created_at <= end)
    
    total_records = base_query.count()
    total_pages = math.ceil(total_records / limit) if total_records > 0 else 1
    
    invoices = base_query.order_by(database.Invoice.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    
    report_data = []; grand_total = 0; total_pieces = 0
    all_invoices = base_query.all()
    for inv in all_invoices: grand_total += inv.total_amount
    
    for inv in invoices:
        customer = db.query(database.Customer).filter(database.Customer.id == inv.customer_id).first()
        cust_name = customer.business_name if customer else "Unknown"
        jobs = db.query(database.JobCard).filter(database.JobCard.invoice_id == inv.id).all()
        req_nos = ", ".join([j.request_number for j in jobs if j.request_number])
        pcs = 0; item_descriptions = []
        for j in jobs:
            items = db.query(database.JobItem).filter(database.JobItem.job_card_id == j.id).all()
            for i in items: pcs += i.quantity; item_descriptions.append(f"{i.quantity}x {i.item_description}")
        item_details_str = ", ".join(item_descriptions)
        bill_no_display = inv.bill_no if inv.bill_no else f"#00{inv.id}"
        report_data.append({"date": inv.created_at.strftime("%d-%m-%Y"), "invoice_no": bill_no_display, "request_no": req_nos, "customer_name": cust_name, "item_details": item_details_str, "pieces": pcs, "amount": round(inv.total_amount, 2)})
        total_pieces += pcs
        
    return { "customer_name": "ALL JEWELERS (MASTER REPORT)", "report_data": report_data, "grand_total": round(grand_total, 2), "page_items": total_pieces, "current_page": page, "total_pages": total_pages }

from datetime import timedelta

@app.get("/royalty_report/")
def generate_royalty_report(month: str, db: Session = Depends(get_db), user: database.AppUser = Depends(get_current_user)):
    # The 'month' comes in as "YYYY-MM" (e.g., "2026-05")
    try:
        start_date = datetime.strptime(f"{month}-01", "%Y-%m-%d")
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year+1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = start_date.replace(month=start_date.month+1, day=1) - timedelta(days=1)
        end_date = end_date.replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid month format.")

    # Get all invoices for that specific month
    invoices = db.query(database.Invoice).filter(
        database.Invoice.created_at >= start_date,
        database.Invoice.created_at <= end_date
    ).order_by(database.Invoice.created_at.asc()).all()

    report_data = []
    total_monthly_royalty = 0.0

    for inv in invoices:
        customer = db.query(database.Customer).filter(database.Customer.id == inv.customer_id).first()
        cust_name = customer.business_name if customer else "Unknown"

        # Count the pieces for the specific invoice
        jobs = db.query(database.JobCard).filter(database.JobCard.invoice_id == inv.id).all()
        total_pcs = 0
        hm_pcs = 0
        req_nos = []
        for j in jobs:
            if j.request_number: req_nos.append(j.request_number)
            items = db.query(database.JobItem).filter(database.JobItem.job_card_id == j.id).all()
            for item in items:
                total_pcs += item.quantity
                hm_pcs += item.hm

        # --- YOUR EXACT ROYALTY MATH ---
        if 1 <= total_pcs <= 4:
            inv_royalty = 20.00
        elif total_pcs > 4:
            inv_royalty = hm_pcs * 4.50
        else:
            inv_royalty = 0.00

        total_monthly_royalty += inv_royalty

        report_data.append({
            "date": inv.created_at.strftime("%d-%m-%Y"),
            "invoice_no": inv.bill_no or f"#00{inv.id}",
            "customer_name": cust_name,
            "request_nos": ", ".join(req_nos),
            "total_pieces": total_pcs,
            "hm_pieces": hm_pcs,
            "royalty_amount": round(inv_royalty, 2)
        })

    # Final GST and Grand Total calculations
    total_monthly_royalty = round(total_monthly_royalty, 2)
    monthly_gst = round(total_monthly_royalty * 0.18, 2)
    grand_total = round(total_monthly_royalty + monthly_gst, 2)

    return {
        "month_display": start_date.strftime("%B %Y"),
        "report_data": report_data,
        "total_royalty": total_monthly_royalty,
        "gst_amount": monthly_gst,
        "grand_total": grand_total
    }
