import json
import subprocess
import sys
import textwrap

from fastapi.exceptions import RequestValidationError

from app.asgi import validation_exception_handler


def test_validation_error_with_value_error_context_returns_json_response():
    error = RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body",),
                "msg": "Value error, model choice must not be blank",
                "input": {"model_choice": ""},
                "ctx": {"error": ValueError("model choice must not be blank")},
            }
        ]
    )

    response = validation_exception_handler(None, error)

    assert response.status_code == 400
    payload = json.loads(response.body)
    assert payload["message"] == "field required"
    assert payload["data"][0]["msg"] == "Value error, model choice must not be blank"


def test_asgi_import_survives_without_thumbnail_prompt_posix_capabilities():
    script = textwrap.dedent(
        """
        import builtins
        import json
        import os

        import app.services.cloud_agent.browser_lock

        real_import = builtins.__import__

        def block_fcntl(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("fcntl is unavailable on this simulated platform")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = block_fcntl
        for capability in ("O_DIRECTORY", "O_NOFOLLOW"):
            delattr(os, capability)

        from fastapi.testclient import TestClient

        from app.asgi import get_application
        from app.services.cloud_agent import factory
        from app.services.cloud_agent.thumbnail_prompt.errors import ThumbnailPromptError

        application = get_application()
        with TestClient(application) as client:
            other_route = client.get("/api/v1/cloud-agent/research/providers")
            thumbnail_route = client.get(
                "/api/v1/cloud-agent/thumbnail-prompt/settings"
            )

        factory_errors = []
        for service, method_name in (
            (factory.build_thumbnail_prompt_settings_service(), "get_settings"),
            (factory.build_thumbnail_prompt_service(), "generate_for_job"),
        ):
            try:
                if method_name == "generate_for_job":
                    getattr(service, method_name)("unused-job")
                else:
                    getattr(service, method_name)()
            except ThumbnailPromptError as exc:
                factory_errors.append(exc.code)

        print(json.dumps({
            "other_status": other_route.status_code,
            "thumbnail_status": thumbnail_route.status_code,
            "thumbnail_code": thumbnail_route.json()["data"]["code"],
            "factory_errors": factory_errors,
        }))
        """
    )

    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == {
        "other_status": 200,
        "thumbnail_status": 501,
        "thumbnail_code": "THUMBNAIL_PROMPT_PLATFORM_UNSUPPORTED",
        "factory_errors": [
            "THUMBNAIL_PROMPT_PLATFORM_UNSUPPORTED",
            "THUMBNAIL_PROMPT_PLATFORM_UNSUPPORTED",
        ],
    }
