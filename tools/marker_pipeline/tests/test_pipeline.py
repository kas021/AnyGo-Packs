import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.marker_pipeline.generate_clips import generate
from tools.marker_pipeline.models import (
    ClipWindow,
    Episode,
    Show,
    Marker,
    balanced_chunks,
    content_bounds,
    join_markers,
    normalize_title,
)
from tools.marker_pipeline.one_catalogue import (
    CatalogueNetworkError,
    NetworkOptions,
    enumerate_one_catalogue,
)


class PipelineTests(unittest.TestCase):
    def test_marker_join_by_mal_id_and_episode(self):
        show = Show(21, "One Piece", (Episode(21, 1, 1440),))
        marker = Marker("one piece", 21, "One Piece", 1, 0, 90, 1320, 1440, "episode")
        joined, review, failed = join_markers([show], [marker])
        self.assertEqual([(m.mal_id, m.episode) for m in joined], [(21, 1)])
        self.assertFalse(review)
        self.assertFalse(failed)

    def test_null_mal_id_is_review(self):
        show = Show(21, "One Piece", (Episode(21, 1, 1440),))
        marker = Marker("one piece", None, "One Piece", 1, None, 90, None, 1320, "episode")
        joined, review, failed = join_markers([show], [marker])
        self.assertEqual(joined[0].status, "review")
        self.assertEqual(review[0]["reason"], "null_mal_id")
        self.assertFalse(failed)

    def test_ambiguous_title_is_not_joined(self):
        shows = [Show(1, "Twin", (Episode(1, 1, 1440),)), Show(2, "Twin", (Episode(2, 1, 1440),))]
        marker = Marker("twin", None, "Twin", 1, None, 90, None, 1320, "episode")
        joined, review, failed = join_markers(shows, [marker])
        self.assertFalse(joined)
        self.assertEqual(review[0]["reason"], "ambiguous_title_match")
        self.assertFalse(failed)

    def test_intro_and_outro_removal(self):
        marker = Marker("x", 1, "X", 1, 0, 90, 1320, 1440, "episode")
        self.assertEqual(content_bounds(1440, marker), (90.0, 1320.0))

    def test_missing_markers_uses_full_episode(self):
        self.assertEqual(content_bounds(1440, None), (0.0, 1440.0))

    def test_invalid_marker_ranges_are_ignored(self):
        marker = Marker("x", 1, "X", 1, 1325, 1415, 0, 90, "episode")
        self.assertEqual(content_bounds(1380, marker), (0.0, 1380.0))

    def test_balanced_chunks_stay_in_bounds(self):
        chunks = balanced_chunks(90, 1320)
        self.assertEqual(chunks[0][0], 90)
        self.assertEqual(chunks[-1][1], 1320)
        self.assertTrue(all(120 <= end - start <= 300 for start, end in chunks))
        self.assertTrue(all(right[0] == left[1] for left, right in zip(chunks, chunks[1:])))

    def test_duplicate_marker_is_reported(self):
        show = Show(21, "One Piece", (Episode(21, 1, 1440),))
        marker = Marker("one piece", 21, "One Piece", 1, 0, 90, 1320, 1440, "episode")
        _, _, failed = join_markers([show], [marker, marker])
        self.assertEqual(failed[0]["reason"], "duplicate_join")

    def test_marker_episode_must_exist_in_catalogue(self):
        show = Show(21, "One Piece", (Episode(21, 1, 1440),))
        marker = Marker("one piece", 21, "One Piece", 2, 0, 90, 1320, 1440, "episode")
        joined, review, failed = join_markers([show], [marker])
        self.assertFalse(joined)
        self.assertFalse(review)
        self.assertEqual(failed[0]["reason"], "episode_not_in_catalogue")

    def test_title_normalization(self):
        self.assertEqual(normalize_title("  My_Hero-Academia "), "my hero academia")

    def test_resume_reuses_all_episode_parts(self):
        catalogue = {
            "schemaVersion": 1,
            "shows": [{"malId": 21, "title": "One Piece", "episodes": [{"episode": 1, "durationSec": 1440}]}],
        }
        manifest = {"records": []}
        first, first_summary = generate(catalogue, manifest)
        second, second_summary = generate(catalogue, manifest, first)
        self.assertEqual(first["clips"], second["clips"])
        self.assertEqual(first_summary["episodesGenerated"], 1)
        self.assertEqual(second_summary["episodesGenerated"], 0)
        self.assertEqual(second_summary["episodesReused"], 1)

    def test_invalid_combined_markers_fall_back_without_dropping_episode(self):
        catalogue = {
            "schemaVersion": 1,
            "shows": [{"malId": 21, "title": "One Piece", "episodes": [{"episode": 7, "durationSec": 300}]}],
        }
        manifest = {"records": [{
            "malId": 21,
            "episode": 7,
            "introStartSec": 0,
            "introEndSec": 200,
            "outroStartSec": 100,
            "outroEndSec": 300,
            "status": "matched",
        }]}
        output, summary = generate(catalogue, manifest)
        self.assertEqual(output["clips"][0]["startSec"], 0)
        self.assertEqual(output["clips"][-1]["endSec"], 300)
        self.assertEqual(summary["markerFallbackCount"], 1)

    def test_resume_regenerates_stale_episode_parts(self):
        catalogue = {
            "schemaVersion": 1,
            "shows": [{"malId": 21, "title": "One Piece", "episodes": [{"episode": 1, "durationSec": 300}]}],
        }
        stale = {"clips": [{
            "malId": 21, "episode": 1, "part": 1, "startSec": 0, "endSec": 180, "label": "E1 P1",
        }]}
        output, summary = generate(catalogue, {"records": []}, stale)
        self.assertNotEqual(output["clips"], stale["clips"])
        self.assertEqual(summary["episodesGenerated"], 1)
        self.assertEqual(summary["episodesReused"], 0)

    def test_network_catalogue_is_bounded_deterministic_and_dub_only(self):
        responses = {
            "https://one.example/v1/feed/more-fast?page=1": {
                "items": [{"malId": 21, "title": "One Piece", "unexpectedValue": "discard-me"}], "hasMore": True,
            },
            "https://one.example/v1/feed/more-fast?page=2": {
                "items": [{"malId": 20, "title": "Naruto"}], "hasMore": False,
            },
            "https://one.example/v1/anime/21/episodes": {
                "items": [
                    {"number": 2, "dubAvailable": True, "streamCachedDub": False},
                    {"number": 1, "dubAvailable": True, "streamCachedDub": True},
                    {"number": 3, "dubAvailable": False, "streamCachedDub": True},
                ],
            },
            "https://one.example/v1/anime/20/episodes": {
                "items": [{"number": 1, "dubAvailable": True, "streamCachedDub": True}],
            },
        }

        def fake_fetch(url, etag, options):
            return f'etag-{url.rsplit("/", 1)[-1]}', responses[url]

        offline = {
            "shows": [{
                "malId": 21,
                "title": "One Piece",
                "episodes": [{"episode": 1, "durationSec": 1380, "durationSource": "fixture"}],
            }],
        }
        with TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            options = NetworkOptions("https://one.example", checkpoint, workers=2)
            result = enumerate_one_catalogue(options, offline, fake_fetch)
            self.assertNotIn("unexpectedUrl", checkpoint.read_text())
            self.assertNotIn("m3u8", checkpoint.read_text())
        self.assertEqual([show["malId"] for show in result["shows"]], [20, 21])
        one_piece = result["shows"][1]["episodes"]
        self.assertEqual([episode["episode"] for episode in one_piece], [1, 2])
        self.assertEqual(one_piece[0]["durationSec"], 1380)
        self.assertEqual(one_piece[0]["availability"], "oneCached")
        self.assertEqual(one_piece[1]["availability"], "onDemand")
        self.assertEqual(one_piece[1]["durationSource"], "fallback-1440")

    def test_network_catalogue_requires_terminal_page(self):
        def fake_fetch(url, etag, options):
            return None, {"items": [], "hasMore": True}

        with TemporaryDirectory() as directory:
            options = NetworkOptions(
                "https://one.example",
                Path(directory) / "checkpoint.json",
                max_pages=2,
            )
            with self.assertRaisesRegex(CatalogueNetworkError, "max-pages"):
                enumerate_one_catalogue(options, {"shows": []}, fake_fetch)

    def test_network_catalogue_rejects_duplicate_episode(self):
        responses = {
            "https://one.example/v1/feed/more-fast?page=1": {
                "items": [{"malId": 21, "title": "One Piece"}], "hasMore": False,
            },
            "https://one.example/v1/anime/21/episodes": {
                "items": [
                    {"number": 1, "dubAvailable": True, "streamCachedDub": True},
                    {"number": 1, "dubAvailable": True, "streamCachedDub": True},
                ],
            },
        }

        def fake_fetch(url, etag, options):
            return None, responses[url]

        with TemporaryDirectory() as directory:
            options = NetworkOptions("https://one.example", Path(directory) / "checkpoint.json")
            with self.assertRaisesRegex(CatalogueNetworkError, "duplicate episode"):
                enumerate_one_catalogue(options, {"shows": []}, fake_fetch)


if __name__ == "__main__":
    unittest.main()
