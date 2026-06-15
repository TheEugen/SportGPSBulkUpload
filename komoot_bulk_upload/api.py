"""Thin client over komoot's undocumented internal API.

These endpoints are not officially supported and may change without notice.
"""

from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth

API_BASE = "https://api.komoot.de"
SIGNIN_URL = API_BASE + "/v006/account/email/{email}/"
UPLOAD_URL = API_BASE + "/v007/tours/"
TOURS_URL = API_BASE + "/v007/users/{user_id}/tours/"
TOUR_URL = API_BASE + "/v007/tours/{tour_id}"

# Public web page for a tour id (handy for the duplicate report).
TOUR_WEB_URL = "https://www.komoot.com/tour/{tour_id}"

# Common komoot sport identifiers (not exhaustive). See README.
SPORTS = (
    "touringbicycle",   # Bike Touring
    "e_touringbicycle",
    "racebike",         # Road Cycling
    "e_racebike",
    "mtb",              # Mountain Biking
    "e_mtb",
    "mtb_easy",         # Gravel
    "citybike",
    "hike",
    "jogging",
)

PRIVACY = ("private", "friends", "public")

# German default tour names per sport, used when the user gives no name.
GERMAN_SPORT_NAMES = {
    "touringbicycle": "Fahrradtour",
    "e_touringbicycle": "E-Bike-Tour",
    "racebike": "Rennradtour",
    "e_racebike": "E-Rennradtour",
    "mtb": "Mountainbike-Tour",
    "e_mtb": "E-Mountainbike-Tour",
    "mtb_easy": "Gravel-Tour",
    "citybike": "Citybike-Tour",
    "hike": "Wanderung",
    "jogging": "Laufen",
}


def german_activity_name(sport):
    """German default tour name for a komoot sport id (fallback: 'Aktivität')."""
    return GERMAN_SPORT_NAMES.get(sport, "Aktivität")


# File formats komoot's importer recognizes. GPX and TCX are uploaded raw with
# their own value as the `data_type` query param; FIT is gated (see payload.py).
DATA_TYPES = ("gpx", "tcx", "fit")


def data_type_for(path):
    """Return the komoot data_type key for a file, by extension (or None)."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return ext if ext in DATA_TYPES else None


class KomootError(Exception):
    """Base error for komoot API problems."""


class KomootAuthError(KomootError):
    """Raised when sign-in fails (bad credentials / token)."""


class UploadResult:
    CREATED = "created"
    DUPLICATE = "duplicate"

    def __init__(self, status, tour_id=None, response=None):
        self.status = status
        self.tour_id = tour_id
        self.response = response

    @property
    def created(self):
        return self.status == self.CREATED


class KomootClient:
    """Authenticate against komoot and upload GPX tours one at a time."""

    def __init__(self, email, password=None, token=None, user_agent=None):
        if not (password or token):
            raise ValueError("Provide either a password or a token.")
        self.email = email
        self._password = password
        self.token = token
        self.username = None
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent or "SportGPSBulkUpload/1.9"
        # When a token is supplied we can authenticate directly.
        self.auth = HTTPBasicAuth(email, token) if token else None

    def signin(self):
        """Validate credentials and capture the session token + user id.

        Safe to skip when a token was supplied to the constructor.
        """
        if self.token and self._password is None:
            return self.username  # already have a usable token

        resp = self.session.get(
            SIGNIN_URL.format(email=quote(self.email, safe="")),
            auth=HTTPBasicAuth(self.email, self._password),
        )
        if resp.status_code in (401, 403):
            raise KomootAuthError("komoot rejected the email/password.")
        resp.raise_for_status()
        data = resp.json()

        self.username = data.get("username") or data.get("user", {}).get("username")
        self.token = data.get("password") or self.token
        # Post sign-in, komoot expects Basic Auth with (email, token).
        self.auth = HTTPBasicAuth(self.email, self.token or self._password)
        return self.username

    def upload_tour(self, data, name, sport, data_type="gpx", status="private",
                    time_in_motion=None):
        """Upload one activity. `data` is the raw GPX bytes.

        Returns an UploadResult or raises KomootError. NOTE: the body is sent
        with NO Content-Type header on purpose — komoot's backend reads the raw
        GPX bytes, and declaring a content type (e.g. application/xml) makes it
        try to deserialize the body and fail with HTTP 400 HttpMessageNotReadable.
        """
        if self.auth is None:
            self.signin()

        if data_type not in DATA_TYPES:
            raise KomootError("Unsupported data_type: {!r}".format(data_type))

        params = {"data_type": data_type, "sport": sport, "name": name,
                  "status": status}
        if time_in_motion:
            params["time_in_motion"] = int(time_in_motion)

        resp = self.session.post(
            UPLOAD_URL,
            params=params,
            data=data,
            auth=self.auth,
        )

        if resp.status_code == 201:
            return UploadResult(UploadResult.CREATED, _tour_id(resp), resp)
        if resp.status_code == 202:
            return UploadResult(UploadResult.DUPLICATE, _tour_id(resp), resp)
        if resp.status_code in (401, 403):
            raise KomootAuthError("Not authorized to upload (session expired?).")
        raise KomootError(
            "Upload failed (HTTP {}): {}".format(resp.status_code, resp.text[:300])
        )

    def list_tours(self, tour_type="tour_recorded", page_size=50):
        """Yield the account's tours (default: recorded activities) as raw dicts.

        Paginates komoot's HAL+JSON tour list at
        `GET /v007/users/{user_id}/tours/`. Requires a prior sign-in for the
        numeric user id and token auth. Undocumented and brittle like the rest
        of this module — kept here so a break is a one-file fix.
        """
        if self.auth is None or not self.username:
            self.signin()
        if not self.username:
            raise KomootError("No komoot user id available; sign in first.")

        url = TOURS_URL.format(user_id=quote(str(self.username), safe=""))
        page = 0
        while True:
            params = {"type": tour_type, "sort_field": "date",
                      "sort_direction": "desc", "page": page, "limit": page_size}
            resp = self.session.get(url, params=params, auth=self.auth)
            if resp.status_code in (401, 403):
                raise KomootAuthError("Not authorized to list tours (session expired?).")
            resp.raise_for_status()
            body = resp.json()

            embedded = body.get("_embedded") or {}
            # The recorded-tours list nests tours under "tours"; be lenient about
            # the exact key in case komoot returns "items" on some accounts.
            tours = embedded.get("tours") or embedded.get("items") or []
            for tour in tours:
                yield tour

            if not tours:
                break
            page_info = body.get("page") or {}
            total_pages = page_info.get("totalPages")
            if total_pages is not None:
                if page + 1 >= total_pages:
                    break
            elif not (body.get("_links") or {}).get("next"):
                break
            page += 1

    def get_tour(self, tour_id, embedded="coordinates"):
        """Fetch one tour via `GET /v007/tours/{id}` (with embedded track data).

        Returns the raw tour dict (its `_embedded.coordinates.items` hold the
        lat/lng/alt/time points). Used to back a tour up locally before deleting.
        """
        if self.auth is None:
            self.signin()
        url = TOUR_URL.format(tour_id=quote(str(tour_id), safe=""))
        params = {"_embedded": embedded} if embedded else None
        resp = self.session.get(url, params=params, auth=self.auth)
        if resp.status_code in (401, 403):
            raise KomootAuthError(
                "Not authorized to read tour {} (session expired?).".format(tour_id))
        if resp.status_code == 404:
            raise KomootError("Tour {} not found.".format(tour_id))
        resp.raise_for_status()
        return resp.json()

    def delete_tour(self, tour_id):
        """Delete one tour by id via `DELETE /v007/tours/{tour_id}`.

        Returns True on success; raises KomootError otherwise. Destructive and
        irreversible — callers must confirm with the user first.
        """
        if self.auth is None:
            self.signin()
        url = TOUR_URL.format(tour_id=quote(str(tour_id), safe=""))
        resp = self.session.delete(url, auth=self.auth)
        if resp.status_code in (200, 204):
            return True
        if resp.status_code == 404:
            raise KomootError("Tour {} not found (already deleted?).".format(tour_id))
        if resp.status_code in (401, 403):
            raise KomootAuthError(
                "Not authorized to delete tour {} (session expired?).".format(tour_id))
        raise KomootError(
            "Delete failed (HTTP {}): {}".format(resp.status_code, resp.text[:200]))


def _tour_id(resp):
    try:
        return resp.json().get("id")
    except ValueError:
        return None
