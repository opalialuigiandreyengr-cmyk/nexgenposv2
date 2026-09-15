"""Static sanity check for the re-skinned CSS files (read-only diagnostic)."""
import re

FILES = [
    r"c:\Users\Marketing Head\Desktop\POS\POS_V2\static\css\pos\order_details.css",
    r"c:\Users\Marketing Head\Desktop\POS\POS_V2\static\css\pos\settlement.css",
    r"c:\Users\Marketing Head\Desktop\POS\POS_V2\static\css\pos\orders.css",
    r"c:\Users\Marketing Head\Desktop\POS\POS_V2\static\css\tokens.css",
]

# Tokens that exist after the audit (tokens.css + njX)
DEFINED = {
    "bg-main", "bg-secondary", "bg-card", "bg-inset", "bg-page", "bg-elev", "bg-subtle",
    "bg-hover", "color-dark", "color-dark-secondary", "color-light", "color-light-secondary",
    "color-muted", "text-main", "text-secondary", "text-light", "text-primary", "text-muted",
    "text-inverse", "font-color", "nav-bg", "nav-border", "nav-text", "nav-text-hover",
    "nav-active", "form-bg-input", "form-border", "form-border-hover", "form-focus-ring",
    "form-text", "form-placeholder", "color-primary", "color-accent", "color-success",
    "color-warning", "color-error", "color-info", "color-danger", "color-grey",
    "color-primary-opacity", "color-primary-shadow", "color-link", "color-link-hover",
    "accent-primary", "accent-primary-hover", "accent-primary-alpha", "accent-success",
    "accent-warning", "accent-info", "accent-info-alpha", "accent-warning-alpha",
    "shadow-sm", "shadow-md", "shadow-lg", "ring-focus", "ease-out", "dur-fast",
    "dur-base", "dur-emphasis", "transition-fast", "border-color", "border-default",
    "border-subtle", "border-hover", "border-strong", "font-sans", "font-heading",
    "font-mono", "radius-sm", "radius-md", "radius-lg", "sb-thumb", "sb-thumb-strong",
}

ok = True
for path in FILES:
    src = open(path, encoding="utf-8").read()
    name = path.split("\\")[-1]
    # brace balance
    if src.count("{") != src.count("}"):
        print(f"{name}: UNBALANCED braces {{={src.count('{')} }}={src.count('}')}")
        ok = False
    # undefined token references
    used = set(re.findall(r"var\(--([a-zA-Z0-9-]+)", src))
    dead = sorted(t for t in used if t not in DEFINED)
    if dead:
        print(f"{name}: unresolved tokens -> {dead}")
        ok = False
    if ok:
        print(f"{name}: braces balanced, all var() tokens resolve")

print("RESULT:", "OK" if ok else "DEFECTS FOUND")
