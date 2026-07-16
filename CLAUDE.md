Guidelines for writing good code for a developer

1. Choose clean code over clever code.
2. Write object oriented code as much as possible.
3. Keep function sizes small, ideally 10 lines.
4. Try and keep files between 100 and 300 lines.
5. Don't keep too many files in a folder or module. Try and keep it under 15.
6. Avoid abbreviations.
7. Use standard API as much as possible.
8. Reuse. Write as little code as possible.
9. Use Frappe UI, espresso design system for UI styling.
10. Always write tests, and make sure they work.
11. Build the minimum working app, then iterate towards your goals.
12. Keep the verbosity less in new changes (inline comments, docstrings etc). 
    Explain only what's absolutely needed in inline comments.
    Actual changes explanation can be part of commit message.
13. For a no-argument method that just computes/returns one value (a noun,
    e.g. nginx_version), use @property. For everything else (takes
    arguments, or does multi-step work), name it get_<what-it-returns>()
    so the name alone explains what it does, e.g. get_commit_sha().
14. Default to public (no leading underscore). Mark something private
    only when it's a genuinely internal/weird implementation detail
    (raw format parsing, security-sensitive validation, OS/plumbing
    internals) that callers should never reach for directly. Don't
    privatize a method just because it currently has one caller.
15. Don't split code into more functions/methods than necessary. Before
    extracting a helper, check it's reused or non-trivial enough to
    earn its own name — a single-use one-liner usually reads better
    inlined at its call site.
16. Name boolean-returning properties/methods with an is_/has_ prefix
    (e.g. is_workload_running, has_passwordless_sudo), never a bare
    verb or adjective (e.g. not workload_running). This applies even
    when the value can be None for "unknown" — the name still describes
    the yes/no question being answered.
