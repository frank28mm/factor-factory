# WorldQuant Official Course Public Gate

状态：PUBLIC_BOOTSTRAP_CONFIRMED

This public release includes a static course-read gate so the local candidate
factory can bootstrap without bundling private course transcripts, OCR dumps, or
account-specific research artifacts.

The original private working tree used a full transcript/OCR audit as an internal
quality gate. That raw audit bundle is intentionally not part of the public
repository. Public users should read WorldQuant BRAIN's current official learning
materials with their own account before enabling live simulation.

For local-only candidate generation, this file confirms that:

- The public factory may generate starter candidates from public bootstrap
  metadata.
- Live WorldQuant requests still require the user's own logged-in session.
- Generated candidates are research drafts, not submission recommendations.

