import hmac
import json
import os
import secrets
import threading
import time
import uuid

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import db as dbm
from .db import db, now
from .runner import create_batch, run_job, tick

BASE = os.path.dirname(os.path.dirname(__file__))
MAX_IMAGE = 4 * 1024 * 1024  # Vercel limita o corpo da requisição a ~4,5 MB

_env_key = os.environ.get("API_KEY", "").strip()
if not _env_key:
    # Sem API_KEY: gera uma chave só para esta instância (útil p/ dev local). Em produção defina API_KEY.
    _env_key = secrets.token_urlsafe(32)
    print(f"[prompt-agent] API_KEY não definida; chave temporária desta execução: {_env_key}", flush=True)
API_KEY = _env_key


def seed():
    path = os.path.join(BASE, "seed.json")
    with db() as c:
        c.execute("SELECT pg_advisory_xact_lock(7002)")
        if c.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"] or not os.path.isfile(path):
            return
        data = json.load(open(path, encoding="utf-8"))
        for p in data.get("products", []):
            c.execute("INSERT INTO products(name,description,benefits,price,audience,notes,images,active,created_at)"
                      " VALUES(?,?,?,?,?,?,'[]',1,?)",
                      (p["name"], p.get("description", ""), p.get("benefits", ""), p.get("price", ""),
                       p.get("audience", ""), p.get("notes", ""), now()))
        if data.get("default_job") and not c.execute("SELECT 1 FROM jobs").fetchone():
            j = data["default_job"]
            c.execute("INSERT INTO jobs(name,enabled,interval_seconds,products_per_run,prompts_per_product,"
                      "seconds_each,mode,language,target_tool,style,extra,max_backlog) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (j["name"], 0, j["interval_seconds"], j["products_per_run"], j["prompts_per_product"],
                       j["seconds_each"], j["mode"], j["language"], j.get("target_tool", ""), j.get("style", ""),
                       j.get("extra", ""), j.get("max_backlog", 30)))


_ready = False
_ready_lock = threading.Lock()


def ensure_ready():
    """Cria tabelas e dados iniciais uma vez por instância (cold start)."""
    global _ready
    if _ready:
        return
    with _ready_lock:
        if not _ready:
            dbm.init()
            seed()
            _ready = True


def auth(x_api_key: str | None = Header(default=None)):
    if not x_api_key or not hmac.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "API key inválida")
    try:
        ensure_ready()
    except RuntimeError as e:
        raise HTTPException(503, str(e))


app = FastAPI(title="Prompt Agent", version="2.0")

if os.environ.get("RUN_LOCAL_SCHEDULER") == "1":  # só p/ Docker/VPS: a Vercel usa Cron
    @app.on_event("startup")
    def _local_ticker():
        def loop():
            while True:
                try:
                    ensure_ready()
                    tick()
                except Exception as e:
                    print("[ticker]", e, flush=True)
                time.sleep(15)
        threading.Thread(target=loop, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


@app.get("/api/health")
def health():
    return {"ok": True, "mock": os.environ.get("MOCK_LLM") == "1",
            "has_anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "has_database": bool(os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")),
            "has_api_key": bool(os.environ.get("API_KEY"))}


# ---------- produtos ----------
class ProductIn(BaseModel):
    name: str
    description: str = ""
    benefits: str = ""
    price: str = ""
    audience: str = ""
    notes: str = ""
    active: bool = True


def _product(r) -> dict:
    d = dict(r)
    d["images"] = json.loads(d.get("images") or "[]")
    d["active"] = bool(d["active"])
    return d


@app.get("/api/products", dependencies=[Depends(auth)])
def list_products():
    with db() as c:
        rows = c.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM prompts WHERE product_id=p.id AND status='new') AS prompts_new,"
            " (SELECT COUNT(*) FROM prompts WHERE product_id=p.id) AS prompts_total FROM products p ORDER BY p.id"
        ).fetchall()
    return [_product(r) for r in rows]


@app.post("/api/products", dependencies=[Depends(auth)])
def create_product(p: ProductIn):
    with db() as c:
        new_id = c.insert("INSERT INTO products(name,description,benefits,price,audience,notes,images,active,created_at)"
                          " VALUES(?,?,?,?,?,?,'[]',?,?)",
                          (p.name, p.description, p.benefits, p.price, p.audience, p.notes, int(p.active), now()))
        return _product(c.execute("SELECT * FROM products WHERE id=?", (new_id,)).fetchone())


@app.put("/api/products/{pid}", dependencies=[Depends(auth)])
def update_product(pid: int, p: ProductIn):
    with db() as c:
        c.execute("UPDATE products SET name=?,description=?,benefits=?,price=?,audience=?,notes=?,active=? WHERE id=?",
                  (p.name, p.description, p.benefits, p.price, p.audience, p.notes, int(p.active), pid))
        r = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404, "produto não existe")
    return _product(r)


@app.delete("/api/products/{pid}", dependencies=[Depends(auth)])
def delete_product(pid: int):
    with db() as c:
        if not c.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "produto não existe")
        c.execute("DELETE FROM products WHERE id=?", (pid,))  # imagens e prompts saem em cascata
    return {"ok": True}


_MEDIA = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


@app.post("/api/products/{pid}/images", dependencies=[Depends(auth)])
async def upload_image(pid: int, file: UploadFile = File(...)):
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in _MEDIA:
        raise HTTPException(400, "use jpg, png ou webp")
    data = await file.read()
    if len(data) > MAX_IMAGE:
        raise HTTPException(400, "imagem acima de 4 MB (o painel reduz sozinho; via API envie menor)")
    name = f"{uuid.uuid4().hex}.{ext}"
    with db() as c:
        r = c.execute("SELECT images FROM products WHERE id=? FOR UPDATE", (pid,)).fetchone()
        if not r:
            raise HTTPException(404, "produto não existe")
        imgs = json.loads(r["images"] or "[]") + [name]
        c.execute("INSERT INTO images(id,product_id,content_type,data,created_at) VALUES(?,?,?,?,?)",
                  (name, pid, _MEDIA[ext], data, now()))
        c.execute("UPDATE products SET images=? WHERE id=?", (json.dumps(imgs), pid))
    return {"images": imgs}


@app.delete("/api/products/{pid}/images/{name}", dependencies=[Depends(auth)])
def delete_image(pid: int, name: str):
    with db() as c:
        r = c.execute("SELECT images FROM products WHERE id=? FOR UPDATE", (pid,)).fetchone()
        if not r:
            raise HTTPException(404, "produto não existe")
        imgs = [i for i in json.loads(r["images"] or "[]") if i != name]
        c.execute("UPDATE products SET images=? WHERE id=?", (json.dumps(imgs), pid))
        c.execute("DELETE FROM images WHERE id=? AND product_id=?", (name, pid))
    return {"images": imgs}


@app.get("/images/{name}")
def get_image(name: str):
    """Pública (nome é um UUID não adivinhável), para o <img> do painel."""
    try:
        ensure_ready()
        with db() as c:
            r = c.execute("SELECT content_type, data FROM images WHERE id=?", (name,)).fetchone()
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    if not r:
        raise HTTPException(404, "imagem não existe")
    return Response(bytes(r["data"]), media_type=r["content_type"],
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ---------- agentes (jobs) ----------
class JobIn(BaseModel):
    name: str
    enabled: bool = False
    interval_seconds: int = 600
    products_per_run: int = 1
    product_ids: list[int] = []
    prompts_per_product: int = 3
    seconds_each: int = 7
    mode: str = "separate"  # separate | sequence
    language: str = "en"
    target_tool: str = ""
    style: str = ""
    extra: str = ""
    max_backlog: int = 30
    webhook_url: str = ""
    webhook_auth: str = ""


def _job(r) -> dict:
    d = dict(r)
    d["product_ids"] = json.loads(d.get("product_ids") or "[]")
    d["enabled"] = bool(d["enabled"])
    return d


def _job_values(j: JobIn):
    if j.mode not in ("separate", "sequence"):
        raise HTTPException(400, "mode deve ser separate ou sequence")
    if not (1 <= j.prompts_per_product <= 12 and 1 <= j.products_per_run <= 50 and 1 <= j.seconds_each <= 60):
        raise HTTPException(400, "valores fora do limite")
    return (j.name, int(j.enabled), max(10, j.interval_seconds), j.products_per_run, json.dumps(j.product_ids),
            j.prompts_per_product, j.seconds_each, j.mode, j.language, j.target_tool, j.style, j.extra,
            j.max_backlog, j.webhook_url, j.webhook_auth)


_JOB_COLS = ("name=?,enabled=?,interval_seconds=?,products_per_run=?,product_ids=?,prompts_per_product=?,"
             "seconds_each=?,mode=?,language=?,target_tool=?,style=?,extra=?,max_backlog=?,webhook_url=?,webhook_auth=?")


@app.get("/api/jobs", dependencies=[Depends(auth)])
def list_jobs():
    with db() as c:
        return [_job(r) for r in c.execute("SELECT * FROM jobs ORDER BY id").fetchall()]


@app.post("/api/jobs", dependencies=[Depends(auth)])
def create_job(j: JobIn):
    vals = _job_values(j)
    with db() as c:
        new_id = c.insert(f"INSERT INTO jobs(name,enabled,interval_seconds,products_per_run,product_ids,"
                          f"prompts_per_product,seconds_each,mode,language,target_tool,style,extra,max_backlog,"
                          f"webhook_url,webhook_auth) VALUES({','.join('?' * 15)})", vals)
        r = c.execute("SELECT * FROM jobs WHERE id=?", (new_id,)).fetchone()
    return _job(r)


@app.put("/api/jobs/{jid}", dependencies=[Depends(auth)])
def update_job(jid: int, j: JobIn):
    vals = _job_values(j)
    with db() as c:
        c.execute(f"UPDATE jobs SET {_JOB_COLS} WHERE id=?", vals + (jid,))
        r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        raise HTTPException(404, "agente não existe")
    return _job(r)


@app.delete("/api/jobs/{jid}", dependencies=[Depends(auth)])
def delete_job(jid: int):
    with db() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (jid,))
    return {"ok": True}


@app.post("/api/jobs/{jid}/run", dependencies=[Depends(auth)])
def run_job_now(jid: int):
    """Executa uma rodada agora e devolve o resultado (em serverless não há 'segundo plano')."""
    return run_job(jid)


@app.api_route("/api/cron/tick", methods=["GET", "POST"])
def cron_tick(authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)):
    """Roda os agentes que estão no prazo. Aceita 'Authorization: Bearer CRON_SECRET' (Vercel Cron) ou X-API-Key."""
    secret = os.environ.get("CRON_SECRET", "")
    ok = bool(secret and authorization and hmac.compare_digest(authorization, f"Bearer {secret}"))
    ok = ok or bool(x_api_key and hmac.compare_digest(x_api_key, API_KEY))
    if not ok:
        raise HTTPException(401, "não autorizado")
    try:
        ensure_ready()
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return tick()


@app.get("/api/runs", dependencies=[Depends(auth)])
def list_runs(limit: int = 30):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


# ---------- geração manual ----------
class GenerateIn(BaseModel):
    product_id: int
    count: int = 3
    seconds: int = 7
    mode: str = "separate"
    language: str = "en"
    target_tool: str = ""
    style: str = ""
    extra: str = ""


@app.post("/api/generate", dependencies=[Depends(auth)])
def generate(g: GenerateIn):
    if not (1 <= g.count <= 12 and 1 <= g.seconds <= 60):
        raise HTTPException(400, "valores fora do limite")
    with db() as c:
        r = c.execute("SELECT * FROM products WHERE id=?", (g.product_id,)).fetchone()
    if not r:
        raise HTTPException(404, "produto não existe")
    try:
        return create_batch(dict(r), None, g.count, g.seconds, g.mode, g.language, g.target_tool, g.style, g.extra)
    except Exception as e:
        raise HTTPException(502, f"falha ao gerar: {e}")


# ---------- prompts ----------
_PROMPT_SELECT = ("SELECT pr.*, p.name AS product_name FROM prompts pr JOIN products p ON p.id=pr.product_id")


@app.get("/api/prompts", dependencies=[Depends(auth)])
def list_prompts(status: str | None = None, product_id: int | None = None, job_id: int | None = None,
                 limit: int = 100):
    q, params = _PROMPT_SELECT + " WHERE 1=1", []
    for col, val in (("pr.status", status), ("pr.product_id", product_id), ("pr.job_id", job_id)):
        if val is not None:
            q += f" AND {col}=?"
            params.append(val)
    q += " ORDER BY pr.id DESC LIMIT ?"
    params.append(min(limit, 500))
    with db() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


class StatusIn(BaseModel):
    status: str  # new | used | discarded


@app.patch("/api/prompts/{prid}", dependencies=[Depends(auth)])
def set_status(prid: int, s: StatusIn):
    if s.status not in ("new", "used", "discarded"):
        raise HTTPException(400, "status inválido")
    with db() as c:
        c.execute("UPDATE prompts SET status=?, used_at=? WHERE id=?",
                  (s.status, now() if s.status == "used" else None, prid))
    return {"ok": True}


def _filters(product_id, job_id):
    where, params = "pr.status='new'", []
    if product_id is not None:
        where += " AND pr.product_id=?"
        params.append(product_id)
    if job_id is not None:
        where += " AND pr.job_id=?"
        params.append(job_id)
    return where, params


@app.get("/api/next", dependencies=[Depends(auth)])
def next_prompt(product_id: int | None = None, job_id: int | None = None, peek: bool = False):
    """Entrega o prompt 'new' mais antigo e marca como 'used' (use peek=true para só olhar)."""
    where, params = _filters(product_id, job_id)
    with db() as c:
        c.execute("SELECT pg_advisory_xact_lock(7003)")  # dois consumidores nunca recebem o mesmo prompt
        r = c.execute(f"{_PROMPT_SELECT} WHERE {where} ORDER BY pr.id LIMIT 1", params).fetchone()
        if r and not peek:
            c.execute("UPDATE prompts SET status='used', used_at=? WHERE id=?", (now(), r["id"]))
    if not r:
        raise HTTPException(404, "nenhum prompt novo")
    return dict(r)


@app.get("/api/next-batch", dependencies=[Depends(auth)])
def next_batch(product_id: int | None = None, job_id: int | None = None, peek: bool = False):
    """Entrega o lote (ex.: os 3 prompts de 7s) mais antigo ainda 'new' e marca todos como 'used'."""
    where, params = _filters(product_id, job_id)
    with db() as c:
        c.execute("SELECT pg_advisory_xact_lock(7003)")
        first = c.execute(f"{_PROMPT_SELECT} WHERE {where} ORDER BY pr.id LIMIT 1", params).fetchone()
        rows = []
        if first:
            rows = c.execute(f"{_PROMPT_SELECT} WHERE pr.batch_id=? AND pr.status='new' ORDER BY pr.idx",
                             (first["batch_id"],)).fetchall()
            if not peek:
                c.execute("UPDATE prompts SET status='used', used_at=? WHERE batch_id=? AND status='new'",
                          (now(), first["batch_id"]))
    if not rows:
        raise HTTPException(404, "nenhum lote novo")
    rows = [dict(r) for r in rows]
    return {"batch_id": rows[0]["batch_id"], "product_id": rows[0]["product_id"],
            "product_name": rows[0]["product_name"], "total_seconds": sum(r["seconds"] for r in rows),
            "prompts": rows}
