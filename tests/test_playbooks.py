# tests/test_playbooks.py
"""
Tests for the deterministic playbook pipeline (no LLM, no API keys).

Coverage:
  - loader: YAML load, validation errors
  - runner: step states, degradation, executive summary counts, PDF flag
  - CLI: playbook subcommand arg parsing
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# Synthetic tool outputs that match EXTRACTOR_REGISTRY prefix conventions
# ---------------------------------------------------------------------------

_WHOIS_OUTPUT = """\
[+] Registrar: Example Registrar, LLC
[+] Emails: admin@example.com, tech@example.com
[+] Org: Example Corp
[+] Name Servers: ns1.example.com, ns2.example.com
"""

_DNS_OUTPUT = """\
[DNS] A: 93.184.216.34, 93.184.216.35, 93.184.216.36
[DNS] MX: mail.example.com (priority 10)
[DNS] NS: ns1.example.com, ns2.example.com
[DNS] TXT: v=spf1 include:_spf.example.com ~all
"""

_DORKS_OUTPUT = """\
https://www.google.com/search?q=site%3Aexample.com
https://www.google.com/search?q=filetype%3Apdf+site%3Aexample.com
"""

_DOMAIN_OUTPUT = """\
[+] sub1.example.com
[+] sub2.example.com
[+] sub3.example.com
[+] sub4.example.com
"""

_FOOTPRINT_OUTPUT = """\
[Footprint] URL: https://example.com/about
[Footprint] URL: https://example.com/contact
[Footprint] Domain: example.com
"""

_ALL_CANNED = {
    "search_whois": _WHOIS_OUTPUT,
    "search_dns": _DNS_OUTPUT,
    "generate_dorks": _DORKS_OUTPUT,
    "search_domain": _DOMAIN_OUTPUT,
    "search_footprint": _FOOTPRINT_OUTPUT,
}


def _make_tool_mocks(overrides: dict | None = None) -> dict:
    """Return AsyncMock functions for all 5 domain-recipe tools."""
    mocks = {tool: AsyncMock(return_value=output) for tool, output in _ALL_CANNED.items()}
    if overrides:
        mocks.update(overrides)
    return mocks


# ---------------------------------------------------------------------------
# TestPlaybookLoader
# ---------------------------------------------------------------------------


class TestPlaybookLoader:
    def test_loads_built_in_domain_recipe(self):
        from openosint.playbooks.loader import load_recipe

        recipe = load_recipe("domain")
        assert recipe.name == "domain"
        assert recipe.label == "Domain Investigation"
        assert recipe.target_type == "domain"
        assert len(recipe.steps) == 5
        assert [s.id for s in recipe.steps] == [
            "whois", "dns", "dorks", "subdomains", "footprint"
        ]

    def test_raises_on_unknown_recipe(self):
        import pytest

        from openosint.playbooks.loader import load_recipe

        with pytest.raises(ValueError, match="not found"):
            load_recipe("nonexistent_recipe_xyz")

    def test_raises_on_missing_label_field(self, tmp_path):
        import pytest

        from openosint.playbooks.loader import load_recipe

        recipe_file = tmp_path / "bad.yaml"
        recipe_file.write_text(
            "name: bad\ntarget_type: domain\nsteps:\n"
            "  - id: w\n    tool: search_whois\n    section: S\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="label"):
            load_recipe(str(recipe_file))

    def test_raises_on_duplicate_step_ids(self, tmp_path):
        import pytest

        from openosint.playbooks.loader import load_recipe

        recipe_file = tmp_path / "dup.yaml"
        recipe_file.write_text(
            "name: dup\nlabel: Dup\ntarget_type: domain\nsteps:\n"
            "  - id: same\n    tool: search_whois\n    section: S1\n"
            "  - id: same\n    tool: search_dns\n    section: S2\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="duplicate"):
            load_recipe(str(recipe_file))

    def test_raises_on_unknown_tool(self, tmp_path):
        import pytest

        from openosint.playbooks.loader import load_recipe

        recipe_file = tmp_path / "unk.yaml"
        recipe_file.write_text(
            "name: unk\nlabel: Unk\ntarget_type: domain\nsteps:\n"
            "  - id: x\n    tool: search_unicorn\n    section: S\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="search_unicorn"):
            load_recipe(str(recipe_file))


# ---------------------------------------------------------------------------
# TestPlaybookRunner
# ---------------------------------------------------------------------------


class TestPlaybookRunner:
    async def test_produces_markdown_with_all_sections(self, tmp_path):
        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        with patch.dict(TOOL_MAP, _make_tool_mocks()):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        content = report_path.read_text(encoding="utf-8")
        for heading in [
            "## WHOIS Registration",
            "## DNS Records",
            "## Google Dork URLs",
            "## Subdomain Enumeration",
            "## Search Engine Footprint",
        ]:
            assert heading in content, f"Missing: {heading}"

    async def test_report_written_to_reports_dir(self, tmp_path):
        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        with patch.dict(TOOL_MAP, _make_tool_mocks()):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        assert report_path.exists()
        assert report_path.suffix == ".md"
        assert report_path.parent == tmp_path

    async def test_executive_summary_present(self, tmp_path):
        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        with patch.dict(TOOL_MAP, _make_tool_mocks()):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        assert "## Executive Summary" in report_path.read_text(encoding="utf-8")

    async def test_executive_summary_counts_correct(self, tmp_path):
        """Counts are driven by EXTRACTOR_REGISTRY, not ad-hoc regex."""
        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        with patch.dict(TOOL_MAP, _make_tool_mocks()):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        content = report_path.read_text(encoding="utf-8")
        # search_domain step: 4 subdomains only (WHOIS nameservers excluded)
        assert "Subdomains of target:** 4" in content, content
        # _DNS_OUTPUT: 3 IPs on the A line → 3 IP entities
        assert "IP addresses found:** 3" in content, content
        # _WHOIS_OUTPUT: 2 emails → 2 EMAIL entities
        assert "Registrant emails:** 2" in content, content

    async def test_not_configured_step_renders_info_block(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
        monkeypatch.delenv("BRIGHTDATA_SERP_ZONE", raising=False)

        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        with patch.dict(TOOL_MAP, _make_tool_mocks()):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        content = report_path.read_text(encoding="utf-8")
        assert "ℹ️ Skipped" in content
        assert "⚠ Step error" not in content

    async def test_not_configured_footprint_includes_brightdata_note(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
        monkeypatch.delenv("BRIGHTDATA_SERP_ZONE", raising=False)

        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        with patch.dict(TOOL_MAP, _make_tool_mocks()):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        content = report_path.read_text(encoding="utf-8")
        assert "brightdata" in content.lower()

    async def test_not_configured_other_sections_still_present(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
        monkeypatch.delenv("BRIGHTDATA_SERP_ZONE", raising=False)

        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        with patch.dict(TOOL_MAP, _make_tool_mocks()):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        content = report_path.read_text(encoding="utf-8")
        for heading in [
            "## WHOIS Registration",
            "## DNS Records",
            "## Google Dork URLs",
            "## Subdomain Enumeration",
        ]:
            assert heading in content, f"Missing section after skip: {heading}"

    async def test_error_step_renders_error_block(self, tmp_path):
        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        mocks = _make_tool_mocks(
            overrides={"search_domain": AsyncMock(side_effect=RuntimeError("binary missing"))}
        )
        with patch.dict(TOOL_MAP, mocks):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        assert "⚠ Step error" in report_path.read_text(encoding="utf-8")

    async def test_error_step_does_not_crash(self, tmp_path):
        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        mocks = _make_tool_mocks(
            overrides={"search_domain": AsyncMock(side_effect=RuntimeError("binary missing"))}
        )
        with patch.dict(TOOL_MAP, mocks):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        assert report_path.exists()

    async def test_all_steps_fail_does_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
        monkeypatch.delenv("BRIGHTDATA_SERP_ZONE", raising=False)

        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        failing = {tool: AsyncMock(side_effect=RuntimeError("fail")) for tool in _ALL_CANNED}
        with patch.dict(TOOL_MAP, failing), patch("shutil.which", return_value=None):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        assert report_path.exists()

    async def test_empty_step_renders_no_results(self, tmp_path):
        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        mocks = _make_tool_mocks(overrides={"search_domain": AsyncMock(return_value="")})
        with patch.dict(TOOL_MAP, mocks):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        assert "No results found." in report_path.read_text(encoding="utf-8")

    async def test_pdf_skipped_when_disabled(self, tmp_path):
        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        pdf_mock = AsyncMock(return_value=None)
        with patch.dict(TOOL_MAP, _make_tool_mocks()), patch(
            "openosint.pdf_report.generate_pdf_report", pdf_mock
        ):
            await run_playbook(recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path)

        pdf_mock.assert_not_called()

    async def test_pdf_called_when_enabled(self, tmp_path):
        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        pdf_mock = AsyncMock(return_value=None)
        # Patch at the source module so the local import inside run_playbook picks it up.
        with patch.dict(TOOL_MAP, _make_tool_mocks()), patch(
            "openosint.pdf_report.generate_pdf_report", pdf_mock
        ):
            await run_playbook(recipe, "example.com", is_pdf_disabled=False, reports_dir=tmp_path)

        pdf_mock.assert_called_once()
        called_path = pdf_mock.call_args[0][0]
        assert isinstance(called_path, Path)
        assert called_path.suffix == ".md"

    async def test_report_filename_follows_convention(self, tmp_path):
        from openosint.playbooks.loader import load_recipe
        from openosint.playbooks.runner import TOOL_MAP, run_playbook

        recipe = load_recipe("domain")
        with patch.dict(TOOL_MAP, _make_tool_mocks()):
            report_path = await run_playbook(
                recipe, "example.com", is_pdf_disabled=True, reports_dir=tmp_path
            )

        assert re.match(
            r"\d{4}-\d{2}-\d{2}_.*_domain_report\.md", report_path.name
        ), f"Unexpected filename: {report_path.name}"


# ---------------------------------------------------------------------------
# TestPlaybookCLI
# ---------------------------------------------------------------------------


class TestPlaybookCLI:
    def test_playbook_subcommand_registered(self):
        from openosint.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["playbook", "domain", "example.com"])
        assert args.command == "playbook"
        assert args.recipe == "domain"
        assert args.target == "example.com"

    def test_no_pdf_flag_passes_through_to_playbook(self):
        from openosint.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--no-pdf", "playbook", "domain", "example.com"])
        assert args.is_pdf_disabled is True
