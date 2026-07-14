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
- **[UI Guide](ui-guide.md)** — the `agentainer serve` HTTP control plane
  (observability, terminal snapshot, send-from-UI, availability toggle, dynamic
  reconcile) and its security invariants.
- **[Telegram Bridge](telegram-bridge.md)** — mirror agent mail to a Telegram chat
  so you stay reachable from your phone.

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

## Security note

The UI and any agent running `--dangerously-skip-permissions`/`--yolo` are a control
plane. Never bind `serve` to `0.0.0.0` without a token, and prefer a private tunnel
(Tailscale / SSH) over a raw public exposure. Treat agent `command` strings and
`telegram.bot_token` as secrets — keep them out of git.
