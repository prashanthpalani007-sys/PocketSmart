"""PocketSmart AI - FastAPI backend."""
import json
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, RedirectResponse  # noqa: E402
from fastapi.security import OAuth2PasswordRequestForm  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import auth  # noqa: E402
import gemini_utils as ai  # noqa: E402
from database import History, User, get_db, init_db  # noqa: E402

MAX_BUDGET = 100_000_000
MAX_IMAGE_BYTES = 5 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # startup: create tables
    yield


app = FastAPI(title="PocketSmart AI", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.exception_handler(auth.NotAuthenticated)
async def not_authenticated(request: Request, exc: auth.NotAuthenticated):
    return RedirectResponse("/login", status_code=303)


def render(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, ctx)


def check_budget(budget: float) -> float:
    if not (0 < budget <= MAX_BUDGET):
        raise HTTPException(status_code=422, detail="Budget must be greater than 0 and at most ₹10 crore.")
    return budget


def finish(request, db, user, category, title, budget, inputs, result):
    row = History(user_id=user.id, category=category, inputs=json.dumps(inputs), result=json.dumps(result))
    db.add(row)
    db.commit()
    return render(request, "results.html", user=user, title=title, result=result, inputs=inputs, category=category)


# ----------------------------------------------------------------- auth pages
@app.get("/")
async def index(request: Request):
    return RedirectResponse("/dashboard" if request.cookies.get("access_token") else "/login", status_code=303)


@app.get("/register")
async def register_page(request: Request):
    return render(request, "register.html", error=None)


@app.post("/register")
async def register(request: Request, username: str = Form(...), password: str = Form(...),
                   confirm: str = Form(...), db: Session = Depends(get_db)):
    username = username.strip().lower()
    error = None
    if len(username) < 3:
        error = "Username needs at least 3 characters."
    elif len(password) < 6:
        error = "Password needs at least 6 characters."
    elif password != confirm:
        error = "Passwords do not match."
    elif db.query(User).filter(User.username == username).first():
        error = "That username is taken. Try another."
    if error:
        return render(request, "register.html", error=error)
    db.add(User(username=username, hashed_password=auth.hash_password(password)))
    db.commit()
    return RedirectResponse("/login?registered=1", status_code=303)


@app.get("/login")
async def login_page(request: Request, registered: int = 0):
    return render(request, "login.html", error=None, registered=bool(registered))


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = auth.authenticate(db, username, password)
    if not user:
        return render(request, "login.html", error="Wrong username or password.", registered=False)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("access_token", auth.create_token(user.username), httponly=True, samesite="lax",
                        max_age=auth.TOKEN_MINUTES * 60)
    return response


@app.post("/token")
async def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 password flow for API clients (returns a JWT)."""
    user = auth.authenticate(db, form.username, form.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    return {"access_token": auth.create_token(user.username), "token_type": "bearer"}


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("access_token")
    return response


# --------------------------------------------------------------- app pages
@app.get("/dashboard")
async def dashboard(request: Request, user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    recent = db.query(History).filter(History.user_id == user.id).order_by(History.id.desc()).limit(5).all()
    return render(request, "dashboard.html", user=user, recent=recent)


@app.get("/home-planner")
async def home_planner(request: Request, user: User = Depends(auth.get_current_user)):
    return render(request, "home_planner.html", user=user)


@app.get("/party-planner")
async def party_planner(request: Request, user: User = Depends(auth.get_current_user)):
    return render(request, "party_planner.html", user=user)


@app.get("/jewelry-planner")
async def jewelry_planner(request: Request, user: User = Depends(auth.get_current_user)):
    return render(request, "jewelry_planner.html", user=user)


# ------------------------------------------------------- recommendation routes
@app.post("/generate-home")
async def generate_home(request: Request, budget: float = Form(...), rooms: list[str] = Form(default=[]),
                        style: str = Form("modern"), lights: int = Form(0), fans: int = Form(0),
                        dining_tables: int = Form(0), sofas: int = Form(0), beds: int = Form(0),
                        user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    check_budget(budget)
    quantities = {k: max(0, min(v, 50)) for k, v in
                  {"lights": lights, "ceiling fans": fans, "dining tables": dining_tables, "sofas": sofas, "beds": beds}.items() if v > 0}
    inputs = {"budget": budget, "rooms": rooms, "style": style, "quantities": quantities}
    result = await run_in_threadpool(ai.home_recommendations, budget, inputs)
    return finish(request, db, user, "home", "Home interior plan", budget, inputs, result)


@app.post("/generate-party")
async def generate_party(request: Request, budget: float = Form(...), guests: int = Form(...),
                         event_type: str = Form("birthday"), venue: str = Form(""), city: str = Form(""),
                         services: list[str] = Form(default=["catering", "decoration", "entertainment"]),
                         user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    check_budget(budget)
    if not (1 <= guests <= 5000):
        raise HTTPException(status_code=422, detail="Guest count must be between 1 and 5000.")
    inputs = {"budget": budget, "guests": guests, "event_type": event_type, "venue": venue.strip()[:200],
              "city": city.strip()[:80], "services": services or ["catering"]}
    result = await run_in_threadpool(ai.party_recommendations, budget, inputs)
    return finish(request, db, user, "party", "Party plan", budget, inputs, result)


@app.post("/generate-jewelry")
async def generate_jewelry(request: Request, budget: float = Form(...), occasion: str = Form("wedding"),
                           style: str = Form("traditional"), metal: str = Form(""),
                           outfit: UploadFile | None = File(None),
                           user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    check_budget(budget)
    image_bytes, mime = None, None
    if outfit is not None and outfit.filename:
        if not (outfit.content_type or "").startswith("image/"):
            raise HTTPException(status_code=422, detail="Please upload an image file (JPG, PNG or WebP).")
        image_bytes = await outfit.read()
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=422, detail="Image is larger than 5 MB.")
        mime = outfit.content_type
    inputs = {"budget": budget, "occasion": occasion, "style": style, "metal": metal, "outfit_image": bool(image_bytes)}
    result = await run_in_threadpool(ai.jewelry_recommendations, budget, inputs, image_bytes, mime)
    return finish(request, db, user, "jewelry", "Jewelry plan", budget, inputs, result)


# ------------------------------------------------------------ history/session
@app.get("/history")
async def history(request: Request, user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    rows = db.query(History).filter(History.user_id == user.id).order_by(History.id.desc()).all()
    return render(request, "history.html", user=user, rows=rows)


@app.get("/recommendations-details/{history_id}")
async def recommendation_details(request: Request, history_id: int, user: User = Depends(auth.get_current_user),
                                 db: Session = Depends(get_db)):
    row = db.query(History).filter(History.id == history_id, History.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return render(request, "results.html", user=user, title=f"{row.category.title()} plan (saved)",
                  result=row.result_data, inputs=row.inputs_data, category=row.category)


@app.get("/session-info")
async def session_info(user: User = Depends(auth.get_current_user)):
    return {"user_id": user.id, "username": user.username, "logged_in": True}


@app.get("/session-data")
async def session_data(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    rows = db.query(History).filter(History.user_id == user.id).order_by(History.id.desc()).all()
    return JSONResponse({"total_plans": len(rows),
                         "by_category": {c: sum(1 for r in rows if r.category == c) for c in ("home", "party", "jewelry")},
                         "last_plan_at": rows[0].created_at.isoformat() if rows else None})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
