import json
import unittest
from unittest.mock import patch

import httpx

from core import trakt


_REAL_ASYNC_CLIENT = httpx.AsyncClient


class TraktClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_history_movies_fetches_every_page(self) -> None:
        requested_pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/sync/history/movies")
            self.assertEqual(request.url.params["limit"], "250")
            self.assertEqual(request.headers["authorization"], "Bearer access-token")

            page = int(request.url.params["page"])
            requested_pages.append(page)
            page_items = {
                1: [{"id": index, "watched_at": "2026-07-15T20:00:00.000Z", "movie": {"ids": {"tmdb": index}}} for index in range(1, 251)],
                2: [{"id": index, "watched_at": "2026-07-15T20:00:00.000Z", "movie": {"ids": {"tmdb": index}}} for index in range(251, 501)],
                3: [{"id": index, "watched_at": "2026-07-15T20:00:00.000Z", "movie": {"ids": {"tmdb": index}}} for index in range(501, 518)],
            }[page]
            return httpx.Response(
                200,
                json=page_items,
                headers={"X-Pagination-Page-Count": "3"},
            )

        transport = httpx.MockTransport(handler)
        with patch.object(
            trakt.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            plays = await trakt.get_history_movies("client-id", "access-token")

        self.assertEqual(requested_pages, [1, 2, 3])
        self.assertEqual(len(plays), 517)
        self.assertEqual(plays[-1]["movie"]["ids"]["tmdb"], 517)


    async def test_get_history_movies_returns_multiple_plays_of_same_title(self) -> None:
        """Regression test for #61/#77: a title watched more than once must
        appear as multiple distinct history entries, not collapse to one."""

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            if page > 1:
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "watched_at": "2026-01-01T20:00:00.000Z", "movie": {"ids": {"tmdb": 42}}},
                    {"id": 2, "watched_at": "2026-06-01T20:00:00.000Z", "movie": {"ids": {"tmdb": 42}}},
                ],
                headers={"X-Pagination-Page-Count": "1"},
            )

        transport = httpx.MockTransport(handler)
        with patch.object(
            trakt.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            plays = await trakt.get_history_movies("client-id", "access-token")

        self.assertEqual(len(plays), 2)
        self.assertEqual({p["watched_at"] for p in plays}, {"2026-01-01T20:00:00.000Z", "2026-06-01T20:00:00.000Z"})


    async def test_get_history_episodes_fetches_every_page(self) -> None:
        requested_pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/sync/history/episodes")
            self.assertEqual(request.url.params["limit"], "250")
            page = int(request.url.params["page"])
            requested_pages.append(page)
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 9001,
                        "watched_at": "2026-07-15T20:00:00.000Z",
                        "episode": {"season": 1, "number": 1},
                        "show": {"title": "Some Show", "ids": {"tmdb": 1396}},
                    }
                ],
                headers={"X-Pagination-Page-Count": "1"},
            )

        transport = httpx.MockTransport(handler)
        with patch.object(
            trakt.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            plays = await trakt.get_history_episodes("client-id", "access-token")

        self.assertEqual(requested_pages, [1])
        self.assertEqual(plays[0]["episode"]["number"], 1)


    async def test_get_history_movies_stops_without_page_count_header(self) -> None:
        """Regression test: a non-paginating response that omits
        X-Pagination-Page-Count must not be re-requested forever."""
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(
                200,
                json=[{"id": 1, "watched_at": "2026-07-15T20:00:00.000Z", "movie": {"ids": {"tmdb": 1}}}],
            )

        transport = httpx.MockTransport(handler)
        with patch.object(
            trakt.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            plays = await trakt.get_history_movies("client-id", "access-token")

        self.assertEqual(request_count, 1)
        self.assertEqual(len(plays), 1)


    async def test_get_ratings_fetches_movies_shows_and_seasons(self) -> None:
        requested: list[tuple[str, int]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            kind = request.url.path.rsplit("/", 1)[-1]
            page = int(request.url.params["page"])
            requested.append((kind, page))
            payload = {
                "movies": {"movie": {"ids": {"tmdb": 550}}},
                "shows": {"show": {"ids": {"tmdb": 1396}}},
                "seasons": {
                    "show": {"ids": {"tmdb": 1396}},
                    "season": {"number": page, "ids": {"tmdb": 3572 + page - 1}},
                },
            }
            return httpx.Response(
                200,
                json=[payload[kind]],
                headers={"X-Pagination-Page-Count": "2"},
            )

        transport = httpx.MockTransport(handler)
        with patch.object(
            trakt.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            ratings = await trakt.get_ratings("client-id", "access-token")

        self.assertCountEqual(
            requested,
            [
                ("movies", 1),
                ("movies", 2),
                ("shows", 1),
                ("shows", 2),
                ("seasons", 1),
                ("seasons", 2),
            ],
        )
        self.assertEqual(len(ratings["seasons"]), 2)
        self.assertEqual(ratings["seasons"][1]["season"]["number"], 2)

    async def test_rating_batches_preserve_season_tmdb_ids(self) -> None:
        requests: list[tuple[str, dict]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"added": {}, "deleted": {}})

        transport = httpx.MockTransport(handler)
        with patch.object(
            trakt.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
        ):
            await trakt.set_ratings_batch(
                "client-id",
                "access-token",
                [(550, 8.4)],
                [(1396, 9.0)],
                [(3572, 7.6)],
            )
            await trakt.remove_ratings_batch(
                "client-id",
                "access-token",
                [550],
                [1396],
                [3572],
            )

        self.assertEqual(requests[0][0], "/sync/ratings")
        self.assertEqual(
            requests[0][1]["seasons"],
            [{"rating": 8, "ids": {"tmdb": 3572}}],
        )
        self.assertEqual(requests[1][0], "/sync/ratings/remove")
        self.assertEqual(
            requests[1][1]["seasons"],
            [{"ids": {"tmdb": 3572}}],
        )

if __name__ == "__main__":
    unittest.main()
