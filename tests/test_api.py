"""Tests for api.py — status-code mapping, pagination, and the upload invariant.

komoot's network is mocked with a fake session so these run offline. The most
important guard here is `test_upload_sends_no_content_type`: sending any
Content-Type on the upload POST makes komoot return HTTP 400 (this broke every
upload until it was removed — see CLAUDE.md / TASKS.md task 15).
"""

import unittest

import requests

from komoot_bulk_upload.api import (
    KomootClient, KomootError, KomootAuthError, UploadResult,
)

_RAISE = object()


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is _RAISE:
            raise ValueError("no json body")
        return self._json if self._json is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP {}".format(self.status_code))


class FakeSession:
    """Records calls and returns canned responses; `get` is page-aware."""

    def __init__(self):
        self.calls = []
        self.post_response = None
        self.delete_response = None
        self.get_handler = None  # callable(page) -> FakeResponse

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self.post_response

    def delete(self, url, **kwargs):
        self.calls.append(("delete", url, kwargs))
        return self.delete_response

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        page = kwargs.get("params", {}).get("page", 0)
        return self.get_handler(page)


def make_client():
    """A client with a fake session, pre-authenticated (no real sign-in)."""
    client = KomootClient("user@example.com", token="tok")
    client.session = FakeSession()
    client.username = "12345"  # numeric user id captured at sign-in
    return client


class UploadTourTests(unittest.TestCase):
    def test_201_is_created_with_tour_id(self):
        c = make_client()
        c.session.post_response = FakeResponse(201, {"id": 99})
        result = c.upload_tour(b"<gpx/>", name="Ride", sport="racebike")
        self.assertEqual(result.status, UploadResult.CREATED)
        self.assertTrue(result.created)
        self.assertEqual(result.tour_id, 99)

    def test_202_is_duplicate(self):
        c = make_client()
        c.session.post_response = FakeResponse(202, {"id": 7})
        result = c.upload_tour(b"<gpx/>", name="Ride", sport="racebike")
        self.assertEqual(result.status, UploadResult.DUPLICATE)
        self.assertFalse(result.created)

    def test_auth_errors_raise(self):
        for code in (401, 403):
            c = make_client()
            c.session.post_response = FakeResponse(code)
            with self.assertRaises(KomootAuthError):
                c.upload_tour(b"<gpx/>", name="Ride", sport="racebike")

    def test_other_errors_raise_komoot_error(self):
        c = make_client()
        c.session.post_response = FakeResponse(400, text="HttpMessageNotReadable")
        with self.assertRaises(KomootError):
            c.upload_tour(b"<gpx/>", name="Ride", sport="racebike")

    def test_invalid_data_type_rejected_before_network(self):
        c = make_client()
        with self.assertRaises(KomootError):
            c.upload_tour(b"x", name="Ride", sport="racebike", data_type="kml")
        self.assertEqual(c.session.calls, [])  # never hit the network

    def test_tour_id_none_when_body_not_json(self):
        c = make_client()
        c.session.post_response = FakeResponse(201, _RAISE)
        self.assertIsNone(c.upload_tour(b"x", name="R", sport="racebike").tour_id)

    def test_upload_sends_no_content_type(self):
        """Regression: the upload POST must carry no Content-Type header."""
        c = make_client()
        c.session.post_response = FakeResponse(201, {"id": 1})
        c.upload_tour(b"<gpx/>", name="Ride", sport="racebike")
        _, url, kwargs = c.session.calls[0]
        headers = kwargs.get("headers") or {}
        self.assertNotIn("Content-Type", {k.title() for k in headers})
        # body is the raw bytes; format/sport/name go as query params
        self.assertEqual(kwargs["data"], b"<gpx/>")
        self.assertEqual(kwargs["params"]["data_type"], "gpx")
        self.assertEqual(kwargs["params"]["sport"], "racebike")

    def test_time_in_motion_passed_only_when_given(self):
        c = make_client()
        c.session.post_response = FakeResponse(201, {"id": 1})
        c.upload_tour(b"x", name="R", sport="racebike", time_in_motion=1800)
        self.assertEqual(c.session.calls[0][2]["params"]["time_in_motion"], 1800)

        c2 = make_client()
        c2.session.post_response = FakeResponse(201, {"id": 1})
        c2.upload_tour(b"x", name="R", sport="racebike")
        self.assertNotIn("time_in_motion", c2.session.calls[0][2]["params"])


def page_response(tours, total_pages, key="tours"):
    return FakeResponse(200, {"_embedded": {key: tours},
                              "page": {"totalPages": total_pages}})


class ListToursTests(unittest.TestCase):
    def test_aggregates_across_pages(self):
        c = make_client()
        pages = {
            0: page_response([{"id": 1}, {"id": 2}], total_pages=2),
            1: page_response([{"id": 3}], total_pages=2),
        }
        c.session.get_handler = lambda page: pages[page]
        ids = [t["id"] for t in c.list_tours()]
        self.assertEqual(ids, [1, 2, 3])

    def test_stops_at_total_pages(self):
        c = make_client()
        calls = []

        def handler(page):
            calls.append(page)
            return page_response([{"id": page}], total_pages=1)

        c.session.get_handler = handler
        list(c.list_tours())
        self.assertEqual(calls, [0])  # only one page fetched

    def test_lenient_about_items_key(self):
        c = make_client()
        c.session.get_handler = lambda page: page_response(
            [{"id": 5}], total_pages=1, key="items")
        self.assertEqual([t["id"] for t in c.list_tours()], [5])

    def test_empty_page_stops(self):
        c = make_client()
        c.session.get_handler = lambda page: page_response([], total_pages=9)
        self.assertEqual(list(c.list_tours()), [])

    def test_auth_error_raises(self):
        c = make_client()
        c.session.get_handler = lambda page: FakeResponse(403)
        with self.assertRaises(KomootAuthError):
            list(c.list_tours())


class GetTourTests(unittest.TestCase):
    def test_returns_tour_json_and_requests_coordinates(self):
        c = make_client()
        c.session.get_handler = lambda page: FakeResponse(200, {"id": 7})
        self.assertEqual(c.get_tour(7), {"id": 7})
        _, url, kwargs = c.session.calls[0]
        self.assertIn("/tours/7", url)
        self.assertEqual(kwargs["params"], {"_embedded": "coordinates"})

    def test_404_raises_komoot_error(self):
        c = make_client()
        c.session.get_handler = lambda page: FakeResponse(404)
        with self.assertRaises(KomootError):
            c.get_tour(7)

    def test_auth_codes_raise_auth_error(self):
        for code in (401, 403):
            c = make_client()
            c.session.get_handler = lambda page, code=code: FakeResponse(code)
            with self.assertRaises(KomootAuthError):
                c.get_tour(7)


class DeleteTourTests(unittest.TestCase):
    def test_success_codes_return_true(self):
        for code in (200, 204):
            c = make_client()
            c.session.delete_response = FakeResponse(code)
            self.assertTrue(c.delete_tour(42))
            self.assertIn("/tours/42", c.session.calls[0][1])

    def test_404_raises_komoot_error(self):
        c = make_client()
        c.session.delete_response = FakeResponse(404)
        with self.assertRaises(KomootError):
            c.delete_tour(42)

    def test_auth_codes_raise_auth_error(self):
        for code in (401, 403):
            c = make_client()
            c.session.delete_response = FakeResponse(code)
            with self.assertRaises(KomootAuthError):
                c.delete_tour(42)

    def test_other_error_raises(self):
        c = make_client()
        c.session.delete_response = FakeResponse(500, text="boom")
        with self.assertRaises(KomootError):
            c.delete_tour(42)


if __name__ == "__main__":
    unittest.main()
