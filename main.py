import os
import urllib.parse
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session, joinedload
import sqlalchemy

from database import engine, get_db, init_db, User, Unit, SimCard, Pairing
from auth import require_user, require_admin, verify_password, hash_password

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB and seed CSV files on startup
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

# Session middleware (signed cookies)
app.add_middleware(SessionMiddleware, secret_key="sim-pairing-super-secret-key-2026")

# Jinja2 Templates
templates = Jinja2Templates(directory="templates")

# Custom Response Helper for HTMX Toast Messages
class HTMXResponse(HTMLResponse):
    def __init__(self, content: str = "", status_code: int = 200, flash_message: str = "", flash_type: str = "success", **kwargs):
        headers = kwargs.get("headers", {})
        if flash_message:
            # URL-encode to safely pass message through headers
            headers["X-Flash-Message"] = urllib.parse.quote(flash_message)
            headers["X-Flash-Type"] = flash_type
        kwargs["headers"] = headers
        super().__init__(content, status_code, **kwargs)

# Helper to get user directly from session (available to all route handlers after SessionMiddleware runs)
def get_current_user_template(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return {
        "id": user_id,
        "username": request.session.get("username"),
        "is_admin": request.session.get("is_admin", False)
    }

def get_available_units_and_sims(db: Session):
    active_paired_unit_ids = db.query(Pairing.unit_id).filter(Pairing.is_active == True)
    active_paired_sim_ids = db.query(Pairing.sim_card_id).filter(Pairing.is_active == True)

    active_units = db.query(Unit).filter(
        Unit.is_active == True,
        ~Unit.id.in_(active_paired_unit_ids)
    ).order_by(Unit.id.asc()).all()

    sims = db.query(SimCard).filter(
        ~SimCard.id.in_(active_paired_sim_ids)
    ).order_by(SimCard.msisdn.asc()).all()
    
    return active_units, sims


# ----------------- AUTH ENDPOINTS -----------------

@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    user = get_current_user_template(request)
    if user:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {"error": None})

@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid username or password"})
    
    # Store user info in session
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["is_admin"] = user.is_admin
    
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

# ----------------- PAGE ENDPOINTS -----------------

@app.get("/", response_class=HTMLResponse)
async def dashboard_get(request: Request, current_user = Depends(require_user), db: Session = Depends(get_db)):
    # 1. Total Active Units
    total_active_units = db.query(Unit).filter(Unit.is_active == True).count()
    
    # 2. Active Pairings Count
    active_pairings_count = db.query(Pairing).filter(Pairing.is_active == True).count()
    
    # 3. Pending Active Units (Active units without any active pairing)
    # SELECT count(*) FROM units WHERE is_active=1 AND id NOT IN (SELECT unit_id FROM pairings WHERE is_active=1)
    active_paired_unit_ids = db.query(Pairing.unit_id).filter(Pairing.is_active == True)
    pending_active_units = db.query(Unit).filter(
        Unit.is_active == True,
        ~Unit.id.in_(active_paired_unit_ids)
    ).count()
    
    stats = {
        "total_active_units": total_active_units,
        "active_pairings_count": active_pairings_count,
        "pending_active_units": pending_active_units
    }
    
    # Recent pairing logs (latest 50, both active and historic)
    recent_pairings = db.query(Pairing).options(
        joinedload(Pairing.sim_card),
        joinedload(Pairing.paired_by)
    ).order_by(Pairing.paired_at.desc()).limit(50).all()
    
    return templates.TemplateResponse(request, "dashboard.html", {
        "current_user": current_user,
        "active_page": "dashboard",
        "stats": stats,
        "recent_pairings": recent_pairings
    })

@app.get("/pairing", response_class=HTMLResponse)
async def pairing_get(request: Request, current_user = Depends(require_user), db: Session = Depends(get_db)):
    active_pairings = db.query(Pairing).options(
        joinedload(Pairing.sim_card),
        joinedload(Pairing.paired_by)
    ).filter(Pairing.is_active == True).order_by(Pairing.paired_at.desc()).all()
    
    active_units, sims = get_available_units_and_sims(db)
    
    return templates.TemplateResponse(request, "pairing.html", {
        "current_user": current_user,
        "active_page": "pairing",
        "active_pairings": active_pairings,
        "active_units": active_units,
        "sims": sims
      })

@app.get("/pairing/list", response_class=HTMLResponse)
async def pairing_list_get(request: Request, filter: str = "", current_user = Depends(require_user), db: Session = Depends(get_db)):
    query = db.query(Pairing).options(
        joinedload(Pairing.sim_card),
        joinedload(Pairing.paired_by)
    ).filter(Pairing.is_active == True)
    
    if filter.strip():
        f = f"%{filter.strip()}%"
        query = query.join(Pairing.sim_card).filter(
            (Pairing.unit_id.like(f)) |
            (SimCard.msisdn.like(f)) |
            (SimCard.iccid.like(f))
        )
    
    active_pairings = query.order_by(Pairing.paired_at.desc()).all()
    active_units, sims = get_available_units_and_sims(db)
    
    return templates.TemplateResponse(request, "partials/active_pairings.html", {
        "current_user": current_user,
        "active_pairings": active_pairings,
        "active_units": active_units,
        "sims": sims
    })

# ----------------- HTMX AUTOCOMPLETE ENDPOINTS -----------------

@app.get("/search/units", response_class=HTMLResponse)
async def search_units(request: Request, q: str = "", current_user = Depends(require_user), db: Session = Depends(get_db)):
    q = q.strip()
    units = []
    if q:
        units = db.query(Unit).filter(
            Unit.is_active == True,
            Unit.id.like(f"%{q}%")
        ).limit(10).all()
        
    html_content = ""
    for unit in units:
        html_content += f"""
        <button type="button" class="list-group-item list-group-item-action py-2 px-3" onclick="selectUnit('{unit.id}')">
          <div class="d-flex justify-content-between align-items-center">
            <div class="text-start">
              <i class="ti ti-cpu me-2 text-muted"></i>
              <span class="fw-bold text-dark">Unit ID: {unit.id}</span>
            </div>
            <span class="badge bg-green-lt">Active</span>
          </div>
        </button>
        """
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    return HTMLResponse(content=html_content, headers=headers)

@app.get("/search/sims", response_class=HTMLResponse)
async def search_sims(request: Request, q: str = "", current_user = Depends(require_user), db: Session = Depends(get_db)):
    q = q.strip()
    sims = []
    if q:
        # Match MSISDN contains q, or ICCID prefix match, or first 18 chars match the first 18 of search query
        if len(q) >= 18:
            q_18 = q[:18]
            sims = db.query(SimCard).filter(
                (SimCard.msisdn.like(f"%{q}%")) |
                (sqlalchemy.func.substr(SimCard.iccid, 1, 18) == q_18)
            ).limit(10).all()
        else:
            sims = db.query(SimCard).filter(
                (SimCard.msisdn.like(f"%{q}%")) |
                (SimCard.iccid.like(f"%{q}%"))
            ).limit(10).all()

    html_content = ""
    for sim in sims:
        # Check if already active paired
        is_paired = db.query(Pairing).filter(Pairing.sim_card_id == sim.id, Pairing.is_active == True).count() > 0
        status_badge = '<span class="badge bg-yellow-lt">Paired</span>' if is_paired else '<span class="badge bg-green-lt">Available</span>'
        
        html_content += f"""
        <button type="button" class="list-group-item list-group-item-action py-2" onclick="selectSim('{sim.id}', '{sim.msisdn}', '{sim.iccid}', '{sim.provider}')">
          <div class="d-flex justify-content-between align-items-center">
            <div class="text-start">
              <div class="fw-bold text-dark"><i class="ti ti-device-mobile me-1 text-muted"></i>MSISDN: {sim.msisdn}</div>
              <div class="small text-muted font-monospace"><i class="ti ti-id me-1"></i>ICCID: {sim.iccid}</div>
            </div>
            <div class="text-end">
              <span class="badge bg-azure-lt d-block mb-1">{sim.provider}</span>
              {status_badge}
            </div>
          </div>
        </button>
        """
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    return HTMLResponse(content=html_content, headers=headers)

# ----------------- PAIRING OPERATIONS -----------------

@app.post("/pair", response_class=HTMLResponse)
async def pair_post(
    request: Request, 
    unit_id: str = Form(...), 
    sim_card_id: int = Form(...), 
    current_user = Depends(require_user), 
    db: Session = Depends(get_db)
):
    unit = db.query(Unit).filter(Unit.id == unit_id).first()
    sim = db.query(SimCard).filter(SimCard.id == sim_card_id).first()
    
    if not unit or not sim:
        active_pairings = db.query(Pairing).options(
            joinedload(Pairing.sim_card),
            joinedload(Pairing.paired_by)
        ).filter(Pairing.is_active == True).order_by(Pairing.paired_at.desc()).all()
        active_units, sims = get_available_units_and_sims(db)
        
        return templates.TemplateResponse(request, "partials/active_pairings.html", {
            "current_user": current_user,
            "active_pairings": active_pairings,
            "active_units": active_units,
            "sims": sims
        }, headers={
            "X-Flash-Message": "Invalid Unit ID or SIM Card selected.",
            "X-Flash-Type": "error"
        })
        
    if not unit.is_active:
        active_pairings = db.query(Pairing).options(
            joinedload(Pairing.sim_card),
            joinedload(Pairing.paired_by)
        ).filter(Pairing.is_active == True).order_by(Pairing.paired_at.desc()).all()
        active_units, sims = get_available_units_and_sims(db)
        
        return templates.TemplateResponse(request, "partials/active_pairings.html", {
            "current_user": current_user,
            "active_pairings": active_pairings,
            "active_units": active_units,
            "sims": sims
        }, headers={
            "X-Flash-Message": f"Unit {unit_id} is inactive and cannot be paired.",
            "X-Flash-Type": "error"
        })

    # 1. Deactivate existing active pairings for this unit
    db.query(Pairing).filter(Pairing.unit_id == unit_id, Pairing.is_active == True).update({Pairing.is_active: False})
    
    # 2. Deactivate existing active pairings for this SIM card
    db.query(Pairing).filter(Pairing.sim_card_id == sim_card_id, Pairing.is_active == True).update({Pairing.is_active: False})
    
    # 3. Create a new active pairing
    pairing = Pairing(
        unit_id=unit_id,
        sim_card_id=sim_card_id,
        paired_by_user_id=current_user["id"],
        is_active=True
    )
    db.add(pairing)
    db.commit()
    
    active_pairings = db.query(Pairing).options(
        joinedload(Pairing.sim_card),
        joinedload(Pairing.paired_by)
    ).filter(Pairing.is_active == True).order_by(Pairing.paired_at.desc()).all()
    active_units, sims = get_available_units_and_sims(db)
    
    # Render and return active pairings HTML with flash headers
    content = templates.TemplateResponse(request, "partials/active_pairings.html", {
        "current_user": current_user,
        "active_pairings": active_pairings,
        "active_units": active_units,
        "sims": sims
    }).body.decode("utf-8")
    
    return HTMXResponse(
        content=content, 
        flash_message=f"Unit {unit_id} paired successfully with MSISDN {sim.msisdn}!",
        flash_type="success"
    )

@app.post("/unpair/{pairing_id}", response_class=HTMLResponse)
async def unpair_post(
    request: Request, 
    pairing_id: int, 
    current_user = Depends(require_user), 
    db: Session = Depends(get_db)
):
    pairing = db.query(Pairing).filter(Pairing.id == pairing_id).first()
    if not pairing:
        raise HTTPException(status_code=404, detail="Pairing not found")
        
    # Check permissions: Admin can unpair any, Operator can only unpair their own pairings
    if not current_user.get("is_admin") and pairing.paired_by_user_id != current_user["id"]:
        active_pairings = db.query(Pairing).options(
            joinedload(Pairing.sim_card),
            joinedload(Pairing.paired_by)
        ).filter(Pairing.is_active == True).order_by(Pairing.paired_at.desc()).all()
        active_units, sims = get_available_units_and_sims(db)
        
        content = templates.TemplateResponse(request, "partials/active_pairings.html", {
            "current_user": current_user,
            "active_pairings": active_pairings,
            "active_units": active_units,
            "sims": sims
        }).body.decode("utf-8")
        
        return HTMXResponse(
            content=content,
            flash_message="Forbidden: You can only unpair your own links.",
            flash_type="error"
        )

    pairing.is_active = False
    db.commit()
    
    active_pairings = db.query(Pairing).options(
        joinedload(Pairing.sim_card),
        joinedload(Pairing.paired_by)
    ).filter(Pairing.is_active == True).order_by(Pairing.paired_at.desc()).all()
    active_units, sims = get_available_units_and_sims(db)
    
    content = templates.TemplateResponse(request, "partials/active_pairings.html", {
        "current_user": current_user,
        "active_pairings": active_pairings,
        "active_units": active_units,
        "sims": sims
    }).body.decode("utf-8")
    
    return HTMXResponse(
        content=content,
        flash_message=f"Pairing for Unit {pairing.unit_id} deactivated successfully.",
        flash_type="success"
    )

# ----------------- ADMIN USER ENDPOINTS -----------------

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_get(request: Request, current_user = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.username.asc()).all()
    return templates.TemplateResponse(request, "admin.html", {
        "current_user": current_user,
        "active_page": "admin",
        "users": users
    })

@app.post("/admin/users/create", response_class=HTMLResponse)
async def admin_users_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    is_admin: bool = Form(False),
    current_user = Depends(require_admin),
    db: Session = Depends(get_db)
):
    username = username.strip().lower()
    existing_user = db.query(User).filter(User.username == username).first()
    
    users = db.query(User).order_by(User.username.asc()).all()
    
    if existing_user:
        content = templates.TemplateResponse(request, "partials/users_table.html", {
            "current_user": current_user,
            "users": users
        }).body.decode("utf-8")
        return HTMXResponse(content=content, flash_message="Username already exists!", flash_type="error")
        
    new_user = User(
        username=username,
        password_hash=hash_password(password),
        is_admin=is_admin
    )
    db.add(new_user)
    db.commit()
    
    # Fetch updated list
    users = db.query(User).order_by(User.username.asc()).all()
    
    content = templates.TemplateResponse(request, "partials/users_table.html", {
        "current_user": current_user,
        "users": users
    }).body.decode("utf-8")
    
    return HTMXResponse(content=content, flash_message=f"User '{username}' created successfully!", flash_type="success")

@app.post("/admin/users/delete/{user_id}", response_class=HTMLResponse)
async def admin_users_delete(
    request: Request,
    user_id: int,
    current_user = Depends(require_admin),
    db: Session = Depends(get_db)
):
    if user_id == current_user["id"]:
        users = db.query(User).order_by(User.username.asc()).all()
        content = templates.TemplateResponse(request, "partials/users_table.html", {
            "current_user": current_user,
            "users": users
        }).body.decode("utf-8")
        return HTMXResponse(content=content, flash_message="You cannot delete your own account!", flash_type="error")
        
    db.query(User).filter(User.id == user_id).delete()
    db.commit()
    
    users = db.query(User).order_by(User.username.asc()).all()
    
    content = templates.TemplateResponse(request, "partials/users_table.html", {
        "current_user": current_user,
        "users": users
    }).body.decode("utf-8")
    
    return HTMXResponse(content=content, flash_message="User deleted successfully.", flash_type="success")

@app.get("/report", response_class=HTMLResponse)
async def report_get(
    request: Request,
    unit_filter: str = "",
    msisdn_filter: str = "",
    iccid_filter: str = "",
    provider_filter: str = "",
    user_filter: str = "",
    current_user = Depends(require_user),
    db: Session = Depends(get_db)
):
    query = db.query(Pairing).options(
        joinedload(Pairing.sim_card),
        joinedload(Pairing.paired_by)
    ).filter(Pairing.is_active == True)
    
    if unit_filter.strip():
        query = query.filter(Pairing.unit_id.like(f"%{unit_filter.strip()}%"))
        
    # Join SimCard if filter matches are provided
    if msisdn_filter.strip() or iccid_filter.strip() or provider_filter.strip():
        query = query.join(Pairing.sim_card)
        if msisdn_filter.strip():
            query = query.filter(SimCard.msisdn.like(f"%{msisdn_filter.strip()}%"))
        if iccid_filter.strip():
            query = query.filter(SimCard.iccid.like(f"%{iccid_filter.strip()}%"))
        if provider_filter.strip():
            query = query.filter(SimCard.provider == provider_filter)
            
    if user_filter.strip():
        try:
            query = query.filter(Pairing.paired_by_user_id == int(user_filter))
        except ValueError:
            pass
            
    pairings = query.order_by(Pairing.paired_at.desc()).all()
    
    # Get unique providers and users for dropdowns
    providers = [r[0] for r in db.query(SimCard.provider).distinct().all() if r[0]]
    users = db.query(User).order_by(User.username.asc()).all()
    
    is_htmx = request.headers.get("HX-Request") == "true"
    template_name = "partials/report_table.html" if is_htmx else "report.html"
    
    return templates.TemplateResponse(request, template_name, {
        "current_user": current_user,
        "active_page": "report",
        "pairings": pairings,
        "providers": providers,
        "users": users,
        "filters": {
            "unit": unit_filter,
            "msisdn": msisdn_filter,
            "iccid": iccid_filter,
            "provider": provider_filter,
            "user": user_filter
        }
    })

@app.post("/admin/users/password/{user_id}", response_class=HTMLResponse)
async def admin_users_password_post(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    current_user = Depends(require_admin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    user.password_hash = hash_password(new_password)
    db.commit()
    
    return HTMXResponse(
        content="",
        flash_message=f"Password for user '{user.username}' updated successfully.",
        flash_type="success"
    )
