"""List all routes including those in sub-routers."""
from app.main import create_app

app = create_app()
# Drill into _IncludedRouter
def walk(routes, depth=0, prefix=""):
    for r in routes:
        if hasattr(r, "path") and hasattr(r, "methods"):
            print("  " * depth + f"{r.path} {sorted(r.methods)}")
        if hasattr(r, "original_router"):
            # _IncludedRouter; recurse into its original_router
            walk(r.original_router.routes, depth + 1)
        elif hasattr(r, "routes") and depth < 3:
            walk(r.routes, depth + 1)

walk(app.routes)
