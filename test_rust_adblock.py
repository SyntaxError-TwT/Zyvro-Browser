import threading
import unittest

from rust_adblock_engine import RustAdblockEngine


class RustAdblockEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = RustAdblockEngine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_native_engine_and_filter_lists_load(self):
        self.assertTrue(self.engine.available, self.engine.error)
        self.assertEqual(self.engine.version, "adblock-rust 0.13.3")
        self.assertEqual(self.engine.list_count, 7)

    def test_known_ad_script_is_blocked(self):
        self.assertTrue(
            self.engine.should_block(
                "https://securepubads.g.doubleclick.net/tag/js/gpt.js",
                "securepubads.g.doubleclick.net",
                "example.com",
                "script",
                "GET",
                "https://example.com/",
            )
        )

    def test_google_logo_and_generic_banner_are_not_blocked(self):
        for url in (
            "https://www.google.com/images/branding/googlelogo/2x/"
            "googlelogo_color_272x92dp.png",
            "https://example.com/banner/logo.png",
        ):
            with self.subTest(url=url):
                self.assertFalse(
                    self.engine.should_block(
                        url, url.split("/")[2], "example.com", "image",
                        "GET", "https://example.com/",
                    )
                )

    def test_cosmetic_and_generic_rules_are_available(self):
        resources = self.engine.cosmetic_resources(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        self.assertGreater(len(resources.get("hide_selectors", [])), 0)
        selectors = self.engine.generic_selectors(
            ["advertisement"], [], resources.get("exceptions", [])
        )
        self.assertTrue(any("advertisement" in item for item in selectors))

    def test_youtube_document_start_scriptlets_are_bundled(self):
        main, isolated = self.engine.supplemental_scriptlets("www.youtube.com")
        self.assertIn("adPlacements", main)
        self.assertGreater(len(isolated), 100_000)
        self.assertEqual(self.engine.supplemental_scriptlets("www.google.com"), ("", ""))

    def test_request_checks_are_thread_safe(self):
        outcomes = []

        def check():
            outcomes.append(
                self.engine.should_block(
                    "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js",
                    "pagead2.googlesyndication.com", "example.com", "script",
                    "GET", "https://example.com/",
                )
            )

        threads = [threading.Thread(target=check) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes, [True] * len(threads))


if __name__ == "__main__":
    unittest.main()

