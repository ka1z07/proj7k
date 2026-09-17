"""
StaticAssetGuard: Verification guard ensuring 100% offline self-contained static assets.

SPEC-P2.4-04 / ADR-0010.
Detects any external CDN, remote font, external script, or network dependency
in static HTML, CSS, and JS assets.
"""

from pathlib import Path
import re
from typing import Dict, List, Set, Union


# Standard XML schema namespaces that do not trigger network fetch
XML_NAMESPACES: Set[str] = {
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/1999/xhtml",
}

# Regex to find external URLs (requiring slashes, e.g. http://, https://, or //cdn...)
EXTERNAL_URL_PATTERN = re.compile(
    r"""(?:https?:\/\/|(?<!:)\/\/(?:cdn|cdnjs|unpkg|fonts|ajax|code\.jquery|raw\.githubusercontent))\S+""",
    re.IGNORECASE,
)


class StaticAssetGuard:
    """
    Static analysis guard that traverses and verifies all static frontend assets,
    asserting that zero third-party CDN or remote assets are referenced.
    """

    @classmethod
    def scan_content(cls, content: str, filename: str = "") -> List[str]:
        """
        Scans a text content string for external CDN, stylesheet, or script references.
        Returns a list of violation messages.
        """
        violations: List[str] = []
        matches = EXTERNAL_URL_PATTERN.findall(content)
        for raw_match in matches:
            clean_match = raw_match.strip("'\"<>()[]{};,")
            if clean_match in XML_NAMESPACES:
                continue
            violations.append(
                f"[{filename or 'content'}] External network reference detected: '{clean_match}'"
            )
        return violations

    @classmethod
    def scan_file(cls, file_path: Union[str, Path]) -> List[str]:
        """Scans a single static file for external network references."""
        path = Path(file_path)
        if not path.is_file():
            return []
        try:
            content = path.read_text(encoding="utf-8")
            return cls.scan_content(content, filename=path.name)
        except UnicodeDecodeError:
            # Binary files like png/ico are not text-scanned
            return []

    @classmethod
    def scan_directory(cls, directory_path: Union[str, Path]) -> Dict[str, List[str]]:
        """
        Recursively scans a directory for all .html, .css, .js files.
        Returns a dict mapping relative file path to list of violations.
        """
        dir_path = Path(directory_path)
        report: Dict[str, List[str]] = {}
        if not dir_path.is_dir():
            return report

        for ext in ("*.html", "*.css", "*.js", "*.svg"):
            for file_path in dir_path.rglob(ext):
                rel_name = str(file_path.relative_to(dir_path))
                v = cls.scan_file(file_path)
                if v:
                    report[rel_name] = v
        return report

    @classmethod
    def assert_offline_safe_content(cls, content: str, filename: str = "") -> None:
        """Raises AssertionError if content contains any external network references."""
        violations = cls.scan_content(content, filename=filename)
        if violations:
            raise AssertionError(
                f"StaticAssetGuard offline safety check failed:\n" + "\n".join(violations)
            )

    @classmethod
    def assert_offline_safe(cls, path_or_dir: Union[str, Path]) -> None:
        """Raises AssertionError if a file or directory contains any external references."""
        target = Path(path_or_dir)
        if target.is_file():
            violations = cls.scan_file(target)
            if violations:
                raise AssertionError(
                    f"StaticAssetGuard offline safety check failed for {target}:\n"
                    + "\n".join(violations)
                )
        elif target.is_dir():
            report = cls.scan_directory(target)
            if report:
                msg_lines = [f"Offline safety check failed for directory {target}:"]
                for f, vs in report.items():
                    msg_lines.extend(vs)
                raise AssertionError("\n".join(msg_lines))
