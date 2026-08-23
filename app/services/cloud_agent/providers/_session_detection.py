from app.models.cloud_agent import ServiceSessionStatus


def classify_security_challenge(*, html: str) -> ServiceSessionStatus | None:
    """Return a conservative security-challenge state before any ready signal."""
    body = str(html or "").lower()

    active_captcha_prompt = any(
        marker in body
        for marker in (
            "not a robot",
            "captcha challenge",
            "complete the captcha",
            "solve the captcha",
        )
    )
    active_hcaptcha_check = "security check" in body and any(
        marker in body for marker in ("h-captcha", "hcaptcha")
    )
    if active_captcha_prompt or active_hcaptcha_check:
        return ServiceSessionStatus.CAPTCHA_REQUIRED

    if any(
        marker in body
        for marker in (
            "2-step verification",
            "two-factor authentication",
            "one-time-code",
            "verification code",
            "authentication code",
        )
    ):
        return ServiceSessionStatus.TWO_FACTOR_REQUIRED

    if any(
        marker in body
        for marker in (
            "verify it's you",
            "verify it’s you",
            "use your phone to confirm",
            "security verification",
        )
    ):
        return ServiceSessionStatus.VERIFICATION_REQUIRED

    if any(
        marker in body
        for marker in (
            'type="password"',
            "type='password'",
            "current-password",
            "enter your password",
        )
    ):
        return ServiceSessionStatus.LOGIN_REQUIRED

    return None
