# PocketSmart AI

Budget-based recommendations for home interiors, parties and jewelry.
Stack: FastAPI, Jinja2, SQLite, JWT login (cookie), Google Gemini.

## Run it

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# open .env and paste your Gemini API key and a random SECRET_KEY
python main.py
```

Open http://127.0.0.1:8000, register, log in and try a planner.

## Files

| File | Job |
|---|---|
| `main.py` | Routes: pages, `/generate-home`, `/generate-party`, `/generate-jewelry`, auth, history, session |
| `gemini_utils.py` | Prompts, Gemini call (text + image), JSON cleanup, product links, fallback plans |
| `auth.py` | bcrypt passwords, JWT, current-user dependency |
| `database.py` | SQLite tables: users, history |
| `templates/`, `static/` | Jinja2 pages and CSS |

## Notes

- No key or no network: the app shows a default plan instead of failing (Activity 5.4).
- Product links are search links built by the app, so they never point to made-up pages.
- Change the model with `GEMINI_MODEL` in `.env` (see ai.google.dev for current names).
- Never commit `.env`. It is already in `.gitignore`.

## API routes

`POST /token` (OAuth2 form, returns JWT), `GET /session-info`, `GET /session-data`,
`GET /history`, `GET /recommendations-details/{id}`.
