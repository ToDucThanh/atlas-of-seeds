"""Check that assets/index.html is still the installed Flet's index.html plus Atlas's meta block.

Flet serves assets/index.html in place of its own page (and patches it at startup), which is how the
site gets its title and link-preview tags. That makes the file a copy of Flet's template, so after a
Flet upgrade it could quietly go stale. This compares the two with Atlas's block taken out.

To refresh it: copy the new template over assets/index.html, then put the atlas:meta block back where
the template's description meta was, and delete the template's own title and apple-mobile-web-app-title.
"""

import difflib
import re
import sys
from pathlib import Path

import flet_web

ROOT = Path(__file__).resolve().parent.parent
OURS = ROOT / "assets" / "index.html"
TEMPLATE = Path(flet_web.__file__).parent / "web" / "index.html"

# Lines of Flet's template that the atlas:meta block replaces.
REPLACED = re.compile(r'^\s*(<title>|<meta name="(description|apple-mobile-web-app-title)")')
BLOCK = re.compile(r"^\s*<!-- atlas:meta\b.*?^\s*<!-- /atlas:meta -->\n", re.DOTALL | re.MULTILINE)


def main() -> int:
    ours, n = BLOCK.subn("", OURS.read_text())
    if n != 1:
        print(f"{OURS}: expected exactly one atlas:meta block, found {n}")
        return 1
    template = "".join(line for line in TEMPLATE.read_text().splitlines(keepends=True) if not REPLACED.match(line))
    if ours == template:
        print(f"{OURS.relative_to(ROOT)} matches the installed flet_web/web/index.html plus the atlas:meta block")
        return 0
    print(f"{OURS.relative_to(ROOT)} has drifted from the installed Flet's index.html:\n")
    sys.stdout.writelines(difflib.unified_diff(template.splitlines(keepends=True), ours.splitlines(keepends=True),
                                               "flet_web/web/index.html", "assets/index.html (without atlas:meta)"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
