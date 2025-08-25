# pr_reviewer/prompts.py

__all__ = ["build_prompts"]

def build_prompts(cfg: dict):
    """
    Returns (system, user, hint) prompt triplet based on cfg["output_format"].
    Default is HTML (fragment). Adds a 'Suggested Test Cases' section and colgroups to widen tables.
    """
    output_format = (cfg.get("output_format") or "html").lower().strip()

    if output_format == "html":
        system = (
            "You are a senior software engineer performing a rigorous code review of a GitHub PR unified diff. "
            "Return only valid HTML (no markdown). Be precise, concise, and actionable. Identify correctness, security, "
            "performance, concurrency, API/contract, error handling, logging, testing, and maintainability issues. "
            "Do not restate the entire diff; focus on material issues with specific lines/hunks, ignore the diff for generated code (code with generated package). "
            "In addition to the review, generate a prioritized set of SUGGESTED TEST CASES with concrete inputs, steps, and expected outcomes. "
            "Cover happy-path, negative, edge/boundary, error-handling, concurrency/race, performance, and security scenarios as applicable."
        )

        user = """
&lt;section&gt;
  &lt;h2&gt;Change Requirement&lt;/h2&gt;
  &lt;p&gt;&lt;strong&gt;High-Level Summary:&lt;/strong&gt; &lt;one to two sentences describing the intent and purpose of this change and be precise&gt;&lt;/p&gt;

  &lt;h3&gt;Key Points&lt;/h3&gt;
  &lt;ul&gt;
    &lt;li&gt;&lt;Criterion 1&gt;&lt;/li&gt;
    &lt;li&gt;&lt;Criterion 2&gt;&lt;/li&gt;
    &lt;li&gt;&lt;Criterion 3&gt;&lt;/li&gt;
  &lt;/ul&gt;

  &lt;h2&gt;Change Summary by File&lt;/h2&gt;
  &lt;ul&gt;
    &lt;li&gt;&lt;strong&gt;&lt;file1&gt;&lt;/strong&gt;
      &lt;ol&gt;
        &lt;li&gt;&lt;Step 1: what changed and why it matters&gt;&lt;/li&gt;
        &lt;li&gt;&lt;Step 2: what changed and why it matters&gt;&lt;/li&gt;
      &lt;/ol&gt;
    &lt;/li&gt;
    &lt;li&gt;&lt;strong&gt;&lt;file2&gt;&lt;/strong&gt;
      &lt;ol&gt;&lt;li&gt;...&lt;/li&gt;&lt;/ol&gt;
    &lt;/li&gt;
  &lt;/ul&gt;

  &lt;h2&gt;Review Table&lt;/h2&gt;
  &lt;table&gt;
    &lt;colgroup&gt;
      &lt;col style="width:18%" /&gt;
      &lt;col style="width:8%" /&gt;
      &lt;col style="width:12%" /&gt;
      &lt;col style="width:12%" /&gt;
      &lt;col style="width:25%" /&gt;
      &lt;col style="width:25%" /&gt;
    &lt;/colgroup&gt;
    &lt;thead&gt;
      &lt;tr&gt;
        &lt;th&gt;File&lt;/th&gt;
        &lt;th&gt;Line No.&lt;/th&gt;
        &lt;th&gt;Category&lt;/th&gt;
        &lt;th&gt;Code Change Risk (LOW/MEDIUM/HIGH)&lt;/th&gt;
        &lt;th&gt;Observation&lt;/th&gt;
        &lt;th&gt;Recommendation&lt;/th&gt;
      &lt;/tr&gt;
    &lt;/thead&gt;
    &lt;tbody&gt;
      &lt;tr&gt;
        &lt;td&gt;path/to/file&lt;/td&gt;&lt;td&gt;L87&lt;/td&gt;&lt;td&gt;Correctness&lt;/td&gt;&lt;td&gt;HIGH&lt;/td&gt;
        &lt;td&gt;Wrong null check&lt;/td&gt;&lt;td&gt;Add explicit None check&lt;/td&gt;
      &lt;/tr&gt;
    &lt;/tbody&gt;
  &lt;/table&gt;

  &lt;h2&gt;Suggested Test Cases&lt;/h2&gt;
  &lt;table&gt;
    &lt;colgroup&gt;
      &lt;col style="width:6%" /&gt;
      &lt;col style="width:14%" /&gt;
      &lt;col style="width:8%" /&gt;
      &lt;col style="width:16%" /&gt;
      &lt;col style="width:16%" /&gt;
      &lt;col style="width:26%" /&gt;
      &lt;col style="width:18%" /&gt;
      &lt;col style="width:10%" /&gt;
    &lt;/colgroup&gt;
    &lt;thead&gt;
      &lt;tr&gt;
        &lt;th&gt;ID&lt;/th&gt;
        &lt;th&gt;Title&lt;/th&gt;
        &lt;th&gt;Type&lt;/th&gt;
        &lt;th&gt;Area / File&lt;/th&gt;
        &lt;th&gt;Preconditions / Setup&lt;/th&gt;
        &lt;th&gt;Steps (numbered)&lt;/th&gt;
        &lt;th&gt;Expected Result&lt;/th&gt;
        &lt;th&gt;Priority (P0/P1/P2)&lt;/th&gt;
      &lt;/tr&gt;
    &lt;/thead&gt;
    &lt;tbody&gt;
      &lt;tr&gt;
        &lt;td&gt;TC-001&lt;/td&gt;
        &lt;td&gt;Happy path example&lt;/td&gt;
        &lt;td&gt;Functional&lt;/td&gt;
        &lt;td&gt;path/to/file.py&lt;/td&gt;
        &lt;td&gt;Valid config; service available&lt;/td&gt;
        &lt;td&gt;1) ... 2) ... 3) ...&lt;/td&gt;
        &lt;td&gt;Returns 200 with payload X&lt;/td&gt;
        &lt;td&gt;P0&lt;/td&gt;
      &lt;/tr&gt;
    &lt;/tbody&gt;
  &lt;/table&gt;

  &lt;h2&gt;Overall Verdict&lt;/h2&gt;
  &lt;p&gt;&lt;short paragraph on readiness and risk&gt;&lt;/p&gt;
&lt;/section&gt;
""".strip()

        hint = "Return a single HTML fragment only (no <html> wrapper). Include the 'Suggested Test Cases' table."
        return system, user, hint

    else:
        system = (
            "You are a senior software engineer performing a rigorous code review of a GitHub PR unified diff. "
            "Always return output in structured Markdown. In addition to review findings, produce a prioritized list "
            "of SUGGESTED TEST CASES with concrete inputs, steps, and expected outcomes, covering happy-path, negative, "
            "edge/boundary, error-handling, concurrency/race, performance, and security scenarios where applicable."
        )

        user = (
            "Using the unified diff below, produce:\n"
            "0) Change Requirement — high-level summary and acceptance criteria.\n"
            "1) Change Summary by File — list step-wise bullets per file: what changed and why it matters.\n"
            "2) Review Table — a markdown table with columns:\n"
            "   File | Location | Category | Severity | Comment | Suggested fix\n"
            "3) Suggested Test Cases — a markdown table with concrete steps and expected results.\n"
            "4) Overall Verdict — short paragraph on readiness.\n"
        )

        hint = (
            "OUTPUT FORMAT (strict):\n\n"
            "## Change Requirement\n"
            "**High-Level Summary:** <one to two sentences> Be precise\n\n"
            "### Acceptance Criteria\n"
            "- Criterion 1\n"
            "- Criterion 2\n"
            "- Criterion 3\n\n"
            "## Change Summary by File\n"
            "- **<file1>**\n"
            "  1) <Step>\n"
            "  2) <Step>\n"
            "- **<file2>**\n"
            "  1) ...\n\n"
            "## Review Table\n"
            "| File | Location | Category | Severity | Comment | Suggested fix |\n"
            "|------|----------|----------|----------|---------|---------------|\n"
            "| path/to/file | L87 | Correctness | HIGH | Wrong null check | Add explicit None check |\n\n"
            "## Suggested Test Cases\n"
            "| ID | Title | Type | Area/File | Preconditions | Steps | Expected Result | Priority |\n"
            "|----|-------|------|-----------|---------------|-------|-----------------|----------|\n"
            "| TC-001 | Happy path example | Functional | path/to/file.py | Valid config; service up | 1) ... 2) ... | Returns 200 with payload X | P0 |\n\n"
            "## Overall Verdict\n"
            "<short paragraph>\n"
        )

        return system, user, hint
