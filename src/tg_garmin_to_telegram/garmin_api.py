"""Garmin Connect Personal API (official, self-service): the user generates an API key in their
Garmin Connect settings (https://connect.garmin.com/app/settings/personalApiToken, read-only, their
own data), the key is exchanged for a bearer token, and the bearer calls the endpoints below.
Scope here: the activity list and detail — they drive the ride-end matching for the photo albums. GPX/FIT
download is intentionally not ported (this service uploads no traces). The user's api key lives in
users.garmin_api_key (set during onboarding).
Docs: developer.garmin.com/gc-developer-program/personal/personal-api."""
import json
import threading

import requests



BASE = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
SEARCH_PATH = "/activitylist-service/activities/search/activities"
DETAIL_PATH = "/activity-service/activity/{id}"


class GarminApiError(RuntimeError):
    def __init__(self, what: str, status: int, detail: str = ""):
        super().__init__(f"{what}: HTTP {status}" + (f": {detail}" if detail else ""))
        self.what, self.status, self.detail = what, status, detail


def is_auth_error(exc: Exception) -> bool:
    return isinstance(exc, GarminApiError) and exc.status in (401, 403)


class GarminApiClient:
    """Official Personal API client for one user's API key. The bearer token is exchanged lazily and
    re-exchanged once on auth failures (key expired/revoked -> the caller asks for a new key)."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._bearer = None
        self._lock = threading.Lock()

    def _token(self) -> str:
        with self._lock:
            if not self._bearer:
                r = requests.post(BASE + TOKEN_PATH, headers={"Authorization": f"ApiKey {self.api_key}"},
                                  timeout=30)
                if r.status_code != 200:
                    raise GarminApiError("api key exchange failed", r.status_code, r.text[:200])
                token = r.text.strip().strip('"')
                if not token:
                    raise GarminApiError("api key exchange failed", r.status_code, "empty token")
                self._bearer = token
            return self._bearer

    def _reset_token(self) -> None:
        with self._lock:
            self._bearer = None

    def _get(self, path: str, ok=(200,), timeout: int = 120) -> requests.Response:
        r = requests.get(BASE + path, headers={"Authorization": f"Bearer {self._token()}"},
                         timeout=timeout)
        if r.status_code in (401, 403):  # token expired: one clean re-exchange, then give up
            self._reset_token()
            r = requests.get(BASE + path, headers={"Authorization": f"Bearer {self._token()}"},
                             timeout=timeout)
        if r.status_code not in ok:
            raise GarminApiError(f"GET {path.split('?')[0]} failed", r.status_code, r.text[:200])
        return r

    # -- the same duck-typed surface GarminClient offers to the runner -------------------------------

    def recent_activities(self, limit: int = 10) -> list:
        return self.activities_page(0, limit)

    def activities_page(self, offset: int, limit: int = 100) -> list:
        r = self._get(f"{SEARCH_PATH}?start={int(offset)}&limit={int(limit)}")
        return json.loads(r.text) or []

    def activity(self, activity_id) -> dict:
        r = self._get(DETAIL_PATH.format(id=int(activity_id)))
        return json.loads(r.text)
