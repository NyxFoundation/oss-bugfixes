# domains/crypto

**Scope rule.** A repository belongs here when it implements a cryptographic
primitive or protocol, and a defect in it **breaks a security guarantee rather
than merely crashing**.

This is the domain where the most valuable bug fixes are the quietest: a
constant-time regression, a missing subgroup check, a nonce derived from the
wrong input, a rejected-but-not-really invalid curve point. None of those read as
security fixes in a commit message — which is the entire argument for walking
whole histories instead of grepping for "security".

## Coverage — 42 repositories

| | |
|---|---|
| TLS / toolkits | openssl · boringssl · libressl · mbedtls · wolfssl · gnutls · rustls · s2n-tls · bouncycastle |
| primitives | libsodium · RustCrypto (hashes, elliptic-curves, AEADs) · noble-curves · noble-hashes · libsecp256k1 · blst · libgcrypt · nettle · pyca/cryptography · golang/crypto · tink |
| post-quantum | liboqs · PQClean · kyber · dilithium · sphincs+ |
| zero-knowledge | arkworks · halo2 · circom · circomlib · snarkjs · gnark · plonky3 · risc0 · sp1 |
| MPC / threshold | MP-SPDZ · tss-lib |
| randomness / KDF | getrandom · argon2 |

Each entry carries a `primitive` tag — tls 10 · zk 9 · hash 7 · pq 6 · aead 3 ·
curve 3 · mpc 2 · signature 1 · rng 1 — for scoping a run. By tier: 20 / 18 / 4.

```bash
oss-bugfixes repos --domain crypto --tier 1
oss-bugfixes run   --domain crypto --repo argon2 --engine heuristic
```

## No migrated corpus

There is no predecessor dataset for this domain — `data/` fills up when you run
the pipeline. That makes it the cleanest place to see what the harness finds
without a keyword-era corpus to anchor expectations.

A worked example from a real run over `argon2` and `noble-hashes` (747 commits
walked, 10 firing any deterministic signal):

```
Fix integer overflow check                 → impact: overflow
fix potential alignment segfault           → impact: memory
fix memory leak in fill_memory_blocks      → impact: memory
prevent unwanted wrapping                  → impact: overflow
```

`prevent unwanted wrapping` is the interesting one. It carries no advisory, no
severity and no security vocabulary; a keyword crawl for "security" or "CVE"
would never have surfaced it.
