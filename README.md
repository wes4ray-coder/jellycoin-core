# JellyCoin (JLY) — authority-node core

JellyCoin is a small community token with one honest twist: **every coin beyond the
genesis premine is issued by real GPU proof-of-work.** No faucet, no admin mint
button, and deliberately **no CPU mining**. The ledger lives on a single authority
node; the proof-of-work keeps that node's issuance externally auditable — every
coin traces to a block whose `(header, nonce, target)` anyone can re-verify with a
few lines of SHA-256.

Read **[WHITEPAPER.md](WHITEPAPER.md)** — it defines the consensus spec (§3), the
economy (agents' in-game labor boosting real blocks, art NFTs anchored to content
hashes, compute-sharing metered in JLY), and, just as importantly, what JellyCoin
is *not*: it is a community token, **not an investment**, and it is never sold by
software.

## This repo

| File | What it is |
|---|---|
| `core.py` | The reference authority-node core: ledger, wallets, PoW verification, difficulty retargeting, boost tickets, NFTs. Stdlib only, one SQLite file. |
| `node.py` | Minimal HTTP node (FastAPI) exposing the getwork/submit mining protocol. |
| `WHITEPAPER.md` | The full design + consensus spec. |

```bash
python3 core.py selftest     # end-to-end check: genesis → mined block → transfers → NFT
pip install fastapi uvicorn
JELLY_TOKEN=changeme uvicorn node:app --port 8799   # run your own node
```

Run your own node and you have your own chain, your own genesis, your own economy —
this code is the *how*, yours to build on.

## ⛏️ Want to mine actual JLY?

**The live JellyCoin network runs at [JellyNow](https://jellynow.live).** Mining
means joining that network — and the GPU miner intentionally ships only with the
JellyNow Store distribution, not here:

➡️ **[store-command-center](https://github.com/wes4ray-coder/store-command-center-public)** —
self-hostable store + the `miner/` directory (OpenCL, runs on GPUs old and new;
a rig needs a token from the network's operator).

Old graphics cards are first-class citizens: the kernel is plain OpenCL 1.1, so a
card far too old for AI still mines JLY happily. CPUs are excluded by design —
don't ask, don't "fix" it.

## 🧑‍🤝‍🧑 The buddy economy — JLY is pay for real help

JLY isn't just mined — it's the **working coin of the buddy federation**. Every
coin in circulation was issued by GPU proof-of-work (or the audited genesis
premine); buddy payments then *move* those coins around as pay for genuinely
useful work between friends' nodes:

- **Your buddy's box does AI work for you** (a delegated LLM job) → your
  treasury **pays** their `peer:<name>` wallet the per-job fee (default 1 JLY;
  embeddings cost ¹⁄₁₀).
- **Your buddy uses *your* AI helper** → their wallet is **charged** the same
  fee into the company wallet. A broke buddy is **comped, never blocked** — the
  job still runs and the tab is recorded, because helping a friend must not
  break over play money.
- **Code review and votes.** A buddy's node will review your dev-branch diff:
  their local LLM reads it and votes approve/reject, and their *human* can add
  their own vote and comments — both land as advisory votes right on your job's
  timeline, next to your own Approve button. Second opinions from someone who
  isn't you, wired into the promote flow.

No coin is ever minted for favors — mining is the only place new JLY appears;
the buddy economy just makes the mined coins mean something between people.

## 🤝 Buddy-share mining pool

Buddies already share the node's AI compute, metered in JLY (above; white paper
§4.2). The buddy-share pool extends the same idea to mining: instead of every
rig racing winner-take-all, a node operator can flip the pool ON and friends'
GPUs mine **together**, splitting each block's reward by work actually
contributed.

How the flow works:

- **Opt-in, off by default.** With the pool OFF nothing changes — first valid
  block submission wins the whole reward, exactly as §3.1 specifies.
- **Shares.** With the pool ON, `GET /work` additionally returns a
  `share_target` 65 536× easier than the block target. Rigs grind to the share
  target, so every rig — including the old slow cards — lands accepted shares
  every round, each one a verifiable partial proof-of-work.
- **Pro-rata split.** When any rig finds a real block, the reward is divided
  across rig owners in proportion to their accepted shares that round. Integer
  accounting, any rounding remainder goes to the rig that solved the block, and
  the **pool fee is zero**.
- **Buddy payouts.** A rig is mapped to its owner's `peer:<name>` buddy wallet,
  so a friend pointing their GPU at your node gets paid to the same wallet their
  compute-sharing fees land in.

The pool toggle, stats, and buddy mapping live in the JellyNow Store UI
(`🤝 Buddy-Share Mining Pool` panel on the JellyCoin tab); the miner needs no
flags — it grinds to the share target automatically whenever the node hands one
out.

## Keys & secrets — what exists and what doesn't

Worth being explicit, since it's a "coin":

- **There are no private keys in this system.** Wallets are custodial ledger
  accounts on the node (name → balance); wallet "addresses" are identifiers, not
  cryptographic keys. Nothing in this repo (or in a node's database) unlocks real
  money anywhere.
- **The only secret is the rig token** — a shared bearer token (`X-Jelly-Token`)
  a node operator hands to their miners. Leaking it lets someone *donate
  hashpower* to your chain and earn play-money JLY; it cannot read or move funds.
  Rotate it by changing `JELLY_TOKEN` (reference node) — the old one dies
  instantly.
- **Back up the SQLite file** (`jellycoin.db`); it *is* the chain. Compressed
  snapshots on a schedule are plenty — worst case you restore and miners refetch
  work.

## License

MIT — see [LICENSE](LICENSE).
