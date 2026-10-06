import os
import csv
import logging
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
import bcrypt

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite:///./pairing.db"

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)

    pairings = relationship("Pairing", back_populates="paired_by")

class Unit(Base):
    __tablename__ = 'units'
    id = Column(String, primary_key=True)  # UnitId from CSV is a string to match the raw format
    is_active = Column(Boolean, default=True)

    pairings = relationship("Pairing", back_populates="unit")

class SimCard(Base):
    __tablename__ = 'sim_cards'
    id = Column(Integer, primary_key=True, autoincrement=True)
    msisdn = Column(String, nullable=False, index=True)
    iccid = Column(String, nullable=False, index=True)
    provider = Column(String, nullable=False)

    pairings = relationship("Pairing", back_populates="sim_card")

class Pairing(Base):
    __tablename__ = 'pairings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    unit_id = Column(String, ForeignKey('units.id'), nullable=False)
    sim_card_id = Column(Integer, ForeignKey('sim_cards.id'), nullable=False)
    paired_by_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    paired_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)  # True = current pairing, False = historic pairing

    unit = relationship("Unit", back_populates="pairings")
    sim_card = relationship("SimCard", back_populates="pairings")
    paired_by = relationship("User", back_populates="pairings")

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    logger.info("Initializing database...")
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        # 1. Seed users if empty
        if db.query(User).count() == 0:
            logger.info("Seeding default users...")
            admin_user = User(
                username="admin",
                password_hash=hash_password("admin123"),
                is_admin=True
            )
            operator_user = User(
                username="operator",
                password_hash=hash_password("operator123"),
                is_admin=False
            )
            db.add_all([admin_user, operator_user])
            db.commit()
            logger.info("Default users seeded: admin / operator")

        # 2. Seed Units from units.csv if empty
        if db.query(Unit).count() == 0:
            csv_path = "units.csv"
            if os.path.exists(csv_path):
                logger.info(f"Seeding units from {csv_path}...")
                units_to_insert = []
                with open(csv_path, mode='r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        unit_id = row.get('UnitId')
                        is_active_raw = row.get('IsActive')
                        if unit_id:
                            # Map "0" / "1" or boolean strings
                            is_active = is_active_raw == "1"
                            units_to_insert.append(Unit(id=unit_id, is_active=is_active))
                
                # Bulk insert for efficiency
                if units_to_insert:
                    db.bulk_save_objects(units_to_insert)
                    db.commit()
                logger.info(f"Seeded {len(units_to_insert)} units.")
            else:
                logger.warning("units.csv not found, skipping unit seeding.")

        # 3. Seed SimCards from sim-cards.csv if empty
        if db.query(SimCard).count() == 0:
            csv_path = "sim-cards.csv"
            if os.path.exists(csv_path):
                logger.info(f"Seeding SIM cards from {csv_path}...")
                sims_to_insert = []
                with open(csv_path, mode='r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        msisdn = row.get('MSISDN')
                        iccid = row.get('ICCID')
                        provider = row.get('Provider')
                        if msisdn and iccid:
                            sims_to_insert.append(SimCard(
                                msisdn=msisdn,
                                iccid=iccid,
                                provider=provider or "Unknown"
                            ))
                
                if sims_to_insert:
                    db.bulk_save_objects(sims_to_insert)
                    db.commit()
                logger.info(f"Seeded {len(sims_to_insert)} SIM cards.")
            else:
                logger.warning("sim-cards.csv not found, skipping SIM card seeding.")
                
    except Exception as e:
        logger.error(f"Error seeding database: {e}")
        db.rollback()
    finally:
        db.close()

def add_units_from_lines(
    db, 
    lines_text: str, 
    is_active: bool = True, 
    reactivate_existing: bool = True
) -> dict:
    """
    Parses a multiline string of Unit IDs, trims whitespace and quotes, deduplicates,
    inserts new units into the database, and optionally reactivates existing inactive units.
    Returns a dict with statistics and lists of added/existing IDs.
    """
    raw_lines = lines_text.splitlines() if lines_text else []
    seen = set()
    unique_ids = []
    
    for raw in raw_lines:
        cleaned = raw.strip().strip('"\'')
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique_ids.append(cleaned)
            
    if not unique_ids:
        return {
            "total_lines": len(raw_lines),
            "unique_count": 0,
            "added_count": 0,
            "existing_count": 0,
            "reactivated_count": 0,
            "added_ids": [],
            "existing_ids": []
        }
        
    # Query existing IDs in chunks of 500 to stay well under SQLite parameter limits
    existing_ids = set()
    chunk_size = 500
    for i in range(0, len(unique_ids), chunk_size):
        chunk = unique_ids[i:i + chunk_size]
        found = db.query(Unit.id).filter(Unit.id.in_(chunk)).all()
        for (fid,) in found:
            existing_ids.add(fid)
            
    added_ids = [uid for uid in unique_ids if uid not in existing_ids]
    existing_in_batch = [uid for uid in unique_ids if uid in existing_ids]
    
    # Insert new units
    if added_ids:
        new_units = [Unit(id=uid, is_active=is_active) for uid in added_ids]
        db.bulk_save_objects(new_units)
        
    reactivated_count = 0
    if reactivate_existing and existing_in_batch:
        for i in range(0, len(existing_in_batch), chunk_size):
            chunk = existing_in_batch[i:i + chunk_size]
            updated = db.query(Unit).filter(
                Unit.id.in_(chunk),
                Unit.is_active == False
            ).update({Unit.is_active: True}, synchronize_session=False)
            reactivated_count += updated
            
    db.commit()
    
    return {
        "total_lines": len(raw_lines),
        "unique_count": len(unique_ids),
        "added_count": len(added_ids),
        "existing_count": len(existing_in_batch),
        "reactivated_count": reactivated_count,
        "added_ids": added_ids,
        "existing_ids": existing_in_batch
    }
