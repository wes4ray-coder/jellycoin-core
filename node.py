"""Minimal JellyCoin reference node — HTTP wrapper over core.py.

    pip install fastapi uvicorn
    JELLY_TOKEN=changeme uvicorn node:app --host 0.0.0.0 --port 8799

Endpoints mirror the live network's mining protocol:
    GET  /status                      chain overview (public)
    GET  /work?miner=&gpu=&hashrate=  issue PoW work        (X-Jelly-Token)
    POST /submit {work_id,nonce,miner} verify + append block (X-Jelly-Token)
    GET  /wallets                     ledger overview (public, read-only)

Set JELLY_TOKEN to require the header on mining calls (recommended for anything
beyond localhost). The node only VERIFIES proof-of-work — it never mines.
"""
import os

from fastapi import Body, FastAPI, HTTPException, Request

import core

app = FastAPI(title="JellyCoin reference node")
TOKEN = os.environ.get("JELLY_TOKEN", "")


def _check(request: Request):
    if TOKEN and request.headers.get("X-Jelly-Token", "") != TOKEN:
        raise HTTPException(403, "bad or missing X-Jelly-Token")


@app.get("/status")
def status():
    return core.status()


@app.get("/work")
def work(request: Request, miner: str, gpu: str = "", hashrate: float = 0.0):
    _check(request)
    try:
        return core.get_work(miner, gpu=gpu, hashrate=hashrate)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/submit")
def submit(request: Request, payload: dict = Body(...)):
    _check(request)
    return core.submit_work(str(payload.get("work_id", "")),
                            int(payload.get("nonce", 0)),
                            str(payload.get("miner", "")))


@app.get("/wallets")
def wallets():
    conn = core.get_conn()
    try:
        core.ensure_schema(conn)
        rows = conn.execute("SELECT name,address,balance,kind FROM jelly_wallets "
                            "ORDER BY balance DESC").fetchall()
        return {"unit": core.UNIT, "wallets": [dict(r) for r in rows]}
    finally:
        conn.close()
