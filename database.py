from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

# Your live Neon PostgreSQL database connection!
SQLALCHEMY_DATABASE_URL = "postgresql://neondb_owner:npg_5qWFMUNe2QoE@ep-rough-smoke-a1bn3tx2-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"

# We are adding pool pre-ping and recycling to handle Neon's serverless connection drops
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,      # Tests the connection before using it
    pool_recycle=300,        # Automatically recycles connections older than 5 minutes
    pool_size=5,             # Keeps a small, healthy pool of connections
    max_overflow=10          # Allows a few extra connections during traffic spikes
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True)
    business_name = Column(String, index=True)
    address = Column(String)
    license_number = Column(String) 
    gstin = Column(String, unique=True, index=True, nullable=True) 
    phone = Column(String)

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True) 
    customer_id = Column(Integer, ForeignKey("customers.id"))
    service_description = Column(String) 
    taxable_amount = Column(Float)
    total_amount = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    bill_no = Column(String, nullable=True)

class JobCard(Base):
    __tablename__ = "job_cards"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id")) 
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True) 
    request_number = Column(String, index=True, nullable=True)
    status = Column(String, default="Pending") 
    date_received = Column(DateTime, default=datetime.utcnow)
    receipt_no = Column(String, nullable=True)

class JobItem(Base):
    __tablename__ = "job_items"
    id = Column(Integer, primary_key=True, index=True)
    job_card_id = Column(Integer, ForeignKey("job_cards.id")) 
    item_description = Column(String) 
    quantity = Column(Integer)
    declared_purity = Column(String) 
    weight_grams = Column(Float) 
    hm = Column(Integer, default=0)
    rej = Column(Integer, default=0)
    melt = Column(Integer, default=0)
    rtn = Column(Integer, default=0)

# This command will instantly create all these tables inside your new Neon database!
Base.metadata.create_all(bind=engine)
