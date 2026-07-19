# JellyCoin (JLY) — White Paper

*Version 1.0 — July 2026*

## Abstract

JellyCoin is a community token with one unusual property for its size: **supply is
issued exclusively by real GPU proof-of-work**. There is no faucet, no admin mint
button, and deliberately no CPU mining. A single authority node (the JellyNow
Store) keeps the ledger and verifies every block, while any GPU — from a decade-old
Radeon to a current RTX — competes to solve blocks. JLY powers a small, honest
economy: a virtual company whose agents boost mining through in-world labor, an
art-NFT registry anchored to content hashes, a compute-sharing market between
federated peers, and an AI assistant with its own tipping wallet.

**JellyCoin is a community token, not an investment.** It has no price, no
exchange listing, and no promise of value. This paper describes exactly what it
is, so nobody has to guess.

## 1. Design goals

1. **Real scarcity, honest issuance.** New JLY must cost physical work
   (electricity through a GPU), even though the ledger itself is centrally kept.
2. **Second life for old hardware.** The proof-of-work must run on any OpenCL 1.1
   GPU — cards too old for modern AI workloads remain first-class miners.
3. **No CPU mining.** Botnets, cloud burst instances, and "just run it on the
   server" shortcuts are all CPU-shaped. Excluding CPUs by protocol rule keeps
   issuance tied to hardware people actually own and can see.
4. **Human-approved commerce.** Software may *propose* promotion or sale of JLY;
   only a human may approve it.

## 2. Architecture: an authority-node chain

JellyCoin makes a deliberate trade: **one authority node** (the Store) holds the
ledger and validates proof-of-work, instead of a distributed consensus network.

What this gives up: trustlessness. The node operator can, in principle, edit the
ledger — exactly like every game economy, loyalty-points system, and community
scrip that has ever existed.

What it keeps — and what separates JLY from a points table:

- **Issuance is externally constrained.** The operator's database cannot create
  coins faster than GPUs solve blocks without breaking its own audit trail: every
  coin in `supply` traces to a block whose header hashes below its recorded
  target with its recorded nonce. Anyone with the block table can re-verify every
  hash with ~10 lines of any SHA-256 library.
- **Blocks are portable proofs.** A block (header + nonce + target) is valid or
  invalid independent of who stores it.
- **The chain is exportable.** Nightly snapshots mean the full history can be
  audited or re-hosted.

### 2.1 One network, not one per install

An authority-node design has a failure mode that decentralized chains don't: if
every install founds its own chain on first run, a hundred users produce a
hundred unrelated coins. Nothing is shared, no balance means anything to anyone
else, and the network effect is exactly zero — the ledger is centralized *and*
fragmented, the worst of both.

A node therefore starts in one of two modes:

- **host** — founds its own chain (genesis, premine, mining) and *is* a network.
- **joined** — founds **no chain**: no genesis block is written, no premine is
  issued, and the node refuses to serve `getwork` so a rig cannot quietly start
  the island the operator declined. It participates on a chosen node's chain,
  reading its wallet there over the authenticated peer RPC (§4.2) and pointing
  its rigs at that node's URL.

Joining is permitted only while the local chain is unused (no mined blocks, no
transfers beyond the genesis premine); past that, switching would strand coins
that exist on no other ledger. Switching back to host re-founds a chain, and the
coins earned on the old network stay there — they were never this node's to move.

The consequence worth stating plainly: **growth means more participants on one
chain, not more chains.** A federation of joined nodes around one host is a
network; a hundred hosts are a hundred toys.

## 3. Proof-of-work specification

An implementation needs exactly this section.

```
header76 :=  prev_hash      (32 bytes — SHA-256 of previous block)
          || merkle         (32 bytes — SHA-256 binding height, prev, issue-time, miner)
          || height         (4 bytes, big-endian)
          || time           (4 bytes, big-endian, unix seconds)
          || reserved       (4 bytes, zero)

message  :=  header76 || nonce (4 bytes, big-endian)          -- 80 bytes total
pow_hash :=  SHA-256( SHA-256( message ) )

valid    :=  int(pow_hash, big-endian) < target               -- 256-bit compare
```

- **Genesis target** (difficulty 1.0): `2^240` — an average of 65,536 hashes per
  block, so bootstrap mining works on any hardware.
- **Difficulty retarget:** every 20 blocks, target scales by
  `actual_span / expected_span` toward **60-second blocks**, clamped to 4× per
  adjustment, never easier than genesis.
- **Reward:** 50 JLY per block, halving every 50,000 blocks.
- **Premine:** 1,000,000 JLY minted in the genesis block to the treasury — the
  float that funds NFT fees, compute payouts, grants, and store perks. All
  further supply is mined.
- **Units:** 1 JLY = 1,000,000 µJLY. All ledger math is integer µJLY.

### 3.1 The getwork protocol

Miners speak plain HTTP to the node: `GET /work` returns
`{work_id, header76, target, height}`; the miner grinds nonces; `POST /submit`
returns accept/reject. Work expires in 10 minutes; the first valid submission at
a height wins; later ones are rejected as stale. The node *verifies* hashes — it
never generates them.

When the node's buddy-share pool (§4.5) is enabled, `GET /work` additionally
carries a `share_target` — a target 65 536× easier than the block target — and
submissions meeting it are recorded as shares rather than rejected. Consensus is
unchanged: only hashes meeting the true block target mint a block.

### 3.2 Why GPU-only holds

The reference miner enumerates OpenCL **GPU devices only** and refuses to start
otherwise. Could someone write a CPU miner against the open protocol? Yes — and
they would lose: sha256d throughput on a GPU is 2–4 orders of magnitude above a
CPU, so difficulty retargeting driven by GPU participants prices CPUs out
structurally, not just by policy.

## 4. The economy

| Wallet | Role |
|---|---|
| `treasury` | Premine float; pays compute credits and grants; collects NFT fees |
| `company` | The virtual company's fund; receives boost shares and compute charges |
| `assistant` | The AI assistant's tipping purse (500 JLY genesis grant) |
| `miner:<rig>` | Coinbase rewards per mining rig |
| `agent:<name>` | Virtual-company agents' earnings |
| `peer:<name>` | Federated buddies' compute earnings/spending |

### 4.1 Skilling boosts — play labor meets real work

Agents in the operator's virtual company gather resources (woodcutting, mining,
fishing…). Each unit of in-world yield queues a **boost ticket**. Tickets do
nothing on their own: they cash out **only inside a real mined block**, minting a
small bonus (0.05 JLY/ticket, ≤ 20 JLY/block, 24 h expiry) split between the
agent and the company. No GPU online → tickets expire worthless. The game can
*decorate* proof-of-work; it can never *replace* it. The whole mechanism sits
behind an operator toggle.

### 4.2 Buddy compute — JLY as a metering currency

Federated peer nodes ("buddies") share LLM compute. JLY meters it: a buddy's
machine completing a job for the node **earns** their wallet a fee from the
treasury; a buddy consuming the node's AI **spends** the same fee into the
company wallet (embeddings cost ¹⁄₁₀). A buddy with no balance is **comped, never
blocked** — the tab is recorded, because compute sharing must not break over play
money. Peers audit their own wallet through an authenticated RPC.

Buddies also lend judgment, not just cycles: a node can send a job's dev-branch
diff to a buddy's node for **code review**. The buddy's local LLM reviews it and
votes approve/reject, and the buddy's human may add their own vote and comments;
both arrive back as advisory votes on the requesting job's timeline, beside the
operator's own approve/reject decision. Review work is metered exactly like
compute: the reviewing node charges the requester's wallet on a delivered
verdict (comped, never blocked, if broke), and the requesting node's treasury
credits the reviewer's wallet when the verdict first arrives — once per review,
with a later human vote on the same review never billed again. Promotion
authority never leaves the operator (§4.4's human-approval rule applies) — a
buddy's vote informs, it never decides. Note that JLY payments settle existing PoW-issued coins between
wallets; no coin is ever minted for buddy work — issuance remains exclusively
§3's proof-of-work.

### 4.3 Art NFTs

An NFT is minted from a real artwork file: the file's SHA-256 becomes the token's
immutable content hash, recorded with title, owner, and mint height. The same
content can never be minted twice. Minting costs 5 JLY to the treasury;
tokens are transferable between wallets.

### 4.4 Promotion under human approval

LLM agents may draft promotion or sale pitches for JLY. Every draft lands in a
**proposed** state that only the operator can approve, and approval merely
announces it inside the community. Nothing external is ever auto-posted, and JLY
is never sold for real money by software. This is a hard design rule, kept partly
for honesty and partly because selling tokens for real money is a regulated
activity that a hobby coin has no business wandering into.

### 4.5 Buddy-share mining pool

Buddy compute (§4.2) shares the node's AI; the buddy-share pool shares its
mining. It is **opt-in and off by default** — a disabled pool is exactly the
winner-take-all protocol of §3.1.

Enabled, each rig grinds to the easier share target and its accepted shares are
tallied per round as verifiable partial proofs-of-work. When a rig finds a real
block, the reward splits across rig **owners** pro-rata by shares that round:
integer accounting, remainder to the solving rig, **zero pool fee**. A buddy's
rig is mapped to their `peer:<name>` wallet — the same wallet their compute
fees settle in — so hashpower and AI-sharing earn into one balance. Old GPUs
benefit most: a card too slow to ever win a block outright still lands shares
every round and gets paid for them.

## 5. Security model

- **Ledger integrity:** every balance change is a logged transaction; every coin
  traces to a verifiable block. Wallets are custodial accounts on the node.
- **Mining endpoints** are the only unauthenticated-session surface, and they
  self-guard with a shared rig token; all other operations require the node
  operator's authenticated session. The rig token is stored encrypted at rest.
- **Work replay:** work IDs are single-use, expire in 10 minutes, and submissions
  are re-verified server-side in full 256-bit precision.
- **Backups:** the ledger database is snapshotted on a schedule (compressed,
  rotated); the rig token ships in the operator's encrypted key-backup archive.
- **Blast radius:** JLY holds no real-money value by design, so the worst-case
  compromise is a corrupted play economy — recoverable from any snapshot.

## 6. What JellyCoin is not

- Not decentralized, and not pretending to be.
- Not an investment, a security, or a store of real-world value.
- Not for sale by software; humans approve every outward-facing action.
- Not mineable by CPU, botnet, or API shortcut — GPUs only, by protocol economics
  and by reference-implementation rule.

## 7. Reference implementation

The authority-node core (ledger + PoW validation, standalone, zero infrastructure
dependencies) is published as open source. The GPU miner ships with the JellyNow
Store distribution — mining JLY means joining a JellyNow node's network, which is
the point: the coin exists to make one small community's hardware, art, and
agents worth playing with.
