# Lakrish Mart

Django e-commerce and admin platform.

## GitHub Publishing Notes

- Do not commit secrets: `.env` is ignored by `.gitignore`.
- Local artifacts such as `venv/`, `.venv/`, `db.sqlite3`, and generated media are ignored.
- Use `.env.example` as the template for environment variables.

## Local Run (Windows)

This project can run with `uv` (recommended) without relying on a global Python install.

```powershell
# install uv (one-time)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
$env:Path = "C:\Users\$env:USERNAME\.local\bin;$env:Path"

# create environment
uv python install 3.11
uv venv --python 3.11 --seed venv

# install dependencies
venv\Scripts\python.exe -m pip install -r requirements.txt

# create database
venv\Scripts\python.exe manage.py migrate

# run server
venv\Scripts\python.exe manage.py runserver 127.0.0.1:8888
```

Open: http://127.0.0.1:8888/

## Environment Variables

Copy `.env.example` to `.env` and fill values:

- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `DATABASE_URL` (optional for PostgreSQL)
