"""JellyCoin (JLY) authority-node core — reference implementation.

A single-node community token where supply is issued EXCLUSIVELY by real GPU
proof-of-work. The node keeps the ledger and VERIFIES blocks; it never mines.
See WHITEPAPER.md for the full design; §3 is the consensus spec this file
implements:

  header76 :=  prev(32) || merkle(32) || height(4,BE) || time(4,BE) || zero(4)
  message  :=  header76 || nonce(4,BE)                       -- 80 bytes
  valid    :=  int(sha256(sha256(message)), big-endian) < target

Standalone: stdlib only, state in one SQLite file (env JELLY_DB, default
./jellycoin.db). Run `python core.py selftest` for an end-to-end check.

There is deliberately no CPU miner here and none will be added — JellyCoin is
GPU-mined only. If you want to mine on the live network, see README.md.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import struct
import threading
import time

# ── tokenomics / consensus constants (WHITEPAPER.md §3) ──────────────────────
SYMBOL = "JLY"
UNIT = 1_000_000                      # µJLY per JLY; all ledger math is integer µJLY
BLOCK_REWARD = 50 * UNIT
HALVING_INTERVAL = 50_000
PREMINE = 1_000_000 * UNIT            # genesis → treasury
TARGET_BLOCK_SEC = 60
RETARGET_INTERVAL = 20
MAX_TARGET = 1 << 240                 # genesis difficulty 1.0 (~65k hashes/block)
WORK_TTL_SEC = 600
MINER_FRESH_SEC = 300

# external-labor boost tickets (§4.1) — pay out ONLY inside real mined blocks
BOOST_PER_TICKET = UNIT // 20
BOOST_MAX_PER_BLOCK = 20 * UNIT
BOOST_MAX_PENDING = 500
BOOST_TTL_SEC = 86_400
BOOST_AGENT_SHARE = 0.5

NFT_MINT_FEE = 5 * UNIT

TREASURY, COMPANY = "treasury", "company"

DB_PATH = os.environ.get("JELLY_DB", "jellycoin.db")
_GENESIS_PREV = "0" * 64
_lock = threading.Lock()
_works: dict = {}
_schema_done = False


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ── schema ───────────────────────────────────────────────────────────────────
def ensure_schema(conn=None):
    global _schema_done
    own = conn is None
    if own:
        conn = get_conn()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS jelly_blocks (
            height INTEGER PRIMARY KEY, hash TEXT NOT NULL, prev TEXT NOT NULL,
            merkle TEXT NOT NULL, target TEXT NOT NULL, nonce INTEGER NOT NULL,
            time INTEGER NOT NULL, miner TEXT NOT NULL, reward INTEGER NOT NULL,
            boost INTEGER NOT NULL DEFAULT 0, txs TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS jelly_wallets (
            name TEXT PRIMARY KEY, address TEXT UNIQUE,
            balance INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL DEFAULT 'user',
            created_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE IF NOT EXISTS jelly_txs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, height INTEGER, time INTEGER NOT NULL,
            frm TEXT, dst TEXT, amount INTEGER NOT NULL, kind TEXT NOT NULL,
            memo TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS jelly_miners (
            name TEXT PRIMARY KEY, gpu TEXT DEFAULT '', last_seen INTEGER NOT NULL DEFAULT 0,
            blocks INTEGER NOT NULL DEFAULT 0, hashrate REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS jelly_boosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, agent_key TEXT NOT NULL,
            agent_name TEXT NOT NULL, skill TEXT NOT NULL, units INTEGER NOT NULL DEFAULT 1,
            created INTEGER NOT NULL, height INTEGER);
        CREATE TABLE IF NOT EXISTS jelly_nfts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, token_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL, file_path TEXT NOT NULL, sha256 TEXT NOT NULL,
            meta TEXT DEFAULT '{}', owner TEXT NOT NULL, minted_height INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')));
        """)
        if not conn.execute("SELECT 1 FROM jelly_blocks WHERE height=0").fetchone():
            _write_genesis(conn)
        conn.commit()
        _schema_done = True
    finally:
        if own:
            conn.close()


def _ensure(conn):
    if not _schema_done:
        ensure_schema(conn)


def _write_genesis(conn):
    t = int(time.time())
    merkle = _sha256_hex(f"jellycoin-genesis:{PREMINE}".encode())
    header = _header76(_GENESIS_PREV, merkle, 0, t)
    h = _pow_hash(header, 0)
    conn.execute(
        "INSERT INTO jelly_blocks (height,hash,prev,merkle,target,nonce,time,miner,reward,boost,txs)"
        " VALUES (0,?,?,?,?,0,?, 'genesis',?,0,?)",
        (h, _GENESIS_PREV, merkle, f"{MAX_TARGET:064x}", t, PREMINE,
         json.dumps([{"kind": "premine", "dst": TREASURY, "amount": PREMINE}])))
    _wallet(conn, TREASURY, kind="system")
    conn.execute("UPDATE jelly_wallets SET balance=balance+? WHERE name=?", (PREMINE, TREASURY))
    conn.execute("INSERT INTO jelly_txs (height,time,frm,dst,amount,kind,memo) VALUES (0,?,NULL,?,?,?,?)",
                 (t, TREASURY, PREMINE, "premine", "JellyCoin genesis premine"))
    _wallet(conn, COMPANY, kind="system")


# ── hashing / PoW primitives ─────────────────────────────────────────────────
def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _header76(prev_hex: str, merkle_hex: str, height: int, t: int) -> bytes:
    return (bytes.fromhex(prev_hex) + bytes.fromhex(merkle_hex)
            + struct.pack(">I", height) + struct.pack(">I", t) + b"\x00\x00\x00\x00")


def _pow_hash(header76: bytes, nonce: int) -> str:
    msg = header76 + struct.pack(">I", nonce)
    return hashlib.sha256(hashlib.sha256(msg).digest()).hexdigest()


def meets_target(hash_hex: str, target: int) -> bool:
    return int(hash_hex, 16) < target


def difficulty(target: int) -> float:
    return MAX_TARGET / max(1, target)


# ── wallets & transfers ──────────────────────────────────────────────────────
_KIND_PREFIX = {"peer:": "peer", "miner:": "miner", "agent:": "agent"}


def _wallet(conn, name: str, kind: str = "user"):
    row = conn.execute("SELECT * FROM jelly_wallets WHERE name=?", (name,)).fetchone()
    if row:
        return row
    if kind == "user":
        kind = next((k for pre, k in _KIND_PREFIX.items() if name.startswith(pre)), "user")
    addr = "jly1" + secrets.token_hex(18)
    conn.execute("INSERT INTO jelly_wallets (name,address,balance,kind) VALUES (?,?,0,?)",
                 (name, addr, kind))
    return conn.execute("SELECT * FROM jelly_wallets WHERE name=?", (name,)).fetchone()


def wallet(name: str, kind: str = "user", conn=None) -> dict:
    own = conn is None
    if own:
        conn = get_conn()
    try:
        _ensure(conn)
        row = _wallet(conn, name, kind)
        if own:
            conn.commit()
        return dict(row)
    finally:
        if own:
            conn.close()


def transfer(frm: str, dst: str, amount: int, memo: str = "", kind: str = "transfer") -> dict:
    amount = int(amount)
    if amount <= 0:
        raise ValueError("amount must be positive")
    if frm == dst:
        raise ValueError("cannot send to self")
    conn = get_conn()
    try:
        _ensure(conn)
        with _lock:
            src = _wallet(conn, frm)
            _wallet(conn, dst)
            if src["balance"] < amount:
                raise ValueError(f"insufficient funds: {frm} has {src['balance']/UNIT:.2f} {SYMBOL}")
            conn.execute("UPDATE jelly_wallets SET balance=balance-? WHERE name=?", (amount, frm))
            conn.execute("UPDATE jelly_wallets SET balance=balance+? WHERE name=?", (amount, dst))
            conn.execute("INSERT INTO jelly_txs (height,time,frm,dst,amount,kind,memo) VALUES (?,?,?,?,?,?,?)",
                         (_tip_height(conn), int(time.time()), frm, dst, amount, kind, memo[:300]))
            conn.commit()
        return {"ok": True, "from": frm, "to": dst, "amount": amount}
    finally:
        conn.close()


# ── chain state ──────────────────────────────────────────────────────────────
def _tip(conn):
    return conn.execute("SELECT * FROM jelly_blocks ORDER BY height DESC LIMIT 1").fetchone()


def _tip_height(conn) -> int:
    r = _tip(conn)
    return int(r["height"]) if r else 0


def block_reward(height: int) -> int:
    return BLOCK_REWARD >> (height // HALVING_INTERVAL)


def current_target(conn) -> int:
    tip = _tip(conn)
    height = int(tip["height"])
    target = int(tip["target"], 16)
    nxt = height + 1
    if nxt < RETARGET_INTERVAL or nxt % RETARGET_INTERVAL:
        return target
    first = conn.execute("SELECT time FROM jelly_blocks WHERE height=?",
                         (height - RETARGET_INTERVAL + 1,)).fetchone()
    if not first:
        return target
    actual = max(1, int(tip["time"]) - int(first["time"]))
    expected = TARGET_BLOCK_SEC * (RETARGET_INTERVAL - 1)
    ratio = min(4.0, max(0.25, actual / expected))
    return min(MAX_TARGET, max(1, int(target * ratio)))


# ── mining: getwork / submit (the node VERIFIES; it never mines) ─────────────
def get_work(miner: str, gpu: str = "", hashrate: float = 0.0) -> dict:
    miner = (miner or "").strip()[:40]
    if not miner:
        raise ValueError("miner name required")
    conn = get_conn()
    try:
        _ensure(conn)
        now = int(time.time())
        conn.execute("INSERT INTO jelly_miners (name,gpu,last_seen,hashrate) VALUES (?,?,?,?) "
                     "ON CONFLICT(name) DO UPDATE SET gpu=excluded.gpu, last_seen=excluded.last_seen, "
                     "hashrate=excluded.hashrate", (miner, (gpu or "")[:120], now, float(hashrate or 0)))
        conn.commit()
        tip = _tip(conn)
        height = int(tip["height"]) + 1
        target = current_target(conn)
        merkle = _sha256_hex(f"{height}:{tip['hash']}:{now}:{miner}".encode())
        header = _header76(tip["hash"], merkle, height, now)
        work_id = secrets.token_hex(8)
        with _lock:
            for wid in [w for w, v in _works.items() if now - v["issued"] > WORK_TTL_SEC]:
                _works.pop(wid, None)
            _works[work_id] = {"header": header, "prev": tip["hash"], "merkle": merkle,
                               "height": height, "time": now, "target": target,
                               "miner": miner, "issued": now}
        return {"work_id": work_id, "header76": header.hex(), "target": f"{target:064x}",
                "height": height, "difficulty": difficulty(target),
                "symbol": SYMBOL, "reward": block_reward(height) / UNIT}
    finally:
        conn.close()


def submit_work(work_id: str, nonce: int, miner: str) -> dict:
    with _lock:
        w = _works.get(work_id)
    if not w:
        return {"ok": False, "reason": "unknown or expired work"}
    nonce = int(nonce) & 0xFFFFFFFF
    h = _pow_hash(w["header"], nonce)
    if not meets_target(h, w["target"]):
        return {"ok": False, "reason": "hash does not meet target"}
    conn = get_conn()
    try:
        _ensure(conn)
        with _lock:
            tip = _tip(conn)
            if tip["hash"] != w["prev"]:
                return {"ok": False, "reason": "stale: chain moved on"}
            height, now = w["height"], int(time.time())
            reward = block_reward(height)
            miner = (miner or w["miner"]).strip()[:40]
            miner_wallet = f"miner:{miner}"
            _wallet(conn, miner_wallet, kind="miner")
            txs = [{"kind": "coinbase", "dst": miner_wallet, "amount": reward}]
            boost_total = _payout_boosts(conn, height, now, txs)
            conn.execute(
                "INSERT INTO jelly_blocks (height,hash,prev,merkle,target,nonce,time,miner,reward,boost,txs)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (height, h, w["prev"], w["merkle"], f"{w['target']:064x}", nonce, now,
                 miner, reward, boost_total, json.dumps(txs)))
            conn.execute("UPDATE jelly_wallets SET balance=balance+? WHERE name=?", (reward, miner_wallet))
            conn.execute("INSERT INTO jelly_txs (height,time,frm,dst,amount,kind,memo) VALUES (?,?,NULL,?,?,?,?)",
                         (height, now, miner_wallet, reward, "coinbase", f"block {height} reward"))
            conn.execute("UPDATE jelly_miners SET blocks=blocks+1, last_seen=? WHERE name=?", (now, miner))
            conn.commit()
            _works.pop(work_id, None)
        return {"ok": True, "height": height, "hash": h, "reward": reward / UNIT,
                "boost_paid": boost_total / UNIT, "wallet": miner_wallet}
    finally:
        conn.close()


def _payout_boosts(conn, height: int, now: int, txs: list) -> int:
    conn.execute("DELETE FROM jelly_boosts WHERE height IS NULL AND created < ?", (now - BOOST_TTL_SEC,))
    rows = conn.execute("SELECT * FROM jelly_boosts WHERE height IS NULL ORDER BY id LIMIT ?",
                        (BOOST_MAX_PER_BLOCK // BOOST_PER_TICKET,)).fetchall()
    total = 0
    per_agent: dict = {}
    for r in rows:
        conn.execute("UPDATE jelly_boosts SET height=? WHERE id=?", (height, r["id"]))
        per_agent[r["agent_key"]] = per_agent.get(r["agent_key"], 0) + BOOST_PER_TICKET
        total += BOOST_PER_TICKET
    if not total:
        return 0
    _wallet(conn, COMPANY, kind="system")
    company_cut = 0
    for key, amt in per_agent.items():
        agent_amt = int(amt * BOOST_AGENT_SHARE)
        company_cut += amt - agent_amt
        wname = f"agent:{key}"
        _wallet(conn, wname, kind="agent")
        conn.execute("UPDATE jelly_wallets SET balance=balance+? WHERE name=?", (agent_amt, wname))
        conn.execute("INSERT INTO jelly_txs (height,time,frm,dst,amount,kind,memo) VALUES (?,?,NULL,?,?,?,?)",
                     (height, now, wname, agent_amt, "boost", "labor boost payout"))
        txs.append({"kind": "boost", "dst": wname, "amount": agent_amt})
    conn.execute("UPDATE jelly_wallets SET balance=balance+? WHERE name=?", (company_cut, COMPANY))
    conn.execute("INSERT INTO jelly_txs (height,time,frm,dst,amount,kind,memo) VALUES (?,?,NULL,?,?,?,?)",
                 (height, now, COMPANY, company_cut, "boost", "company share of labor boosts"))
    txs.append({"kind": "boost", "dst": COMPANY, "amount": company_cut})
    return total


def skill_pulse(conn, agent_key: str, agent_name: str, skill: str, units: int):
    """External-labor hook (a game, a chore tracker, anything): queue boost tickets.
    Tickets pay out ONLY inside a real GPU-mined block — this never mints by itself."""
    _ensure(conn)
    now = int(time.time())
    pending = conn.execute("SELECT COUNT(*) c FROM jelly_boosts WHERE height IS NULL").fetchone()["c"]
    n = min(int(units), BOOST_MAX_PENDING - int(pending))
    for _ in range(max(0, n)):
        conn.execute("INSERT INTO jelly_boosts (agent_key,agent_name,skill,units,created) VALUES (?,?,?,1,?)",
                     (agent_key, agent_name, skill, now))


# ── NFTs ─────────────────────────────────────────────────────────────────────
def mint_nft(owner: str, file_path: str, title: str, meta: dict = None) -> dict:
    if not os.path.isfile(file_path):
        raise ValueError(f"file not found: {file_path}")
    with open(file_path, "rb") as f:
        content_hash = hashlib.sha256(f.read()).hexdigest()
    conn = get_conn()
    try:
        _ensure(conn)
        if conn.execute("SELECT 1 FROM jelly_nfts WHERE sha256=?", (content_hash,)).fetchone():
            raise ValueError("this exact artwork is already minted")
    finally:
        conn.close()
    if owner != TREASURY:
        transfer(owner, TREASURY, NFT_MINT_FEE, memo=f"NFT mint fee: {title[:60]}", kind="nft_fee")
    conn = get_conn()
    try:
        _ensure(conn)
        token_id = "jnft_" + _sha256_hex(f"{content_hash}:{owner}:{time.time()}".encode())[:24]
        conn.execute("INSERT INTO jelly_nfts (token_id,title,file_path,sha256,meta,owner,minted_height)"
                     " VALUES (?,?,?,?,?,?,?)",
                     (token_id, title[:120], file_path, content_hash,
                      json.dumps(meta or {}), owner, _tip_height(conn)))
        conn.commit()
        return {"ok": True, "token_id": token_id, "sha256": content_hash, "fee": NFT_MINT_FEE / UNIT}
    finally:
        conn.close()


def transfer_nft(token_id: str, frm: str, dst: str) -> dict:
    conn = get_conn()
    try:
        _ensure(conn)
        row = conn.execute("SELECT * FROM jelly_nfts WHERE token_id=?", (token_id,)).fetchone()
        if not row:
            raise ValueError("unknown NFT")
        if row["owner"] != frm:
            raise ValueError(f"{frm} does not own this NFT")
        _wallet(conn, dst)
        conn.execute("UPDATE jelly_nfts SET owner=? WHERE token_id=?", (dst, token_id))
        conn.execute("INSERT INTO jelly_txs (height,time,frm,dst,amount,kind,memo) VALUES (?,?,?,?,0,?,?)",
                     (_tip_height(conn), int(time.time()), frm, dst, "nft_transfer", token_id))
        conn.commit()
        return {"ok": True, "token_id": token_id, "owner": dst}
    finally:
        conn.close()


# ── status ───────────────────────────────────────────────────────────────────
def status() -> dict:
    conn = get_conn()
    try:
        _ensure(conn)
        tip = _tip(conn)
        now = int(time.time())
        supply = conn.execute("SELECT COALESCE(SUM(reward+boost),0) s FROM jelly_blocks").fetchone()["s"]
        miners = [dict(r) for r in conn.execute(
            "SELECT name,gpu,last_seen,blocks,hashrate FROM jelly_miners ORDER BY last_seen DESC LIMIT 20")]
        for m in miners:
            m["online"] = (now - m["last_seen"]) < MINER_FRESH_SEC
        pending = conn.execute("SELECT COUNT(*) c FROM jelly_boosts WHERE height IS NULL").fetchone()["c"]
        nfts = conn.execute("SELECT COUNT(*) c FROM jelly_nfts").fetchone()["c"]
        target = current_target(conn)
        return {"symbol": SYMBOL, "name": "JellyCoin", "unit": UNIT,
                "height": int(tip["height"]), "tip_hash": tip["hash"],
                "difficulty": round(difficulty(target), 3), "target": f"{target:064x}",
                "supply": supply / UNIT, "block_reward": block_reward(int(tip["height"]) + 1) / UNIT,
                "miners": miners, "miners_online": sum(1 for m in miners if m["online"]),
                "boosts_pending": int(pending), "nft_count": int(nfts)}
    finally:
        conn.close()


# ── selftest ─────────────────────────────────────────────────────────────────
def _selftest():
    """End-to-end check against a throwaway DB: genesis, a CPU-*verified* mined
    block (grinding a nonce at genesis difficulty to exercise the validator —
    this is a test, not a mining path), transfers, boosts, NFTs."""
    import tempfile
    global DB_PATH, _schema_done
    DB_PATH = os.path.join(tempfile.mkdtemp(prefix="jellytest_"), "jellycoin.db")
    _schema_done = False
    st = status()
    assert st["height"] == 0 and st["supply"] == PREMINE / UNIT, "genesis/premine"
    w = get_work("selftest", gpu="none/selftest")
    header, target = bytes.fromhex(w["header76"]), int(w["target"], 16)
    nonce = next(n for n in range(5_000_000)
                 if int(_pow_hash(header, n), 16) < target)
    conn = get_conn()
    skill_pulse(conn, "tester", "Tester", "selftest", 3)
    conn.commit(); conn.close()
    r = submit_work(w["work_id"], nonce, "selftest")
    assert r["ok"] and r["height"] == 1 and r["boost_paid"] == 3 * BOOST_PER_TICKET / UNIT, r
    transfer(TREASURY, "alice", 10 * UNIT, "hello")
    assert wallet("alice")["balance"] == 10 * UNIT
    art = os.path.join(os.path.dirname(DB_PATH), "art.bin")
    with open(art, "wb") as f:
        f.write(b"jelly-art")
    nft = mint_nft(TREASURY, art, "Selftest Art")
    transfer_nft(nft["token_id"], TREASURY, "alice")
    print(f"selftest OK — height {status()['height']}, supply {status()['supply']} {SYMBOL}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        _selftest()
    else:
        print(json.dumps(status(), indent=2))
