from apscheduler.schedulers.background import BackgroundScheduler

from .db import db
from .runner import run_job

sched = BackgroundScheduler(timezone="UTC")


def sync_jobs():
    """Recria o agendamento a partir do banco (chamar ao criar/editar/ligar/desligar um agente)."""
    with db() as c:
        rows = c.execute("SELECT id, enabled, interval_seconds FROM jobs").fetchall()
    wanted = {f"job{r['id']}": r for r in rows if r["enabled"]}
    for j in sched.get_jobs():
        if j.id not in wanted:
            j.remove()
    for jid, r in wanted.items():
        sched.add_job(run_job, "interval", seconds=max(10, int(r["interval_seconds"])), args=[r["id"]],
                      id=jid, replace_existing=True, max_instances=1, coalesce=True)


def start():
    if not sched.running:
        sched.start()
    sync_jobs()
