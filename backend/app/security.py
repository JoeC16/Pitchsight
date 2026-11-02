"""HTTP-level protections: CSRF check for cookie-authenticated writes, and security headers."""
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from . import auth, config

CSRF_HEADER = "x-pitchsight"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # CSRF: a write that rides on the session cookie must carry our custom header. Another site
        # can't add a custom header to a cross-site request without a CORS preflight, which this API
        # never grants. Bearer-token callers (scripts) don't use the cookie, so aren't affected.
        if (request.method in WRITE_METHODS and request.url.path.startswith("/api/")
                and auth.COOKIE in request.cookies
                and not request.headers.get("authorization", "").lower().startswith("bearer ")
                and request.headers.get(CSRF_HEADER) != "1"):
            return JSONResponse({"detail": "Missing X-PitchSight header (CSRF protection)"}, status_code=403)
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        h.setdefault("Content-Security-Policy", content_security_policy())
        if config.SECURE_COOKIES:  # served over HTTPS
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def content_security_policy() -> str:
    # Videos stream from, and uploads go to, the bucket's signed URLs: allow that origin.
    import os
    bucket = ""
    if endpoint := os.environ.get("PITCHSIGHT_S3_ENDPOINT"):
        u = urlparse(endpoint)
        bucket = f" {u.scheme}://{u.netloc}"
    elif os.environ.get("PITCHSIGHT_STORAGE") == "s3":
        bucket = " https://*.amazonaws.com"
    return ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
            f"media-src 'self' blob:{bucket}; connect-src 'self'{bucket}; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'; object-src 'none'")
