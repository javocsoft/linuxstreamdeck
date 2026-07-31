"""Stable Twitch identifiers, endpoints and the scopes this application asks for."""

# --- application identity ---

# A Client ID is a public identifier, not a secret. The device code flow exists
# precisely for clients that cannot keep a secret, so distributing this value is
# what makes the "just link my account" path possible at all, and Twitch's rate
# limit is counted per client ID *per user*, so one shared identifier cannot let
# one person exhaust anyone else's budget.
#
# This is LinuxStreamDeck's own registered application, so nobody has to create
# one to connect an account. A user who prefers their own registered
# application overrides it from the Twitch account dialog, and leaving this
# empty puts the dialog back into asking for one.
DEFAULT_CLIENT_ID = "btmxpmk13p66snuk3e8r7hu8at8k4y"

# --- endpoints ---

ID_BASE = "https://id.twitch.tv/oauth2"
DEVICE_URL = f"{ID_BASE}/device"
TOKEN_URL = f"{ID_BASE}/token"
VALIDATE_URL = f"{ID_BASE}/validate"
REVOKE_URL = f"{ID_BASE}/revoke"

HELIX_BASE = "https://api.twitch.tv/helix"

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

# --- authorization ---

# Only what the Phase 1 actions actually use. Asking for more than the feature
# needs is what makes an authorization screen frightening, and every extra scope
# is one the user has to be talked into.
SCOPES = (
    "channel:manage:broadcast",       # set the title and category, add a marker
    "clips:edit",                     # create a clip
    "moderator:read:followers",       # read the follower count
    "channel:edit:commercial",        # start an ad break
    "channel:manage:raids",           # start or cancel a raid
    "moderator:manage:announcements",  # post an announcement in chat
)

# Twitch reports the device flow's outcome as an RFC 8628 error **code** in the
# `message` field of a 400, not as prose: `authorization_pending` while the user
# has not finished, and the others below once something has actually happened.
#
# They are matched after normalizing, because the exact spelling is not
# something to rely on: this was first written expecting "authorization
# pending" with a space, and the underscore form meant the very first poll read
# a perfectly normal "not yet" as a refusal and abandoned the flow — while the
# user was still typing the code into Twitch.
PENDING_CODE = "authorization pending"
SLOW_DOWN_CODE = "slow down"

# What each outcome means in words. A code is an identifier, and showing one to
# somebody trying to connect an account tells them nothing they can act on; an
# unknown code is answered with a generic sentence rather than passed through.
DEVICE_ERROR_MESSAGES = {
    "access denied": "The authorization was declined on Twitch.",
    "expired token": "The code expired before it was entered. Try again.",
    "invalid device code": "That code is no longer valid. Start again.",
    "invalid client": (
        "Twitch did not recognise this application. Check the Client ID."
    ),
    "invalid grant": "That code is no longer valid. Start again.",
}
GENERIC_DEVICE_ERROR = "Twitch refused the authorization. Try again."

# How much of a token's remaining life is treated as "about to expire". Twitch
# user tokens last about four hours; refreshing a little early costs one request
# and avoids a key failing at the moment it is pressed.
REFRESH_MARGIN_SECONDS = 600.0

# Where a user removes the authorization itself. Twitch has no API for this:
# revoking a token kills that token and deliberately leaves the app-to-user
# link in place, so this page is the only way to undo the connection and the
# account dialog has to say so rather than claim to have done it.
CONNECTIONS_URL = "https://www.twitch.tv/settings/connections"

# --- naming ---

SERVICE_NAME = "Twitch"
