---
name: challenge-problem-formatter
description: "Convert a natural-language coding problem into the files required by this repository's /challenge add command: statement.md, test-inputs.json, expected-outputs.json, and Python, JavaScript, C++, and Java boilerplates with the {{SOLUTION}} marker. Use when creating, formatting, or validating a new challenge problem package."
---

# Challenge Problem Formatter

Turn a problem description into a ready-to-upload challenge package for this bot. Do not change bot code; create or update only the requested challenge files.

## Workflow

1. Read the problem description and identify the required function name, parameters, return value, input format, output format, constraints, and edge cases.
2. If any of those are ambiguous, ask for clarification before generating tests or drivers. Do not silently invent semantics.
3. Create a package directory, normally `bot/docs/assets/challenges/<slug>/`, containing:

   ```text
   statement.md
   test-inputs.json
   expected-outputs.json
   python-boilerplate.py
   javascript-boilerplate.js
   cpp-boilerplate.cpp
   java-boilerplate.java
   ```

4. Add every sample input/output pair from `statement.md` inside the entry-point function of each boilerplate as commented, runnable-looking code. Keep the examples commented so they do not affect judging.
5. Validate the package before handing it off. The two JSON files must be arrays of equal length, every input must be a string, and every expected output must correspond to the input at the same index.

## File requirements

### `statement.md`

Include the title, concise description, function signature, input format, output format, constraints, examples, and important edge cases. Keep the statement consistent with the driver files. Do not put the solution implementation in the statement.

### Test JSON files

Use JSON arrays of stdin and stdout strings:

```json
["4\n2 7 11 15\n9\n", "2\n3 3\n6\n"]
```

`test-inputs.json` and `expected-outputs.json` must have the same number of entries. Include normal cases, boundary cases, duplicate/negative/empty cases where applicable, and at least one case that distinguishes a correct solution from a plausible incorrect one. Never include malformed inputs unless the statement explicitly defines error handling.

### Boilerplates

Each driver must contain the literal marker `{{SOLUTION}}` exactly where the submitted function implementation belongs. The user submits only that function implementation; do not require a `main`, class wrapper, imports, or input parser from the user.

Immediately above `{{SOLUTION}}`, add a commented instruction that includes the exact function signature the user must submit. Use the language's comment syntax (`#` for Python, `//` for JavaScript/C++/Java), for example:

```text
// Submit only this function; do not submit imports, input/output handling, or main():
// long long solve(int N, vector<int>& numbers) {
//     // Write your logic here
// }
```

The signature comment must match the generated driver exactly, including function name, parameter names and types, return type, references, array syntax, and Java modifiers. Keep the instruction and signature commented so they cannot affect compilation or judging.

All drivers must parse the same stdin format described in `statement.md`, call equivalent language-specific functions, and serialize output exactly as described. Keep language-specific syntax outside the submitted function marker.

Each driver must have an entry-point function: `main()` plus the usual Python guard, `main()` followed by its JavaScript call, C++ `main()`, or Java `public static void main(String[] args)`. Put the commented sample cases inside that entry point.

Represent each sample in the language's normal executable data syntax while keeping every line commented. For example, use a commented triple-quoted string and expected-output string in Python, a commented template literal and expected-output string in JavaScript, and commented escaped strings or stream setup in C++/Java. Include both sample input and expected output, preserving the exact whitespace and newlines from the JSON/statement.

Place `{{SOLUTION}}` at top level for Python and JavaScript, after includes/imports for C++, and inside `Main` for Java. Keep function names and argument order equivalent across languages.

## Validation checklist

- Parse both JSON files and assert equal test counts.
- Check every boilerplate contains exactly one `{{SOLUTION}}` marker.
- Check every boilerplate contains the statement's sample input/output pairs as comments inside its entry point.
- Run the reference solution against every test and compare normalized output to `expected-outputs.json`.
- Compile/run each driver when the corresponding toolchain is available; report skipped checks.
- Keep within the bot limits: 30 tests, 100,000 bytes per statement/boilerplate, and 500,000 bytes per test file.
- Report generated files and any assumptions clearly.
