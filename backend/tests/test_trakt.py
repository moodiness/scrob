import unittest
from unittest.mock import patch

import httpx

from core import trakt


_REAL_ASYNC_CLIENT = httpx.AsyncClient


class TraktClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_watched_movies_fetches_every_page(self) -> None:
        requested_pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/sync/watched/movies")
            self.assertEqual(request.url.params["limit"], "100")
            self.assertEqual(request.headers["authorization"], "Bearer access-token")

            page = int(request.url.params["page"])
            requested_pages.append(page)
            page_items = {
                1: [{"movie": {"ids": {"tmdb": index}}} for index in range(1, 101)],
                2: [{"movie": {"ids": {"tmdb": index}}} for index in range(101, 201)],
                3: [{"movie": {"ids": {"tmdb": index}}} for index in range(201, 218)],
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
            movies = await trakt.get_watched_movies("client-id", "access-token")

        self.assertEqual(requested_pages, [1, 2, 3])
        self.assertEqual(len(movies), 217)
        self.assertEqual(movies[-1]["movie"]["ids"]["tmdb"], 217)


if __name__ == "__main__":
    unittest.main()
