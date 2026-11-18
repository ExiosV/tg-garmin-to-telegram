"""Gmail seam for the service: google_kit's refresh-token Gmail client behind the local class name."""

from google_kit import Gmail as _Gmail
from google_kit import GoogleAuth


class Gmail:
    """Read-only Gmail for the LiveTrack mailbox, built from the three GOOGLE_* values in Config."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self._gmail = _Gmail(GoogleAuth(client_id, client_secret, refresh_token))

    def list_ids(self, query: str) -> list:
        return self._gmail.list_ids(query)

    def get(self, msg_id: str) -> dict:
        return self._gmail.get(msg_id)

    def email(self) -> str:
        return self._gmail.email()
