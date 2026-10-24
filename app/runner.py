import json
import time
import uuid

import httpx

from .db import db, now
from .generator import generate_prompts

# Tempo máximo (s) que uma rodada gasta antes de parar de pegar novos produtos (limite da função serverless)
RUN_BUDGET = 200


def _history(c, product_id: int, n: int = 12) -> list[str]:
    rows = c.execute(
        "SELECT text FROM prompts WHERE product_id=? ORDER BY id DESC LIMIT ?", (product_id, n)
    ).fetchall()
    return [r["text"][:180] for r in rows]


def create_batch(product: dict, job: dict | None, count: int, seconds: int, mode: str,
                 language: str, target_tool: str, style: str, extra: str) -> list[dict]:
    with db() as c:
        history = _history(c, product["id"])
    texts = generate_prompts(product, count, seconds, mode, language, target_tool, style, extra, history)
    batch_id = uuid.uuid4().hex[:12]
    created = []
    with db() as c:
        for i, t in enumerate(texts, 1):
            pid = c.insert(
                "INSERT INTO prompts(product_id,job_id,batch_id,idx,seconds,mode,text,status,created_at) "
                "VALUES(?,?,?,?,?,?,?, 'new', ?)",
                (product["id"], job["id"] if job else None, batch_id, i, seconds, mode, t, now()),
            )
            created.append({"id": pid, "batch_id": batch_id, "idx": i, "seconds": seconds,
                            "mode": mode, "text": t, "product_id": product["id"],
                            "product_name": product["name"]})
    return created


def _webhook(job: dict, product: dict, prompts: list[dict]):
    if not job.get("webhook_url"):
        return
    headers = {"Content-Type": "application/json"}
    if job.get("webhook_auth"):
        headers["Authorization"] = job["webhook_auth"]
    payload = {"event": "prompts.created", "job_id": job["id"], "job_name": job["name"],
               "product": {"id": product["id"], "name": product["name"]},
               "batch_id": prompts[0]["batch_id"] if prompts else None, "prompts": prompts}
    httpx.post(job["webhook_url"], json=payload, headers=headers, timeout=15).raise_for_status()


def pick_products(c, job: dict):
    ids = json.loads(job.get("product_ids") or "[]")
    q = ("SELECT p.*, (SELECT MAX(created_at) FROM prompts WHERE product_id=p.id) AS last_gen, "
         "(SELECT COUNT(*) FROM prompts WHERE product_id=p.id AND status='new') AS backlog "
         "FROM products p WHERE p.active=1")
    params: list = []
    if ids:
        q += f" AND p.id IN ({','.join('?' * len(ids))})"
        params = ids
    q += " ORDER BY last_gen ASC NULLS FIRST, p.id ASC"
    return [dict(r) for r in c.execute(q, params).fetchall()]


def run_job(job_id: int) -> dict:
    """Executa uma rodada do agente. Sem estado em memória: seguro em serverless."""
    started = time.time()
    total, errors, skipped, cut = 0, [], 0, False
    with db() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return {"ok": False, "message": "agente não existe", "count": 0}
        job = dict(row)
        candidates = pick_products(c, job)
    chosen = []
    for p in candidates:
        if p["backlog"] >= job["max_backlog"]:
            skipped += 1
            continue
        chosen.append(p)
        if len(chosen) >= job["products_per_run"]:
            break
    for p in chosen:
        if time.time() - started > RUN_BUDGET:
            cut = True
            break
        try:
            made = create_batch(p, job, job["prompts_per_product"], job["seconds_each"], job["mode"],
                                job["language"], job["target_tool"], job["style"], job["extra"])
            total += len(made)
            try:
                _webhook(job, p, made)
            except Exception as e:  # falha de webhook não pode perder os prompts
                errors.append(f"webhook({p['name'][:30]}): {e}")
        except Exception as e:
            errors.append(f"{p['name'][:30]}: {e}")
    if not chosen and skipped:
        msg = f"fila cheia em {skipped} produto(s) (max_backlog); nada gerado"
    else:
        msg = "; ".join(errors) if errors else "ok"
    if cut:
        msg = "tempo da função esgotado, parte dos produtos fica p/ próxima rodada; " + msg
    ok = not errors
    with db() as c:
        c.execute("INSERT INTO runs(job_id,at,ok,count,message) VALUES(?,?,?,?,?)",
                  (job_id, now(), int(ok), total, msg[:500]))
        c.execute("UPDATE jobs SET last_run=? WHERE id=?", (now(), job_id))
    return {"ok": ok, "count": total, "message": msg}


# Tolerância p/ o cron (que dispara com jitter de alguns segundos)
_SLACK = 20


def tick() -> dict:
    """Roda os agentes que estão no prazo. Chamado pelo cron (Vercel / GitHub Actions / etc.)."""
    from datetime import datetime, timezone
    with db() as c:
        jobs = [dict(r) for r in c.execute("SELECT id, interval_seconds, last_run FROM jobs WHERE enabled=1 ORDER BY id")]
    results = {}
    for j in jobs:
        due = True
        if j["last_run"]:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(j["last_run"])).total_seconds()
            due = elapsed >= j["interval_seconds"] - _SLACK
        if not due:
            continue
        # "claim" otimista: só uma instância executa essa rodada
        with db() as c:
            got = c.execute("UPDATE jobs SET last_run=? WHERE id=? AND last_run IS NOT DISTINCT FROM ? RETURNING id",
                            (now(), j["id"], j["last_run"])).fetchone()
        if not got:
            continue
        results[j["id"]] = run_job(j["id"])
    return {"ran": results, "checked": len(jobs)}
