"""Check inline Markdown links to local files without requesting external URLs."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]\n]*\]\((<[^>\n]+>|[^\s)]+)(?:\s+[^)]*)?\)")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def without_fenced_code(content: str) -> str:
    """Remove fenced code examples so illustrative paths do not become link errors."""
    lines: list[str] = []
    fence_character = ""
    fence_length = 0
    for line in content.splitlines():
        match = FENCE.match(line)
        if not fence_character:
            if match:
                marker = match.group(1)
                fence_character = marker[0]
                fence_length = len(marker)
            else:
                lines.append(line)
        elif match:
            marker = match.group(1)
            if (
                marker[0] == fence_character
                and len(marker) >= fence_length
                and not line[match.end() :].strip()
            ):
                fence_character = ""
                fence_length = 0
    return "\n".join(lines)


def main() -> int:
    """Report missing repository targets while skipping remote URLs and anchors."""
    files = [
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        *sorted((ROOT / "docs").rglob("*.md")),
    ]
    errors: list[str] = []
    checked = 0
    for source in files:
        if not source.is_file():
            errors.append(f"{source.relative_to(ROOT)}: missing Markdown source")
            continue
        content = without_fenced_code(source.read_text(encoding="utf-8"))
        for match in LINK.finditer(content):
            target = match.group(1).strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            checked += 1
            resolved = (source.parent / unquote(parsed.path)).resolve()
            if not resolved.is_relative_to(ROOT) or not resolved.exists():
                errors.append(
                    f"{source.relative_to(ROOT)}: missing local target {target}"
                )
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Checked {checked} local links in {len(files)} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
