from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel   
from typing import Optional, List 
from datetime import datetime, timedelta
import database
from fastapi.responses import Response

app = FastAPI(title="Lakshmi Narasimha Hallmarking API")

# --- KEEP AWAKE WIDGET ---
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

# --- SECURITY MODULE ---
class LoginRequest(BaseModel):
    username: str
    password: str

VALID_USERNAME = "admin"
VALID_PASSWORD = "admin123"
SECRET_TOKEN = "lnhc-secure-token-2026"

@app.post("/login")
def login(req: LoginRequest):
    if req.username == VALID_USERNAME and req.password == VALID_PASSWORD:
        return {"access_token": SECRET_TOKEN}
    raise HTTPException(status_code=401, detail="Invalid username or password")

def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization or authorization != f"Bearer {SECRET_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized access. Please log in.")
    return True

# --- PYDANTIC SCHEMAS ---
class ItemInput(BaseModel):
    description: str; quantity: int; purity: str; weight: float

class JobCardInput(BaseModel):
    customer_id: int; request_number: str; items: List[ItemInput]
    custom_date: Optional[str] = None

class ItemGrading(BaseModel):
    item_id: int; hm: int; rej: int; melt: int; rtn: int

class BillPayload(BaseModel):
    customer_id: int; request_number: str; results: List[ItemGrading]
    custom_date: Optional[str] = None

class ItemUpdate(BaseModel):
    description: str; quantity: int; purity: str; weight: float

class NewItemCreate(BaseModel):
    description: str; quantity: int; purity: str; weight: float

# --- CUSTOMER ENDPOINTS ---
@app.post("/customers/")
def create_customer(business_name: str, phone: str, address: str, license_number: str, gstin: Optional[str] = None, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    new_customer = database.Customer(business_name=business_name, phone=phone, address=address, license_number=license_number, gstin=gstin)
    db.add(new_customer)
    db.commit()
    db.refresh(new_customer)
    return new_customer

@app.get("/customers/")
def get_all_customers(db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    return db.query(database.Customer).order_by(database.Customer.business_name.asc()).all()

@app.delete("/customers/{customer_id}")
def delete_customer(customer_id: int, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    customer = db.query(database.Customer).filter(database.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Jeweler not found.")

    existing_jobs = db.query(database.JobCard).filter(database.JobCard.customer_id == customer_id).first()
    if existing_jobs:
        raise HTTPException(status_code=400, detail="Cannot delete this Jeweler because they have existing Job Cards or Bills in the system.")

    db.delete(customer)
    db.commit()
    return {"message": "Jeweler deleted successfully"}

# --- JOB CARD ENDPOINTS ---
@app.post("/jobcards/")
def create_job_card(data: JobCardInput, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    final_date = datetime.utcnow()
    if data.custom_date:
        try: final_date = datetime.strptime(data.custom_date, "%Y-%m-%dT%H:%M")
        except ValueError: pass

    new_job = database.JobCard(customer_id=data.customer_id, request_number=data.request_number, date_received=final_date)
    db.add(new_job)
    db.commit()
    db.refresh(new_job)
    for item in data.items:
        new_item = database.JobItem(job_card_id=new_job.id, item_description=item.description, quantity=item.quantity, declared_purity=item.purity, weight_grams=item.weight)
        db.add(new_item)
    db.commit()
    return {"job_card_id": new_job.id, "request_number": new_job.request_number, "message": "Saved."}

@app.get("/pending_requests/{customer_id}")
def get_pending_requests(customer_id: int, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    jobs = db.query(database.JobCard.request_number).filter(database.JobCard.customer_id == customer_id, database.JobCard.status == "Pending").distinct().all()
    return [j[0] for j in jobs if j[0]]

@app.get("/pending_items/{request_no}")
def get_pending_items(request_no: str, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    pending_jobs = db.query(database.JobCard).filter(database.JobCard.request_number == request_no, database.JobCard.status == "Pending").all()
    items = []
    for job in pending_jobs:
        job_items = db.query(database.JobItem).filter(database.JobItem.job_card_id == job.id).all()
        for i in job_items:
            items.append({"item_id": i.id, "request_number": job.request_number, "description": i.item_description, "quantity": i.quantity, "purity": i.declared_purity, "weight": i.weight_grams})
    return items

@app.put("/update_item/{item_id}")
def update_item(item_id: int, item: ItemUpdate, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    db_item = db.query(database.JobItem).filter(database.JobItem.id == item_id).first()
    if not db_item: raise HTTPException(status_code=404, detail="Item not found")
    db_item.item_description = item.description; db_item.quantity = item.quantity; db_item.declared_purity = item.purity; db_item.weight_grams = item.weight
    db.commit()
    return {"message": "Item updated successfully"}

@app.post("/add_item_to_request/{request_number}")
def add_item_to_request(request_number: str, item: NewItemCreate, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    job = db.query(database.JobCard).filter(database.JobCard.request_number == request_number).first()
    if not job: raise HTTPException(status_code=404, detail="Job not found")
    
    new_item = database.JobItem(job_card_id=job.id, item_description=item.description, quantity=item.quantity, weight_grams=item.weight, declared_purity=item.purity)
    db.add(new_item)
    db.commit()
    return {"message": "Item added successfully"}

@app.delete("/delete_request/{request_no}")
def delete_request(request_no: str, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    pending_jobs = db.query(database.JobCard).filter(database.JobCard.request_number == request_no, database.JobCard.status == "Pending").all()
    if not pending_jobs: raise HTTPException(status_code=404, detail="Pending request not found")
    
    for job in pending_jobs:
        db.query(database.JobItem).filter(database.JobItem.job_card_id == job.id).delete()
        db.delete(job)
    db.commit()
    
    # MAGIC TRICK: Force sequence recalculation after delete
    db.execute(text("SELECT setval(pg_get_serial_sequence('job_cards', 'id'), coalesce(max(id), 1), max(id) IS NOT null) FROM job_cards;"))
    db.commit()
    return {"message": "Request deleted and sequence reset"}

# --- INVOICE ENDPOINTS ---
@app.post("/generate_invoice/")
def generate_invoice(payload: BillPayload, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    pending_jobs = db.query(database.JobCard).filter(database.JobCard.customer_id == payload.customer_id, database.JobCard.request_number == payload.request_number, database.JobCard.status == "Pending").all()
    if not pending_jobs: raise HTTPException(status_code=404, detail="No pending jobs for this Request Number.")

    final_date = datetime.utcnow()
    if payload.custom_date:
        try: final_date = datetime.strptime(payload.custom_date, "%Y-%m-%dT%H:%M")
        except ValueError: pass

    total_pieces = 0
    for res in payload.results:
        db_item = db.query(database.JobItem).filter(database.JobItem.id == res.item_id).first()
        if db_item:
            db_item.hm = res.hm; db_item.rej = res.rej; db_item.melt = res.melt; db_item.rtn = res.rtn
            total_pieces += db_item.quantity
            
    calculated_amount = total_pieces * 45.0
    final_amount = max(calculated_amount, 200.0)
    
    new_invoice = database.Invoice(customer_id=payload.customer_id, service_description=f"Assaying & Hallmarking ({total_pieces} items)", taxable_amount=final_amount, total_amount=final_amount, created_at=final_date)
    db.add(new_invoice)
    db.commit(); db.refresh(new_invoice)
    
    for job in pending_jobs:
        job.invoice_id = new_invoice.id; job.status = "Billed"
    db.commit() 
    return {"invoice_number": new_invoice.id, "total_bill": final_amount}

@app.get("/find_invoice_by_request/{request_no}")
def find_invoice_by_request(request_no: str, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    job = db.query(database.JobCard).filter(database.JobCard.request_number == request_no).first()
    if not job or not job.invoice_id: raise HTTPException(status_code=404, detail="No billed invoice found for this Request No.")
    return {"invoice_id": job.invoice_id}

@app.get("/print_invoice/{invoice_id}")
def print_invoice(invoice_id: int, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    invoice = db.query(database.Invoice).filter(database.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    customer = db.query(database.Customer).filter(database.Customer.id == invoice.customer_id).first()

    item_list = []; request_numbers = []
    billed_jobs = db.query(database.JobCard).filter(database.JobCard.invoice_id == invoice_id).order_by(database.JobCard.date_received.asc()).all()
    
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

    return {
        "invoice_no": invoice.id, "job_date_time": job_time_ist, "bill_date_time": bill_time_ist,
        "customer_name": customer.business_name or "Unknown", "customer_address": customer.address or "", 
        "customer_license": customer.license_number or "", "request_numbers": ", ".join(set(request_numbers)),
        "total_amount": round(invoice.total_amount or 0, 2), "items": item_list 
    }

# --- REPORTS ---
@app.get("/report/{customer_id}")
def generate_report(customer_id: int, start_date: str, end_date: str, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    start = datetime.strptime(start_date, "%Y-%m-%d"); end = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
    invoices = db.query(database.Invoice).filter(database.Invoice.customer_id == customer_id, database.Invoice.created_at >= start, database.Invoice.created_at <= end).order_by(database.Invoice.created_at.asc()).all()
    customer = db.query(database.Customer).filter(database.Customer.id == customer_id).first()
    cust_name = customer.business_name if customer else "Unknown"
    
    report_data = []; grand_total = 0; total_pieces = 0
    for inv in invoices:
        jobs = db.query(database.JobCard).filter(database.JobCard.invoice_id == inv.id).all()
        req_nos = ", ".join([j.request_number for j in jobs if j.request_number])
        pcs = 0; item_descriptions = []
        for j in jobs:
            items = db.query(database.JobItem).filter(database.JobItem.job_card_id == j.id).all()
            for i in items:
                pcs += i.quantity; item_descriptions.append(f"{i.quantity}x {i.item_description}")
        item_details_str = ", ".join(item_descriptions)
            
        report_data.append({"date": inv.created_at.strftime("%d-%m-%Y"), "invoice_no": inv.id, "request_no": req_nos, "customer_name": cust_name, "item_details": item_details_str, "pieces": pcs, "amount": round(inv.total_amount, 2)})
        grand_total += inv.total_amount; total_pieces += pcs
        
    return { "customer_name": cust_name, "report_data": report_data, "grand_total": round(grand_total, 2), "total_items": total_pieces }

@app.get("/report_all/")
def generate_master_report(start_date: str, end_date: str, db: Session = Depends(get_db), secure: bool = Depends(verify_token)):
    start = datetime.strptime(start_date, "%Y-%m-%d"); end = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
    invoices = db.query(database.Invoice).filter(database.Invoice.created_at >= start, database.Invoice.created_at <= end).order_by(database.Invoice.created_at.asc()).all()
    
    report_data = []; grand_total = 0; total_pieces = 0
    for inv in invoices:
        customer = db.query(database.Customer).filter(database.Customer.id == inv.customer_id).first()
        cust_name = customer.business_name if customer else "Unknown"
        jobs = db.query(database.JobCard).filter(database.JobCard.invoice_id == inv.id).all()
        req_nos = ", ".join([j.request_number for j in jobs if j.request_number])
        pcs = 0; item_descriptions = []
        for j in jobs:
            items = db.query(database.JobItem).filter(database.JobItem.job_card_id == j.id).all()
            for i in items:
                pcs += i.quantity; item_descriptions.append(f"{i.quantity}x {i.item_description}")
        item_details_str = ", ".join(item_descriptions)
            
        report_data.append({"date": inv.created_at.strftime("%d-%m-%Y"), "invoice_no": inv.id, "request_no": req_nos, "customer_name": cust_name, "item_details": item_details_str, "pieces": pcs, "amount": round(inv.total_amount, 2)})
        grand_total += inv.total_amount; total_pieces += pcs
        
    return { "customer_name": "ALL JEWELERS (MASTER REPORT)", "report_data": report_data, "grand_total": round(grand_total, 2), "total_items": total_pieces }
