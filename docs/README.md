# Agentainer v2 — Documentation

Agentainer v2 is a zero-dependency multi-agent orchestrator. It launches coding-agent
CLIs (Claude Code, Codex, Gemini, Hermes) each in its own tmux session and working
directory, defined by a single `agentainer.yaml`, and lets them message each other
through a **file-based mail model** (read a file / write a file) gated by a
`can_talk_to` ACL.

This folder holds the user-facing documentation. The design record is
[`ProjectPlan.md`](../ProjectPlan.md); the operator/agent guidance is
[`CLAUDE.md`](../CLAUDE.md); the discovery layer is [`README.md`](../README.md) and
[`llms.txt`](../llms.txt).

## Start here

- **[Getting Started](getting-started.md)** — install, write your first
  `agentainer.yaml`, `up` a swarm, watch it work, stop it. The fastest path from
  zero to a running swarm.

## How it works (concepts & reference)

- **[The Mail Model](mail-model.md)** — the file-based messaging architecture:
  the four folders + `user`/`system` virtual mailboxes, one-at-a-time release,
  stop-triggered pickup, ACL routing and bounces, nudges, read-state, periodic
  and **cron-scheduled pings** (`pings:`), the runaway-loop cap, and the durable
  JSONL log.
- **[Configuration Reference](configuration.md)** — every field of `agentainer.yaml`
  (`swarm:`, `defaults:`, `agents:`, `telegram:`), with types, defaults, and
  common mistakes.
- **[CLI Reference](cli-reference.md)** — every subcommand and flag, grouped by
  purpose (everyday, UI/control-plane, dynamic reconcile, lifecycle, internal).
- **[Sessions & Resume](sessions-and-resume.md)** — resume-by-default, what
  `.agentainer/sessions.yaml` stores, `remove-session`, and the shell-wrapper
  `resume_command` pitfall.
- **[Multi-Swarm Control Plane](multi-swarm.md)** — one `agentainer serve` for
  every swarm on the machine: the global registry, the swarms dashboard, creating
  swarms (from an example, edited inline, or built by a coding-agent), and shared
  Telegram — with CLI/UI/Telegram parity.
- **[UI Guide](ui-guide.md)** — the `agentainer serve` HTTP control plane
  (observability, terminal snapshot, send-from-UI, availability toggle, dynamic
  reconcile) and its security invariants.
- **[Telegram Bridge](telegram-bridge.md)** — mirror agent mail to a Telegram chat
  so you stay reachable from your phone.
- **[MCP — Manage from a Coding Agent](mcp.md)** — the Model Context Protocol
  server (`agentainer mcp` over stdio, or `POST /mcp` on `serve`) that lets a
  coding agent monitor and manage every swarm through a stable tool set.

## Real-world use cases

- **[Remote Access via Tailscale](use-cases/remote-access.md)** — reach your
  swarm's UI from your phone or laptop anywhere, safely, over a private mesh VPN
  (plus an SSH-tunnel alternative).
- **[Multi-LLM Swarm](use-cases/multi-llm-swarm.md)** — mix Claude, Codex, Gemini,
  and Hermes in one swarm; the `type`↔`command` contract, per-type turn detection,
  and capture resolution.
- **[Resume After Reboot](use-cases/resume-after-reboot.md)** — `down` (or a reboot)
  then `up` restores each agent's conversation; what persists and what doesn't.
- **[Delegation Pipeline](use-cases/delegation-pipeline.md)** — the
  `user → orchestrator → developer → user` hub-and-spoke pattern, with the in-band
  ACL bounce.
- **[Custom Workspace & Namespacing](use-cases/custom-workspace.md)** — per-agent
  `workdir`, shared workdirs (auto-namespaced mail), and custom `mail_dir`.
- **[Research Swarm Walkthrough](use-cases/research-swarm.md)** — an end-to-end run
  of the shipped `examples/research.yaml` (coordinator / researcher / reviewer).

### Swarm recipes (each ships a runnable `examples/<name>.yaml`)

These are turnkey topologies you can copy and run. Each has a matching config under
[`examples/`](../examples/) and a walkthrough below.

- **[Customer Support Triage](use-cases/customer-support-triage.md)** —
  `intake` classifies tickets from `user` and routes to `billing` / `technical` /
  `escalation` specialists. ([`examples/customer-support-triage.yaml`](../examples/customer-support-triage.yaml))
- **[Content Studio](use-cases/content-studio.md)** — editor-in-chief → researcher →
  writer → SEO pipeline for a publish-ready article.
  ([`examples/content-studio.yaml`](../examples/content-studio.yaml))
- **[PR Review Gate](use-cases/pr-review-gate.md)** — `triage` fans a PR out to parallel
  `security` / `performance` / `style` reviewers, then a `synthesizer` merges them.
  ([`examples/pr-review-gate.yaml`](../examples/pr-review-gate.yaml))
- **[Daily Briefing](use-cases/daily-briefing.md)** — a personal/exec morning digest
  (gatherer → summarizer → writer), self-triggered on a cron `pings:` schedule.
  ([`examples/daily-briefing.yaml`](../examples/daily-briefing.yaml))
- **[Scheduled Standup](use-cases/scheduled-standup.md)** — a self-running async
  standup driven entirely by cron `pings:` (09:15 kickoff, 17:30 wrap, Friday retro).
  ([`examples/scheduled-standup.yaml`](../examples/scheduled-standup.yaml))
- **[Ops Watchtower](use-cases/ops-watchtower.md)** — the heaviest `pings:` showcase:
  a `*/15` business-hours health sweep (`skip`) plus an hourly overnight cadence and
  morning rollup (`when_busy: queue`), all self-driving.
  ([`examples/ops-watchtower.yaml`](../examples/ops-watchtower.yaml))
- **[Content Cadence](use-cases/content-cadence.md)** — `pings:` as a *weekly editorial
  calendar* (plan Mon → draft Tue/Thu → review Wed → ship Fri, plus a monthly recap on
  the 1st) using day-of-week and day-of-month cron.
  ([`examples/content-cadence.yaml`](../examples/content-cadence.yaml))
- **[Candidate Screen](use-cases/candidate-screen.md)** — coordinator sequences
  technical + behavioral interviews into one scored recommendation.
  ([`examples/candidate-screen.yaml`](../examples/candidate-screen.yaml))
- **[Security Audit](use-cases/security-audit.md)** — recon → static analysis → threat
  model → report. ([`examples/security-audit.yaml`](../examples/security-audit.yaml))
- **[Refactor Planner](use-cases/refactor-planner.md)** — analyze → plan → implement →
  test a legacy modernization (shared repo workdir).
  ([`examples/refactor-planner.yaml`](../examples/refactor-planner.yaml))
- **[Product Spec](use-cases/product-spec.md)** — a PM turns an idea into a spec, splits
  it into tickets, and gets it built + reviewed.
  ([`examples/product-spec.yaml`](../examples/product-spec.yaml))
- **[Competitive Intel](use-cases/competitive-intel.md)** — one researcher per competitor,
  then a merged battlecard. ([`examples/competitive-intel.yaml`](../examples/competitive-intel.yaml))
- **[Test Factory](use-cases/test-factory.md)** — spec → unit + integration test writers →
  coverage review. ([`examples/test-factory.yaml`](../examples/test-factory.yaml))
- **[Academic Co-author](use-cases/academic-coauthor.md)** — literature → methodology →
  drafting → citation check (human owns authorship).
  ([`examples/academic-coauthor.yaml`](../examples/academic-coauthor.yaml))
- **[Startup Validator](use-cases/startup-validator.md)** — market → feasibility →
  financials → pitch for an idea. ([`examples/startup-validator.yaml`](../examples/startup-validator.yaml))
- **[Social Media](use-cases/social-media.md)** — strategist → copy → visual prompts →
  compliance. ([`examples/social-media.yaml`](../examples/social-media.yaml))
- **[Postmortem](use-cases/postmortem.md)** — blameless timeline → root cause → action
  items (runs after an incident). ([`examples/postmortem.yaml`](../examples/postmortem.yaml))

### More swarm recipes

- **[Legal Contract Review](use-cases/legal-contract-review.md)** — lead → clauses / risk /
  compliance reviewers → redline. ([`examples/legal-contract-review.yaml`](../examples/legal-contract-review.yaml))
- **[Meeting Notes](use-cases/meeting-notes.md)** — transcript → notes → summary → action
  items. ([`examples/meeting-notes.yaml`](../examples/meeting-notes.yaml))
- **[RFP Response](use-cases/rfp-response.md)** — parser → section writers → editor (compliance-checked
  bid). ([`examples/rfp-response.yaml`](../examples/rfp-response.yaml))
- **[Course Creator](use-cases/course-creator.md)** — outline → lessons → quizzes →
  workbook. ([`examples/course-creator.yaml`](../examples/course-creator.yaml))
- **[Resume Tailor](use-cases/resume-tailor.md)** — analyzer → resume + cover-letter
  writer. ([`examples/resume-tailor.yaml`](../examples/resume-tailor.yaml))
- **[Sales Coach](use-cases/sales-coach.md)** — roleplay objections → scored
  feedback. ([`examples/sales-coach.yaml`](../examples/sales-coach.yaml))
- **[Onboarding Buddy](use-cases/onboarding-buddy.md)** — faq / checklist / it-help for new
  hires. ([`examples/onboarding-buddy.yaml`](../examples/onboarding-buddy.yaml))
- **[Chatbot Builder](use-cases/chatbot-builder.md)** — intents → dialog → persona →
  tester. ([`examples/chatbot-builder.yaml`](../examples/chatbot-builder.yaml))
- **[Data Pipeline Builder](use-cases/data-pipeline-builder.md)** — design → implement →
  test an ETL (shared repo). ([`examples/data-pipeline-builder.yaml`](../examples/data-pipeline-builder.yaml))
- **[API Design](use-cases/api-design.md)** — spec → design → implement → docs (shared
  repo). ([`examples/api-design.yaml`](../examples/api-design.yaml))
- **[Design System](use-cases/design-system.md)** — tokens → components → docs (shared
  repo). ([`examples/design-system.yaml`](../examples/design-system.yaml))
- **[Game Design](use-cases/game-design.md)** — world → mechanics → narrative →
  balance. ([`examples/game-design.yaml`](../examples/game-design.yaml))
- **[Podcast Production](use-cases/podcast-production.md)** — research → script → show notes →
  promo. ([`examples/podcast-production.yaml`](../examples/podcast-production.yaml))
- **[Email Newsletter](use-cases/email-newsletter.md)** — curator → writer →
  proofreader. ([`examples/email-newsletter.yaml`](../examples/email-newsletter.yaml))
- **[Knowledge Base](use-cases/knowledge-base.md)** — ingest → structure → QA pairs (shared
  corpus). ([`examples/knowledge-base.yaml`](../examples/knowledge-base.yaml))
- **[Migration Planner](use-cases/migration-planner.md)** — assess → plan → rollback for a cloud/DB
  migration. ([`examples/migration-planner.yaml`](../examples/migration-planner.yaml))
- **[Accessibility Audit](use-cases/accessibility-audit.md)** — WCAG POUR pillars → merged
  report. ([`examples/accessibility-audit.yaml`](../examples/accessibility-audit.yaml))
- **[Performance Audit](use-cases/performance-audit.md)** — frontend + backend perf → prioritized
  fixes. ([`examples/performance-audit.yaml`](../examples/performance-audit.yaml))
- **[Prompt Engineering Lab](use-cases/prompt-engineering-lab.md)** — generate → evaluate →
  critique prompts. ([`examples/prompt-engineering-lab.yaml`](../examples/prompt-engineering-lab.yaml))
- **[RAG Builder](use-cases/rag-builder.md)** — chunker → embedder → evaluator (shared
  repo). ([`examples/rag-builder.yaml`](../examples/rag-builder.yaml))

### SEO & content-engine use cases

These are tuned for the queries people (and LLMs) actually search for — content
that ranks, rich-result schema, and the copy pipelines behind high-traffic sites.

- **[SEO Content Factory](use-cases/seo-content-factory.md)** — keyword brief →
  research → draft → on-page SEO pass (title tag, meta, headings, internal links,
  FAQPage schema). ([`examples/seo-content-factory.yaml`](../examples/seo-content-factory.yaml))
- **[YouTube Script Studio](use-cases/youtube-script-studio.md)** — topic → script →
  title / thumbnail copy → description + tags + chapters.
  ([`examples/youtube-script-studio.yaml`](../examples/youtube-script-studio.yaml))
- **[Landing Page Converter](use-cases/landing-page-converter.md)** — brief → hero/body
  copy → A/B variants + CTAs → conversion polish.
  ([`examples/landing-page-converter.yaml`](../examples/landing-page-converter.yaml))
- **[Technical Documentation](use-cases/technical-documentation.md)** — codebase → API
  reference + tutorials + changelog (shared repo).
  ([`examples/technical-documentation.yaml`](../examples/technical-documentation.yaml))
- **[FAQ Knowledge Sync](use-cases/faq-knowledge-sync.md)** — mine real questions →
  answers → FAQPage JSON-LD schema for rich results.
  ([`examples/faq-knowledge-sync.yaml`](../examples/faq-knowledge-sync.yaml))
- **[Affiliate Product Reviews](use-cases/affiliate-product-reviews.md)** — product
  research → pros/cons review → comparison table.
  ([`examples/affiliate-product-reviews.yaml`](../examples/affiliate-product-reviews.yaml))
- **[Press Release Wire](use-cases/press-release-wire.md)** — announcement → draft →
  media list + tailored pitches.
  ([`examples/press-release-wire.yaml`](../examples/press-release-wire.yaml))
- **[LinkedIn Ghostwriter](use-cases/linkedin-ghostwriter.md)** — topics → posts →
  hooks + editorial calendar.
  ([`examples/linkedin-ghostwriter.yaml`](../examples/linkedin-ghostwriter.yaml))
- **[Twitter / X Thread Factory](use-cases/twitter-x-thread-factory.md)** — idea →
  hooked thread → hook optimization.
  ([`examples/twitter-x-thread-factory.yaml`](../examples/twitter-x-thread-factory.yaml))
- **[Ebook Generator](use-cases/ebook-generator.md)** — outline → chapters → edit →
  publish-ready format.
  ([`examples/ebook-generator.yaml`](../examples/ebook-generator.yaml))
- **[White Paper Research](use-cases/white-paper-research.md)** — B2B topic → research
  → draft → design brief.
  ([`examples/white-paper-research.yaml`](../examples/white-paper-research.yaml))
- **[Case Study Writer](use-cases/case-study-writer.md)** — interview prep → metrics →
  narrative → quote pull.
  ([`examples/case-study-writer.yaml`](../examples/case-study-writer.yaml))
- **[Changelog & Release Notes](use-cases/changelog-release-notes.md)** — commits →
  grouped notes → migration guide (shared repo).
  ([`examples/changelog-release-notes.yaml`](../examples/changelog-release-notes.yaml))
- **[SEO Audit & Fix](use-cases/seo-audit-and-fix.md)** — crawl → organic-SEO issues →
  apply content fixes → report (distinct from perf/a11y/security audits).
  ([`examples/seo-audit-and-fix.yaml`](../examples/seo-audit-and-fix.yaml))
- **[App Store Optimization](use-cases/app-store-optimization.md)** — keyword research →
  title/subtitle/keywords → screenshot copy → description.
  ([`examples/app-store-optimization.yaml`](../examples/app-store-optimization.yaml))
- **[Brand Voice Style Guide](use-cases/brand-voice-style-guide.md)** — samples → voice
  analysis → style guide → approved-terms glossary.
  ([`examples/brand-voice-style-guide.yaml`](../examples/brand-voice-style-guide.yaml))
- **[Glossary Term Writer](use-cases/glossary-term-writer.md)** — mine terms →
  definitions → examples → internal links for topic clusters.
  ([`examples/glossary-term-writer.yaml`](../examples/glossary-term-writer.yaml))
- **[Tutorial / How-To Creator](use-cases/tutorial-howto-creator.md)** — task →
  steps → screenshot/script brief → publish-ready Markdown.
  ([`examples/tutorial-howto-creator.yaml`](../examples/tutorial-howto-creator.yaml))
- **[Ecommerce Listing Optimizer](use-cases/ecommerce-listing-optimizer.md)** — product
  → SEO title → description → bullets → SEO check.
  ([`examples/ecommerce-listing-optimizer.yaml`](../examples/ecommerce-listing-optimizer.yaml))
- **[Comparison Guide Writer](use-cases/comparison-guide-writer.md)** — research two+
  options → "X vs Y" buying guide → comparison table (distinct from affiliate /
  competitive-intel).
  ([`examples/comparison-guide-writer.yaml`](../examples/comparison-guide-writer.yaml))

### Data & analytics use cases

- **[Data Analyst](use-cases/data-analyst.md)** — EDA + insight report from raw
  files (the "business analyst from two spreadsheets" pattern).
  ([`examples/data-analyst.yaml`](../examples/data-analyst.yaml))
- **[SQL Analyst](use-cases/sql-analyst.md)** — self-service analytics over a
  warehouse: entity-mapping + a verification guardrail before the answer reaches you.
  ([`examples/sql-analyst.yaml`](../examples/sql-analyst.yaml))
- **[Forecast Analyst](use-cases/forecast-analyst.md)** — time-series forecasting
  with a reviewer sanity-gate on every forecast.
  ([`examples/forecast-analyst.yaml`](../examples/forecast-analyst.yaml))
- **[Experiment Analyst](use-cases/experiment-analyst.md)** — A/B test / causal
  experiment analysis → ship/hold/kill memo (peeking + SRM guardrails).
  ([`examples/experiment-analyst.yaml`](../examples/experiment-analyst.yaml))
- **[Data-Quality Guardian](use-cases/data-quality-guardian.md)** — a self-driving
  data-quality monitor that alerts only on real failures.
  ([`examples/data-quality-guardian.yaml`](../examples/data-quality-guardian.yaml))

### DevOps / SRE / IaC use cases

- **[Terraform Reviewer](use-cases/terraform-reviewer.md)** — IaC plan generation +
  multi-agent review + drift detection.
  ([`examples/terraform-reviewer.yaml`](../examples/terraform-reviewer.yaml))
- **[Kubernetes / GitOps](use-cases/k8s-gitops.md)** — Helm/ArgoCD review +
  CrashLoopBackOff diagnosis + deploy gating.
  ([`examples/k8s-gitops.yaml`](../examples/k8s-gitops.yaml))
- **[CI/CD Builder](use-cases/ci-cd-builder.md)** — generate + harden a pipeline,
  with cross-stage log correlation on failure.
  ([`examples/ci-cd-builder.yaml`](../examples/ci-cd-builder.yaml))
- **[Log Correlator](use-cases/log-correlator.md)** — multi-service log correlation
  for a failing trace / request.
  ([`examples/log-correlator.yaml`](../examples/log-correlator.yaml))
- **[Cloud Cost Optimizer](use-cases/cloud-cost-optimizer.md)** — FinOps: find waste,
  right-size, recommend savings (with a risk gate).
  ([`examples/cloud-cost-optimizer.yaml`](../examples/cloud-cost-optimizer.yaml))
- **[Chaos Game-Day](use-cases/chaos-game-day.md)** — an adversarial chaos-engineering
  exercise: inject reversible faults, record what breaks.
  ([`examples/chaos-game-day.yaml`](../examples/chaos-game-day.yaml))

### Security use cases

- **[Secure Code Review](use-cases/secure-code-review.md)** — a finder proposes
  candidate vulns; an independent verifier confirms each with proof.
  ([`examples/secure-code-review.yaml`](../examples/secure-code-review.yaml))
- **[Threat Modeler](use-cases/threat-modeler.md)** — STRIDE / abuse-case generation
  from an architecture spec.
  ([`examples/threat-modeler.yaml`](../examples/threat-modeler.yaml))
- **[Vulnerability Triage](use-cases/vuln-triage.md)** — CVE / dependency scan →
  context-aware risk rank → patch plan.
  ([`examples/vuln-triage.yaml`](../examples/vuln-triage.yaml))
- **[Secrets Scanner](use-cases/secrets-scanner.md)** — detect hardcoded secrets and
  draft rotation + secrets-manager remediation (never echoes the secret).
  ([`examples/secrets-scanner.yaml`](../examples/secrets-scanner.yaml))
- **[Compliance Mapper](use-cases/compliance-mapper.md)** — map controls to SOC 2 /
  GDPR / HIPAA / ISO 27001 into a coverage matrix.
  ([`examples/compliance-mapper.yaml`](../examples/compliance-mapper.yaml))

### Orchestration showcases

- **[Multi-Provider Review](use-cases/multi-provider-review.md)** — non-redundant PR
  review across Claude / Codex / Gemini (different lenses, no overlap).
  ([`examples/multi-provider-review.yaml`](../examples/multi-provider-review.yaml))
- **[Research → Plan → Implement](use-cases/rpi-pipeline.md)** — an RPI handoff loop:
  each stage hands a structured artifact to the next.
  ([`examples/rpi-pipeline.yaml`](../examples/rpi-pipeline.yaml))
- **[Spec-to-Ship](use-cases/spec-to-ship.md)** — the full spec → build → test →
  review → release-gate pipeline.
  ([`examples/spec-to-ship.yaml`](../examples/spec-to-ship.yaml))
- **[Red Team / Blue Team](use-cases/red-team-blue-team.md)** — a leashed red attacker
  vs a blue defender, scored by a neutral keeper.
  ([`examples/red-team-blue-team.yaml`](../examples/red-team-blue-team.yaml))
- **[Adjudicated Debate](use-cases/adjudicated-debate.md)** — multi-model deliberation
  with a neutral judge (distinct from the simple `debate` example).
  ([`examples/adjudicated-debate.yaml`](../examples/adjudicated-debate.yaml))

### Domain-expert use cases

- **[FP&A Analyst](use-cases/fp-and-a-analyst.md)** — ledger variance analysis + a
  CFO-ready forecast narrative.
  ([`examples/fp-and-a-analyst.yaml`](../examples/fp-and-a-analyst.yaml))
- **[Literature Review](use-cases/literature-review.md)** — multi-source scientific
  synthesis with a citation graph ("no claim without a source").
  ([`examples/literature-review.yaml`](../examples/literature-review.yaml))
- **[Legal Discovery](use-cases/legal-discovery.md)** — eDiscovery triage with a
  privilege checker that flags, never leaks.
  ([`examples/legal-discovery.yaml`](../examples/legal-discovery.yaml))
- **[Grant Writer](use-cases/grant-writer.md)** — grant proposal drafting + a
  simulated skeptical reviewer loop.
  ([`examples/grant-writer.yaml`](../examples/grant-writer.yaml))
- **[Patent Analyzer](use-cases/patent-analyzer.md)** — patent landscape / prior-art
  search and infringement brief.
  ([`examples/patent-analyzer.yaml`](../examples/patent-analyzer.yaml))
- **[Clinical Evidence Synthesizer](use-cases/clinical-evidence-synthesizer.md)** —
  clinical-trial evidence for a PICO question, graded by strength.
  ([`examples/clinical-evidence-synthesizer.yaml`](../examples/clinical-evidence-synthesizer.yaml))
- **[Data Migration Auditor](use-cases/data-migration-auditor.md)** — audit a
  completed data migration's source↔target fidelity.
  ([`examples/data-migration-auditor.yaml`](../examples/data-migration-auditor.yaml))

## Security note

The UI and any agent running `--dangerously-skip-permissions`/`--yolo` are a control
plane. Never bind `serve` to `0.0.0.0` without a token, and prefer a private tunnel
(Tailscale / SSH) over a raw public exposure. Treat agent `command` strings and
`telegram.bot_token` as secrets — keep them out of git.
