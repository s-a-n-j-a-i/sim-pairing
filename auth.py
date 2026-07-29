import bcrypt
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

async def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return {
        "id": user_id,
        "username": request.session.get("username"),
        "is_admin": request.session.get("is_admin", False)
    }

async def require_user(request: Request):
    user = await get_current_user(request)
    if not user:
        # Redirect to login page
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"}
        )
    return user

async def require_admin(request: Request):
    user = await require_user(request)
    if not user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user
