import json

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
