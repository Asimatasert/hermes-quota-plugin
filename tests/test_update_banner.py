from __future__ import annotations

import unittest

from widget_harness import render, text


class UpdateBannerTests(unittest.TestCase):
    def test_banner_shows_catalog_and_latest_github_versions_separately(self):
        tree = render(component="update", update={
            "catalogVersion": "2.10.0",
            "githubVersion": "2.12.0",
            "installedVersion": "2.11.3",
            "seen": "installed",
            "githubSha": "latest",
        })
        rendered = text(tree)
        self.assertIn("Catalog: 2.10.0", rendered)
        self.assertIn("GitHub: 2.12.0", rendered)
        self.assertIn("older than installed", rendered)
        self.assertIn("newer commit available", rendered)

    def test_update_button_opens_github_releases_through_desktop_bridge(self):
        tree = render(component="update", clickLinks=True, update={
            "catalogVersion": "2.10.0",
            "githubVersion": "2.12.0",
            "installedVersion": "2.11.3",
            "seen": "installed",
            "githubSha": "latest",
        })
        self.assertEqual(tree["openedExternal"], ["https://github.com/rarf/hermes-quota-plugin/releases"])


if __name__ == "__main__":
    unittest.main()
