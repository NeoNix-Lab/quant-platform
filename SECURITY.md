# Security Policy

## Supported Versions

| Version / Branch | Supported | Notes |
| :--- | :---: | :--- |
| `main` | ✅ | Production release branch |
| `implement/*` (active wave) | ✅ | Active integration development |
| Prior feature branches / tags | ❌ | Not supported |

## Reporting a Vulnerability

We take the security and integrity of `quant-platform` seriously. If you discover a potential security vulnerability, credential leak, or financial integrity bug, please **do not open a public GitHub issue**.

Instead, please report it via GitHub's **Private Vulnerability Reporting**:
👉 **[Report a Security Vulnerability](https://github.com/NeoNix-Lab/quant-platform/security/advisories/new)**

Alternatively, you may contact the maintainers directly via private GitHub message or email to the repository owner.

### What to Include

Please provide:
- A clear description of the vulnerability and its potential impact.
- Step-by-step instructions or proof-of-concept code to reproduce the issue.
- Details regarding the affected module, contract, or architecture seam.

We will acknowledge receipt within 48 hours and work with you to remediate the issue responsibly before any public disclosure.

---

## Core Security Invariants

The `quant-platform` architecture is designed with several non-negotiable security and integrity invariants:

1. **Zero Credentials in Repository**:
   - API keys, exchange secrets, private keys, database passwords, and environment secrets (`.env`) must **never** be committed to Git.
   - GitHub Secret Scanning with Push Protection is enforced to block accidental credential pushes.

2. **Causal & Temporal Anti-Leakage Invariant**:
   - The platform strictly enforces point-in-time correctness:
     $$t_{\text{available}} \le t_{\text{decision}}$$
   - Any strategy feature, indicator, or market event with an availability timestamp in the future fails closed with a fatal exception to prevent lookahead bias or predictive data leakage (ADR-0006, ADR-0031).

3. **Deterministic Double-Entry Accounting Conservation**:
   - All financial calculations (cash balances, position quantities, cost basis, realized/unrealized PnL, fee schedules, slippage) use exact `Decimal` arithmetic.
   - Financial operations conserve fundamental double-entry balance sheets:
     $$\text{Equity}_t = \text{Cash}_t + \sum_{i} \text{PositionValue}_{i,t}$$

4. **DataGateway Seam Isolation**:
   - Upper layers (Strategy, Execution, Replay, Portfolio) must never bypass `DataGateway` to directly open raw database tables or storage files (ADR-0019).
