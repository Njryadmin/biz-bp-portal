"""Batch-fix all BFF routes: add cookie forwarding.

Pattern to replace:
    export async function GET() {
        const base = ...
        try {
            const res = await fetch(`${base}/api/...`, { cache: "no-store" });

With:
    import type { NextRequest } from "next/server";
    export async function GET(request: NextRequest) {
        const base = ...
        try {
            const res = await fetch(`${base}/api/...`, {
                cache: "no-store",
                headers: { cookie: request.headers.get("cookie") ?? "" },
            });
"""
import os, re, pathlib

ROOT = pathlib.Path(r"C:\Users\mozzi\.mavis\workspace\fin-bp-portal\apps\web\app\api")
# Skip auth/* (already fixed) and registry (already fixed)
SKIP = {"registry", "auth"}

count = 0
for f in ROOT.rglob("route.ts"):
    rel = f.relative_to(ROOT)
    top = rel.parts[0] if len(rel.parts) > 1 else ""
    if top in SKIP:
        continue
    text = f.read_text(encoding="utf-8")
    orig = text

    # Add import if missing
    if "import type { NextRequest }" not in text:
        text = text.replace(
            "import { NextResponse } from \"next/server\";",
            "import { NextResponse } from \"next/server\";\nimport type { NextRequest } from \"next/server\";",
        )

    # Pattern 1: export async function GET() {  (no args)
    # Replace with GET(request: NextRequest) {
    text = re.sub(
        r"export async function (GET|POST|PUT|DELETE|PATCH)\(\)\s*\{",
        r"export async function \1(request: NextRequest) {",
        text,
    )

    # Pattern 2: fetch with cache: "no-store" only (no headers)
    text = re.sub(
        r"await fetch\(`\$\{base\}(/api/[^`]+)`,\s*\{\s*cache:\s*\"no-store\"\s*\}\);",
        lambda m: (
            f"await fetch(`${{base}}{m.group(1)}`, {{\n"
            f"                cache: \"no-store\",\n"
            f"                headers: {{ cookie: request.headers.get(\"cookie\") ?? \"\" }},\n"
            f"            }});"
        ),
        text,
    )

    # Pattern 3: fetch with body (POST/PUT/DELETE) - no cache but has headers
    # Look for: await fetch(`${base}/api/...`, {
    #                 method: "POST",
    #                 headers: { "Content-Type": "application/json" },
    #                 body: ...
    #             });
    text = re.sub(
        r"(await fetch\(`\$\{base\}(/api/[^`]+)`,\s*\{\s*method:\s*\"(POST|PUT|DELETE|PATCH)\",\s*headers:\s*\{\s*\"Content-Type\":\s*\"application/json\"\s*\},)",
        r"\1\n                headers: { \"Content-Type\": \"application/json\", cookie: request.headers.get(\"cookie\") ?? \"\" },",
        text,
    )

    if text != orig:
        f.write_text(text, encoding="utf-8")
        count += 1
        print(f"  fixed: {rel}")

print(f"\nTotal fixed: {count} files")
