import httpx

from lead_agent.infrastructure.communications import CommunicationsClient


def test_cloudtalk_number_inventory_paginates_and_returns_unique_e164_numbers():
    seen_pages = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen_pages.append(request.url.params.get("page"))
        assert request.url.path == "/api/numbers/index.json"
        assert request.headers["accept"] == "application/json"
        if request.url.params.get("page") == "1":
            data = [
                {"id": 11, "number": "+14155550123"},
                {"Number": {"phone_number": "+442071838750"}},
            ]
        else:
            data = [
                {"id": 13, "number": "+14155550123"},
                {"id": 14, "name": "Number without a phone value"},
            ]
        return httpx.Response(
            200,
            json={
                "responseData": {
                    "pageNumber": int(request.url.params["page"]),
                    "pageCount": 2,
                    "limit": 1000,
                    "data": data,
                }
            },
        )

    credentials = {"access_key_id": "test-key", "access_key_secret": "test-secret"}
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        numbers = CommunicationsClient(
            "cloudtalk", credentials, client=client
        ).list_cloudtalk_numbers()

    assert numbers == ["+14155550123", "+442071838750"]
    assert seen_pages == ["1", "2"]
