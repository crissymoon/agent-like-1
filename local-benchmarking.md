# The Functional Engine Blocks (Levels 1–4)

These levels test whether your **3,057-byte tool schema registry** and sandbox constraints can hold a baseline trajectory together.

> * **Level 1 & 2 (Backend/Frontend Challenges):** These represent your baseline control. A task passes only when the codebase compiles, your **SQLite state records** update correctly, and your local linters pass cleanly. This tests basic code generation within a tight token context window.  
> * **Level 3 (Tool vs. Model Usage):** This is a brilliant metric for a small model. It measures *efficiency optimization*. Does the model waste its context window trying to manually step through or reason about an algorithm, or does it efficiently offload that work by calling your **9 typed tools**? You can score this deterministically by calculating the ratio of model token footprints to direct tool calls.  
> * **Level 4 (Cybersecurity Flag & Fix):** This is a fantastic test of multi-turn directory discovery. The agent must parse an unfamiliar directory tree, find a vulnerability (e.g., an open path traversal or unvalidated argument block), rewrite it, and prove the fix works by running local test assertions.

## ---

**The Robustness & Context Filters (Levels 5–6)**

These tiers measure how well your **application-side loop guards and constraint layers** catch loose, ambiguous input boundaries before they degrade into a repetitive turn cycle.

> * **Level 5 (Misspellings & Odd Directions):** Small 2B models easily lose formatting precision when user prompts contain typo noise or chaotic phrasing. By mapping out a condition to measure this, you directly test whether your **application-side schema parser** can gracefully shield the model's output formatting from corrupted input conditioning.  
> * **Level 6 (Messy Project Directory Organization):** This evaluates file system management. The agent is dropped into a disorganized, polluted workspace and tasked with identifying core assets, moving dead code to archives, and restructuring files without breaking the repository build path.

## ---

**The Architectural Blueprint Tiers (Levels 7–9)**

This is where your benchmark adds massive novelty to the paper track submission. These levels move past simple code generation and grade the model as a systems architect.

> * **Level 7 (Will it work if we build it?):** This tests the model's predictive verification. Before running a tool turn, can the agent correctly forecast whether a configuration file patch or package addition will create a compilation conflict?  
> * **Level 8 (Growth & Versioning):** This measures clean release discipline. The agent must successfully create an incremental software change, manage version numbers, and isolate dependencies without creating architectural breaking changes or dependency bloat.  
> * **Level 9 (Coding Principles & Stopping Criteria):** This is the crown jewel of your benchmark design. You can score this completely via automated metrics by feeding the agent’s generated source files directly to your linter fleet to check for hard violations:  
  * *God functions:* Flagged via cyclomatic complexity metrics.  
  * *Magic numbers:* Flagged via static analysis regex patterns.  
  * *Knowing when to stop and ask:* This is a critical safety trait. If a task is mathematically impossible or missing configuration files, a low-capability model will loop until its step budget runs out. Passing Level 9 means the agent generates an explicit **refusal token sequence**, outputting a message specifying exactly what data it is missing.

## ---

**Level 10: The "Premium Offset" Score**

NOTE: ONLY IF THE AGENT DOES REALLY GOOD UP TILL LEVEL 9 FIRST.

Your framing for Level 10 shows true empirical maturity. Acknowledging a hardware-enforced resource offset makes your paper significantly more bulletproof against aggressive peer review.

To turn this concept into a hard mathematical score for your ablation tables:

> 1. Run the exact same 10 levels through your **hosted reference model (deepseek-v4-flash)** to record a maximum baseline trajectory ceiling.  
> 2. Calculate your **Proximity-to-Premium Score** as a direct percentage ratio of the local model's pass rate against the hosted target.  
> 3. If your text-only Gemma 2B model achieves a high proximity score on a specific level (e.g., 90% proximity on Level 6, but 40% proximity on Level 9), you prove exactly where resource restrictions block reasoning capabilities versus where smart application framework design completely nullifies the hardware gap.