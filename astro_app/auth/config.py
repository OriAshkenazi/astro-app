"""Sign in with ChatGPT, open-source client flow: endpoints and fixed parameters.

Source: https://developers.openai.com/siwc/token-sharing-open-source (registration and sign-in,
accounts and sessions, token reference, errors and recovery, preview limitations).
"""

ISSUER = "https://auth.openai.com"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"

DYNAMIC_CLIENT = "dynamic_agent_client"  # first-time registration entry point, never saved
AGENT_NAME = "astro-app"                 # agent_name_hint: same on every installation

IDENTITY_SCOPES = ("openid", "profile", "email")
PLAN_SCOPES = ("offline_access", "resource.invoke", "chatgpt.tokens.use.direct")
PLAN_SCOPE = "chatgpt.tokens.use.direct"  # without it, sign-in is kept but inference is disabled
SCOPE = " ".join(IDENTITY_SCOPES + PLAN_SCOPES)

CALLBACK_HOST = "127.0.0.1"  # never "localhost"
CALLBACK_PATH = "/auth/callback"
DEFAULT_PORT = 1455          # only the port may vary between sign-ins

PENDING_TTL_S = 600          # an authorization attempt expires after 10 minutes
REFRESH_MARGIN_S = 120       # refresh this long before the access token expires
CLOCK_SKEW_S = 5


def redirect_uri(port: int) -> str:
    return f"http://{CALLBACK_HOST}:{port}{CALLBACK_PATH}"
