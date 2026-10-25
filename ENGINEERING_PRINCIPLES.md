# Engineering Principles: Simplicity First

## Goal

Build the simplest correct solution that meets today's requirements and that a human developer can understand, debug, and maintain without relying on an AI agent. Prefer clarity over cleverness, not brevity over correctness.

## 1. Before Coding

- Understand the goal, constraints, and how to verify success before choosing an implementation.
- For non-trivial work, briefly identify the simplest viable approach and important tradeoffs. Skip elaborate plans for routine changes.
- Ask questions only when uncertainty would materially change the result. Otherwise state a reasonable assumption and proceed.
- Check existing conventions before introducing new patterns. In an empty repository, choose a small, conventional starting point.

## 2. Starting From Scratch

- Build the smallest working application first, with a clear entry point and one complete, usable flow.
- Prefer standard framework features and familiar patterns over custom infrastructure.
- Keep the initial file and folder structure shallow. Add structure only when current code benefits from it.
- Do not build speculative features, extension systems, generic service layers, queues, caching, or elaborate configuration for hypothetical future needs.
- Add dependencies only when they solve a real problem. Prefer trusted libraries over custom implementations for security-sensitive or genuinely difficult tasks.

## 3. Growing and Changing the Project

- Deliver features in small, working slices. Let architecture emerge from actual requirements.
- Follow existing naming, patterns, and organization unless there is a concrete benefit to changing them.
- Introduce helpers and abstractions when they meaningfully reduce repeated logic or clarify a real concept, not merely because reuse is possible someday.
- Keep related logic close together when it aids comprehension. Split code by responsibility, not arbitrary line-count rules.
- Make focused changes. Avoid unrelated rewrites, formatting churn, or drive-by refactors.
- Clean up unused code created by your changes; flag unrelated existing problems separately.

## 4. Code People Can Understand

- Prefer explicit control flow, descriptive names, straightforward functions, and simple data structures.
- Avoid clever one-liners, deeply nested conditionals, excessive indirection, and complicated types where direct code is clearer.
- Do not introduce classes, factories, repositories, interfaces, or custom hooks by default. Use them when their benefit is concrete.
- Make data flow and side effects easy to trace. Optimize for readability, not minimum line count.
- Use comments to explain non-obvious decisions and constraints, not to restate what the code says.

## 5. Quality Without Ceremony

- Handle realistic errors and trust boundaries. Never sacrifice security, accessibility, data integrity, or correctness for fewer lines.
- Avoid defensive machinery for impossible or speculative scenarios.
- Add tests in proportion to risk, especially around business logic, edge cases, and bug fixes.
- Preserve behavior when refactoring and run relevant checks. Never claim tests passed if they were not run.
- After substantial changes, review only the changed code for unnecessary complexity. Don't simplify already-clear code just for the sake of it.

## 6. Keep the Developer in the Loop

- Assume the developer wants to understand and maintain the code independently.
- When adding an unfamiliar library, pattern, or architectural layer, briefly explain what it does, why it is needed now, and why a simpler choice would not work.
- Avoid unexplained jargon. For non-trivial changes, summarize where the logic starts, how data flows, and how to verify it.
- Keep explanations concise by default; go deeper when asked.



Before finishing, ask: **Does each new moving part solve a problem we have today? Could someone unfamiliar with this feature follow and change it?** If not, simplify.
