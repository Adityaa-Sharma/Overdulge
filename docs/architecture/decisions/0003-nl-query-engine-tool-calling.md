# ADR-0003: NL query engine — constrained tool-calling over PostgREST, not text-to-SQL; numeric grounding enforcement

Status: Accepted
Context issue: #5 (FR-4 Natural-language query engine)

## Context

FR-4.1 asks for a LangChain agent that "translates NL questions into queries
over the canonical schema." BRD §3 fixes the datastore access path: the
backend talks to Supabase only over PostgREST (`core/db.py`), never raw SQL —
Pyodide/Workers cannot assume `supabase-py`/a direct Postgres driver works.
That constraint, plus NFR-4 ("LLM numeric outputs must be DB-computed, not
model recall") and FR-4.2 (RLS-enforced isolation, adversarial-prompt-proof),
rules out the obvious "LLM writes SQL, backend executes it" design before it
starts: there is no SQL execution path to hand it, and even if there were,
executing LLM-authored SQL against Postgres is an injection surface no static
guard can fully close.

## Decision

### Tool-calling agent over a fixed, typed tool set — never free-form SQL
`backend/app/llm/tools.py` exposes a small, fixed set of parameterized
Python functions, each a thin wrapper over `core/db.py`'s `user_client(jwt)`
(user-JWT-forwarding mode, ADR-0002 — RLS is the isolation mechanism, so no
tool ever takes or forwards a `user_id` parameter):

- `spend_total(start_date, end_date, platform=None, category=None,
  item_name_contains=None) -> {total_paise, order_count}`
- `spend_by_category(start_date, end_date) -> [{category, total_paise}]`
- `spend_by_platform(start_date, end_date) -> [{platform, total_paise}]`
- `top_items(start_date, end_date, limit=10) -> [{name, order_count,
  total_paise}]`
- `data_coverage() -> {earliest_order_at, latest_order_at, platforms_synced}`

Every tool queries only `orders`/`order_items`, filters `is_cancelled=false`
by default (BRD §2.9/FR-2.3), and sums `grand_total_paise` — never
recomputed from item components (BRD §2.8). `backend/app/llm/agent.py` binds
these as LangChain tools (`bind_tools`) to the chat model from
`init_chat_model` and runs a capped tool-calling loop (max 3 tool calls per
question) — a hard ceiling both for the <10s latency target (FR-4 AC-5) and
because an uncapped agentic loop against a Worker's execution limits is
itself an availability risk.

The agent can express almost every question in BRD's examples ("how much did
I spend on X since Y") as one or two calls into this set. Questions that
don't fit the tool set get the FR-4.3 fallback (below), not a wider tool —
widening the tool set is a deliberate SA-reviewed decision per question class
actually seen in use, not a default reflex.

### Numeric grounding: the agent never re-types the number
The model's own text after tool calls is not trusted as the source of the
answer's number. `agent.py` extracts the number from the structured tool
result (e.g. `total_paise`), formats it in code (`₹` + paise→rupee, BRD §2.5
convention), and only asks the model to produce the explanatory sentence
*around* a number it is given as a fait accompli in the prompt ("You
computed: total = ₹X across N orders. Write one sentence explaining this to
the user."). A post-generation guard scans the model's sentence for any
`₹`-prefixed or bare numeric token and rejects/regenerates once if it
diverges from the code-computed figure — this is a backstop, not the primary
mechanism; the primary mechanism is that the API response's `answer.amount`
field is always the raw tool output, and the frontend renders that field
directly rather than parsing it out of the model's prose (FR-4.1's "number +
explanation" are two separate response fields, not one blob of text).

### "I don't have enough data" vs. a true zero
A zero-row tool result is ambiguous: it could mean "you truly spent ₹0 on
this in range" or "Overdulge has no synced data for this range/platform at
all." `data_coverage()` disambiguates: if the question's date range has no
overlap with `[earliest_order_at, latest_order_at]` for any synced platform,
or the user has zero `linked_accounts`, the agent must return the FR-4.3
fallback message instead of a `₹0` answer. If coverage overlaps and the
specific query still returns zero, `₹0` is the correct, real answer and is
returned as such.

### Provider-agnostic model init — fixing SYSTEM.md's stale "Azure OpenAI" line
SYSTEM.md previously stated the LLM provider as fixed Azure OpenAI; BRD §3 is
explicit that the *current* provider is Groq (`GROQ_API_KEY`,
`init_chat_model("groq:<model>")`), selected at runtime via a single
`LLM_PROVIDER` setting so a later move to Azure OpenAI is config-only. That
was a drift between SYSTEM.md and BRD — BRD is the source of truth here; this
ADR corrects SYSTEM.md §1 in the same PR. `core/config.py` currently has only
`azure_openai_*` fields and no `llm_provider`/`groq_api_key`; the FR-4 task
that builds `llm/agent.py` must add those (and the matching `.env.example`
keys) since the agent cannot be constructed without them — this is not a new
feature, it's closing a gap the config layer never filled in because no
LLM-calling code existed yet.

## Consequences

- Widening what the query engine can answer means adding a new named tool
  function (reviewed, tested, RLS-safe by construction) — never loosening
  the agent into raw-query territory. This is slower than text-to-SQL but
  matches NFR-4 and FR-4.2's adversarial-isolation requirement structurally
  rather than by prompt-engineering discipline alone.
- `data_coverage()` is a shared dependency for every "not enough data" check
  across FR-4/FR-5/FR-6/FR-7 answers that state a number; future features
  needing the same distinction should reuse it rather than re-deriving
  coverage logic per feature.
- The 3-tool-call cap means genuinely multi-hop questions ("compare this
  month to last month by category") may need either a slightly larger cap or
  a dedicated comparison tool later — track as a follow-up if QA's regression
  surfaces it, not a reason to uncap now.
